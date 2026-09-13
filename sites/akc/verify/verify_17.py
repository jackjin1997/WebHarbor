#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--17.

Report all three Rottweiler coat colours with their registration codes.

Checks: nav breed profile | every colour name present and each code bound to its own colour
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--17")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    colors = colors_for(initial, "rottweiler")
    if not j.derived("expected_colors", colors):
        j.emit()
    j.check("nav_breed_profile", navigated_to(t, "/breeds/rottweiler"), f"urls={visited_urls(t)[-6:]}")
    j.check("answer_lists_all_colors", contains_all(fa, [name for name, _ in colors]),
            f"expected={[n for n, _ in colors]} final={fa[:250]!r}")
    expected = {name: code for name, code in colors}
    ok_bind, bound = bound_as(fa, list(expected), expected, r"\d{3}")
    j.check("codes_bound_to_colors", ok_bind, f"expected={expected} bound={bound}")
    j.emit()


if __name__ == "__main__":
    main()
