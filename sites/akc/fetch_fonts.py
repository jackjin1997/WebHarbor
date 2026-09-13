"""Fetch the open-licence webfonts akc.org renders with.

akc.org sets Lato (body/UI), Merriweather (section headings) and Paytone One
(hero headline). All three are SIL Open Font License families published on
Google Fonts, so the mirror bundles them locally rather than hot-linking a CDN
— the benchmark image must not reach off-site.

Writes static/fonts/ and font_sources.json (source URL + sha256 per file).

Run with: uv run --with certifi python sites/akc/fetch_fonts.py
"""
from __future__ import annotations

import hashlib
import json
import re
import ssl
import sys
from pathlib import Path
from urllib.request import Request, urlopen

SITE_DIR = Path(__file__).resolve().parent
OUT_DIR = SITE_DIR / "static" / "fonts"
MANIFEST = SITE_DIR / "font_sources.json"

# A modern browser UA so the API returns woff2 rather than a legacy format.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
CSS_URL = "https://fonts.googleapis.com/css2?family={spec}&display=swap"

FAMILIES = [
    ("Lato", "Lato:wght@400;700", "SIL Open Font License 1.1"),
    ("Merriweather", "Merriweather:wght@400", "SIL Open Font License 1.1"),
    ("Paytone One", "Paytone+One", "SIL Open Font License 1.1"),
]


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_CTX = ssl_context()


def get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=45, context=_CTX) as resp:
        return resp.read()


def latin_faces(css: str) -> list[tuple[str, str]]:
    """(weight, woff2 url) for the latin subset blocks of a Google Fonts CSS."""
    faces = []
    for block in css.split("@font-face")[1:]:
        if "/* latin */" not in css.split(block)[0][-40:] and "unicode-range" in block:
            # Keep only the plain latin subset: it is the last block emitted for
            # a weight and covers everything this mirror renders.
            if "U+0000-00FF" not in block:
                continue
        weight = (re.search(r"font-weight:\s*(\d+)", block) or [None, "400"])[1]
        url = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", block)
        if url:
            faces.append((weight, url.group(1)))
    return faces


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for family, spec, licence in FAMILIES:
        css_url = CSS_URL.format(spec=spec)
        css = get(css_url).decode("utf-8")
        faces = latin_faces(css)
        if not faces:
            print(f"  !! {family}: no latin woff2 in the stylesheet")
            continue
        for weight, url in faces:
            slug = family.lower().replace(" ", "-")
            name = f"{slug}-{weight}.woff2"
            data = get(url)
            (OUT_DIR / name).write_bytes(data)
            entries.append({
                "file": f"fonts/{name}",
                "family": family,
                "weight": int(weight),
                "licence": licence,
                "source_page": css_url,
                "source_url": url,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            })
            print(f"  {name}: {len(data) // 1024} KiB")

    MANIFEST.write_text(
        json.dumps({"fonts": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(e["bytes"] for e in entries)
    print(f"\n{len(entries)} files, {total // 1024} KiB -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
