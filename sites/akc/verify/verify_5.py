#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--5.

Filter events to Conformation, open the AKC National Championship and report the groups judged on Saturday.

Checks: nav filtered events + event page | groups matched against the event body in initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--5")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    body = event_body(initial, "conformation-national-championship")
    if not j.derived("expected_event_body", body, f"body={str(body)[:120]!r}"):
        j.emit()
    saturday = [g for g in ("Sporting", "Hound", "Toy", "Non-Sporting")
                if g.casefold() in norm(body)]
    absent = [g for g in ("Working", "Terrier", "Herding")
              if g.casefold() not in norm(body)]
    if not j.derived("expected_saturday_groups", saturday):
        j.emit()
    j.check("nav_conformation_filter", navigated_to(t, "sport=Conformation"),
            f"urls={visited_urls(t)[-8:]}")
    j.check("nav_event_page", navigated_to(t, "/events/conformation-national-championship"),
            f"urls={visited_urls(t)[-8:]}")
    j.check("answer_lists_saturday_groups", contains_all(fa, saturday),
            f"expected={saturday} final={fa[:200]!r}")
    j.check("answer_excludes_other_groups", contains_none(fa, absent),
            f"must_not_contain={absent} final={fa[:200]!r}")
    j.emit()


if __name__ == "__main__":
    main()
