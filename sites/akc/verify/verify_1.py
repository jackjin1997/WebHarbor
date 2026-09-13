#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--1.

Run the Breed Selector for an apartment home with the four given ratings and report the top recommendation.

Checks: nav selector | top pick recomputed from initial_db with the site's own scoring
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--1")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    expected = selector_top(initial, "apartment", energy=2, grooming=2, children=5, training=4)
    if not j.derived("expected_top_breed", expected,
                     f"ranking={ (selector_ranking(initial, 'apartment', energy=2, grooming=2, children=5, training=4) or [])[:3] }"):
        j.emit()
    j.check("nav_selector", navigated_to(t, "/breed-selector"), f"urls={visited_urls(t)[-6:]}")
    j.check("answer_names_top_breed", contains_all(fa, [expected]),
            f"expected={expected!r} final={fa[:160]!r}")
    j.emit()


if __name__ == "__main__":
    main()
