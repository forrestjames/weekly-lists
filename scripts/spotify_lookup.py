#!/usr/bin/env python3
"""
Finds a release on Spotify and prints its link, so the weekly run never
guesses one.

  python3 scripts/spotify_lookup.py "Artist" "Title" album
  python3 scripts/spotify_lookup.py "Artist" "Title" track

Prints the open.spotify.com URL and what it matched, or NOT FOUND.
Needs SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import add_tracks as sp  # noqa: E402
from build_site_data import spotify_token  # noqa: E402


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    artist, title = argv[0], argv[1]
    kind = (argv[2] if len(argv) > 2 else "album").lower()
    kind = "track" if kind.startswith("track") else "album"
    token = spotify_token()
    if not token:
        print("NOT AVAILABLE: Spotify keys missing, leave spotify_url out")
        return 0
    found = sp.search(token, artist, title, kind)
    if not found:
        print("NOT FOUND")
        return 0
    url = (found.get("external_urls") or {}).get("spotify") or f"https://open.spotify.com/{kind}/{found['id']}"
    who = ", ".join(a.get("name", "") for a in found.get("artists", []))
    when = found.get("release_date") or (found.get("album") or {}).get("release_date", "")
    print(url)
    print(f"Matched: {who} — {found.get('name', '')} ({kind}, released {when or 'unknown'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
