#!/usr/bin/env python3
"""Versus--9: build the R6 Mark II vs A7 IV comparison in the picker and name the winner."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verify_lib as V

LEFT, RIGHT = "canon-eos-r6-mark-ii", "sony-a7-iv"


def body(j, traj, initial, after):
    left, right = V.product(initial, LEFT), V.product(initial, RIGHT)
    if not (left and right):
        return j.fail("seed data missing", "one of the two cameras is not in initial_db")
    if left["score"] == right["score"]:
        return j.fail("ambiguous ground truth",
                      "the two cameras tie on Versus Score in initial_db, so the "
                      "site's winner depends on argument order")
    # The site declares the higher Versus Score the winner (app.winner()).
    target = left if left["score"] > right["score"] else right
    loser = right if target is left else left

    ans = V.final_answer(traj)
    j.check("used the compare picker", V.navigated_to(traj, "/compare"),
            f"steps={V.step_urls(traj)}")
    j.check("landed on the comparison page for this pair",
            V.navigated_to(traj, f"/compare/{LEFT}-vs-{RIGHT}")
            or V.navigated_to(traj, f"/compare/{RIGHT}-vs-{LEFT}"),
            f"steps={V.step_urls(traj)[-6:]}")
    j.check("answer is non-empty and not a denial",
            bool(ans) and not V.looks_negated(ans), f"answer={ans!r}")
    j.check("answer names the product the site declares the winner",
            V.mentions_product(ans, target["name"]),
            f"expected={target['name']!r} (score {target['score']} vs {loser['score']})")
    ok, why = V.llm_text_match(
        ans, f"the winner is {target['name']}",
        "which product the site declares the winner of this comparison")
    j.check("anchored LLM agreement", ok, why, llm=True)


if __name__ == "__main__":
    V.run("Versus--9", body)
