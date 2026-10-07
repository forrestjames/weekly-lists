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
`tags` (list), `note`, `source`, and `link` (a Bandcamp or label link when
there's no Spotify one). The Spotify script ignores them.

To rebuild by hand: `python scripts/build_site_data.py`.
