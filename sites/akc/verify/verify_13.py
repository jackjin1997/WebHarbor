#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--13.

Report the Siberian Husky's Barking Level and Drooling Level ratings.

Checks: nav breed profile | each rating bound to its own trait so a swap fails
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--13")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    barking = trait_score(initial, "siberian-husky", "barking_level")
    drooling = trait_score(initial, "siberian-husky", "drooling_level")
    if not (j.derived("expected_barking", barking) and j.derived("expected_drooling", drooling)):
        j.emit()
    j.check("nav_breed_profile", navigated_to(t, "/breeds/siberian-husky"),
            f"urls={visited_urls(t)[-6:]}")
    ok_bind, bound = bound_as(fa, ["barking", "drooling"],
                              {"barking": barking, "drooling": drooling})
    j.check("ratings_bound_to_traits", ok_bind,
            f"expected={{'barking': {barking}, 'drooling': {drooling}}} bound={bound}")
    j.emit()


if __name__ == "__main__":
    main()
