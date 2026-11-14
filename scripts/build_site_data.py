#!/usr/bin/env python3
"""
Builds the JSON the website reads, from the weekly picks files.

Usage:  python scripts/build_site_data.py

Reads   picks/rob/*.json, picks/juno/*.json and design/*.json
Writes  site/data/latest.json    this week's picks for every edition
        site/data/archive.json   every past week, newest first

The page only needs to fetch these two files. One-off lists (like
picks/dub) are left out.

Design Shelf picks live in design/ rather than picks/, so the Spotify
workflow never sees them. Each design pick looks like this (all but
"title" optional):
  {"title": "...", "by": "author, studio or editor", "kind": "book",
   "tags": ["posters", "type"], "note": "...", "instagram": ["@handle"],
   "shop": "Magma", "price": "£25", "link": "https://...", "art": "https://..."}
kind is "book", "magazine" or "designer". For any design pick without
"art", this script reads the shop page and saves its cover image (and, for
Shopify shops, up to four more pictures) into archive.json. Earlier
results are reused, so only new picks cost a request.

Each pick can carry extra fields for the site. All optional:
  "tags":   ["ambient", "jazz"]
  "note":   "One or two honest sentences."
  "source": "The Quietus"
  "source_url": "https://thequietus.com/..."  # exact article; the page links it as "Source: ..."
  "link":   "https://...bandcamp.com/..."   # listen link when not on Spotify
  "art":    "https://...jpg"                # cover image, if you want to set it by hand

Cover art: for any pick without an "art" field, this script looks the album
up on Spotify (the same search add_tracks.py uses) and writes the cover URL,
plus the album link if the pick had none, into archive.json. Results from
earlier runs are reused, so only new picks cost an API call. This needs
SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in the environment. Without them,
or if Spotify is down, the script skips this step and the site falls back to
looking covers up in the browser.
"""

import base64
import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import add_tracks as sp  # noqa: E402  (reuses its Spotify search and matching)

PICKS = ROOT / "picks"
DESIGN = ROOT / "design"
OUT = ROOT / "site" / "data"

EDITIONS = {
    "rob": {"name": "Rob's List", "playlist": "https://open.spotify.com/playlist/6YpWmU0EK7fFa62dpR8Ip7"},
    "juno": {"name": "Juno Weekly", "playlist": ""},
    "design": {"name": "Design Shelf", "playlist": ""},
}
FOLDERS = {"rob": PICKS / "rob", "juno": PICKS / "juno", "design": DESIGN}
MUSIC = ["rob", "juno"]

FIELDS = ["artist", "title", "kind", "tags", "note", "source", "source_url", "spotify_url", "link", "art",
          "by", "instagram", "shop", "price", "images"]
MAX_IMAGES = 5
MAX_SPOTIFY_FAILURES = 3  # give up on Spotify for this run after this many errors in a row


def clean(item):
    out = {k: item[k] for k in FIELDS if item.get(k)}
    if isinstance(out.get("tags"), str):
        out["tags"] = [t.strip() for t in out["tags"].split(",") if t.strip()]
    return out


def load_weeks(edition):
    weeks = []
    for path in sorted(FOLDERS[edition].glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"Skipping {path}: {e}", file=sys.stderr)
            continue
        date = data.get("date") or path.stem
        items = [clean(i) for i in data.get("items", []) if i.get("artist") or i.get("title")]
        if items:
            weeks.append({"date": date, "items": items})
    weeks.sort(key=lambda w: w["date"], reverse=True)
    return weeks


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def spotify_token():
    """Public-catalogue token (client credentials), or None if no keys are set."""
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = sp.http(
        "POST",
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        headers={"Authorization": f"Basic {basic}"},
        form=True,
        tries=2,
    )
    return data["access_token"]


def resolve_on_spotify(token, item):
    """Returns (spotify_url, cover_url) for a pick, or None if Spotify has no match."""
    kind = "album" if (item.get("kind") or "album").lower() == "album" else "track"
    sid = sp.id_from_url(item.get("spotify_url"), kind)
    if sid:
        obj = sp.http("GET", f"{sp.API}/{kind}s/{sid}", token, tries=2)
    else:
        obj = sp.search(token, item.get("artist", ""), item.get("title", ""), kind)
    if not obj:
        return None
    images = obj.get("images") or (obj.get("album") or {}).get("images") or []
    cover = images[0].get("url", "") if images else ""
    sid = obj.get("id") or sid
    url = (obj.get("external_urls") or {}).get("spotify") or (f"https://open.spotify.com/{kind}/{sid}" if sid else "")
    return url, cover


def add_art(archive, previous):
    """Fills in "art" (and a missing "spotify_url") on every pick that lacks them."""
    known = {}
    for ed in (previous or {}).get("editions", {}).values():
        for week in ed.get("weeks", []):
            for it in week.get("items", []):
                if it.get("art"):
                    known[(it.get("artist"), it.get("title"))] = it

    todo = []
    for key in MUSIC:
        for week in archive[key]["weeks"]:
            for it in week["items"]:
                if it.get("art"):
                    continue
                old = known.get((it.get("artist"), it.get("title")))
                if old:
                    it["art"] = old["art"]
                    if not it.get("spotify_url") and old.get("spotify_url"):
                        it["spotify_url"] = old["spotify_url"]
                else:
                    todo.append(it)
    if not todo:
        return

    try:
        token = spotify_token()
    except sp.SpotifyError as e:
        print(f"Skipping cover art lookup, Spotify token failed: {e}", file=sys.stderr)
        return
    if not token:
        print(f"No Spotify keys set; {len(todo)} pick(s) left without cover art.")
        return

    found, failures, cache = 0, 0, {}
    for it in todo:
        key = (it.get("artist"), it.get("title"))
        if key not in cache:
            if failures >= MAX_SPOTIFY_FAILURES:
                break
            try:
                cache[key] = resolve_on_spotify(token, it)
                failures = 0
            except sp.SpotifyError as e:
                failures += 1
                print(f"Spotify lookup failed for {key[0]} - {key[1]}: {e}", file=sys.stderr)
                continue
        hit = cache[key]
        if not hit:
            print(f"Not found on Spotify: {key[0]} - {key[1]}")
            continue
        url, cover = hit
        if url and not it.get("spotify_url"):
            it["spotify_url"] = url
        if cover:
            it["art"] = cover
            found += 1
    if failures >= MAX_SPOTIFY_FAILURES:
        print("Spotify kept failing; stopped early. The next run will try the rest.", file=sys.stderr)
    print(f"Cover art: {found} pick(s) filled in, {len(todo)} needed it.")


UA = "Mozilla/5.0 (weekly-lists site builder; +https://github.com/forrestjames/weekly-lists)"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", "replace")


def absolute(u, base):
    u = html.unescape((u or "").strip())
    if u.startswith("//"):
        u = "https:" + u
    return urllib.parse.urljoin(base, u) if u else ""


def shop_pictures(link):
    """Cover and extra pictures for a design pick, read from its shop page.

    Shopify shops (Magma, magCulture, Draw Down, Counter-Print, Perimeter)
    publish every product photo at the product URL plus ".json". Anything
    else falls back to the page's og:image. Returns a list of URLs, cover
    first, or [] if nothing was found."""
    parts = urllib.parse.urlsplit(link)
    if "/products/" in parts.path:
        try:
            data = json.loads(fetch(urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, parts.path.rstrip("/") + ".json", "", ""))))
            imgs = [absolute(i.get("src"), link) for i in (data.get("product") or {}).get("images", [])]
            imgs = [i for i in imgs if i]
            if imgs:
                return imgs[:MAX_IMAGES]
        except Exception as e:  # noqa: BLE001  (not Shopify, or the shop is down: try the page)
            print(f"  no product JSON for {link}: {e}", file=sys.stderr)
    page = fetch(link)
    for pat in (r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)'):
        m = re.search(pat, page, re.I)
        if m:
            return [absolute(m.group(1), link)]
    return []


def add_design_art(archive, previous):
    """Fills in "art" (and "images") on design picks that lack them."""
    known = {}
    for week in ((previous or {}).get("editions", {}).get("design") or {}).get("weeks", []):
        for it in week.get("items", []):
            if it.get("art") and it.get("link"):
                known[it["link"]] = it
    found = tried = 0
    for week in archive["design"]["weeks"]:
        for it in week["items"]:
            if it.get("art") or not it.get("link"):
                continue
            old = known.get(it["link"])
            if old:
                it["art"] = old["art"]
                if old.get("images") and not it.get("images"):
                    it["images"] = old["images"]
                continue
            tried += 1
            try:
                pics = shop_pictures(it["link"])
            except Exception as e:  # noqa: BLE001
                print(f"Couldn't read {it['link']}: {e}", file=sys.stderr)
                continue
            if pics:
                it["art"] = pics[0]
                if len(pics) > 1 and not it.get("images"):
                    it["images"] = pics
                found += 1
            else:
                print(f"No picture found for {it.get('title')}")
    if tried:
        print(f"Design pictures: {found} of {tried} new pick(s) filled in.")


def main():
    latest, archive = {}, {}
    for key, meta in EDITIONS.items():
        weeks = load_weeks(key)
        archive[key] = {**meta, "weeks": weeks}

    archive_path = OUT / "archive.json"
    try:
        previous_archive = json.loads(archive_path.read_text()) if archive_path.exists() else {}
    except json.JSONDecodeError:
        previous_archive = {}
    add_art(archive, previous_archive)
    add_design_art(archive, previous_archive)

    for key, meta in EDITIONS.items():
        weeks = archive[key]["weeks"]
        latest[key] = {**meta, **(weeks[0] if weeks else {"date": None, "items": []})}

    # Only bump "updated" when the picks themselves change, so the
    # Action doesn't commit a new timestamp on every run.
    latest_path = OUT / "latest.json"
    previous = json.loads(latest_path.read_text()) if latest_path.exists() else {}
    updated = previous.get("updated")
    if previous.get("editions") != latest or not updated:
        updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    write(latest_path, {"updated": updated, "editions": latest})
    write(OUT / "archive.json", {"updated": updated, "editions": archive})
    for key in EDITIONS:
        print(f"{key}: {len(archive[key]['weeks'])} week(s), latest {latest[key]['date']}")


if __name__ == "__main__":
    main()
