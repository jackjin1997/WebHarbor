#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--7.

Filter the directory to the Working Group; report the count and the one breed absent from Largest Dog Breeds.

Checks: nav group filter + collection | count and the set difference derived from initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--7")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    working = breeds_in_group(initial, "Working Group")
    largest = breeds_with_characteristic(initial, "largest-dog-breeds")
    if not (j.derived("expected_working_group", working)
            and j.derived("expected_largest_collection", largest)):
        j.emit()
    largest_slugs = {slug for slug, _ in largest}
    missing = [name for slug, name in working if slug not in largest_slugs]
    if not j.derived("expected_single_missing_breed", missing if len(missing) == 1 else None,
                     f"missing={missing}"):
        j.emit()
    answer_breed = missing[0]
    others = [name for slug, name in working if slug in largest_slugs]
    j.check("nav_working_filter", navigated_any(t, ["group=Working", "group=Working+Group", "group=Working%20Group"]),
            f"urls={visited_urls(t)[-8:]}")
    j.check("consulted_largest_collection",
            navigated_any(t, ["largest-dog-breeds"] + [f"/breeds/{slug}" for slug, _ in working]),
            f"urls={visited_urls(t)[-8:]}")
    j.check("answer_has_group_count", str(len(working)) in fa,
            f"expected={len(working)} final={fa[:200]!r}")
    j.check("answer_names_missing_breed", contains_all(fa, [answer_breed]),
            f"expected={answer_breed!r} final={fa[:200]!r}")
    ok, ev = llm_text_match(
        fa, f"{len(working)} Working Group breeds; the one NOT in Largest Dog Breeds is {answer_breed} "
            f"(the others — {', '.join(others)} — are all in the collection)",
        "How many Working Group breeds are listed, and which one is not in the Largest Dog Breeds collection?")
    j.check("answer_identifies_exclusion", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
