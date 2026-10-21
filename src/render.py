"""Render layer. Prepares assets, writes data.json, compiles with Typst.

Typst rather than headless Chrome because internal anchors and the PDF outline
come out reliably — and the outline is what drives the Scribe's Contents panel,
which is the navigation you can actually depend on.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import qrcode
import requests
from PIL import Image

from models import Article

BUILD = Path("build")
PAGE_W_PX = 1560            # 6.1in at 256dpi — plenty for the Scribe's 300ppi panel


def _prepare_image(a: Article, user_agent: str) -> str | None:
    """Greyscale + Floyd-Steinberg dither. Looks far better on e-ink than a
    plain greyscale downscale, and shrinks the file by an order of magnitude."""
    if not a.image_url:
        return None
    try:
        resp = requests.get(a.image_url, headers={"User-Agent": user_agent}, timeout=20)
        resp.raise_for_status()
        raw = BUILD / "img" / f"{a.id}_raw"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(resp.content)

        img = Image.open(raw).convert("L")
        w = min(PAGE_W_PX, img.width)
        img = img.resize((w, int(img.height * w / img.width)), Image.LANCZOS)
        img = img.convert("1", dither=Image.FLOYDSTEINBERG)

        out = BUILD / "img" / f"{a.id}.png"
        img.save(out, optimize=True)
        raw.unlink(missing_ok=True)
        return out.relative_to(BUILD).as_posix()   # Typst wants forward slashes even on Windows
    except Exception as exc:
        print(f"  image failed for {a.id}: {exc}")
        return None


def _qr(a: Article) -> str:
    """The Scribe's browser is unusable. A QR code is how you get to the web."""
    out = BUILD / "qr" / f"{a.id}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(box_size=6, border=1,
                       error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(a.url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(out)
    return out.relative_to(BUILD).as_posix()   # Typst wants forward slashes even on Windows


def build_payload(plan: dict, articles: list[Article], user_agent: str,
                  edition_date: datetime) -> dict:
    for a in articles:
        a.local_image = _prepare_image(a, user_agent) if a.full_text else None
        a.qr_path = _qr(a)

    def story_ref(a: Article) -> dict:
        """The compact form used inside a group's story list and the
        headline list — enough to print a headline, link it, and show a
        page reference if it's also a full-text article."""
        return {"id": a.id, "title": a.title, "source_name": a.source_name,
                "url": a.url, "full_text": a.full_text}

    groups = []
    for g in plan.get("_groups", []):
        stories = g["stories"]
        # Unique source names in first-seen order, each linked to its first story.
        first = {}
        for a in stories:
            first.setdefault(a.source_name, a.url)
        sources = [{"name": n, "url": u} for n, u in first.items()]
        groups.append({
            "title": g["title"],
            "paragraphs": [p for p in g["summary"].split("\n\n") if p.strip()],
            "sources": sources,
            "stories": [story_ref(a) for a in stories],
        })

    headlines = []
    for h in plan.get("_headlines", []):
        headlines.append({
            "source_name": h["source_name"],
            "items": [{"id": a.id, "title": a.title, "digest": a.digest,
                       "full_text": a.full_text} for a in h["items"]],
        })

    radar = [{"id": a.id, "title": a.title, "source_name": a.source_name, "line": line}
             for a, line in plan.get("_radar", [])]
    chatter = [{"id": a.id, "title": a.title, "source_name": a.source_name, "line": line}
               for a, line in plan.get("_chatter", [])]

    full_text = []
    for a in plan.get("_full_text", []):
        full_text.append({
            "id": a.id, "title": a.title, "source_name": a.source_name,
            "word_count": a.word_count, "local_image": a.local_image,
            "qr_path": a.qr_path, "paragraphs": a.body.split("\n\n"),
        })

    edition = plan.get("edition", "daily")
    # One name for every edition (owner, 2026-09-30); Saturdays are marked by
    # the "The week of ..." line under the masthead, not a different title.
    name = "The Daily Cronicle"

    def day_short(d) -> str:
        return f"{d:%a} {d.day}"

    threads = []
    for t in plan.get("_threads", []):
        days = len(t["coverage"])
        threads.append({
            "title": t["title"],
            "status": t["status"],
            "days": days,
            "paragraphs": [p for p in t["summary"].split("\n\n") if p.strip()],
            "timeline": [{"day": day_short(e["date"]), "line": e["line"]}
                         for e in t["timeline"]],
            "coverage": [{"day": day_short(c["date"]), "sources": c["sources"]}
                         for c in t["coverage"]],
            "reads": [{"id": a.id, "title": a.title} for a in t["today"] if a.full_text],
        })

    world = None
    if plan.get("_world"):
        w = plan["_world"]
        world = {
            "paragraphs": [p for p in w["summary"].split("\n\n") if p.strip()],
            "coverage": [{"day": day_short(c["date"]), "sources": c["sources"]}
                         for c in w["coverage"]],
            "reads": [{"id": a.id, "title": a.title} for a in w["today"] if a.full_text],
        }

    also = [{"day": day_short(x["date"]), "title": x["title"], "line": x["line"],
             "url": x["url"]} for x in plan.get("_also", [])]

    # Automatic weekend: the week as it ran, straight from the archive.
    week = []
    for day in plan.get("_week", []):
        d = datetime.fromisoformat(day["date"])
        items = ([g["title"] for g in day.get("groups", [])]
                 or [f"{h['title']} ({h['source_name']})"
                     for h in day.get("headlines", [])[:12]])
        week.append({"day_long": f"{d:%A} {d.day} {d:%B}",
                     "brief": day.get("brief", ""), "items": items})

    week_start = edition_date - timedelta(days=6)
    return {
        "edition": edition,
        "masthead": name,
        "short_name": name,
        "week_range": f"{day_short(week_start)} {week_start:%b} – "
                      f"{day_short(edition_date)} {edition_date:%b}",
        "threads": threads,
        "world": world,
        "also": also,
        "week": week,
        "title": f"{name} — {edition_date:%a} {edition_date.day} {edition_date:%B %Y}",
        "date_long": f"{edition_date:%A} {edition_date.day} {edition_date:%B %Y}",
        "mode": plan["mode"],
        "brief": plan["brief"],
        "groups": groups,
        "headlines": headlines,
        "radar": radar,
        "chatter": chatter,
        "full_text": full_text,
        "colophon": (
            "Assembled automatically for personal reading. "
            "Full text is reproduced from freely accessible sources; "
            "The Conversation items are CC BY-ND."
        ),
    }


def compile_pdf(payload: dict, out_path: Path) -> Path:
    BUILD.mkdir(exist_ok=True)
    (BUILD / "data.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    shutil.copy("templates/paper.typ", BUILD / "paper.typ")
    shutil.copy("templates/masthead.png", BUILD / "masthead.png")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["typst", "compile", "--root", str(BUILD), str(BUILD / "paper.typ"), str(out_path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"typst failed:\n{result.stderr}")
    size_mb = out_path.stat().st_size / 1e6
    print(f"  rendered {out_path} ({size_mb:.1f} MB)")
    if size_mb > 45:
        raise RuntimeError("PDF over 45 MB — Send to Kindle email caps at 50 MB")
    return out_path
