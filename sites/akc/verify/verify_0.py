#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--0.

Report the AKC registration code for the Labrador Retriever's Chocolate coat colour.

Checks: nav breed profile | code bound to Chocolate, derived from initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--0")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    colors = colors_for(initial, "labrador-retriever")
    expected = dict(colors).get("Chocolate")
    if not j.derived("expected_code", expected, f"colors={colors}"):
        j.emit()
    j.check("nav_breed_profile", navigated_to(t, "/breeds/labrador-retriever"),
            f"urls={visited_urls(t)[-6:]}")
    j.check("code_bound_to_chocolate", pair_bound(fa, "chocolate", expected, r"\d{3}"),
            f"expected={expected} bound={value_near(fa, 'chocolate', r'\d{3}')!r}")
    ok, ev = llm_text_match(fa, f"Chocolate = registration code {expected}",
        "What registration code does the Labrador Retriever profile list for the Chocolate colour?")
    j.check("answer_code", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
