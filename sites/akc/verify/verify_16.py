#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--16.

Search the site for agility and report the Expert Advice and event result counts.

Checks: nav search | both counts recomputed from initial_db and bound to their sections
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--16")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    counts = search_counts(initial, "agility")
    if not (j.derived("expected_article_count", counts.get("articles"))
            and j.derived("expected_event_count", counts.get("events"))):
        j.emit()
    j.check("nav_search", navigated_to(t, "q=agility"), f"urls={visited_urls(t)[-6:]}")
    ok_bind = False
    bound = {}
    for advice_label in ("expert advice", "advice", "article"):
        ok_bind, bound = bound_as(
            fa, [advice_label, "event"],
            {advice_label: counts["articles"], "event": counts["events"]})
        if ok_bind:
            break
    j.check("counts_bound_to_sections", ok_bind,
            f"expected={{'advice': {counts['articles']}, 'event': {counts['events']}}} bound={bound}")
    ok, ev = llm_text_match(
        fa, f"{counts['articles']} Expert Advice results and {counts['events']} event results",
        "How many Expert Advice results and how many event results did the search for agility return?")
    j.check("answer_counts_correct", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
