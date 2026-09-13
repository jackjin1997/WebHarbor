#!/usr/bin/env python3
"""Adversarial fixture matrix for the AKC verifiers.

These are CONSTRUCTED fixtures, not agent runs. They exist to show each
verifier accepts a correct completion and rejects the failure shapes a review
has to rule out: no-op, answer-without-navigation, wrong answer, self-reported
success with no state change, denial answers, a truncated run missing its final
snapshot, another task's trajectory replayed, a run performed against the real
akc.org instead of the mirror, and malformed input.

Both databases are built here from the site itself: the initial DB is the
freshly seeded one, and the after DB is produced by driving the real Flask app
through its own forms, so the state the verifiers read is what the app actually
writes.

    python3 sites/akc/tests/verifier_matrix.py            # run the matrix
    python3 sites/akc/tests/verifier_matrix.py --selftest # prove it catches defects
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
VERIFY = SITE / "verify"
BASE = "http://localhost:40024"

# 1x1 transparent PNG — the verifiers check that step frames exist, not content.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

DEMO_EMAIL = "alice.j@test.com"
DEMO_PASSWORD = "TestPass123!"


# --------------------------------------------------------------------------- state
def build_databases(workdir: Path):
    """Return (initial_db, after_db), leaving the site's own instance DB as found.

    The initial DB has to be a *pristine* seed: a developer's instance DB may
    already carry earlier manual writes, which would make "this row did not
    exist before the run" checks pass or fail for the wrong reason. So the
    existing DB is set aside, the site re-seeds itself from data/, and the
    original is put back at the end.
    """
    sys.path.insert(0, str(SITE))
    instance = SITE / "instance"
    live = instance / "akc.db"
    stashed = None
    if live.exists():
        stashed = workdir / "stashed-instance.db"
        shutil.copy2(live, stashed)
        shutil.rmtree(instance)

    import app as site_app  # type: ignore[import-not-found]  # noqa: E402

    live = Path(site_app.app.instance_path) / "akc.db"
    initial = workdir / "initial.db"
    after = workdir / "after.db"
    shutil.copy2(live, initial)

    client = site_app.app.test_client()
    client.post("/login", data={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
    client.post("/breeds/whippet/save", data={"note": ""})
    client.post("/events/akc-rally-national-championship",
                data={"dog_name": "Scout", "class_name": "Novice"})
    client.post("/account/profile", data={"household": "Apartment",
                                          "activity_level": "Low",
                                          "experience": "First-time owner"})
    fresh = site_app.app.test_client()
    fresh.post("/register", data={"display_name": "Jordan Blake", "username": "jordan_b",
                                  "email": "jordan.b@test.com", "password": DEMO_PASSWORD,
                                  "household": "Condo", "activity_level": "Moderate",
                                  "experience": "First-time owner"})
    shutil.copy2(live, after)
    # Put the developer's environment back the way it was.
    shutil.copy2(stashed if stashed else initial, live)
    return initial, after


# --------------------------------------------------------------------------- fixtures
def make_run(runs: Path, name, urls, answer, *, final_shot=True, raw=None):
    d = runs / name
    if d.exists():
        shutil.rmtree(d)
    (d / "screenshots").mkdir(parents=True)
    if raw is not None:
        (d / "trajectory.json").write_text(raw)
        (d / "screenshots" / "step_000.png").write_bytes(PNG)
        return d

    steps = []
    for i, url in enumerate(urls):
        steps.append({
            "step": i, "url": url, "title": "", "thought": "",
            "action": "navigate" if i < len(urls) - 1 else "done",
            "params": {"url": urls[i + 1]} if i < len(urls) - 1 else {"text": answer},
            "screenshot_before": f"step_{i:03d}.png",
            "screenshot_after": f"step_{i + 1:03d}.png",
        })
    (d / "trajectory.json").write_text(json.dumps({
        "task": name, "site": "akc", "steps": steps, "terminated": True,
        "termination_reason": "agent_done", "final_answer": answer,
        "success_self_report": True,
    }, indent=2))
    for i in range(len(steps) + (1 if final_shot else 0)):
        (d / "screenshots" / f"step_{i:03d}.png").write_bytes(PNG)
    return d


def run_verifier(task_num, run_dir, initial, after):
    cmd = ["uv", "run", "--quiet", "--with", "simpleargparser",
           "python", str(VERIFY / f"verify_{task_num}.py"),
           "--run_dir", str(run_dir), "--initial_db", str(initial),
           "--after_db", str(after), "--no_llm", "True"]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(VERIFY))
    try:
        return json.loads(p.stdout), p.stdout
    except json.JSONDecodeError:
        return None, p.stdout + p.stderr


B = BASE
CASES = {
    0: dict(urls=[f"{B}/", f"{B}/breeds", f"{B}/breeds/labrador-retriever"],
            good="The Labrador Retriever profile lists Chocolate with registration code 071.",
            wrong="The Labrador Retriever profile lists Chocolate with registration code 232."),
    1: dict(urls=[f"{B}/", f"{B}/breed-selector", f"{B}/breed-selector"],
            good="The top recommendation is the French Bulldog.",
            wrong="The top recommendation is the Border Collie."),
    2: dict(urls=[f"{B}/", f"{B}/compare?breed=golden-retriever&breed=border-collie"],
            good="Golden Retriever 3/5, Border Collie 5/5 - the Border Collie is higher.",
            wrong="Golden Retriever 5/5, Border Collie 3/5 - the Golden Retriever is higher."),
    3: dict(urls=[f"{B}/", f"{B}/login", f"{B}/account"],
            good="Saved breeds: Boston Terrier, Cavalier King Charles Spaniel, French Bulldog.",
            wrong="Saved breeds: Boston Terrier and Beagle."),
    4: dict(urls=[f"{B}/", f"{B}/breeds?sort=popularity"],
            good="Whippet 48, Newfoundland 46, West Highland White Terrier 45.",
            wrong="Whippet 45, Newfoundland 46, West Highland White Terrier 48."),
    5: dict(urls=[f"{B}/", f"{B}/events?sport=Conformation",
                  f"{B}/events/conformation-national-championship"],
            good="Sporting, Hound, Toy and Non-Sporting breeds plus Junior Showmanship are judged on Saturday.",
            wrong="Working, Terrier and Herding breeds are judged on Saturday."),
    6: dict(urls=[f"{B}/", f"{B}/articles?category=nutrition"],
            good="3 articles. The earliest is Can Dogs Eat Bananas? Benefits, Risks, and Safe Serving Tips.",
            wrong="5 articles. The earliest is Can Dogs Eat Eggplants?"),
    7: dict(urls=[f"{B}/", f"{B}/breeds?group=Working+Group",
                  f"{B}/breeds?characteristic=largest-dog-breeds"],
            good="7 Working Group breeds are listed; the Rottweiler is the one not in Largest Dog Breeds.",
            wrong="7 Working Group breeds are listed; the Great Dane is the one not in Largest Dog Breeds."),
    8: dict(urls=[f"{B}/", f"{B}/login", f"{B}/events/akc-rally-national-championship", f"{B}/account"],
            good="Scout is registered in the Novice class for the AKC Rally National Championship.",
            wrong="Scout is registered in the Novice class for the AKC Rally National Championship."),
    9: dict(urls=[f"{B}/", f"{B}/login", f"{B}/account"],
            good="The owner profile now shows an activity level of Low.",
            wrong="The owner profile now shows an activity level of Low."),
    10: dict(urls=[f"{B}/", f"{B}/breeds/great-dane"],
             good="Males stand 30-32 inches at the shoulder.",
             wrong="Males stand 28-30 inches at the shoulder."),
    11: dict(urls=[f"{B}/", f"{B}/breeds/labrador-retriever"],
             good="Did You Know says the breed did not come from Labrador but from Newfoundland.",
             wrong="Did You Know says the breed came from Labrador in Canada."),
    12: dict(urls=[f"{B}/", f"{B}/breeds?characteristic=hypoallergenic-dogs",
                   f"{B}/breeds/poodle-standard"],
             good="Poodle (Standard); its Shedding Level is 1 out of 5.",
             wrong="Poodle (Standard); its Shedding Level is 4 out of 5."),
    13: dict(urls=[f"{B}/", f"{B}/breeds/siberian-husky"],
             good="Barking Level 5 out of 5 and Drooling Level 1 out of 5.",
             wrong="Barking Level 1 out of 5 and Drooling Level 5 out of 5."),
    14: dict(urls=[f"{B}/", f"{B}/login", f"{B}/breeds/whippet", f"{B}/account"],
             good="The Whippet is now saved to the profile.",
             wrong="The Whippet is now saved to the profile."),
    15: dict(urls=[f"{B}/", f"{B}/register", f"{B}/account"],
             good="Created the account for Jordan Blake; the account page shows Jordan Blake.",
             wrong="Created the account for Jordan Blake; the account page shows Jordan Blake."),
    16: dict(urls=[f"{B}/", f"{B}/search?q=agility"],
             good="The search returned 3 Expert Advice results and 2 event results.",
             wrong="The search returned 2 Expert Advice results and 3 event results."),
    17: dict(urls=[f"{B}/", f"{B}/breeds/rottweiler"],
             good="Black & Rust 015, Black & Mahogany 013, Black & Tan 018.",
             wrong="Black & Rust 013, Black & Mahogany 018, Black & Tan 015."),
}

STATEFUL = {8, 9, 14, 15}


def run_matrix(workdir: Path, verbose=True):
    initial, after = build_databases(workdir)
    runs = workdir / "runs"
    runs.mkdir(exist_ok=True)
    results = []

    def check(task, case, expected, run_dir, after_db):
        out, raw = run_verifier(task, run_dir, initial, after_db)
        if out is None:
            got, reason = "CRASH", raw[:120]
        else:
            got = "PASS" if out["pass"] else "FAIL"
            reason = out.get("reason", "")
        ok = (got == expected)
        results.append({"task": f"AKC--{task}", "case": case, "expected": expected,
                        "got": got, "reason": reason, "as_expected": ok})
        if verbose:
            print(f"{'  ' if ok else '!!'} AKC--{task:<2} {case:<26} "
                  f"expect={expected:<5} got={got:<5} {reason}")

    for task, spec in sorted(CASES.items()):
        urls, good, wrong = spec["urls"], spec["good"], spec["wrong"]
        stateful_after = after if task in STATEFUL else after

        check(task, "positive", "PASS", make_run(runs, f"t{task}_positive", urls, good), after)
        check(task, "no_op", "FAIL", make_run(runs, f"t{task}_noop", [f"{B}/"], ""), initial)
        check(task, "answer_no_navigation", "FAIL",
              make_run(runs, f"t{task}_no_nav", [f"{B}/"], good), stateful_after)
        if task in STATEFUL:
            check(task, "self_report_no_db_change", "FAIL",
                  make_run(runs, f"t{task}_no_state", urls, wrong), initial)
        else:
            check(task, "wrong_answer", "FAIL",
                  make_run(runs, f"t{task}_wrong", urls, wrong), after)
        check(task, "denial_answer", "FAIL",
              make_run(runs, f"t{task}_denial", urls,
                       "I was unable to determine the answer from the site."), after)
        check(task, "missing_final_snapshot", "FAIL",
              make_run(runs, f"t{task}_truncated", urls, good, final_shot=False), after)
        other = (task + 1) % len(CASES)
        check(task, "wrong_task_replay", "FAIL",
              make_run(runs, f"t{task}_replay", CASES[other]["urls"], CASES[other]["good"]),
              stateful_after)
        check(task, "foreign_origin", "FAIL",
              make_run(runs, f"t{task}_foreign",
                       [u.replace(BASE, "https://www.akc.org") for u in urls], good),
              stateful_after)
        check(task, "malformed_input", "FAIL",
              make_run(runs, f"t{task}_malformed", [], "", raw="{not json"), after)

    return results, initial, after


# --------------------------------------------------------------------------- self-test
INJECTIONS = [
    ("verify_13.py",
     'j.check("nav_breed_profile", navigated_to(t, "/breeds/siberian-husky"),',
     'j.check("nav_breed_profile", True,',
     "drops the navigation check", 13, "t13_no_nav"),
    ("verify_16.py",
     'j.check("counts_bound_to_sections", ok_bind,',
     'j.check("counts_bound_to_sections", True,',
     "accepts any counts", 16, "t16_wrong"),
    ("verify_8.py",
     'j.check("db_registration_recorded", target in after_regs, f"after={after_regs}")',
     'j.check("db_registration_recorded", True, f"after={after_regs}")',
     "drops the after-state check", 8, "t8_no_state"),
]


def run_selftest(workdir: Path, initial, after, verbose=True):
    """Inject one real defect per verifier and confirm the matrix flips to PASS."""
    runs = workdir / "runs"
    problems = []
    for fname, find, repl, label, task, run_name in INJECTIONS:
        path = VERIFY / fname
        backup = path.read_text()
        if find not in backup:
            problems.append(f"{fname}: injection anchor missing (self-test is stale)")
            continue
        run_dir = runs / run_name
        after_db = initial if run_name.endswith("no_state") else after
        out, _ = run_verifier(task, run_dir, initial, after_db)
        clean = "PASS" if out and out["pass"] else "FAIL"
        path.write_text(backup.replace(find, repl))
        try:
            out, _ = run_verifier(task, run_dir, initial, after_db)
            injected = "PASS" if out and out["pass"] else "FAIL"
        finally:
            path.write_text(backup)
        caught = clean == "FAIL" and injected == "PASS"
        if verbose:
            print(f"{'  ' if caught else '!!'} {fname:<14} {label:<30} "
                  f"clean={clean} injected={injected}")
        if not caught:
            problems.append(f"{fname}: defect not detected ({clean} -> {injected})")
        if find not in path.read_text():
            problems.append(f"{fname}: not restored after injection")
    return problems


def main():
    selftest = "--selftest" in sys.argv
    with tempfile.TemporaryDirectory(prefix="akc-verify-") as tmp:
        workdir = Path(tmp)
        results, initial, after = run_matrix(workdir)
        unexpected = [r for r in results if not r["as_expected"]]
        print(f"\n{len(results)} cases, {len(unexpected)} unexpected")
        for r in unexpected:
            print(f"  UNEXPECTED {r['task']} {r['case']}: expected {r['expected']}, got {r['got']}")

        problems = []
        if selftest:
            print()
            problems = run_selftest(workdir, initial, after)
            print(f"{len(INJECTIONS)} injections, {len(problems)} problems")
            for p in problems:
                print(f"  {p}")

        out = Path(__file__).with_name("verifier_matrix_results.json")
        out.write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nresults -> {out}")
    return 1 if (unexpected or problems) else 0


if __name__ == "__main__":
    sys.exit(main())
