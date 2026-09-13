#!/usr/bin/env python3
"""verify_lib.py — shared deterministic utilities for Versus task verification.

Philosophy, same as the merriam_webster reference: DETERMINISTIC FIRST.
  1. Navigation check (anti knowledge-shortcut): the agent MUST have opened the
     on-site page that carries the fact. Versus prints Score/Price/Year on cards
     and in the ranking list but keeps every spec value (camera score, ANC score,
     megapixels, burst, VRAM, power, benchmark, battery hours, weight, display)
     on detail and comparison pages only — so the navigation check is what stops
     a list-scan or a recalled answer from passing.
  2. Answer check against ground truth DERIVED FROM initial_db, never a frozen
     constant: if the seed data changes, the expected answer moves with it and a
     stale verifier fails loudly instead of grading against a dead value.
  3. DB after-state check for stateful tasks, read from the after_db.
  4. LLM utilities are anchored: the model confirms presence of a derived value,
     it never supplies knowledge.

Input signature (per task):
  --run_dir DIR      trajectory.json + screenshots/step_NNN.png
  --initial_db PATH  initial-state SQLite DB (default: instance_seed from container)
  --after_db PATH    after-state SQLite DB  (default: live instance from container)
  --container NAME   docker container to fetch DBs from (default: $WH_CONTAINER)
  --no_llm           deterministic-only
Output: JSON {task_id, pass, reason, evidence[]} on stdout; exit 0 PASS / 1 FAIL.
Malformed or missing input produces a structured FAIL, never a traceback.
"""
from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SITE = "versus"

# ---------------------------------------------------------------- trajectory
def load_run(run_dir):
    d = Path(run_dir)
    traj = json.loads((d / "trajectory.json").read_text())
    traj["_run_dir"] = d
    traj["_shots"] = {p.name: p for p in sorted((d / "screenshots").glob("step_*.png"))}
    return traj


def step_urls(traj):
    return [s.get("url", "") or "" for s in traj.get("steps", [])]


def navigated_to(traj, substr, times=1):
    return sum(1 for u in step_urls(traj) if substr in u) >= times


def navigated_any(traj, substrs):
    return any(navigated_to(traj, s) for s in substrs)


def opened_detail_or_compare(traj, slug):
    """The fact-bearing views for one product: its detail page, or any comparison
    that includes it (either side of the `<left>-vs-<right>` slug)."""
    for url in step_urls(traj):
        if f"/item/{slug}" in url:
            return True
        m = re.search(r"/compare/([a-z0-9\-]+)-vs-([a-z0-9\-]+)", url)
        if m and slug in (m.group(1), m.group(2)):
            return True
    return False


def final_answer(traj):
    return (traj.get("final_answer") or "").strip()


def _shot(traj, name):
    if not name:
        return None
    p = traj["_shots"].get(Path(name).name)
    return p if (p and p.exists()) else None


def shot_after_url(traj, substr):
    for s in traj.get("steps", []):
        if substr in (s.get("url", "") or ""):
            p = _shot(traj, s.get("screenshot_after"))
            if p:
                return p
    return None


def last_shot(traj):
    for s in reversed(traj.get("steps", [])):
        p = _shot(traj, s.get("screenshot_after")) or _shot(traj, s.get("screenshot_before"))
        if p:
            return p
    shots = sorted(traj["_shots"].values())
    return shots[-1] if shots else None


# ---------------------------------------------------------------- answer match
def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


NEGATIONS = ("not ", "n't", "cannot", "unable", "no such", "could not",
             "couldn't", "failed to find", "does not exist")


def looks_negated(text):
    """A final answer that denies the fact must not pass on token containment."""
    return any(n in norm(text) for n in NEGATIONS)


def mentions_product(text, name):
    """Product naming, tolerant of the ways a model writes the same model number.

    'GeForce RTX 4080 Super' matches 'RTX 4080 Super'; 'iPhone 15 Pro' does NOT
    match 'iPhone 15 Pro Max' style over-reach because the distinguishing tokens
    must all be present.
    """
    t = norm(text)
    tokens = [x for x in re.split(r"[\s/]+", norm(name)) if x
              and x not in {"geforce", "radeon", "apple", "samsung", "sony", "google"}]
    return all(tok in t for tok in tokens) if tokens else False


def _numbers(text):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", (text or "").replace(",", ""))]


def mentions_number(text, value, tol=0.05):
    """True when the answer states `value`. Accepts 336, 336.0, '336 h', '336-hour'."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return any(abs(n - value) <= tol for n in _numbers(text))


def mentions_money(text, value):
    """Price match that also accepts '$3,999' and '3999 USD'."""
    return mentions_number(text, value, tol=0.5)


# ---------------------------------------------------------------- DB access
def fetch_db(container, kind):
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
    con = sqlite3.connect(db_path)
    try:
        con.row_factory = sqlite3.Row
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def products(db_path, category_slug=None):
    """Every product with its category and that category's spec labels/units."""
    if not db_path:
        return []
    sql = ("SELECT p.slug, p.name, p.brand, p.score, p.price, p.release_year, "
           "p.spec_1_value, p.spec_2_value, p.spec_3_value, p.battery_hours, "
           "p.weight_grams, c.slug AS category_slug, c.name AS category_name, "
           "c.spec_1, c.spec_2, c.spec_3, c.unit_1, c.unit_2, c.unit_3 "
           "FROM product p JOIN category c ON c.id = p.category_id")
    params = ()
    if category_slug:
        sql += " WHERE c.slug = ?"
        params = (category_slug,)
    return [dict(r) for r in db_query(db_path, sql, params)]


def product(db_path, slug):
    rows = [p for p in products(db_path) if p["slug"] == slug]
    return rows[0] if rows else None


def unique_extreme(rows, key, largest=True):
    """The single row with the extreme value of `key`, or None when it is tied.

    Fail-closed on ambiguity: a task whose answer is not unique in the seed data
    must not be graded as if it were.
    """
    if not rows:
        return None
    vals = sorted((r[key] for r in rows), reverse=largest)
    if len(vals) > 1 and vals[0] == vals[1]:
        return None
    target = vals[0]
    return next(r for r in rows if r[key] == target)


def saved_pairs(db_path, email):
    """{frozenset({left_slug, right_slug})} saved by that user, or None if unreadable."""
    if not db_path:
        return None
    try:
        rows = db_query(db_path,
                        "SELECT l.slug AS l, r.slug AS r FROM saved_comparison sc "
                        "JOIN user u ON u.id = sc.user_id "
                        "JOIN product l ON l.id = sc.left_id "
                        "JOIN product r ON r.id = sc.right_id WHERE u.email = ?",
                        (email,))
    except sqlite3.Error:
        return None
    return {frozenset((row["l"], row["r"])) for row in rows}


# ---------------------------------------------------------------- anchored LLM
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
    req = urllib.request.Request(base, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
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
    return _verdict(_chat([{"role": "user", "content":
        f"You are a STRICT binary grader.\nQuestion: {question}\n"
        f"Ground-truth answer (ANCHOR — judge against THIS, never use your own knowledge): {ground_truth}\n"
        f"Agent's answer: {agent_answer}\n"
        f"Decide PASS or FAIL ignoring case/punctuation/word order/surrounding prose. "
        f"PASS only if the agent's answer is consistent with the ground truth AND actually answers the question. "
        f"Line 1: PASS or FAIL. Line 2: one-sentence reason."}]))


def llm_screenshot_shows(shot_path, must_show, question=""):
    if _NO_LLM or not shot_path:
        return False, "[skipped: --no_llm or no screenshot]"
    b64 = base64.b64encode(Path(shot_path).read_bytes()).decode()
    return _verdict(_chat([{"role": "user", "content": [
        {"type": "text", "text":
            f"You are a STRICT binary grader. Only what is VISIBLY rendered counts.\n"
            f"Question the page should answer: {question}\n"
            f"Expected content to verify PRESENCE of: {must_show}\n"
            f"Do NOT use prior knowledge — judge only the rendered pixels.\n"
            f"Line 1: PASS or FAIL. Line 2: quote the visible evidence."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]))


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
            self.evidence.append(f"[SKIP] {name} (--no_llm)")
            return True
        if cond:
            self.evidence.append(f"[PASS] {name}: {evidence}")
        else:
            self.ok = False
            if not self.reason:
                self.reason = name
            self.evidence.append(f"[FAIL] {name}: {evidence}")
        return bool(cond)

    def fail(self, reason, evidence=""):
        self.ok = False
        if not self.reason:
            self.reason = reason
        self.evidence.append(f"[FAIL] {reason}: {evidence}")

    def emit(self):
        print(json.dumps({"task_id": self.task_id, "pass": self.ok,
                          "reason": self.reason, "evidence": self.evidence}, indent=2))
        sys.exit(0 if self.ok else 1)


def parse_args():
    import simpleArgParser as sap

    @dataclass
    class VerifyArgs:
        run_dir: str = ""
        initial_db: str = ""
        after_db: str = ""
        container: str = os.environ.get("WH_CONTAINER", "wh-review041-candidate")
        no_llm: bool = False

        def post_process(self):
            if not self.run_dir:
                raise SystemExit("--run_dir is required")

    return sap.parse_args(VerifyArgs)


def run(task_id, body):
    """Wrap a verifier body so malformed input is a structured FAIL, not a crash."""
    args = parse_args()
    j = Judge(task_id, no_llm=args.no_llm)
    try:
        traj = load_run(args.run_dir)
    except Exception as exc:
        j.fail("run bundle unreadable", f"{type(exc).__name__}: {exc}")
        j.emit()
    initial = resolve_db(args.initial_db, args.container, "instance_seed")
    after = resolve_db(args.after_db, args.container, "instance")
    if not initial:
        j.fail("initial_db unavailable",
               "ground truth is derived from the seed DB; refusing to grade without it")
        j.emit()
    try:
        body(j, traj, initial, after)
    except Exception as exc:
        j.fail("verifier error", f"{type(exc).__name__}: {exc}")
    j.emit()
