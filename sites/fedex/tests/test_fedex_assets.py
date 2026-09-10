from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE_ROOT))
from refresh_support import refresh  # noqa: E402
from support_content import SUPPORT_CONTENT  # noqa: E402


class FedExAssetTests(unittest.TestCase):
    def test_homepage_assets_match_manifest_and_have_media_headers(self) -> None:
        manifest = json.loads((SITE_ROOT / "docs/homepage-assets.json").read_text())
        self.assertEqual(18, len(manifest["assets"]))
        for item in manifest["assets"]:
            path = SITE_ROOT / item["file"]
            with self.subTest(asset=path.name):
                data = path.read_bytes()
                self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(item["bytes"], len(data))
                if path.suffix == ".woff2":
                    self.assertEqual(b"wOF2", data[:4])
                else:
                    self.assertTrue(
                        data.startswith(b"\xff\xd8\xff")
                        or data.startswith(b"\x89PNG\r\n\x1a\n")
                        or (data.startswith(b"RIFF") and data[8:12] == b"WEBP"),
                        "Expected JPEG, PNG or WebP, not an HTML error page",
                    )

    def test_two_fresh_boots_preserve_seed_bytes(self) -> None:
        seed = SITE_ROOT / "instance_seed/fedex.db"
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "fedex.db"
            shutil.copy2(seed, database)
            expected = hashlib.sha256(database.read_bytes()).hexdigest()
            environment = os.environ.copy()
            environment.pop("WEBSYN_SKIP_BOOTSTRAP", None)
            environment["FEDEX_DATABASE_URI"] = f"sqlite:///{database}"
            for _ in range(2):
                subprocess.run([sys.executable, "-c", "import app"], cwd=SITE_ROOT,
                               env=environment, check=True, capture_output=True)
                self.assertEqual(expected, hashlib.sha256(database.read_bytes()).hexdigest())

    def test_refresh_rolls_back_if_an_expected_article_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "partial.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE support_articles (slug TEXT, summary TEXT, body TEXT, related_topics_json TEXT)")
                connection.execute("INSERT INTO support_articles VALUES (?, ?, ?, ?)",
                                   ("shipment-exception-status", "old", "old", "[]"))
            with self.assertRaises(ValueError):
                refresh(database)
            with sqlite3.connect(database) as connection:
                self.assertEqual(("old", "old", "[]"), connection.execute(
                    "SELECT summary, body, related_topics_json FROM support_articles").fetchone())

    def test_packaged_guides_match_canonical_local_content(self) -> None:
        database = SITE_ROOT / "instance_seed/fedex.db"
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            for slug, content in SUPPORT_CONTENT.items():
                row = connection.execute(
                    "SELECT summary, body, related_topics_json FROM support_articles WHERE slug=?", (slug,)
                ).fetchone()
                self.assertEqual((content["summary"], content["body"], content["topics"]),
                                 (row[0], row[1], json.loads(row[2])))


if __name__ == "__main__":
    unittest.main()
