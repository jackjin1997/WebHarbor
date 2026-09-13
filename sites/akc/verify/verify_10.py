#!/usr/bin/env python3
"""Deterministic verifier for AKC task AKC--10.

Report the male height range on the Great Dane profile.

Checks: nav breed profile | male range bound to 'male', derived from initial_db
Ground truth is derived from the run's --initial_db, never frozen here.
Input/Output: see verify_lib.parse_args / Judge.emit.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_lib import *  # noqa: F401,F403


def main():
    a, j, t, fa = start("AKC--10")
    initial = resolve_db(a.initial_db, a.container, "instance_seed")
    height = breed_field(initial, "great-dane", "height")
    if not j.derived("expected_height_string", height):
        j.emit()
    import re as _re
    male = _re.search(r"([\d.]+\s*-\s*[\d.]+)\s*inches\s*\(male\)", height or "")
    female = _re.search(r"([\d.]+\s*-\s*[\d.]+)\s*inches\s*\(female\)", height or "")
    if not j.derived("expected_male_range", male.group(1) if male else None, f"height={height!r}"):
        j.emit()
    male_lo, male_hi = [p.strip() for p in male.group(1).split("-")]
    j.check("nav_breed_profile", navigated_to(t, "/breeds/great-dane"), f"urls={visited_urls(t)[-6:]}")
    j.check("answer_has_male_range", contains_all(fa, [male_lo, male_hi]),
            f"expected={male.group(1)!r} final={fa[:160]!r}")
    anchor = f"males stand {male.group(1)} inches"
    if female:
        anchor += f"; the female range {female.group(1)} inches is NOT the answer"
    ok, ev = llm_text_match(fa, anchor,
        "What height range does the Great Dane profile list for males?")
    j.check("answer_is_male_not_female", ok, ev, llm=True)
    j.emit()


if __name__ == "__main__":
    main()
