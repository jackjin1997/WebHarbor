"""Explicitly refresh the two revised local guides in an existing seed database.

Run once when preparing assets; never run this from the application boot path.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from support_content import SUPPORT_CONTENT


def refresh(database: Path) -> None:
    connection = sqlite3.connect(f"{database.resolve().as_uri()}?mode=rw", uri=True)
    try:
        with connection:
            for slug, content in SUPPORT_CONTENT.items():
                cursor = connection.execute(
                    "UPDATE support_articles SET summary=?, body=?, related_topics_json=? WHERE slug=?",
                    (content["summary"], content["body"], json.dumps(content["topics"]), slug),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"Expected exactly one existing article: {slug}")
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    refresh(parser.parse_args().database)
