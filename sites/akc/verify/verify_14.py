#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--14.

Sign in and save the Whippet to the demo profile.

Checks: nav login+breed profile | after_db adds Whippet and keeps the pre-existing saves
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--14")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    after = resolve_db(a.after_db, a.container, "instance")
    before_saved = saved_breeds_for(initial)
    after_saved = saved_breeds_for(after)
    if not j.check("after_state_readable", after_saved is not None, f"after={after_saved}"):
        j.emit()
    name = breed_field(initial, "whippet", "name") or "Whippet"
    j.check("not_preexisting", name not in (before_saved or []), f"initial={before_saved}")
    j.check("nav_login", navigated_to(t, "/login"), f"urls={visited_urls(t)[-8:]}")
    j.check("nav_breed_profile", navigated_to(t, "/breeds/whippet"), f"urls={visited_urls(t)[-8:]}")
    j.check("db_whippet_saved", name in after_saved, f"after={after_saved}")
    j.check("db_existing_saves_kept",
            all(b in after_saved for b in (before_saved or [])),
            f"before={before_saved} after={after_saved}")
    j.emit()


if __name__ == "__main__":
    main()
