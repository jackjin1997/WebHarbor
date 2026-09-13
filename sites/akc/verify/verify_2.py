#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--2.

Compare Golden Retriever and Border Collie; report both Energy Level ratings and which is higher.

Checks: nav compare with both breeds | each rating bound to its breed | higher breed named
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--2")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    golden = trait_score(initial, "golden-retriever", "energy_level")
    collie = trait_score(initial, "border-collie", "energy_level")
    if not (j.derived("expected_golden_energy", golden)
            and j.derived("expected_collie_energy", collie)):
        j.emit()
    higher = "Border Collie" if collie > golden else "Golden Retriever"
    j.check("nav_compare", navigated_to(t, "/compare"), f"urls={visited_urls(t)[-6:]}")
    j.check("compare_has_both_breeds",
            navigated_to(t, "golden-retriever") and navigated_to(t, "border-collie"),
            f"urls={visited_urls(t)[-6:]}")
    ok_bind, bound = bound_as(fa, ["golden retriever", "border collie"],
                              {"golden retriever": golden, "border collie": collie})
    j.check("ratings_bound_to_breeds", ok_bind,
            f"expected={{'golden retriever': {golden}, 'border collie': {collie}}} bound={bound}")
    ok, ev = llm_text_match(
        fa, f"Golden Retriever Energy Level {golden}/5, Border Collie Energy Level {collie}/5, "
            f"so {higher} is the higher of the two",
        "Which of the two breeds has the higher Energy Level rating, and what are the two ratings?")
    j.check("answer_identifies_higher", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
