"""Source, asset, task and offline-integrity tests for the AKC mirror.

Run with: uv run --with flask==3.1.0 --with flask-sqlalchemy==3.1.1 \
              python -m unittest discover -s sites/akc/tests
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

SITE = Path(__file__).resolve().parents[1]
ROOT = SITE.parents[1]

BREEDS = json.loads((SITE / "data/breeds.json").read_text(encoding="utf-8"))
CONTENT = json.loads((SITE / "data/content.json").read_text(encoding="utf-8"))
IMAGES = json.loads((SITE / "image_sources.json").read_text(encoding="utf-8"))["images"]
FONTS = json.loads((SITE / "font_sources.json").read_text(encoding="utf-8"))["fonts"]
TASKS = [json.loads(line) for line in
         (SITE / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

SHA256 = re.compile(r"^[0-9a-f]{64}$")
AKC_URL = re.compile(r"^https://www\.akc\.org/")


class SourceProvenanceTests(unittest.TestCase):
    def test_every_breed_has_a_captured_source(self):
        captured = {s["slug"]: s for s in BREEDS["sources"]}
        self.assertEqual(len(BREEDS["records"]), 24)
        for record in BREEDS["records"]:
            with self.subTest(slug=record["slug"]):
                source = captured.get(record["slug"])
                assert source is not None, "no capture source recorded"
                self.assertEqual(source["status"], 200)
                self.assertRegex(source["sha256"], SHA256)
                self.assertEqual(source["url"],
                                 f"https://www.akc.org/dog-breeds/{record['slug']}/")

    def test_content_sources_are_all_akc_and_hashed(self):
        self.assertTrue(CONTENT["sources"])
        for source in CONTENT["sources"]:
            with self.subTest(url=source["url"]):
                self.assertRegex(source["url"], AKC_URL)
                self.assertEqual(source["status"], 200)
                self.assertRegex(source["sha256"], SHA256)

    def test_breed_records_carry_real_upstream_fields(self):
        """Guards against the templated filler the original contribution shipped."""
        for record in BREEDS["records"]:
            with self.subTest(slug=record["slug"]):
                self.assertTrue(record["about_html"].strip())
                self.assertTrue(record["height"].strip())
                self.assertTrue(record["weight"].strip())
                self.assertTrue(record["life_expectancy"].strip())
                self.assertGreaterEqual(len(record["traits"]), 10)
                for trait in record["traits"]:
                    self.assertIn(trait["score"], (1, 2, 3, 4, 5))
                    self.assertTrue(trait["label"].strip())
                self.assertNotIn("benchmark browsing tasks", json.dumps(record))
                self.assertNotIn("AKC-style", record["about_html"])

    def test_articles_and_events_are_sourced(self):
        for article in CONTENT["articles"]:
            with self.subTest(slug=article["slug"]):
                self.assertRegex(article["source_url"], AKC_URL)
                self.assertTrue(article["author"].strip())
                self.assertRegex(article["published"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(article["body"])
        for event in CONTENT["events"]:
            with self.subTest(slug=event["slug"]):
                self.assertRegex(event["source_url"], AKC_URL)
                self.assertRegex(event["start_date"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(event["city"].strip())


class AssetProvenanceTests(unittest.TestCase):
    def test_image_manifest_is_complete(self):
        self.assertEqual(len(IMAGES), 120)
        breeds = {record["slug"] for record in BREEDS["records"]}
        for entry in IMAGES:
            with self.subTest(file=entry["file"]):
                self.assertIn(entry["breed"], breeds)
                self.assertRegex(entry["source_url"], r"^https://www\.akc\.org/")
                self.assertRegex(entry["source_sha256"], SHA256)
                self.assertRegex(entry["output_sha256"], SHA256)
                self.assertTrue(entry["alt"].strip(), "alt text is required")

    def test_image_bytes_match_the_manifest_when_assets_are_present(self):
        """Assets live in the HF bundle, so skip when they are not checked out."""
        missing = [e for e in IMAGES if not (SITE / "static/images" / e["file"]).exists()]
        if len(missing) == len(IMAGES):
            self.skipTest("static/images not present (fetch_assets.sh not run)")
        self.assertEqual(missing, [], "manifest lists images that are not on disk")
        for entry in IMAGES:
            path = SITE / "static/images" / entry["file"]
            with self.subTest(file=entry["file"]):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, entry["output_sha256"])

    def test_fonts_are_open_licence_and_bundled(self):
        for font in FONTS:
            with self.subTest(file=font["file"]):
                self.assertIn("Open Font License", font["licence"])
                self.assertRegex(font["source_url"], r"^https://fonts\.gstatic\.com/")
                path = SITE / "static" / font["file"]
                self.assertTrue(path.exists(), "font file missing from the repo")
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 font["sha256"])


class TaskContractTests(unittest.TestCase):
    REQUIRED = {"web_name", "id", "ques", "web", "upstream_url",
                "verifier_path", "judge_rubric"}

    def test_task_rows_have_exactly_the_reviewed_contract(self):
        self.assertEqual(len(TASKS), 18)
        seen = set()
        for task in TASKS:
            with self.subTest(task=task.get("id")):
                self.assertEqual(set(task), self.REQUIRED)
                self.assertNotIn(task["id"], seen)
                seen.add(task["id"])
                self.assertEqual(task["web"], "http://localhost:40024/")
                self.assertEqual(task["upstream_url"], "https://www.akc.org/")
                self.assertTrue(task["judge_rubric"].startswith("FACT CHECKPOINTS"))

    def test_every_task_has_its_verifier(self):
        for task in TASKS:
            with self.subTest(task=task["id"]):
                number = task["id"].split("--")[1]
                self.assertEqual(task["verifier_path"],
                                 f"sites/akc/verify/verify_{number}.py")
                self.assertTrue((ROOT / task["verifier_path"]).exists())

    def test_verifier_count_matches_task_count(self):
        verifiers = sorted(p.name for p in (SITE / "verify").glob("verify_*.py")
                           if p.name != "verify_lib.py")
        self.assertEqual(len(verifiers), len(TASKS))

    def test_login_tasks_carry_the_demo_credentials(self):
        """CONTRIBUTING requires the demo credentials inline for login tasks."""
        for task in TASKS:
            if "log in" in task["ques"].lower():
                with self.subTest(task=task["id"]):
                    self.assertIn("alice.j@test.com", task["ques"])
                    self.assertIn("TestPass123!", task["ques"])

    # What each task's answer actually is. A breed the question already names
    # (the Rottweiler in AKC--17) is the subject, not the answer, so only the
    # values the agent has to go and find are listed here.
    ANSWER_TOKENS = {
        "AKC--0": ["071"],
        "AKC--1": ["French Bulldog"],
        "AKC--4": ["Whippet", "Newfoundland", "West Highland"],
        "AKC--5": ["Junior Showmanship"],
        "AKC--6": ["Can Dogs Eat Bananas"],
        "AKC--7": ["Rottweiler"],
        "AKC--10": ["30-32"],
        "AKC--11": ["Newfoundland"],
        "AKC--12": ["Poodle"],
        "AKC--17": ["015", "013", "018", "Black & Rust", "Black & Mahogany"],
    }

    def test_neither_question_nor_rubric_leaks_its_own_answer(self):
        """A task file is read by the agent: answers live only in the verifiers."""
        for task in TASKS:
            visible = (task["ques"] + " " + task["judge_rubric"]).casefold()
            for token in self.ANSWER_TOKENS.get(task["id"], []):
                with self.subTest(task=task["id"], token=token):
                    self.assertNotIn(token.casefold(), visible)

    def test_answer_token_map_covers_the_fact_tasks(self):
        """Keeps the leak check honest if tasks are renumbered or added."""
        ids = {task["id"] for task in TASKS}
        self.assertTrue(set(self.ANSWER_TOKENS).issubset(ids),
                        "ANSWER_TOKENS references a task that no longer exists")

    def test_no_answer_key_in_the_agent_facing_file(self):
        raw = (SITE / "tasks.jsonl").read_text(encoding="utf-8").casefold()
        for forbidden in ('"answer"', '"expected"', '"ground_truth"'):
            self.assertNotIn(forbidden, raw)


class OfflineIntegrityTests(unittest.TestCase):
    EXTERNAL = re.compile(r"https?://(?!localhost|127\.0\.0\.1)", re.I)

    def test_templates_and_css_make_no_off_site_requests(self):
        offenders = []
        for path in list((SITE / "templates").rglob("*.html")) + \
                list((SITE / "static").rglob("*.css")):
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.EXTERNAL.search(line):
                    offenders.append(f"{path.relative_to(SITE)}:{line_no}: {line.strip()[:80]}")
        self.assertEqual(offenders, [], "offline benchmark must not reach off-site")

    def test_site_is_registered_on_its_own_port(self):
        start = (ROOT / "websyn_start.sh").read_text(encoding="utf-8")
        control = (ROOT / "control_server.py").read_text(encoding="utf-8")
        self.assertIn("akc", start)
        self.assertIn("'akc'", control)
        sites = re.search(r"SITES=\(([^)]*)\)", start, re.S)
        assert sites is not None
        names = sites.group(1).split()
        self.assertEqual(names.index("akc"), 24, "akc must stay on port 40024")


if __name__ == "__main__":
    unittest.main()
