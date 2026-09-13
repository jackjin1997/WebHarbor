#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--8.

Sign in and register a dog named Scout in the Novice class for the AKC Rally National Championship.

Checks: nav login+event | after_db carries the registration | initial_db did not
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--8")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    after = resolve_db(a.after_db, a.container, "instance")
    before_regs = registrations_for(initial)
    after_regs = registrations_for(after)
    if not j.check("after_state_readable", after_regs is not None, f"after={after_regs}"):
        j.emit()
    target = ("akc-rally-national-championship", "Scout", "Novice")
    j.check("not_preexisting", target not in (before_regs or []), f"initial={before_regs}")
    j.check("nav_login", navigated_to(t, "/login"), f"urls={visited_urls(t)[-8:]}")
    j.check("nav_event_page", navigated_to(t, "/events/akc-rally-national-championship"),
            f"urls={visited_urls(t)[-8:]}")
    j.check("db_registration_recorded", target in after_regs, f"after={after_regs}")
    j.emit()


if __name__ == "__main__":
    main()
