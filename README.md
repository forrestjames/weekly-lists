# Weekly lists

Rob's List, Juno Weekly, Pitchfork Weekly and Design Shelf: picked every week,
added to Spotify, put on the website and emailed. All of it runs here in
GitHub, on its own.

## How it works

The "Weekly lists" action (`.github/workflows/weekly-lists.yml`) runs every
Monday for the music lists and every Saturday for Design Shelf:

1. It reads `lists.yml` to see which lists are due.
2. Claude curates each one, side by side, and writes a picks file.
3. The picks are saved, added to Spotify, the site data is rebuilt, and it
   all goes in one commit.
4. One email with every list. If a list fails, the others still go out and
   the email says which one didn't run.

To run it by hand: Actions > Weekly lists > Run workflow. Leave "lists" blank
for today's lists, or name some, like `pitchfork`.

## Adding or changing a list

Everything about each list is in `lists.yml`: name, day, colour, playlist,
taste and sources. Edit it right on GitHub (the pencil icon).

- New source: add a line under that list's `sources`.
- New music list: copy a block, give it a new `id`, `name`, `colour` and a
  `playlist_name`. It gets its own tab on the site and its own playlist.
- Pause a list: add `paused: true`.

## Secrets

Settings > Secrets and variables > Actions:

- `CLAUDE_CODE_OAUTH_TOKEN`: from `claude setup-token` on your Mac. Uses your
  Claude subscription. Lasts about a year.
- `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REFRESH_TOKEN`,
  `ROB_PLAYLIST_ID`, `JUNO_PLAYLIST_ID`: from `get_token.py`.
- Email (optional): `SMTP_USER` (the Gmail address it sends from),
  `SMTP_PASSWORD` (a Gmail app password), `EMAIL_TO`. Without these the
  rundown arrives as a GitHub issue, which GitHub emails to you.

## Spotify

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

## Design Shelf

The Saturday design list lives in `design/YYYY-MM-DD.json`, outside
`picks/`, so the Spotify action never sees it. Each pick looks like:

```json
{"title": "Poster Cult", "by": "Dafi Kühne", "kind": "book",
 "tags": ["posters", "letterpress"], "note": "One or two honest sentences.",
 "instagram": ["@dafikuhne"], "shop": "Draw Down", "price": "$60",
 "link": "https://draw-down.com/products/dafi-kuhne-poster-cult"}
```

`kind` is `book`, `magazine` or `designer`. Only `title` is required.
If the build can't find a picture, add `"pictures_from": ["https://..."]` with
one or more pages that show the work (a review, a poster archive).
Pictures: the "Build site data" action reads each new pick's shop page and
saves the cover (plus up to four more photos from Shopify shops like Magma,
magCulture, Draw Down, Counter-Print and Perimeter) into `archive.json`. Set
`art` by hand if a shop page has no usable picture.

## Website data

Every time a Rob, Juno or Design Shelf file lands on main, the "Build site
data" action rebuilds two files the website reads:

- `site/data/latest.json`: this week's picks for every edition
- `site/data/archive.json`: every past week, newest first

Picks can carry extra fields for the site, all optional:
`tags` (list), `note`, `source`, `source_url` (the exact article; the page
shows it as a "Source" button, and without it the button links to the
publication's site when the name is one the page knows, listed in
`SOURCE_SITES` in `site/index.html`), `link` (a Bandcamp or label link when
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
(Rob / Juno / on both), genre chip, or search. The Design Shelf button
switches the same views over to the design picks, with tags in place of
genres and an overlay showing the shop, price, extra pictures and Instagram
links.

To preview locally: `python3 -m http.server -d site`, then open
http://localhost:8000.
