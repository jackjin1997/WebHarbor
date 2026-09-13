#!/usr/bin/env python3
"""Harvest the AKC breed records that back this mirror's seed DB.

Every breed page on akc.org embeds its full record as JSON in the
``data-js-props`` attribute of ``<div data-js-component="breedPage">``. That
blob is the authoritative source for traits, vital stats, popularity history,
colors/markings (with AKC registration codes), the standard, the long-form
copy and the photo gallery (with credits).

Writes ``data/breeds.json`` — tracked source data, from which ``seed_data.py``
builds the SQLite seed deterministically at container build time (the same
pattern osu and rotten_tomatoes use).

    python3 harvest_breeds.py            # all breeds in BREED_SLUGS
    python3 harvest_breeds.py beagle     # refresh a subset
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import ssl
import sys
import time
import urllib.request

from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "data", "breeds.json")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
BREED_URL = "https://www.akc.org/dog-breeds/{slug}/"
REQUEST_DELAY_S = 1.5

# The 24 breeds the original contribution shipped. Kept as-is: this mirror is a
# deliberate benchmark-sized subset of the 200+ breeds akc.org carries.
BREED_SLUGS = [
    "labrador-retriever", "golden-retriever", "french-bulldog",
    "german-shepherd-dog", "poodle-standard", "beagle", "dachshund",
    "rottweiler", "cavalier-king-charles-spaniel", "australian-shepherd",
    "boxer", "border-collie", "shih-tzu", "bernese-mountain-dog",
    "doberman-pinscher", "cocker-spaniel", "great-dane", "papillon",
    "whippet", "west-highland-white-terrier", "siberian-husky",
    "boston-terrier", "newfoundland", "chihuahua",
]

# Trait axes we surface on the breed page, in akc.org's own display order.
TRAIT_ORDER = [
    "affectionate_with_family", "good_with_young_children",
    "good_with_other_dogs", "shedding_level", "coat_grooming_frequency",
    "drooling_level", "coat_type", "coat_length", "openness_to_strangers",
    "playfulness_level", "watchdogprotective_nature", "adaptability_level",
    "trainability_level", "energy_level", "barking_level",
    "mental_stimulation_needs",
]


def _ssl_context() -> ssl.SSLContext:
    # python.org builds on macOS ship without a system trust store.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch(url: str) -> tuple[str, int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=_ssl_context()) as resp:
        raw = resp.read()
        status = resp.status
    return raw.decode("utf-8", "replace"), status, hashlib.sha256(raw).hexdigest()


def scalar_trait(t: dict) -> dict | None:
    """A 1-5 scored axis (energy, shedding, ...) with its named poles."""
    if t.get("score") in (None, "", 0) or t.get("choices"):
        return None
    return {
        "key": t["traits_url"],
        "label": t["traits"],
        "score": int(t["score"]),
        "low": t.get("low_value_1") or "",
        "mid": t.get("middle_value_3") or "",
        "high": t.get("high_value_5") or "",
        "description": t.get("description") or "",
    }


def choice_trait(t: dict) -> dict | None:
    """A pick-from-a-set axis (coat type, coat length)."""
    selected = t.get("selected") or []
    if not selected:
        return None
    options = t.get("choices") or []
    if isinstance(options, dict):
        options = list(options.values())
    return {
        "key": t["traits_url"],
        "label": t["traits"],
        "selected": [str(s) for s in selected],
        "options": [str(o) for o in options],
        "description": t.get("description") or "",
    }


# akc.org publishes the vital stats as a PageMap DataObject in an HTML comment
# rather than in the JSON blob, so they are read straight out of the markup.
PAGEMAP_RE = {
    "height": re.compile(r'name="height">Height:\s*(.*?)</Attribute>', re.S),
    "weight": re.compile(r'name="weight">Weight:\s*(.*?)</Attribute>', re.S),
}


def pagemap_value(html: str, field: str) -> str:
    m = PAGEMAP_RE[field].search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def parse_breed(slug: str, html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    node = soup.find("div", attrs={"data-js-component": "breedPage"})
    if node is None:
        raise SystemExit(f"{slug}: no breedPage component on the page")
    props = json.loads(node["data-js-props"])
    data = props["settings"]["breed_data"]

    def section(name: str) -> dict:
        """Not every breed carries every section (e.g. no markings table)."""
        return (data.get(name) or {}).get(slug) or {}

    basics = section("basics")
    traits_blob = section("traits")
    desc = section("description")
    health = section("health")
    history = section("history")
    standards = section("standards")

    scored, choices = [], []
    for key in TRAIT_ORDER:
        t = (traits_blob.get("traits") or {}).get(key)
        if not t:
            continue
        s = scalar_trait(t)
        if s:
            scored.append(s)
            continue
        c = choice_trait(t)
        if c:
            choices.append(c)

    popularity = {
        year: basics[f"popularity_{year}"]
        for year in range(2015, 2026)
        if basics.get(f"popularity_{year}")
    }

    gallery = []
    for item in (props.get("breed", {}).get("media", {}).get("gallery") or []):
        if not item.get("src"):
            continue
        gallery.append({
            "src": item["src"],
            "alt": item.get("alt") or "",
            "caption": item.get("caption") or "",
            "credit": item.get("credit") or "",
        })

    return {
        "slug": slug,
        "name": basics["breed_name"],
        "plural": basics.get("breed_name_plural") or "",
        "nicknames": basics.get("breed_nicknames") or "",
        "akc_code": basics.get("akc_code") or "",
        "group": basics.get("breed_group") or "",
        "origin": basics.get("origin") or "",
        "year_recognized": basics.get("year_recognized") or "",
        "life_expectancy": basics.get("life_expectancy") or "",
        "height": pagemap_value(html, "height"),
        "weight": pagemap_value(html, "weight"),
        # AKC's own characteristic collections (Smallest Dog Breeds, Best Dogs
        # for Apartment Dwellers, ...). Not every breed carries one.
        "characteristics": [
            c.strip()
            for c in (basics.get("related_groups_characteristics") or "").split(",")
            if c.strip()
        ],
        "temperament": traits_blob.get("temperament") or "",
        "popularity_rank": basics.get("popularity_2025"),
        "popularity_history": popularity,
        "related_breeds": basics.get("related_breeds_items_url") or [],
        "traits": scored,
        "trait_choices": choices,
        "colors": [
            {"name": c["color_long"], "code": c.get("cde_color") or "",
             "standard": (c.get("standard_alternate") or "") == "S"}
            for c in (section("colors").get("colors") or [])
        ],
        "markings": [
            {"name": m["markings_long"], "code": m.get("cde_markings") or "",
             "standard": (m.get("standard_alternate") or "") == "S"}
            for m in (section("markings").get("markings") or [])
        ],
        "blurb_html": desc.get("akc_org_blurb") or "",
        "about_html": desc.get("akc_org_about") or "",
        "history_html": history.get("akc_org_history") or "",
        "did_you_know": [
            s.strip() for s in (history.get("did_you_know") or "").split("|")
            if s.strip()
        ],
        "care": {
            "health": health.get("akc_org_health") or "",
            "grooming": health.get("akc_org_grooming") or "",
            "exercise": health.get("akc_org_exercise") or "",
            "training": health.get("akc_org_training") or "",
            "nutrition": health.get("akc_org_nutrition") or "",
        },
        "health_tests": [
            t.strip() for t in
            (health.get("tests_pipe_delimited_list") or "").split("|")
            if t.strip()
        ],
        "standard": {
            f"section_{i}": {
                "title": standards.get(f"title_{i}") or "",
                "text": standards.get(f"description_{i}") or "",
            }
            for i in (1, 2, 3)
            if standards.get(f"title_{i}")
        },
        "gallery": gallery,
    }


def main(argv: list[str]) -> int:
    wanted = argv[1:] or BREED_SLUGS
    unknown = [s for s in wanted if s not in BREED_SLUGS]
    if unknown:
        raise SystemExit(f"not in BREED_SLUGS: {', '.join(unknown)}")

    existing = {}
    if os.path.exists(OUT_PATH):
        existing = json.load(open(OUT_PATH, encoding="utf-8"))

    records = {r["slug"]: r for r in existing.get("records", [])}
    sources = {s["slug"]: s for s in existing.get("sources", [])}

    for i, slug in enumerate(wanted, 1):
        url = BREED_URL.format(slug=slug)
        html, status, sha = fetch(url)
        records[slug] = parse_breed(slug, html)
        sources[slug] = {
            "slug": slug, "url": url, "status": status, "sha256": sha,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        n = records[slug]
        print(f"[{i}/{len(wanted)}] {slug}: {len(n['traits'])} traits, "
              f"{len(n['colors'])} colors, {len(n['gallery'])} photos, "
              f"rank {n['popularity_rank']}")
        if i < len(wanted):
            time.sleep(REQUEST_DELAY_S)

    out = {
        "schema_version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "capture_scope": (
            "Breed detail pages only, for the 24 breeds this mirror carries. "
            "Each record comes from the breedPage data-js-props blob on "
            "akc.org/dog-breeds/<slug>/; no linked-page traversal. Photo "
            "bytes are fetched separately by fetch_images.py."
        ),
        "source_site": "https://www.akc.org/",
        "sources": [sources[s] for s in BREED_SLUGS if s in sources],
        "records": [records[s] for s in BREED_SLUGS if s in records],
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    print(f"\nwrote {OUT_PATH}: {len(out['records'])} breeds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
