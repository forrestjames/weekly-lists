"""Remove playlist tracks added more than MAX_AGE_DAYS ago.

Age comes from the date in each picks/<list>/YYYY-MM-DD.json filename.
Expired pick files are moved to archive/<list>/ (outside picks/, so the add-tracks
workflow is never triggered by them) so history stays in git.
Tracks that also appear in a newer, unexpired pick file are kept.
"""
import datetime as dt
import json
import os
import re
import shutil
import sys
from pathlib import Path

import requests

PLAYLISTS = {
    name: os.environ[var]
    for name, var in (("rob", "ROB_PLAYLIST_ID"), ("juno", "JUNO_PLAYLIST_ID"))
    if os.environ.get(var)
}
MAX_AGE_DAYS = int(os.environ.get("MAX_AGE_DAYS", "60"))
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
API = "https://api.spotify.com/v1"
ID_RE = re.compile(r"open\.spotify\.com/(album|track)/([A-Za-z0-9]+)")


def token():
    cid = os.environ["SPOTIFY_CLIENT_ID"].strip()
    secret = os.environ["SPOTIFY_CLIENT_SECRET"].strip()
    refresh = os.environ["SPOTIFY_REFRESH_TOKEN"].strip()
    print(f"Secrets present: id={bool(cid)} (len {len(cid)}), "
          f"secret={bool(secret)} (len {len(secret)}), refresh={bool(refresh)}")
    base = {"grant_type": "refresh_token", "refresh_token": refresh}
    attempts = (
        ("basic auth", dict(data=base, auth=(cid, secret))),
        ("credentials in body", dict(data={**base, "client_id": cid, "client_secret": secret})),
        ("client id only (PKCE)", dict(data={**base, "client_id": cid})),
    )
    last = ""
    for label, kwargs in attempts:
        r = requests.post("https://accounts.spotify.com/api/token", timeout=30, **kwargs)
        if r.ok:
            print(f"Spotify login ok via {label}")
            return r.json()["access_token"]
        last = f"{label}: {r.status_code} {r.text[:200]}"
        print(f"  login failed, {last}")
    sys.exit(f"Spotify token request failed. Last attempt: {last}")


S = requests.Session()


def get(path, **params):
    r = S.get(f"{API}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def album_uris(album_id):
    uris, url = [], f"/albums/{album_id}/tracks"
    params = {"limit": 50}
    while True:
        data = get(url, **params)
        uris += [t["uri"] for t in data["items"]]
        if not data.get("next"):
            return uris
        params["offset"] = len(uris)


def item_uris(item):
    """Return track URIs for a pick, or None if it cannot be resolved."""
    m = ID_RE.search(item.get("spotify_url", ""))
    if m:
        kind, sid = m.groups()
        return album_uris(sid) if kind == "album" else [f"spotify:track:{sid}"]
    # No stored link: the add script found it by search, so repeat that search.
    q = f'{item["artist"]} {item["title"]}'
    kind = "album" if item.get("kind") == "album" else "track"
    res = get("/search", q=q, type=kind, limit=1)[f"{kind}s"]["items"]
    if not res:
        return None
    return album_uris(res[0]["id"]) if kind == "album" else [res[0]["uri"]]


def pick_files(list_name):
    for p in sorted(Path("picks", list_name).glob("*.json")):
        try:
            yield p, dt.date.fromisoformat(p.stem)
        except ValueError:
            continue


def main():
    S.headers["Authorization"] = f"Bearer {token()}"
    cutoff = dt.date.today() - dt.timedelta(days=MAX_AGE_DAYS)
    print(f"Cutoff {cutoff} ({MAX_AGE_DAYS} days). Dry run: {DRY_RUN}")
    problems = 0

    for name, playlist_id in PLAYLISTS.items():
        old, keep = [], set()
        for path, day in pick_files(name):
            items = json.loads(path.read_text())["items"]
            bucket = old if day < cutoff else None
            for it in items:
                try:
                    uris = item_uris(it)
                except requests.RequestException as e:
                    uris, problems = None, problems + 1
                    print(f"  lookup error {it['artist']} - {it['title']}: {e}")
                if uris is None:
                    print(f"  could not resolve: {it['artist']} - {it['title']} ({path.name})")
                    continue
                if bucket is not None:
                    bucket.extend((path, u) for u in uris)
                else:
                    keep.update(uris)

        remove = sorted({u for _, u in old} - keep)
        print(f"[{name}] expired track entries: {len(remove)}")
        if remove and not DRY_RUN:
            for i in range(0, len(remove), 100):
                chunk = [{"uri": u} for u in remove[i:i + 100]]
                r = S.delete(f"{API}/playlists/{playlist_id}/tracks",
                             json={"tracks": chunk}, timeout=30)
                if not r.ok:
                    problems += 1
                    print(f"  remove failed {r.status_code}: {r.text[:200]}")
        if not DRY_RUN and problems == 0:
            archive = Path("archive", name)
            archive.mkdir(parents=True, exist_ok=True)
            for path in {p for p, _ in old}:
                shutil.move(str(path), archive / path.name)
                print(f"  archived {path.name}")

    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
