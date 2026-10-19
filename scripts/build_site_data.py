#!/usr/bin/env python3
"""
Builds the JSON the website reads, from the weekly picks files.

Usage:  python scripts/build_site_data.py

Reads   picks/rob/*.json and picks/juno/*.json
Writes  site/data/latest.json    this week's picks for both editions
        site/data/archive.json   every past week, newest first

The page only needs to fetch these two files. One-off lists (like
picks/dub) are left out.

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
import json
import os
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import add_tracks as sp  # noqa: E402  (reuses its Spotify search and matching)

PICKS = ROOT / "picks"
OUT = ROOT / "site" / "data"

EDITIONS = {
    "rob": {"name": "Rob's List", "playlist": "https://open.spotify.com/playlist/6YpWmU0EK7fFa62dpR8Ip7"},
    "juno": {"name": "Juno Weekly", "playlist": ""},
}

FIELDS = ["artist", "title", "kind", "tags", "note", "source", "source_url", "spotify_url", "link", "art"]
MAX_SPOTIFY_FAILURES = 3  # give up on Spotify for this run after this many errors in a row


def clean(item):
    out = {k: item[k] for k in FIELDS if item.get(k)}
    if isinstance(out.get("tags"), str):
        out["tags"] = [t.strip() for t in out["tags"].split(",") if t.strip()]
    return out


def load_weeks(edition):
    weeks = []
    for path in sorted((PICKS / edition).glob("*.json")):
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
    for ed in archive.values():
        for week in ed["weeks"]:
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
