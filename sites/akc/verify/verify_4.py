#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--4.

Sort the breed directory by popularity and report the three lowest-ranked breeds with their numbers.

Checks: nav sorted directory | each of the three names bound to its ranking number
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--4")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    lowest = lowest_ranked(initial, 3)
    if not j.derived("expected_lowest_ranked", lowest):
        j.emit()
    j.check("nav_sorted_directory", navigated_to(t, "sort=popularity"),
            f"urls={visited_urls(t)[-6:]}")
    expected = {name: rank for name, rank in lowest}
    ok_bind, bound = bound_as(fa, list(expected), expected)
    j.check("ranks_bound_to_breeds", ok_bind, f"expected={expected} bound={bound}")
    j.emit()


if __name__ == "__main__":
    main()
