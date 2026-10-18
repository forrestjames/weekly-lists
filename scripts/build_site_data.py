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
  "link":   "https://...bandcamp.com/..."   # listen link when not on Spotify
  "cover":  "covers/name.jpg" or an image URL   # when no cover can be found automatically
"""

import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
PICKS = ROOT / "picks"
OUT = ROOT / "site" / "data"

EDITIONS = {
    "rob": {"name": "Rob's List", "playlist": "https://open.spotify.com/playlist/6YpWmU0EK7fFa62dpR8Ip7"},
    "juno": {"name": "Juno Weekly", "playlist": ""},
}

FIELDS = ["artist", "title", "kind", "tags", "note", "source", "spotify_url", "link", "cover"]


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


def main():
    latest, archive = {}, {}
    for key, meta in EDITIONS.items():
        weeks = load_weeks(key)
        archive[key] = {**meta, "weeks": weeks}
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
