# Weekly lists

Adds the Monday picks to Spotify.

- Rob's List picks go in `picks/rob/YYYY-MM-DD.json` and land in "Rob's List, Weekly".
- Juno picks go in `picks/juno/YYYY-MM-DD.json` and land in "Juno Weekly".

When a picks file is pushed, the "Add picks to Spotify" action runs and adds
each album (whole) or single (one track) to the right playlist, in order.
Anything already there is skipped. Anything Spotify doesn't have is listed in
the run summary.

To re-run a week: Actions > Add picks to Spotify > Run workflow, and give it
the file path.

Secrets needed (Settings > Secrets and variables > Actions):
`SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`,
`ROB_PLAYLIST_ID`, `JUNO_PLAYLIST_ID`. Get them by running `get_token.py`
once on your Mac.

## Website data

Every time a Rob or Juno picks file lands on main, the "Build site data"
action rebuilds two files the website reads:

- `site/data/latest.json`: this week's picks for both editions
- `site/data/archive.json`: every past week, newest first

Picks can carry extra fields for the site, all optional:
`tags` (list), `note`, `source`, `link` (a Bandcamp or label link when
there's no Spotify one), and `art` (a cover image URL, to set a cover by
hand). The Spotify script ignores them.

Cover art: the same action looks up every pick that has no `art` on Spotify
and saves the cover (and the album link, if the pick had none) into
`archive.json`. Earlier results are reused, so only new picks cost a lookup.
It uses the `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET` secrets; without
them, or if Spotify is down, the data still builds and the page falls back to
finding covers in the browser. Anything Spotify can't match is logged as
"Not found on Spotify" in the run, and you can add an `art` URL for it.

To rebuild by hand: `python scripts/build_site_data.py`.

## The website

`site/index.html` is a single-page browser for every pick. It reads
`site/data/archive.json`, which carries the cover art, and looks up genre and
release year from the iTunes Search API in the browser (cached in
localStorage). If a pick has no saved cover it also tries Spotify, iTunes and
MusicBrainz. A lookup that fails (offline, rate limited) is retried after an
hour; one that finds nothing is retried after a week. Genre comes from a
pick's `tags` first, then iTunes.

Views: Grid, Mosaic (covers only), Weeks, Genres, List. Filter by list
(Rob / Juno / on both), genre chip, or search.

To preview locally: `python3 -m http.server -d site`, then open
http://localhost:8000.
