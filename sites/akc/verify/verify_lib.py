#!/usr/bin/env python3
"""verify_lib.py — shared deterministic + LLM utilities for the AKC task verifiers.

Philosophy: DETERMINISTIC FIRST, and GROUND TRUTH DERIVED FROM THE RUN'S OWN
INITIAL DB rather than frozen into the verifier.

  1. Navigation check (anti knowledge-shortcut): the agent MUST have opened the
     page that carries the answer. A correct answer with no matching navigation
     is recall, not task completion.
  2. Answer check against ground truth read out of `--initial_db` for this run,
     so a seed refresh cannot silently invalidate the expectation. Only facts
     that do not live in the DB at all are written as constants, and each such
     constant names its source.
  3. After-state check for stateful tasks: query the live SQLite DB directly.
  4. LLM utilities are anchored — the model is handed the derived ground truth
     and asked only whether the agent's text matches it. It never supplies facts.

Input signature (per task):
  --run_dir DIR      agent trajectory dir: trajectory.json + screenshots/step_NNN.png
  --initial_db PATH  initial-state SQLite DB (default: instance_seed from the container)
  --after_db PATH    after-state SQLite DB (default: live instance DB from the container)
  --container NAME   docker container to read DBs from (default: $WH_CONTAINER)
  --no_llm           deterministic-only
Output: JSON {task_id, pass, reason, evidence[]} on stdout; exit 0 PASS / 1 FAIL.
"""
import base64, json, os, re, sqlite3, subprocess, sys, tempfile, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

SITE = "akc"


# ---------------------------------------------------------------- trajectory
def _bail(task_id, reason, detail) -> NoReturn:
    """Structured FAIL for malformed input — never a traceback."""
    print(json.dumps({"task_id": task_id, "pass": False, "reason": reason,
                      "evidence": [f"[FAIL] {detail}"]}, indent=2))
    sys.exit(1)


def load_run(run_dir, task_id=""):
    d = Path(run_dir)
    traj_path = d / "trajectory.json"
    if not traj_path.exists():
        _bail(task_id, "malformed_run_dir", f"no trajectory.json under {d}")
    try:
        traj = json.loads(traj_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        _bail(task_id, "malformed_trajectory", f"trajectory.json unreadable: {exc}")
    if not isinstance(traj, dict):
        _bail(task_id, "malformed_trajectory", "trajectory.json is not an object")
    traj.setdefault("steps", [])
    traj["_run_dir"] = d
    traj["_shots"] = {p.name: p for p in sorted((d / "screenshots").glob("step_*.png"))}
    return traj


def _steps(traj):
    steps = traj.get("steps")
    return steps if isinstance(steps, list) else []


def visited_urls(traj):
    """Every URL the run touched.

    A step's own `url` is the page the action started from, so a navigation
    target only shows up in the following step. Reading the navigate params too
    means a run that ends with `done` on the target page still counts.
    """
    urls = []
    for step in _steps(traj):
        if not isinstance(step, dict):
            continue
        if step.get("url"):
            urls.append(str(step["url"]))
        params = step.get("params") or {}
        if isinstance(params, dict):
            for key in ("url", "href", "link"):
                if params.get(key):
                    urls.append(str(params[key]))
    return urls


def navigated_to(traj, substr, times=1):
    return sum(1 for u in visited_urls(traj) if substr in u) >= times


def navigated_all(traj, substrs):
    return all(navigated_to(traj, s) for s in substrs)


def navigated_any(traj, substrs):
    return any(navigated_to(traj, s) for s in substrs)


def submitted_form(traj, must_contain):
    """True if one step's params carry every token in `must_contain`.

    Covers form values that never reach a URL (POST bodies).
    """
    for step in _steps(traj):
        if not isinstance(step, dict):
            continue
        blob = json.dumps(step.get("params") or {}, ensure_ascii=False).casefold()
        if all(str(token).casefold() in blob for token in must_contain):
            return True
    return False


LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", "host.docker.internal"}


def foreign_origins(traj):
    """Hosts the run touched that are not the local benchmark mirror.

    The mirror copies akc.org's URL shapes, so a path check alone cannot tell
    "opened the mirror's breed page" from "opened the real akc.org". Any
    off-box origin in the trajectory invalidates the run.
    """
    import urllib.parse
    seen = []
    for url in visited_urls(traj):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https"):
            continue
        host = (parsed.hostname or "").lower()
        if host and host not in LOCAL_HOSTS and host not in seen:
            seen.append(host)
    return seen


def final_answer(traj):
    return (traj.get("final_answer") or "").strip()


def _shot(traj, name):
    if not name:
        return None
    p = traj["_shots"].get(Path(str(name)).name)
    return p if (p and p.exists()) else None


def shot_after_url(traj, substr):
    for step in _steps(traj):
        if isinstance(step, dict) and substr in str(step.get("url", "")):
            p = _shot(traj, step.get("screenshot_after"))
            if p:
                return p
    return None


def has_final_snapshot(traj):
    """The run must carry the screenshot taken after its last recorded step.

    A trajectory that stops mid-way, or whose final frame is missing, is an
    evidence gap: the end state was never captured.
    """
    steps = _steps(traj)
    if not steps or not isinstance(steps[-1], dict):
        return False
    return _shot(traj, steps[-1].get("screenshot_after")) is not None


def last_shot(traj):
    for step in reversed(_steps(traj)):
        if not isinstance(step, dict):
            continue
        p = _shot(traj, step.get("screenshot_after")) or _shot(traj, step.get("screenshot_before"))
        if p:
            return p
    shots = sorted(traj["_shots"].values())
    return shots[-1] if shots else None


# ---------------------------------------------------------------- text matching
def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def answer_equals(final, expected):
    return norm(final) == norm(expected)


def contains_all(final, tokens):
    f = norm(final)
    return all(norm(t) in f for t in tokens)


def contains_any(final, tokens):
    f = norm(final)
    return any(norm(t) in f for t in tokens)


def contains_none(final, tokens):
    f = norm(final)
    return not any(norm(t) in f for t in tokens)


def rating_for(text, label, window_chars=40):
    """The rating an answer attaches to `label`, e.g. 'Barking Level: 5/5'.

    Only looks in a short window after the label, so 'barking 5, drooling 1'
    binds each number to the right trait and a swapped answer fails.
    """
    f = norm(text)
    l = norm(label)
    idx = f.find(l)
    if idx < 0:
        return None
    window = f[idx + len(l): idx + len(l) + window_chars]
    m = re.search(r"(\d+)", window)
    return int(m.group(1)) if m else None


def value_near(text, label, pattern=r"\d+", window_chars=60):
    """The `pattern` match closest to `label`, on either side of it.

    Binds a value to the thing it describes, so "Chocolate 071, Black 007" is
    graded per colour rather than by bag-of-numbers containment. Both sides are
    searched because answers phrase it either way ("Barking Level: 5" and
    "5 Expert Advice results").
    """
    f = norm(text)
    l = norm(label)
    idx = f.find(l)
    if idx < 0:
        return None
    end = idx + len(l)
    candidates = []
    for m in re.finditer(pattern, f[end:end + window_chars]):
        candidates.append((m.start(), m.group(0)))
    before = f[max(0, idx - window_chars):idx]
    for m in re.finditer(pattern, before):
        candidates.append((len(before) - m.end(), m.group(0)))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


# Back-compat alias for verifiers that only look forward.
value_after = value_near


# A rating and its scale read as one token, so "5 out of 5" contributes one
# value instead of two competing numbers.
RATING_RE = r"\d+(?:\s*(?:/|out of)\s*\d+)?"

# Words that sit between a label and its value without changing the binding, so
# "Drooling Level 1" reads as tightly bound as "Drooling 1". Conjunctions are
# deliberately absent: "... 5 and Drooling ..." must stay a real separation.
BINDING_FILLER = {
    "level", "levels", "rating", "ratings", "score", "scored", "is", "was",
    "of", "out", "at", "the", "a", "an", "with", "has", "have", "results",
    "result", "listed", "shows", "show", "ranked", "rank", "ranking", "code",
    "registration", "about",
}


# A comma or full stop between a label and a value means they belong to
# different clauses; a colon or dash binds them together.
SEPARATOR_COST = 3


def _gap_cost(gap):
    """How strongly a gap separates a label from a value.

    Filler words and binding punctuation cost nothing; clause separators and
    any other word count.
    """
    words = re.findall(r"[a-z0-9]+", gap)
    cost = sum(len(w) for w in words if w not in BINDING_FILLER)
    cost += SEPARATOR_COST * len(re.findall(r"[,;.!?]", gap))
    return cost


def leading_int(value):
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else None


def bind_all(text, labels, pattern=r"\d+"):
    """Assign every number in the answer to its nearest label.

    Answers put the value on either side of the thing it describes
    ("Drooling Level 1", "3 Expert Advice results"), and several labels compete
    in one sentence. Assigning each value to the closest label, then taking the
    first value each label won, binds both orders correctly and still fails a
    swapped answer.

    Returns {label: value or None}.
    """
    f = norm(text)
    positions = {}
    for label in labels:
        idx = f.find(norm(label))
        if idx >= 0:
            positions[label] = (idx, idx + len(norm(label)))
    out = {label: None for label in labels}
    if not positions:
        return out
    for m in re.finditer(pattern, f):
        # Anchor on the value's first digit, not the end of its scale suffix:
        # "5 out of 5" belongs to the label before it, not the one after it.
        anchor = m.start()
        best, best_key = None, None
        for label, (start, end) in positions.items():
            if anchor >= end:
                gap, dist = f[end:anchor], anchor - end
            elif anchor < start:
                gap, dist = f[m.end():start], start - anchor
            else:
                gap, dist = "", 0
            key = (_gap_cost(gap), dist)
            if best_key is None or key < best_key:
                best, best_key = label, key
        if best is not None and out[best] is None:
            out[best] = m.group(0)
    return out


def bound_as(text, labels, expected, pattern=RATING_RE):
    """True when nearest-label assignment reproduces `expected` exactly.

    `expected` maps label -> value. Numeric expectations compare on the leading
    integer so "5", "5/5" and "5 out of 5" all satisfy 5.
    """
    got = bind_all(text, labels, pattern)
    ok = True
    for label, want in expected.items():
        have = got.get(label)
        if have is None:
            ok = False
            continue
        if isinstance(want, int) or str(want).isdigit():
            if leading_int(have) != leading_int(want):
                ok = False
        elif norm(have) != norm(str(want)):
            ok = False
    return ok, got


def pair_bound(text, label, expected, pattern=RATING_RE, window_chars=60):
    """True when `expected` is the value the answer attaches to `label`."""
    ok, _ = bound_as(text, [label], {label: expected}, pattern)
    return ok


def denies(text):
    """Answers asserting the task could not be done must never count as PASS."""
    f = norm(text)
    return any(p in f for p in (
        "i could not", "i couldn't", "unable to", "not able to", "no such",
        "does not exist", "doesn't exist", "not found", "cannot determine",
        "can't determine", "no information",
    ))


# ---------------------------------------------------------------- DB access
def fetch_db(container, kind):
    """kind: 'instance' (after-state) or 'instance_seed' (initial-state)."""
    src = f"{container}:/opt/WebSyn/{SITE}/{kind}/{SITE}.db"
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    r = subprocess.run(["docker", "cp", src, path], capture_output=True, text=True)
    if r.returncode != 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise RuntimeError(f"docker cp {src} failed: {r.stderr.strip()}")
    return path


def resolve_db(arg, container, kind):
    if arg:
        return arg if Path(arg).exists() else None
    try:
        return fetch_db(container, kind)
    except Exception:
        return None


def db_query(db_path, sql, params=()):
    if not db_path:
        return None
    try:
        con = sqlite3.connect(db_path)
    except sqlite3.Error:
        return None
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return None
    finally:
        con.close()


def one(rows):
    return rows[0][0] if rows else None


# -------- derived expectations (read from the run's initial DB, not frozen)
BREED_FIELDS = {
    "name", "group", "origin", "year_recognized", "life_expectancy",
    "height", "weight", "temperament", "popularity_rank", "akc_code",
    "nicknames", "plural",
}


def breed_field(db, slug, field):
    if field not in BREED_FIELDS:
        raise ValueError(f"unsupported breed field: {field}")
    col = f'"{field}"'
    return one(db_query(db, f"SELECT {col} FROM breed WHERE slug=?", (slug,)))


def trait_score(db, slug, key):
    return one(db_query(
        db,
        "SELECT t.score FROM breed_trait t JOIN breed b ON b.id=t.breed_id "
        "WHERE b.slug=? AND t.key=?", (slug, key)))


def colors_for(db, slug, kind="color"):
    rows = db_query(
        db,
        "SELECT c.name, c.code FROM breed_color c JOIN breed b ON b.id=c.breed_id "
        "WHERE b.slug=? AND c.kind=? ORDER BY c.position", (slug, kind))
    return [(r[0], r[1]) for r in (rows or [])]


def facts_for(db, slug):
    rows = db_query(
        db,
        "SELECT f.text FROM breed_fact f JOIN breed b ON b.id=f.breed_id "
        "WHERE b.slug=? ORDER BY f.position", (slug,))
    return [r[0] for r in (rows or [])]


def breeds_in_group(db, group):
    rows = db_query(db, 'SELECT slug, name FROM breed WHERE "group"=? ORDER BY name', (group,))
    return [(r[0], r[1]) for r in (rows or [])]


def breeds_with_characteristic(db, characteristic_slug):
    rows = db_query(
        db,
        "SELECT b.slug, b.name FROM breed b "
        "JOIN breed_characteristics bc ON bc.breed_id=b.id "
        "JOIN characteristic c ON c.id=bc.characteristic_id "
        "WHERE c.slug=? ORDER BY b.name", (characteristic_slug,))
    return [(r[0], r[1]) for r in (rows or [])]


def lowest_ranked(db, limit=3):
    rows = db_query(
        db,
        "SELECT name, popularity_rank FROM breed WHERE popularity_rank IS NOT NULL "
        "ORDER BY popularity_rank DESC LIMIT ?", (limit,))
    return [(r[0], r[1]) for r in (rows or [])]


# Mirrors app.SELECTOR_AXES and app.breed_selector so the expected top pick is
# recomputed from this run's seed rather than frozen.
SELECTOR_AXES = [
    ("energy", "energy_level"),
    ("grooming", "coat_grooming_frequency"),
    ("children", "good_with_young_children"),
    ("training", "trainability_level"),
]


def selector_ranking(db, home, **answers):
    rows = db_query(db, "SELECT slug, name FROM breed ORDER BY name")
    if rows is None:
        return None
    apartment = {slug for slug, _ in breeds_with_characteristic(db, "best-dogs-for-apartment-dwellers")}
    ranked = []
    for slug, name in rows:
        score = 0
        for field, key in SELECTOR_AXES:
            got = trait_score(db, slug, key)
            if got is None:
                return None
            score += 5 - abs(got - int(answers[field]))
        if home == "apartment":
            adapt = trait_score(db, slug, "adaptability_level")
            if adapt is None:
                return None
            score += 5 - abs(adapt - 5)
            if slug in apartment:
                score += 4
        ranked.append((score, name))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked


def selector_top(db, home, **answers):
    """Top recommendation, or None when it ties with the runner-up."""
    ranked = selector_ranking(db, home, **answers)
    if not ranked:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None  # ambiguous -> fail closed rather than guess
    return ranked[0][1]


def articles_in_category(db, category_slug):
    rows = db_query(
        db,
        "SELECT title, published_on FROM article WHERE category_slug=? "
        "ORDER BY published_on", (category_slug,))
    return [(r[0], r[1]) for r in (rows or [])]


def event_body(db, slug):
    return one(db_query(db, "SELECT body FROM event WHERE slug=?", (slug,)))


def saved_breeds_for(db, email="alice.j@test.com"):
    rows = db_query(
        db,
        "SELECT b.name FROM saved_breed s JOIN user u ON u.id=s.user_id "
        "JOIN breed b ON b.id=s.breed_id WHERE u.email=? ORDER BY b.name", (email,))
    return None if rows is None else [r[0] for r in rows]


def registrations_for(db, email="alice.j@test.com"):
    rows = db_query(
        db,
        "SELECT e.slug, r.dog_name, r.class_name FROM event_registration r "
        "JOIN user u ON u.id=r.user_id JOIN event e ON e.id=r.event_id "
        "WHERE u.email=? ORDER BY e.slug", (email,))
    return None if rows is None else [tuple(r) for r in rows]


def user_row(db, email):
    rows = db_query(
        db,
        "SELECT username, display_name, household, activity_level, experience "
        "FROM user WHERE email=?", (email,))
    if not rows:
        return None
    keys = ("username", "display_name", "household", "activity_level", "experience")
    return dict(zip(keys, rows[0]))


SEARCH_STOP_WORDS = {
    "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
    "dog", "dogs",
}
SEARCH_PAGE_LIMIT = 8


def search_counts(db, term):
    """Reproduce the site's search over the initial DB.

    Mirrors app.scored_search + the per-section cap in app.search, so the
    expectation tracks the seed instead of a frozen number.
    """
    parts = [t for t in re.split(r"\W+", term.lower())
             if len(t) > 1 and t not in SEARCH_STOP_WORDS]
    if not parts:
        return {"breeds": 0, "articles": 0, "events": 0}

    def count(sql):
        rows = db_query(db, sql)
        if rows is None:
            return None
        hit = 0
        for row in rows:
            hay = " ".join(str(v or "") for v in row).lower()
            if any(p in hay for p in parts):
                hit += 1
        return min(hit, SEARCH_PAGE_LIMIT)

    return {
        "breeds": count('SELECT name, "group", temperament, blurb, nicknames FROM breed'),
        "articles": count("SELECT title, category, summary, body, author FROM article"),
        "events": count("SELECT title, sport, city, region, summary FROM event"),
    }


# ---------------------------------------------------------------- anchored LLM
import simpleArgParser as sap

_NO_LLM = False


def _chat(messages, max_tokens=1024):
    if _NO_LLM:
        return None
    key = os.environ.get("OPENAI_API_KEY", "")
    base = os.environ.get("OPENAI_BASE_URL", "")
    model = os.environ.get("JUDGE_MODEL", "")
    if not (key and base and model):
        return None
    payload = {"model": model, "messages": messages,
               "max_tokens": max_tokens, "temperature": 1.0}
    req = urllib.request.Request(
        base, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=180).read())
        return data["choices"][0]["message"]["content"]
    except Exception:
        return None


def _verdict(out):
    if not out:
        return False, "<no reply from LLM>"
    s = out.strip()
    return s.upper().startswith("PASS"), s


def llm_text_match(agent_answer, ground_truth, question):
    if _NO_LLM:
        return False, "[skipped: --no_llm]"
    out = _chat([{"role": "user", "content":
        f"You are a STRICT binary grader.\nQuestion: {question}\n"
        f"Ground-truth answer (ANCHOR — judge against THIS, never use your own knowledge): {ground_truth}\n"
        f"Agent's answer: {agent_answer}\n"
        f"Decide PASS or FAIL ignoring case, punctuation, word order and surrounding prose. "
        f"PASS only if the agent's answer is consistent with the ground truth AND actually answers the question. "
        f"Line 1: PASS or FAIL. Line 2: one-sentence reason."}])
    return _verdict(out)


def llm_screenshot_shows(shot_path, must_show, question=""):
    if _NO_LLM:
        return False, "[skipped: --no_llm]"
    b64 = base64.b64encode(Path(shot_path).read_bytes()).decode()
    out = _chat([{"role": "user", "content": [
        {"type": "text", "text":
            f"You are a STRICT binary grader. Only what is VISIBLY rendered counts.\n"
            f"Question the page should answer: {question}\n"
            f"Expected content to verify PRESENCE of: {must_show}\n"
            f"PASS only if that content (or a semantically equivalent on-screen answer) is visible. "
            f"Do NOT use prior knowledge.\nLine 1: PASS or FAIL. Line 2: quote the visible evidence."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}])
    return _verdict(out)


# ---------------------------------------------------------------- harness
class Judge:
    def __init__(self, task_id, no_llm=False):
        global _NO_LLM
        _NO_LLM = bool(no_llm)
        self.task_id = task_id
        self.no_llm = no_llm
        self.ok = True
        self.reason = ""
        self.evidence = []

    def check(self, name, cond, evidence="", llm=False):
        if llm and self.no_llm:
            self.evidence.append(f"[SKIP] {name} (--no-llm)")
            return True
        if cond:
            self.evidence.append(f"[PASS] {name}: {evidence}")
        else:
            self.ok = False
            if not self.reason:
                self.reason = name
            self.evidence.append(f"[FAIL] {name}: {evidence}")
        return bool(cond)

    def derived(self, name, value, evidence=""):
        """Fail closed when an expectation could not be derived from the DB."""
        ok = value is not None and value != [] and value != ()
        self.check(name, ok, evidence or f"derived={value!r}")
        return ok

    def emit(self):
        print(json.dumps({"task_id": self.task_id, "pass": self.ok,
                          "reason": self.reason, "evidence": self.evidence}, indent=2))
        sys.exit(0 if self.ok else 1)


def parse_args():
    @dataclass
    class VerifyArgs:
        run_dir: str = ""
        initial_db: str = ""
        after_db: str = ""
        container: str = os.environ.get("WH_CONTAINER", "wh-review040-preview")
        no_llm: bool = False

        def post_process(self):
            if not self.run_dir:
                raise SystemExit("--run_dir is required")
    return sap.parse_args(VerifyArgs)


def start(task_id):
    """Common preamble: args, judge, run, final answer, and the no-denial guard."""
    a = parse_args()
    j = Judge(task_id, a.no_llm)
    t = load_run(a.run_dir, task_id)
    fa = final_answer(t)
    j.check("answer_present", bool(fa) and not denies(fa), f"final={fa[:200]!r}")
    foreign = foreign_origins(t)
    j.check("run_stayed_on_mirror", not foreign, f"foreign_origins={foreign}")
    j.check("run_has_screenshots", len(t["_shots"]) > 0, f"shots={len(t['_shots'])}")
    j.check("run_has_final_snapshot", has_final_snapshot(t),
            f"steps={len(_steps(t))} shots={sorted(t['_shots'])[-1:]}")
    return a, j, t, fa
