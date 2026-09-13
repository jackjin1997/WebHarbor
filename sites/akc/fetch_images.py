"""Fetch and normalize the AKC mirror's breed photography.

Sources come from ``data/breeds.json`` — each record's ``gallery`` carries the
photo URLs and the photographer credit akc.org publishes alongside them. This
script downloads those originals, converts them to webp, and records the
provenance (source URL, content type, dimensions, sha256 of both the original
bytes and our output, and the credit line) in ``image_sources.json`` so the
bundle can be re-verified without re-downloading.

Outputs land in ``static/images/`` — gitignored here, shipped via the Hugging
Face asset dataset (see CONTRIBUTING.md "Workflow A").

Run with: uv run --with pillow --with certifi python sites/akc/fetch_images.py
"""
from __future__ import annotations

import hashlib
import io
import json
import ssl
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

SITE_DIR = Path(__file__).resolve().parent
BREEDS = SITE_DIR / "data" / "breeds.json"
OUT_DIR = SITE_DIR / "static" / "images" / "breeds"
MANIFEST = SITE_DIR / "image_sources.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
REQUEST_DELAY_S = 0.4

# One hero (breed detail banner), one card crop (lists, compare, selector), and
# up to three more gallery shots per breed.
HERO_WIDTH = 1200
CARD_SIZE = (600, 400)
GALLERY_WIDTH = 800
EXTRA_GALLERY = 3
WEBP_QUALITY = 82


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def download(url: str, ctx: ssl.SSLContext) -> tuple[bytes, str]:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=60, context=ctx) as resp:
        return resp.read(), resp.headers.get("Content-Type", "")


def save_webp(img: Image.Image, path: Path) -> tuple[str, int, tuple[int, int]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=6)
    data = buf.getvalue()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest(), len(data), img.size


def to_rgb(raw: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    return img


def scaled(img: Image.Image, width: int) -> Image.Image:
    if img.width <= width:
        return img.copy()
    height = round(img.height * width / img.width)
    return img.resize((width, height), Image.LANCZOS)


def main(argv: list[str]) -> int:
    if not BREEDS.exists():
        raise SystemExit("data/breeds.json missing — run harvest_breeds.py first")
    doc = json.loads(BREEDS.read_text(encoding="utf-8"))
    wanted = set(argv[1:])
    ctx = ssl_context()

    entries: list[dict] = []
    if MANIFEST.exists():
        entries = json.loads(MANIFEST.read_text(encoding="utf-8"))["images"]
    by_file = {e["file"]: e for e in entries}

    for record in doc["records"]:
        slug = record["slug"]
        if wanted and slug not in wanted:
            continue
        gallery = record.get("gallery") or []
        if not gallery:
            print(f"  !! {slug}: no gallery photos on the source page")
            continue

        picks = [("hero", gallery[0], HERO_WIDTH)]
        picks.append(("card", gallery[0], None))
        for i, item in enumerate(gallery[1:1 + EXTRA_GALLERY], start=2):
            picks.append((f"{i}", item, GALLERY_WIDTH))

        for kind, item, width in picks:
            name = f"{slug}.webp" if kind == "hero" else f"{slug}-{kind}.webp"
            rel = f"breeds/{name}"
            try:
                raw, ctype = download(item["src"], ctx)
            except Exception as exc:                      # noqa: BLE001
                print(f"  !! {rel}: {exc}")
                continue
            img = to_rgb(raw)
            source_dims = img.size
            if kind == "card":
                out = ImageOps.fit(img, CARD_SIZE, Image.LANCZOS, centering=(0.5, 0.4))
            else:
                out = scaled(img, width)
            out_sha, out_bytes, out_dims = save_webp(out, OUT_DIR / name)
            by_file[rel] = {
                "file": rel,
                "breed": slug,
                "role": kind,
                "alt": item.get("alt") or f"{record['name']} photograph.",
                "credit": item.get("credit") or "",
                "source_page": f"https://www.akc.org/dog-breeds/{slug}/",
                "source_url": item["src"],
                "source_content_type": ctype.split(";")[0],
                "source_dimensions": list(source_dims),
                "output_dimensions": list(out_dims),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "output_sha256": out_sha,
                "output_bytes": out_bytes,
            }
            print(f"  {rel}: {source_dims[0]}x{source_dims[1]} -> "
                  f"{out_dims[0]}x{out_dims[1]}, {out_bytes // 1024} KiB")
            time.sleep(REQUEST_DELAY_S)
        print(f"[{slug}] done")

    ordered = sorted(by_file.values(), key=lambda e: e["file"])
    MANIFEST.write_text(
        json.dumps({"images": ordered}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(e["output_bytes"] for e in ordered)
    print(f"\n{len(ordered)} images, {total / 1_048_576:.1f} MiB -> {OUT_DIR}")
    print(f"manifest: {MANIFEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
