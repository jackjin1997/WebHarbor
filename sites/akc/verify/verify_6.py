#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--6.

Filter Expert Advice to Nutrition; report the article count and the earliest-published title.

Checks: nav category filter | count and earliest title derived from initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--6")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    rows = articles_in_category(initial, "nutrition")
    if not j.derived("expected_nutrition_articles", rows):
        j.emit()
    count = len(rows)
    earliest = rows[0][0]
    j.check("nav_nutrition_filter", navigated_to(t, "category=nutrition"),
            f"urls={visited_urls(t)[-6:]}")
    j.check("answer_has_count", str(count) in fa, f"expected={count} final={fa[:200]!r}")
    j.check("answer_names_earliest", contains_all(fa, [earliest[:40]]),
            f"expected={earliest!r} final={fa[:200]!r}")
    ok, ev = llm_text_match(fa, f"{count} articles; earliest published is \"{earliest}\"",
        "How many Nutrition articles are listed and which was published earliest?")
    j.check("answer_count_and_title", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
