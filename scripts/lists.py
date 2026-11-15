#!/usr/bin/env python3
"""
Reads lists.yml, the one file that describes every weekly list.

Used by the "Weekly lists" workflow and by the other scripts:

  python scripts/lists.py due [ids]        JSON list of ids to run today (or the ids given)
  python scripts/lists.py prompt ID DATE   writes .weekly/prompt-ID.md, the brief for Claude
  python scripts/lists.py check ID FILE    checks a picks file Claude wrote
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "lists.yml"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def load():
    data = yaml.safe_load(CONFIG.read_text()) or {}
    seen = set()
    for item in data.get("lists", []):
        lid = str(item.get("id", "")).strip().lower()
        if not lid or lid in seen:
            raise SystemExit(f"lists.yml: every list needs a unique id (problem with {item.get('name')!r})")
        seen.add(lid)
        item["id"] = lid
        item.setdefault("name", lid.title())
        item.setdefault("type", "music")
        item.setdefault("day", "monday")
        item.setdefault("count", 10)
        item.setdefault("sources", [])
        item.setdefault("kinds", ["book", "magazine", "designer"])
        item["day"] = str(item["day"]).lower()
    return data


def all_lists():
    return load().get("lists", [])


def get(lid):
    for item in all_lists():
        if item["id"] == lid:
            return item
    raise SystemExit(f"No list called {lid!r} in lists.yml")


def folder(item):
    """Where a list's weekly files live, relative to the repo root."""
    return Path("picks") / item["id"] if item["type"] == "music" else Path(item["id"])


def london_today():
    return datetime.now(ZoneInfo("Europe/London")).date()


def due(ids=None, today=None):
    today = today or london_today()
    lists = [l for l in all_lists() if not l.get("paused")]
    if ids:
        wanted = [i.strip().lower() for i in ids if i.strip()]
        return [l["id"] for l in lists if l["id"] in wanted]
    return [l["id"] for l in lists if l["day"] == DAYS[today.weekday()]]


def recent_picks(item, weeks=8):
    """Titles already featured, so Claude doesn't repeat them."""
    files = sorted((ROOT / folder(item)).glob("*.json"))[-weeks:]
    out = []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception:
            continue
        for it in data.get("items", []):
            who = it.get("artist") or it.get("by") or ""
            out.append(f"- {who} — {it.get('title', '')}".strip())
    return out


VOICE = ("Plain and warm, short sentences, no em dashes, no corporate framing. "
         "Honest: only describe what you actually read in a source. "
         "Don't use copywriter words like \"lands\".")

MUSIC_SHAPE = """{
  "list": "%(id)s",
  "date": "%(date)s",
  "email_intro": "One or two plain lines for the top of the email: anything worth knowing about this week, such as a source you couldn't read. Empty if nothing to say.",
  "items": [
    {"artist": "...", "title": "...", "kind": "album",
     "spotify_url": "https://open.spotify.com/album/...",
     "tags": ["ambient", "jazz"], "note": "One or two honest sentences.",
     "source": "Where you found it", "source_url": "https://the exact article or review"},
    {"artist": "...", "title": "...", "kind": "track",
     "link": "https://... (Bandcamp or label, only when Spotify doesn't have it)",
     "tags": ["..."], "note": "...", "source": "...", "source_url": "..."}
  ]
}"""

DESIGN_SHAPE = """{
  "list": "%(id)s",
  "date": "%(date)s",
  "email_intro": "One or two plain lines for the top of the email, or empty.",
  "items": [
    {"title": "...", "by": "author, studio, brand or publisher", "kind": "%(kind)s",
     "tags": ["posters", "typography"], "note": "Two or three honest sentences.",
     "instagram": ["@handle"], "shop": "Magma", "price": "£25",
     "link": "https://the real product or profile page",
     "art": "https://a real image url of the cover or work",
     "pictures_from": ["https://another page that shows the work"]}
  ]
}"""


def prompt(lid, day):
    item = get(lid)
    out_path = f"out/{lid}.json"
    sources = "\n".join(f"- {s}" for s in item["sources"]) or "- Use your judgement."
    already = "\n".join(recent_picks(item)) or "- (nothing yet)"
    notes = (item.get("notes") or "").strip()
    is_design = item["type"] == "design"
    extra = f"\nAlso: {notes}\n" if notes else ""

    if is_design:
        kinds = ", ".join(f'"{k}"' for k in item["kinds"])
        task = f"""Curate this week's {item['name']}: {item['count']} picks.

What I like: {item.get('taste', '').strip()}

Check the newest stock at these first, then widen if you need to:
{sources}

For every pick:
- "kind" is one of: {kinds}.
- Instagram handles: include one for each designer, brand, studio, publisher, shop or magazine where it exists, but only after a web search returns the actual instagram.com profile. Never guess. Leave the list empty if you can't confirm one.
- "link" is the real product or profile page you opened.
- "art" must be a real image URL you found: the og:image, or the first image in a Shopify shop's product JSON (the product URL plus ".json"). Never guess one.
- "pictures_from": one or two more pages showing the work. Always fill it for designers and brands, and for any pick without "art".
- "tags": two to four short lowercase tags. "note": two or three sentences.
- The website is public, so nothing personal about me."""
        shape = DESIGN_SHAPE % {"id": lid, "date": day, "kind": item["kinds"][0]}
    else:
        task = f"""Curate this week's {item['name']}: the {item['count']} best new releases from the past seven days.

What I like: {item.get('taste', '').strip()}

Sources:
{sources}

Rules:
- Only releases out in the past seven days. Check each release date against a second source (label, Bandcamp, Spotify) and leave out anything not out yet.
- "kind" is "album" for albums and EPs, "track" for a single song. Put singles in playlist order among the albums.
- Find each pick on Spotify with the helper: python3 scripts/spotify_lookup.py "ARTIST" "TITLE" album  (or track). Use the URL it prints as "spotify_url". If it prints NOT FOUND, try a different spelling once, then leave spotify_url out and give a Bandcamp or label "link" instead. Never guess a Spotify link.
- "tags": two to four short lowercase genre tags. "note": one or two honest sentences on what it is and why it's worth my time.
- "source": the publication or show it came from. "source_url": the exact page, if you have one.
- If a source can't be read, use another one from the list, and say which ones failed in "email_intro"."""
        shape = MUSIC_SHAPE % {"id": lid, "date": day}

    text = f"""# Weekly list: {item['name']}

Today is {day}. You are running unattended in a scheduled job, so don't ask questions. Make sensible choices and get it done.

{task}
{extra}

Don't repeat anything from recent weeks:
{already}

Voice for notes and the intro: {VOICE}

## Output

Write exactly one file, `{out_path}`, as valid JSON in this shape:

```json
{shape}
```

Leave out any field you don't have, except "list", "date" and "items". Don't edit or commit any other file; the next step does that.
When the file is written, check it with: python3 scripts/lists.py check {lid} {out_path}
If the check complains, fix the file and check again.
"""
    dest = ROOT / ".weekly" / f"prompt-{lid}.md"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(text)
    print(dest.relative_to(ROOT))


def check(lid, path):
    item = get(lid)
    problems = []
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise SystemExit(f"{path} doesn't exist yet")
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} isn't valid JSON: {e}")
    items = data.get("items")
    if data.get("list") != lid:
        problems.append(f'"list" should be "{lid}"')
    try:
        date.fromisoformat(str(data.get("date")))
    except ValueError:
        problems.append('"date" should look like 2026-11-16')
    if not isinstance(items, list) or not items:
        problems.append('"items" should be a non-empty list')
        items = []
    for n, it in enumerate(items, 1):
        if not it.get("title"):
            problems.append(f"item {n} has no title")
        if item["type"] == "music":
            if not it.get("artist"):
                problems.append(f"item {n} has no artist")
            if it.get("kind") not in ("album", "track"):
                problems.append(f'item {n}: kind should be "album" or "track"')
            url = it.get("spotify_url", "")
            if url and not url.startswith("https://open.spotify.com/"):
                problems.append(f"item {n}: spotify_url should be an open.spotify.com link")
        else:
            if it.get("kind") not in item["kinds"]:
                problems.append(f'item {n}: kind should be one of {", ".join(item["kinds"])}')
        if "—" in (it.get("note") or ""):
            problems.append(f"item {n}: note has an em dash")
    if problems:
        print("Problems:\n- " + "\n- ".join(problems))
        sys.exit(1)
    print(f"OK: {len(items)} picks")


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    cmd, rest = argv[0], argv[1:]
    if cmd == "due":
        ids = ",".join(rest).split(",") if rest and any(rest) else None
        print(json.dumps(due(ids)))
    elif cmd == "prompt":
        prompt(rest[0], rest[1] if len(rest) > 1 else london_today().isoformat())
    elif cmd == "check":
        check(rest[0], rest[1])
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
