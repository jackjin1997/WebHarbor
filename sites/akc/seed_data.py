"""Build the AKC mirror's SQLite seed from the captured akc.org source data.

``data/breeds.json`` and ``data/content.json`` are the tracked source of truth
(see harvest_breeds.py / harvest_content.py). Everything a page shows about a
breed, article, event or sport comes from there.

The benchmark accounts at the bottom are the one deliberate exception: they are
local fixtures for the mirror's sign-in, saved-breed and event-registration
flows, not anything akc.org publishes.

Called once by app.py when the database is empty, and at image build time by
the Dockerfile so the seed ships deterministically.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
BREEDS_PATH = DATA_DIR / "breeds.json"
CONTENT_PATH = DATA_DIR / "content.json"

SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Local benchmark fixtures — synthetic, not akc.org data.
BENCHMARK_PASSWORD = "TestPass123!"
BENCHMARK_USERS = [
    {
        "username": "alice_j", "email": "alice.j@test.com",
        "display_name": "Alice Johnson", "household": "Apartment",
        "activity_level": "Moderate", "experience": "First-time owner",
        "saved": ["french-bulldog", "cavalier-king-charles-spaniel",
                  "boston-terrier"],
    },
    {
        "username": "bob_c", "email": "bob.c@test.com",
        "display_name": "Bob Chen", "household": "House with yard",
        "activity_level": "High", "experience": "Sports competitor",
        "saved": ["border-collie", "australian-shepherd",
                  "labrador-retriever"],
    },
    {
        "username": "carol_d", "email": "carol.d@test.com",
        "display_name": "Carol Davis", "household": "Suburban home",
        "activity_level": "Low", "experience": "Family owner",
        "saved": ["golden-retriever", "newfoundland", "beagle"],
    },
    {
        "username": "david_k", "email": "david.k@test.com",
        "display_name": "David Kim", "household": "Condo",
        "activity_level": "Moderate", "experience": "Experienced owner",
        "saved": ["poodle-standard", "whippet", "papillon"],
    },
]

CARE_SECTIONS = [
    ("health", "Health"),
    ("grooming", "Grooming"),
    ("exercise", "Exercise"),
    ("training", "Training"),
    ("nutrition", "Nutrition"),
]


# --------------------------------------------------------------------------- load
def _load(path: Path) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{path.name}: unsupported schema_version")
    for source in doc.get("sources", []):
        if not SHA256_RE.match(source.get("sha256", "")):
            raise ValueError(f"{path.name}: source {source.get('url')} has no capture hash")
        if source.get("status") != 200:
            raise ValueError(f"{path.name}: source {source.get('url')} was not captured with 200")
    return doc


def load_breeds(path: Path = BREEDS_PATH) -> list[dict]:
    doc = _load(path)
    records = doc["records"]
    slugs = [r["slug"] for r in records]
    if len(set(slugs)) != len(slugs):
        raise ValueError("breeds.json: duplicate breed slug")
    captured = {s["slug"] for s in doc["sources"]}
    for record in records:
        if record["slug"] not in captured:
            raise ValueError(f"breeds.json: {record['slug']} has no capture source")
        for field in ("name", "group", "life_expectancy", "height", "weight",
                      "about_html", "temperament"):
            if not record.get(field):
                raise ValueError(f"breeds.json: {record['slug']} is missing {field}")
    return records


def load_content(path: Path = CONTENT_PATH) -> dict:
    doc = _load(path)
    for key in ("sports", "events", "articles"):
        if not doc.get(key):
            raise ValueError(f"content.json: {key} is empty")
    for kind in ("events", "articles"):
        slugs = [r["slug"] for r in doc[kind]]
        if len(set(slugs)) != len(slugs):
            raise ValueError(f"content.json: duplicate {kind} slug")
    return doc


# --------------------------------------------------------------------------- helpers
TAG_RE = re.compile(r"<[^>]+>")
PARA_SPLIT_RE = re.compile(r"</p>\s*<p[^>]*>", re.I)


def html_paragraphs(html: str) -> list[str]:
    """Flatten akc.org's stored HTML into plain paragraphs.

    The mirror renders text, not upstream markup: keeping their inline links and
    ad shortcodes would either dangle or reach off-site.
    """
    if not html:
        return []
    body = re.sub(r"^\s*<p[^>]*>|</p>\s*$", "", html.strip(), flags=re.I)
    out = []
    for chunk in PARA_SPLIT_RE.split(body):
        text = TAG_RE.sub(" ", chunk)
        text = re.sub(r"&nbsp;?", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) > 1:
            out.append(text)
    return out


def first_sentence(text: str, limit: int = 220) -> str:
    if not text:
        return ""
    m = re.search(r"^(.+?[.!?])(\s|$)", text)
    sentence = m.group(1) if m else text
    return sentence if len(sentence) <= limit else sentence[:limit].rsplit(" ", 1)[0] + "…"


def parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- build
def build(db, models: dict, breeds_path: Path = BREEDS_PATH,
          content_path: Path = CONTENT_PATH) -> dict:
    """Populate an empty database. Returns a count summary."""
    breeds = load_breeds(breeds_path)
    content = load_content(content_path)

    Breed = models["Breed"]
    BreedTrait = models["BreedTrait"]
    BreedAttribute = models["BreedAttribute"]
    BreedColor = models["BreedColor"]
    BreedFact = models["BreedFact"]
    BreedCare = models["BreedCare"]
    BreedStandard = models["BreedStandard"]
    BreedPhoto = models["BreedPhoto"]
    Characteristic = models["Characteristic"]
    Article = models["Article"]
    Event = models["Event"]
    Sport = models["Sport"]

    characteristics: dict[str, object] = {}
    for record in breeds:
        for label in record.get("characteristics", []):
            if label not in characteristics:
                row = Characteristic(
                    name=label,
                    slug=re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-"),
                )
                db.session.add(row)
                characteristics[label] = row
    db.session.flush()

    by_slug: dict[str, object] = {}
    for record in breeds:
        about = html_paragraphs(record["about_html"])
        blurb = html_paragraphs(record["blurb_html"])
        breed = Breed(
            slug=record["slug"],
            name=record["name"],
            plural=record.get("plural") or record["name"] + "s",
            nicknames=record.get("nicknames") or "",
            akc_code=record.get("akc_code") or "",
            group=record["group"],
            origin=record.get("origin") or "",
            year_recognized=record.get("year_recognized") or "",
            life_expectancy=record["life_expectancy"],
            height=record["height"],
            weight=record["weight"],
            temperament=record["temperament"],
            popularity_rank=record.get("popularity_rank"),
            popularity_year=max(
                (int(y) for y in (record.get("popularity_history") or {})), default=None
            ),
            blurb=" ".join(blurb),
            about="\n\n".join(about),
            history="\n\n".join(html_paragraphs(record.get("history_html", ""))),
        )
        db.session.add(breed)
        db.session.flush()
        by_slug[record["slug"]] = breed

        for label in record.get("characteristics", []):
            breed.characteristics.append(characteristics[label])

        for position, trait in enumerate(record.get("traits", [])):
            db.session.add(BreedTrait(
                breed_id=breed.id, key=trait["key"], label=trait["label"],
                score=trait["score"], low_label=trait.get("low", ""),
                mid_label=trait.get("mid", ""), high_label=trait.get("high", ""),
                description=trait.get("description", ""), position=position,
            ))
        for position, choice in enumerate(record.get("trait_choices", [])):
            db.session.add(BreedAttribute(
                breed_id=breed.id, key=choice["key"], label=choice["label"],
                value=", ".join(choice.get("selected", [])),
                options=", ".join(choice.get("options", [])),
                description=choice.get("description", ""), position=position,
            ))
        for position, color in enumerate(record.get("colors", [])):
            db.session.add(BreedColor(
                breed_id=breed.id, kind="color", name=color["name"],
                code=color.get("code", ""), standard=bool(color.get("standard")),
                position=position,
            ))
        for position, marking in enumerate(record.get("markings", [])):
            db.session.add(BreedColor(
                breed_id=breed.id, kind="marking", name=marking["name"],
                code=marking.get("code", ""), standard=bool(marking.get("standard")),
                position=position,
            ))
        for position, fact in enumerate(record.get("did_you_know", [])):
            db.session.add(BreedFact(breed_id=breed.id, text=fact, position=position))
        for position, (key, label) in enumerate(CARE_SECTIONS):
            paragraphs = html_paragraphs((record.get("care") or {}).get(key, ""))
            if paragraphs:
                db.session.add(BreedCare(
                    breed_id=breed.id, section=key, label=label,
                    body="\n\n".join(paragraphs), position=position,
                ))
        for position, section in enumerate((record.get("standard") or {}).values()):
            paragraphs = html_paragraphs(section.get("text", ""))
            if section.get("title") and paragraphs:
                db.session.add(BreedStandard(
                    breed_id=breed.id, title=section["title"],
                    body="\n\n".join(paragraphs), position=position,
                ))

    # Photos: image_sources.json is written by fetch_images.py and lists what the
    # asset bundle actually contains, so the DB never points at a missing file.
    manifest_path = BASE_DIR / "image_sources.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["images"]
        for position, entry in enumerate(manifest):
            breed = by_slug.get(entry.get("breed"))
            if breed is None:
                continue
            db.session.add(BreedPhoto(
                breed_id=breed.id, file=entry["file"], role=entry.get("role", ""),
                alt=entry.get("alt", ""), credit=entry.get("credit", ""),
                position=position,
            ))

    for position, sport in enumerate(content["sports"]):
        db.session.add(Sport(
            name=sport["name"], slug=sport["slug"], position=position,
        ))

    for record in content["events"]:
        body = record.get("body") or []
        db.session.add(Event(
            slug=record["slug"], title=record["title"], sport=record.get("sport", ""),
            date_display=record.get("date_display", ""),
            starts_on=parse_iso(record.get("start_date", "")),
            ends_on=parse_iso(record.get("end_date", "")),
            city=record.get("city", ""), region=record.get("region", ""),
            summary=record.get("summary", ""), body="\n\n".join(body),
        ))

    for record in content["articles"]:
        body = record.get("body") or []
        db.session.add(Article(
            slug=record["slug"], title=record["title"],
            category=record.get("category", ""),
            category_slug=record.get("category_slug", ""),
            author=record.get("author", "AKC Staff"),
            published_on=parse_iso(record.get("published", "")),
            updated_on=parse_iso(record.get("modified", "")),
            read_minutes=record.get("read_minutes") or 1,
            word_count=record.get("word_count") or 0,
            summary=record.get("summary") or first_sentence(body[0] if body else ""),
            body="\n\n".join(body),
        ))

    db.session.commit()
    return {
        "breeds": len(breeds),
        "characteristics": len(characteristics),
        "sports": len(content["sports"]),
        "events": len(content["events"]),
        "articles": len(content["articles"]),
    }


def build_users(db, models: dict, password_hasher) -> int:
    """Seed the local benchmark accounts and their saved breeds."""
    User = models["User"]
    Breed = models["Breed"]
    SavedBreed = models["SavedBreed"]

    for spec in BENCHMARK_USERS:
        user = User(
            username=spec["username"], email=spec["email"],
            display_name=spec["display_name"], household=spec["household"],
            activity_level=spec["activity_level"], experience=spec["experience"],
            password_hash=password_hasher(BENCHMARK_PASSWORD),
        )
        db.session.add(user)
        db.session.flush()
        for slug in spec["saved"]:
            breed = Breed.query.filter_by(slug=slug).first()
            if breed is not None:
                db.session.add(SavedBreed(
                    user_id=user.id, breed_id=breed.id,
                    note="Saved from the breed profile.",
                ))
    db.session.commit()
    return len(BENCHMARK_USERS)
