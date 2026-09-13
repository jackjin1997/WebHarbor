#!/usr/bin/env python3
"""Harvest the AKC mirror's editorial and events content from akc.org.

Three catalogs, all from the live site:

* ``sports``   — the AKC sports/disciplines tiled on the akc.org homepage.
* ``events``   — the Featured Events akc.org/sports/ promotes, each enriched
                 from its own detail page.
* ``articles`` — Expert Advice articles, taken per category so the mirror's
                 category filter has real spread. Title, byline, publication
                 date and body all come from the article page (ld+json plus
                 ``div.content-body__text``).

Writes ``data/content.json`` alongside ``data/breeds.json``; ``seed_data.py``
turns both into the SQLite seed at container build time.

    python3 harvest_content.py
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html as html_mod
import json
import os
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "data", "content.json")

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HOME_URL = "https://www.akc.org/"
SPORTS_URL = "https://www.akc.org/sports/"
# The newest of akc.org's six article sitemaps; it indexes the current Expert
# Advice set with lastmod stamps, which is steadier than scraping a category
# page whose list is filled in by a "load more" component.
ARTICLE_SITEMAP = "https://www.akc.org/article-sitemap6.xml"
REQUEST_DELAY_S = 1.2

# Expert Advice categories to pull from, and how many articles each.
ARTICLE_CATEGORIES = [
    ("health", 4),
    ("training", 3),
    ("nutrition", 3),
    ("dog-breeds", 3),
    ("lifestyle", 3),
    ("puppy-information", 2),
    ("sports", 2),
    ("news", 2),
]

ARTICLE_HREF_RE = re.compile(
    r"^https?://(?:www\.)?akc\.org/expert-advice/(?P<cat>[a-z0-9-]+)/"
    r"(?P<slug>[a-z0-9][a-z0-9-]{6,})/$"
)

_sources: list[dict] = []


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CTX = ssl_context()


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=45, context=_CTX) as resp:
        raw = resp.read()
        status = resp.status
    _sources.append({
        "url": url,
        "status": status,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    return raw.decode("utf-8", "replace")


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def clean(node) -> str:
    """Text of an element without the spurious spaces inline tags introduce."""
    return re.sub(r"\s+", " ", html_mod.unescape(node.get_text(""))).strip()


def slugify(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def event_slug(url: str) -> str:
    """Unique slug for an event.

    Two different Featured Events can end in the same path segment (both
    conformation and obedience have a `national-championship`), so qualify the
    last segment with the discipline directory above it.
    """
    parts = [p for p in urllib.parse.urlparse(url).path.strip("/").split("/") if p]
    tail = parts[-1] if parts else "event"
    for part in reversed(parts[:-1]):
        if part not in ("events", "sports"):
            return f"{part}-{tail}" if part not in tail else tail
    return tail


# --------------------------------------------------------------------------- sports
def harvest_sports(home: BeautifulSoup) -> list[dict]:
    heading = next(
        (h for h in home.find_all("h2") if "Sports & Events" in h.get_text()), None
    )
    if heading is None:
        raise SystemExit("homepage: no 'Sports & Events' section")
    block = heading.find_parent(["section", "div"])
    while block is not None and len(block.find_all("h3")) < 5:
        block = block.find_parent(["section", "div"])
    if block is None:
        raise SystemExit("homepage: could not locate the sports tile block")

    out, seen = [], set()
    for h3 in block.find_all("h3"):
        name = h3.get_text(strip=True)
        if not name or name in seen:
            continue
        anchor = h3.find_parent("a") or h3.find("a") or h3.find_next("a", href=True)
        seen.add(name)
        out.append({
            "name": name,
            "slug": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            "source_url": anchor["href"] if anchor and anchor.has_attr("href") else "",
        })
    return out


# --------------------------------------------------------------------------- events
DATE_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]+)\s+(?P<d1>\d{1,2})(?:-(?P<d2>\d{1,2}))?,\s*(?P<year>\d{4})$"
)


def parse_event_dates(text: str) -> tuple[str, str, str]:
    """'December 12-13, 2026' -> ('2026-12-12', '2026-12-13', original)."""
    m = DATE_RE.match(text.strip())
    if not m:
        return "", "", text.strip()
    month = dt.datetime.strptime(m.group("month"), "%B").month
    year = int(m.group("year"))
    start = dt.date(year, month, int(m.group("d1")))
    end = dt.date(year, month, int(m.group("d2"))) if m.group("d2") else start
    return start.isoformat(), end.isoformat(), text.strip()


def harvest_events(sports_page: BeautifulSoup) -> list[dict]:
    slider = sports_page.find(attrs={"data-js-component": "eventSlider"})
    if slider is None:
        raise SystemExit("sports page: no eventSlider")

    events = []
    for anchor in slider.find_all("a", href=True):
        lines = [l for l in anchor.get_text("\n", strip=True).split("\n") if l.strip()]
        if len(lines) < 3:
            continue
        title, raw_date, location = lines[0], lines[1], lines[2]
        start, end, display = parse_event_dates(raw_date)
        if not start:
            continue
        city, _, region = location.partition(",")
        img = anchor.find("img")
        events.append({
            "slug": event_slug(urllib.parse.urljoin(SPORTS_URL, anchor["href"])),
            "title": title,
            "date_display": display,
            "start_date": start,
            "end_date": end,
            "city": city.strip(),
            "region": region.strip(),
            "source_url": urllib.parse.urljoin(SPORTS_URL, anchor["href"]),
            "image_url": (img.get("data-src") or img.get("src") or "") if img else "",
        })

    for ev in events:
        try:
            detail = soup(fetch(ev["source_url"]))
        except Exception as exc:                          # noqa: BLE001
            print(f"  !! {ev['slug']}: {exc}")
            ev["summary"] = ""
            ev["body"] = []
            continue
        body = detail.find("div", class_=re.compile(r"content-body__text"))
        scope = body or detail.find("main") or detail
        paras = [clean(p) for p in scope.find_all("p")]
        paras = [p for p in paras if len(p) > 40]
        if not paras:
            # A few event pages carry no prose in <p> at all; fall back to the
            # page's own meta description rather than inventing copy.
            meta = detail.find("meta", attrs={"name": "description"}) or \
                detail.find("meta", attrs={"property": "og:description"})
            if meta and meta.get("content"):
                paras = [html_mod.unescape(meta["content"]).strip()]
        ev["summary"] = paras[0] if paras else ""
        ev["body"] = paras[:6]
        ev["sport"] = sport_of(ev["source_url"])
        print(f"  event {ev['slug']}: {ev['date_display']}, {ev['city']} "
              f"({len(ev['body'])} paras)")
        time.sleep(REQUEST_DELAY_S)
    return events


def sport_of(url: str) -> str:
    """Map /sports/<discipline>/... to a display name."""
    m = re.search(r"/sports/([a-z0-9-]+)/", url)
    if not m:
        return "Championship"
    return m.group(1).replace("-", " ").title()


# --------------------------------------------------------------------------- articles
def sitemap_articles() -> dict[str, list[tuple[str, str]]]:
    """Expert Advice URLs from the article sitemap, grouped by category.

    Each list is ordered newest-modified first, then by URL, so a re-run picks
    the same articles unless akc.org itself changed.
    """
    xml = fetch(ARTICLE_SITEMAP)
    entries = re.findall(
        r"<url>\s*<loc>(.*?)</loc>(?:\s*<lastmod>(.*?)</lastmod>)?", xml, re.S
    )
    grouped: dict[str, list[tuple[str, str]]] = {}
    for url, lastmod in entries:
        url = url.strip()
        if not url.endswith("/"):
            url += "/"
        m = ARTICLE_HREF_RE.match(url)
        if not m:
            continue
        grouped.setdefault(m.group("cat"), []).append((url, (lastmod or "").strip()))
    for cat in grouped:
        grouped[cat].sort(key=lambda e: (e[1], e[0]), reverse=True)
    return grouped


def harvest_article(url: str, category: str) -> dict | None:
    m = ARTICLE_HREF_RE.match(url)
    if m:
        category = m.group("cat")
    page = soup(fetch(url))
    meta: dict = {}
    for tag in page.find_all("script", type="application/ld+json"):
        try:
            doc = json.loads(tag.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in (doc.get("@graph") or [doc]):
            if node.get("@type") in ("Article", "NewsArticle", "BlogPosting"):
                meta = node
                break
        if meta:
            break

    title = html_mod.unescape(
        meta.get("headline") or (page.h1.get_text(strip=True) if page.h1 else "")
    )
    if not title:
        return None

    body = page.find("div", class_=re.compile(r"content-body__text"))
    paras = [clean(p) for p in body.find_all("p")] if body else []
    paras = [p for p in paras if len(p) > 60]
    if not paras:
        return None

    author = meta.get("author") or {}
    if isinstance(author, list):
        author = author[0] if author else {}
    words = sum(len(p.split()) for p in paras)

    return {
        "slug": slugify(url),
        "title": title,
        "category": category.replace("-", " ").title(),
        "category_slug": category,
        "author": (author or {}).get("name", "AKC Staff"),
        "published": (meta.get("datePublished") or "")[:10],
        "modified": (meta.get("dateModified") or "")[:10],
        "summary": html_mod.unescape(meta.get("description") or paras[0])[:400],
        "body": paras[:12],
        "word_count": words,
        "read_minutes": max(1, round(words / 200)),
        "source_url": url,
    }


# --------------------------------------------------------------------------- main
def main() -> int:
    print("homepage ...")
    home = soup(fetch(HOME_URL))
    sports = harvest_sports(home)
    print(f"  {len(sports)} sports")

    print("sports page ...")
    events = harvest_events(soup(fetch(SPORTS_URL)))
    print(f"  {len(events)} events")

    print("expert advice ...")
    grouped = sitemap_articles()
    print("  sitemap categories: " + ", ".join(
        f"{c}={len(v)}" for c, v in sorted(grouped.items())))
    articles = []
    for category, limit in ARTICLE_CATEGORIES:
        links = [u for u, _ in grouped.get(category, [])[:limit]]
        if not links:
            print(f"  !! {category}: no articles in the sitemap")
        for url in links:
            try:
                art = harvest_article(url, category)
            except Exception as exc:                      # noqa: BLE001
                print(f"  !! {url}: {exc}")
                continue
            if art:
                articles.append(art)
                print(f"  {art['category']}: {art['title'][:52]} "
                      f"({art['author']}, {art['read_minutes']} min)")
            time.sleep(REQUEST_DELAY_S)

    out = {
        "schema_version": 1,
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "capture_scope": (
            "akc.org homepage sports tiles, the Featured Events akc.org/sports/ "
            "promotes plus each event's own detail page, and Expert Advice "
            "articles sampled per category. No event-search or member-only data."
        ),
        "source_site": "https://www.akc.org/",
        "sources": _sources,
        "sports": sports,
        "events": events,
        "articles": articles,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {OUT_PATH}: {len(sports)} sports, {len(events)} events, "
          f"{len(articles)} articles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
