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
