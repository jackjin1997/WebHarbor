#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--12.

Identify the only Hypoallergenic Dogs breed in the directory and report its Shedding Level rating.

Checks: nav collection + breed profile | breed and rating derived from initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--12")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    members = breeds_with_characteristic(initial, "hypoallergenic-dogs")
    if not j.derived("expected_single_member", members if len(members) == 1 else None,
                     f"members={members}"):
        j.emit()
    slug, name = members[0]
    shedding = trait_score(initial, slug, "shedding_level")
    if not j.derived("expected_shedding_level", shedding):
        j.emit()
    j.check("nav_collection", navigated_to(t, "characteristic=hypoallergenic-dogs"),
            f"urls={visited_urls(t)[-8:]}")
    j.check("nav_breed_profile", navigated_to(t, f"/breeds/{slug}"), f"urls={visited_urls(t)[-8:]}")
    j.check("answer_names_breed", contains_all(fa, [name]), f"expected={name!r} final={fa[:160]!r}")
    j.check("shedding_bound", pair_bound(fa, "shedding", shedding),
            f"expected={shedding} final={fa[:160]!r}")
    j.emit()


if __name__ == "__main__":
    main()
