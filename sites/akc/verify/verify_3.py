#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--3.

Sign in with the demo account and report the three breeds already saved in the profile.

Checks: nav login+account | all saved breed names from initial_db present
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--3")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    saved = saved_breeds_for(initial)
    if not j.derived("expected_saved_breeds", saved):
        j.emit()
    j.check("nav_login", navigated_to(t, "/login"), f"urls={visited_urls(t)[-8:]}")
    j.check("nav_account", navigated_to(t, "/account"), f"urls={visited_urls(t)[-8:]}")
    j.check("answer_lists_all_saved", contains_all(fa, saved),
            f"expected={saved} final={fa[:200]!r}")
    j.emit()


if __name__ == "__main__":
    main()
