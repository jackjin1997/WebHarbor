#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--9.

Sign in and change the owner profile activity level to Low.

Checks: nav account | after_db shows Low | initial_db did not
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--9")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    after = resolve_db(a.after_db, a.container, "instance")
    before_user = user_row(initial, "alice.j@test.com")
    after_user = user_row(after, "alice.j@test.com")
    if not j.derived("after_state_readable", after_user, f"after={after_user}"):
        j.emit()
    j.check("not_preexisting", (before_user or {}).get("activity_level") != "Low",
            f"initial={(before_user or {}).get('activity_level')!r}")
    j.check("nav_account", navigated_to(t, "/account"), f"urls={visited_urls(t)[-8:]}")
    j.check("db_activity_level_low", after_user.get("activity_level") == "Low",
            f"after={after_user.get('activity_level')!r}")
    j.check("db_identity_unchanged",
            after_user.get("display_name") == (before_user or {}).get("display_name"),
            f"before={(before_user or {}).get('display_name')!r} after={after_user.get('display_name')!r}")
    j.emit()


if __name__ == "__main__":
    main()
