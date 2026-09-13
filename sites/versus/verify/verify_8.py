#!/usr/bin/env python3
"""Versus--8: battery life of the Garmin Venu 3, reached via the smartwatches category."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_lib as V

SLUG = "garmin-venu-3"


def body(j, traj, initial, after):
    target = V.product(initial, SLUG)
    if not target:
        return j.fail("seed data missing", f"{SLUG} is not in initial_db")
    expected = target["spec_2_value"]

    ans = V.final_answer(traj)
    j.check("opened the smartwatches category listing",
            V.navigated_to(traj, f"/category/{target['category_slug']}"),
            f"steps={V.step_urls(traj)}")
    j.check("opened the fact-bearing page for the product",
            V.opened_detail_or_compare(traj, SLUG),
            f"steps={V.step_urls(traj)[-6:]}")
    j.check("answer is non-empty and not a denial",
            bool(ans) and not V.looks_negated(ans), f"answer={ans!r}")
    j.check("answer states the derived battery life",
            V.mentions_number(ans, expected),
            f"expected={expected} {target['unit_2']} from initial_db")
    ok, why = V.llm_text_match(ans, f"{expected} {target['unit_2']}",
                               "battery life in hours of the Garmin Venu 3")
    j.check("anchored LLM agreement", ok, why, llm=True)


if __name__ == "__main__":
    V.run("Versus--8", body)
