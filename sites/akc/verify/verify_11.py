#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--11.

Report what the Labrador Retriever's Did You Know section says about the breed's actual origin.

Checks: nav breed profile | origin claim matched against the fact stored in initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--11")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    facts = facts_for(initial, "labrador-retriever")
    origin_fact = next((f for f in facts if "newfoundland" in norm(f)), None)
    if not j.derived("expected_origin_fact", origin_fact, f"facts={facts[:2]}"):
        j.emit()
    j.check("nav_breed_profile", navigated_to(t, "/breeds/labrador-retriever"),
            f"urls={visited_urls(t)[-6:]}")
    j.check("answer_names_newfoundland", contains_all(fa, ["Newfoundland"]),
            f"final={fa[:200]!r}")
    ok, ev = llm_text_match(fa, origin_fact,
        "According to the Did You Know section, where did the Labrador Retriever actually come from?")
    j.check("answer_matches_fact", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
