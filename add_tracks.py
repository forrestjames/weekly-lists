#!/usr/bin/env python3
"""
Adds a week's picks to the right Spotify playlist.

Usage:  python add_tracks.py picks/rob/2026-10-05.json [more files...]

Each picks file looks like:
{
  "list": "rob",                 # "rob" or "juno"
  "date": "2026-10-05",
  "items": [
    {"artist": "Lusine", "title": "Melting Days", "kind": "album",
     "spotify_url": "https://open.spotify.com/album/..."},   # url optional
    {"artist": "Audrey Powne", "title": "Bodies of Water", "kind": "track"}
  ]
}

A one-off list can name its own playlist instead of "rob"/"juno":
  {"list": "dub", "playlist_name": "10 Essential Dub Albums", "items": [...]}
The playlist is created the first time and reused after that.

Albums go in whole. Tracks go in as single tracks. Order is kept, so
singles land where they were placed in the list. Anything already in the
playlist is skipped, so re-running is harmless. Anything Spotify doesn't
have is reported and skipped.

Needs these environment variables (set as GitHub secrets):
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN,
  ROB_PLAYLIST_ID, JUNO_PLAYLIST_ID
"""

import base64
import difflib
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.spotify.com/v1"
ADD_CHUNK = 10  # small batches, to stay inside Spotify's current limits

PLAYLIST_ENV = {"rob": "ROB_PLAYLIST_ID", "juno": "JUNO_PLAYLIST_ID"}


class SpotifyError(Exception):
    pass


def env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SpotifyError(f"Missing {name}. Add it under the repo's Actions secrets.")
    return value


def http(method, url, token=None, data=None, headers=None, form=False, tries=5):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                wait = int(e.headers.get("Retry-After", 2 ** attempt))
                time.sleep(min(wait, 30))
                continue
            raise SpotifyError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')}")
    raise SpotifyError(f"{method} {url} kept failing after {tries} tries")


def access_token():
    cid, secret = env("SPOTIFY_CLIENT_ID"), env("SPOTIFY_CLIENT_SECRET")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    data = http(
        "POST",
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "refresh_token", "refresh_token": env("SPOTIFY_REFRESH_TOKEN")},
        headers={"Authorization": f"Basic {basic}"},
        form=True,
    )
    if data.get("refresh_token") and data["refresh_token"] != os.environ["SPOTIFY_REFRESH_TOKEN"]:
        print("::warning::Spotify issued a new refresh token. If adds start failing, "
              "run get_token.py again and update SPOTIFY_REFRESH_TOKEN.")
    return data["access_token"]


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)|\[.*?\]", " ", s)          # drop (Deluxe), [Remastered] etc
    s = re.sub(r"\b(feat|ft)\b.*", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def similar(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b or a in b or b in a:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def id_from_url(url, kind):
    if not url:
        return None
    m = re.search(rf"(?:open\.spotify\.com/(?:intl-[a-z-]+/)?{kind}/|spotify:{kind}:)([A-Za-z0-9]{{22}})", url)
    return m.group(1) if m else None


def search(token, artist, title, kind):
    queries = [
        f'{"album" if kind == "album" else "track"}:"{title}" artist:"{artist}"',
        f"{artist} {title}",
    ]
    best, best_score = None, 0.0
    for q in queries:
        url = f"{API}/search?" + urllib.parse.urlencode({"q": q, "type": kind, "limit": 10})
        results = http("GET", url, token).get(f"{kind}s", {}).get("items", [])
        for r in results:
            if not r:
                continue
            artist_score = max((similar(artist, a.get("name", "")) for a in r.get("artists", [])), default=0)
            title_score = similar(title, r.get("name", ""))
            score = artist_score * 0.5 + title_score * 0.5
            if artist_score >= 0.75 and title_score >= 0.75 and score > best_score:
                best, best_score = r, score
        if best:
            break
    return best


def album_track_uris(token, album_id):
    uris, url = [], f"{API}/albums/{album_id}/tracks?limit=50"
    while url:
        page = http("GET", url, token)
        uris += [t["uri"] for t in page.get("items", []) if t and t.get("uri")]
        url = page.get("next")
    return uris


def existing_uris(token, playlist_id):
    uris, url = set(), f"{API}/playlists/{playlist_id}/items?limit=50"
    while url:
        page = http("GET", url, token)
        for entry in page.get("items", []):
            obj = (entry or {}).get("item") or (entry or {}).get("track")
            if obj and obj.get("uri"):
                uris.add(obj["uri"])
        url = page.get("next")
    return uris


def add_uris(token, playlist_id, uris):
    for i in range(0, len(uris), ADD_CHUNK):
        http("POST", f"{API}/playlists/{playlist_id}/items", token, data={"uris": uris[i:i + ADD_CHUNK]})


def find_or_create_playlist(token, name, description=""):
    url = f"{API}/me/playlists?limit=50"
    while url:
        page = http("GET", url, token)
        for pl in page.get("items", []):
            if pl and pl.get("name") == name:
                print(f"Using existing playlist '{name}' ({pl['id']})")
                return pl["id"]
        url = page.get("next")
    pl = http("POST", f"{API}/me/playlists", token,
              data={"name": name, "description": description, "public": False})
    print(f"Created playlist '{name}' ({pl['id']})")
    return pl["id"]


def process(token, path, summary):
    with open(path) as f:
        picks = json.load(f)
    which = (picks.get("list") or "").lower()
    if picks.get("playlist_name"):
        # One-off list with its own playlist, found by name or created.
        playlist_id = find_or_create_playlist(
            token, picks["playlist_name"], picks.get("playlist_description", ""))
        which = which or "custom"
    elif which in PLAYLIST_ENV:
        playlist_id = env(PLAYLIST_ENV[which])
    else:
        raise SpotifyError(f"{path}: 'list' must be 'rob' or 'juno' (or give a 'playlist_name'), got {which!r}")

    already = existing_uris(token, playlist_id)
    to_add, lines, missing = [], [], []

    for item in picks.get("items", []):
        artist, title = item.get("artist", ""), item.get("title", "")
        kind = "album" if (item.get("kind") or "album").lower() == "album" else "track"
        label = f"{artist} — {title}"

        sid = id_from_url(item.get("spotify_url"), kind)
        if not sid:
            found = search(token, artist, title, kind)
            sid = found["id"] if found else None
        if not sid:
            missing.append(label)
            lines.append(f"- ✗ {label} ({kind}): not on Spotify")
            continue

        uris = album_track_uris(token, sid) if kind == "album" else [f"spotify:track:{sid}"]
        fresh = [u for u in uris if u not in already and u not in to_add]
        to_add += fresh
        already.update(fresh)
        note = f"{len(fresh)} added" if fresh else "already in playlist"
        lines.append(f"- ✓ {label} ({kind}): {note}")

    if to_add:
        add_uris(token, playlist_id, to_add)

    header = f"### {which.title()} list, {picks.get('date', '')}: {len(to_add)} tracks added"
    summary.append("\n".join([header, *lines, ""]))
    print(header)
    print("\n".join(lines))
    if missing:
        print("Not on Spotify:", "; ".join(missing))


def main(paths):
    if not paths:
        print("No picks files to process.")
        return 0
    token = access_token()
    summary = []
    for p in paths:
        process(token, p, summary)
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a") as f:
            f.write("\n".join(summary))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main([p for p in sys.argv[1:] if p.endswith(".json")]))
    except SpotifyError as e:
        print(f"::error::{e}")
        sys.exit(1)
