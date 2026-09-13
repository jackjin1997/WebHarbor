#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--15.

Create a new account for Jordan Blake with the given details and reach the signed-in account page.

Checks: nav register+account | after_db carries the new user with the named fields | not present initially
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--15")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    after = resolve_db(a.after_db, a.container, "instance")
    email = "jordan.b@test.com"
    before_user = user_row(initial, email)
    after_user = user_row(after, email)
    j.check("not_preexisting", before_user is None, f"initial={before_user}")
    j.check("nav_register", navigated_to(t, "/register"), f"urls={visited_urls(t)[-8:]}")
    j.check("nav_account", navigated_to(t, "/account"), f"urls={visited_urls(t)[-8:]}")
    if not j.derived("db_user_created", after_user, f"after={after_user}"):
        j.emit()
    # Values the task dictates, so they are task constants rather than seed facts.
    j.check("db_username", after_user.get("username") == "jordan_b", f"after={after_user}")
    j.check("db_display_name", norm(after_user.get("display_name")) == norm("Jordan Blake"),
            f"after={after_user}")
    j.check("db_household", after_user.get("household") == "Condo", f"after={after_user}")
    j.emit()


if __name__ == "__main__":
    main()
