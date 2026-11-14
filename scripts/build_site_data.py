#!/usr/bin/env python3
"""
Builds the JSON the website reads, from the weekly picks files.

Usage:  python scripts/build_site_data.py

Reads   picks/rob/*.json, picks/juno/*.json and design/*.json
Writes  site/data/latest.json    this week's picks for every edition
        site/data/archive.json   every past week, newest first

The page only needs to fetch these two files. One-off lists (like
picks/dub) are left out.

Design Shelf picks live in design/ rather than picks/, so the Spotify
workflow never sees them. Each design pick looks like this (all but
"title" optional):
  {"title": "...", "by": "author, studio or editor", "kind": "book",
   "tags": ["posters", "type"], "note": "...", "instagram": ["@handle"],
   "shop": "Magma", "price": "£25", "link": "https://...", "art": "https://..."}
kind is "book", "magazine" or "designer". For any design pick without
"art", this script reads the shop page and saves its cover image (and, for
Shopify shops, up to four more pictures) into archive.json. Earlier
results are reused, so only new picks cost a request.

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
import difflib
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import add_tracks as sp  # noqa: E402  (reuses its Spotify search and matching)

PICKS = ROOT / "picks"
DESIGN = ROOT / "design"
OUT = ROOT / "site" / "data"

EDITIONS = {
    "rob": {"name": "Rob's List", "playlist": "https://open.spotify.com/playlist/6YpWmU0EK7fFa62dpR8Ip7"},
    "juno": {"name": "Juno Weekly", "playlist": ""},
    "design": {"name": "Design Shelf", "playlist": ""},
}
FOLDERS = {"rob": PICKS / "rob", "juno": PICKS / "juno", "design": DESIGN}
MUSIC = ["rob", "juno"]

FIELDS = ["artist", "title", "kind", "tags", "note", "source", "source_url", "spotify_url", "link", "art",
          "by", "instagram", "shop", "price", "images", "pictures_from"]
MAX_IMAGES = 5
MAX_SPOTIFY_FAILURES = 3  # give up on Spotify for this run after this many errors in a row


def clean(item):
    out = {k: item[k] for k in FIELDS if item.get(k)}
    if isinstance(out.get("tags"), str):
        out["tags"] = [t.strip() for t in out["tags"].split(",") if t.strip()]
    return out


def load_weeks(edition):
    weeks = []
    for path in sorted(FOLDERS[edition].glob("*.json")):
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
    for key in MUSIC:
        for week in archive[key]["weeks"]:
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


UA = "Mozilla/5.0 (weekly-lists site builder; +https://github.com/forrestjames/weekly-lists)"


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read().decode("utf-8", "replace")


def absolute(u, base):
    u = html.unescape((u or "").strip())
    if u.startswith("//"):
        u = "https:" + u
    u = urllib.parse.urljoin(base, u) if u else ""
    return "https://" + u[7:] if u.startswith("http://") else u   # the site is https, so http pictures wouldn't show


# Design shops that run on Shopify. Each has a product search at
# /search/suggest.json and every product's photos at <product URL>.json, so a
# pick whose page has moved or sold out can still be found by title.
SHOPIFY_SHOPS = ["magma-shop.com", "magculture.com", "draw-down.com", "www.counter-print.co.uk", "www.perimeterbooks.com"]
IMG_EXT = re.compile(r"\.(jpe?g|png|webp|gif)(\?|$)", re.I)
SKIP_IMG = re.compile(r"logo|icon|sprite|favicon|avatar|placeholder|blank|pixel|badge|payment|flag|spacer|space-|arrow|button|"
                      r"menu|close|loader|inactive|\.svg", re.I)
STOP = {"a", "an", "the", "of", "on", "in", "and", "to", "for", "by", "de", "la", "le", "du", "des", "no", "vol", "issue"}
MIN_SIDE = 180  # pictures smaller than this on their short side are icons, not covers


def simplify(s):
    s = html.unescape(s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def words(s):
    return {w for w in simplify(s).split() if w not in STOP}


def same_title(a, b):
    """True when two product titles name the same thing.

    Every word of one title's main part (before any colon) has to appear in
    the other, so "A Book on Zines" never matches its sister "A Book on Books"."""
    if not simplify(a) or not simplify(b):
        return False
    ma, mb = words(re.split(r"[:–—]| - ", a)[0]), words(re.split(r"[:–—]| - ", b)[0])
    wa, wb = words(a), words(b)
    if not ma or not mb:
        return False
    if (ma <= wb and len(mb - wa) <= 1) or (mb <= wa and len(ma - wb) <= 1):
        return True
    # "Bruno Munari: Fantasy" is the book "Fantasy": compare the part after the colon too.
    # Shops also write "Author – Title", so the same goes for a dash.
    split = lambda t: re.split(r"[:–—]| - ", t, maxsplit=1)  # noqa: E731
    sa = words(split(a)[1]) if len(split(a)) > 1 else set()
    sb = words(split(b)[1]) if len(split(b)) > 1 else set()
    return bool(sa and sa == mb) or bool(sb and sb == ma)


def image_ok(url):
    """Downloads a picture and checks it is a real, reasonably large image."""
    try:
        from PIL import Image
    except ImportError:
        return True   # can't check here; trust it
    import io
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as res:
            data = res.read(8_000_000)
        w, h = Image.open(io.BytesIO(data)).size
        return min(w, h) >= MIN_SIDE
    except Exception:  # noqa: BLE001
        return False


def good(imgs):
    """Keeps the pictures that are real and big enough, cover first."""
    out = []
    for u in imgs:
        if u not in out and image_ok(u):
            out.append(u)
    return out[:MAX_IMAGES]


def shopify_product_images(url):
    parts = urllib.parse.urlsplit(url)
    data = json.loads(fetch(urllib.parse.urlunsplit((parts.scheme or "https", parts.netloc, parts.path.rstrip("/") + ".json", "", ""))))
    imgs = [absolute(i.get("src"), url) for i in (data.get("product") or {}).get("images", [])]
    return [i for i in imgs if i][:MAX_IMAGES]


def page_images(url):
    """og:image, then twitter:image, then the first big-looking <img> on the page."""
    page = fetch(url)
    for pat in (r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']twitter:image'):
        m = re.search(pat, page, re.I)
        if m and not SKIP_IMG.search(m.group(1)):
            return [absolute(m.group(1), url)]
    found = []
    for tag in sorted(re.findall(r"<img\b[^>]*>", page, re.I), key=lambda t: ".png" in t.lower()):  # photos before UI pngs
        srcs = re.findall(r'(?:data-src|src|data-srcset|srcset)=["\']([^"\']+)', tag, re.I)
        for src in srcs:
            src = src.split(",")[-1].strip().split(" ")[0]   # largest srcset entry
            if IMG_EXT.search(src) and not SKIP_IMG.search(src + tag):
                u = absolute(src, url)
                if u not in found:
                    found.append(u)
                break
        if len(found) >= MAX_IMAGES:
            break
    return found


def shopify_search(host, title):
    """Finds a product by title on a Shopify shop. Returns (url, images) or None."""
    q = urllib.parse.quote(title[:80])
    data = json.loads(fetch(f"https://{host}/search/suggest.json?q={q}&resources[type]=product&resources[limit]=6"))
    for prod in ((data.get("resources") or {}).get("results") or {}).get("products", []):
        if same_title(prod.get("title"), title):
            url = absolute(prod.get("url", "").split("?")[0], f"https://{host}/")
            try:
                imgs = shopify_product_images(url)
            except Exception:  # noqa: BLE001
                img = prod.get("image") or (prod.get("featured_image") or {}).get("url")
                imgs = [absolute(img, url)] if img else []
            if imgs:
                return url, imgs
    return None


def book_query(title, by):
    t = re.split(r"[:(]", title)[0].strip()
    author = re.sub(r"\(.*?\)", "", by or "").split(" and ")[0].split(",")[0].strip()
    if author.lower().startswith(("studio", "designer", "type collective")):
        author = ""
    return t, author


def open_library(title, by):
    t, author = book_query(title, by)
    q = {"title": t, "limit": 5, **({"author": author} if author else {})}
    for attempt in range(3):
        try:
            data = json.loads(fetch("https://openlibrary.org/search.json?" + urllib.parse.urlencode(q)))
            break
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 2:
                raise
            import time
            time.sleep(5 * (attempt + 1))
    for doc in data.get("docs", []):
        if doc.get("cover_i") and same_title(doc.get("title"), t):
            return [f"https://covers.openlibrary.org/b/id/{doc['cover_i']}-L.jpg"]
    return []


def google_books(title, by):
    t, author = book_query(title, by)
    gq = f'intitle:"{t}"' + (f' inauthor:"{author}"' if author else "")
    data = json.loads(fetch("https://www.googleapis.com/books/v1/volumes?" + urllib.parse.urlencode({"q": gq, "maxResults": 5})))
    for vol in data.get("items", []):
        info = vol.get("volumeInfo", {})
        links = info.get("imageLinks") or {}
        img = links.get("extraLarge") or links.get("large") or links.get("medium") or links.get("thumbnail")
        if img and same_title(info.get("title"), t):
            return [img.replace("http://", "https://").replace("&edge=curl", "").replace("zoom=1", "zoom=3")]
    return []


def wayback(link):
    """Pictures from the last archived copy of a shop page that has gone."""
    data = json.loads(fetch("https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": link})))
    snap = ((data.get("archived_snapshots") or {}).get("closest") or {}).get("url")
    if not snap:
        return []
    imgs = page_images(snap)
    # Archived image addresses point back into archive.org; strip that so the shop's CDN serves them.
    imgs = [re.sub(r"^https?://web\.archive\.org/web/\d+(im_)?/", "", u) for u in imgs]
    return ["https://" + u[7:] if u.startswith("http://") else u for u in imgs]


def web_search(title, by):
    """Last resort: search the web for the title and take the picture from a
    result page whose own title matches."""
    q = f'{title} {re.sub(r"[(].*?[)]", "", by or "")}'.strip()
    urls = []
    try:   # DuckDuckGo's plain HTML results
        page = fetch("https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": q}))
        for href in re.findall(r'class="result__a"[^>]*href="([^"]+)"', page):
            m = re.search(r"uddg=([^&]+)", href)
            urls.append(urllib.parse.unquote(m.group(1)) if m else html.unescape(href))
    except Exception as e:  # noqa: BLE001
        print(f"  duckduckgo: {e}", file=sys.stderr)
    try:   # then Bing
        page = fetch("https://www.bing.com/search?" + urllib.parse.urlencode({"q": q}))
        urls += [html.unescape(u) for u in re.findall(r'<li class="b_algo".*?<a[^>]+href="(https?://[^"]+)"', page, re.S)]
    except Exception as e:  # noqa: BLE001
        print(f"  bing: {e}", file=sys.stderr)
    urls = [u for u in dict.fromkeys(urls) if u.startswith("http")
            and not re.search(r"instagram|facebook|twitter|x\.com|pinterest|youtube|amazon|duckduckgo|bing\.com", u)]
    print(f"  web search for {q!r}: {len(urls)} result(s) {urls[:3]}", file=sys.stderr)
    for u in urls[:8]:
        try:
            body = fetch(u)
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', body, re.I) or \
            re.search(r"<title[^>]*>([^<]+)", body, re.I)
        if m:
            print(f"    {u} -> {m.group(1)[:80]!r}", file=sys.stderr)
        if m and same_title(m.group(1), title):
            imgs = good(page_images(u))
            if imgs:
                return imgs
    return []


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", simplify(s).replace("ö", "o")).strip("-")


def find_pictures(it):
    """Every way we know to get a picture for a design pick, in order. Each
    candidate is downloaded and checked, so icons and broken links never win.

    Returns (images, better_link). better_link is set when the pick's own link
    is dead and the same product turned up elsewhere in the same shop."""
    link, title, by = it.get("link", ""), it.get("title", ""), it.get("by", "")
    host = urllib.parse.urlsplit(link).netloc if link else ""
    designer = it.get("kind") == "designer"
    tried = []

    def attempt(name, fn):
        try:
            res = fn()
        except Exception as e:  # noqa: BLE001
            tried.append(f"{name}: {e}")
            return None
        if not res:
            tried.append(f"{name}: nothing")
        return res

    def checked(name, fn):
        imgs = attempt(name, fn)
        imgs = good(imgs) if imgs else []
        if not imgs and name not in " ".join(tried):
            tried.append(f"{name}: too small or broken")
        return imgs

    # 1. The pick's own page.
    if link:
        if "/products/" in link:
            imgs = checked("product json", lambda: shopify_product_images(link))
            if imgs:
                return imgs, None
        imgs = checked("page", lambda: page_images(link))
        if imgs:
            return imgs, None
    # 1b. Any extra pages the pick names for pictures (a review, a poster archive).
    for extra in it.get("pictures_from") or []:
        imgs = checked(extra, lambda extra=extra: page_images(extra))
        if imgs:
            return imgs, None
    if not designer:
        # 2. Search the same shop by title (products get moved or renamed), then the other shops.
        shops = ([host] if host in SHOPIFY_SHOPS else []) + [h for h in SHOPIFY_SHOPS if h != host]
        for h in shops:
            hit = attempt(f"search {h}", lambda h=h: shopify_search(h, title))
            if hit:
                url, imgs = hit
                imgs = good(imgs)
                if imgs:
                    return imgs, (url if h == host else None)
        # 3. The last archived copy of the pick's own page.
        if link:
            imgs = checked("wayback", lambda: wayback(link))
            if imgs:
                return imgs, None
        # 4. Book cover databases.
        for name, fn in (("open library", open_library), ("google books", google_books)):
            imgs = checked(name, lambda fn=fn: fn(title, by))
            if imgs:
                return imgs, None
    else:
        # Designers: their own home page, then their Typographic Posters archive page.
        name = re.split(r",| m23", title)[0]
        home = f"https://{host}/" if host else ""
        for url in dict.fromkeys(filter(None, (home, f"https://www.typographicposters.com/{slug(title)}",
                                               f"https://www.typographicposters.com/{slug(name)}"))):
            imgs = checked(url, lambda url=url: page_images(url))
            if imgs:
                return imgs, None
    # 5. Search the web.
    imgs = attempt("web search", lambda: web_search(title + (" posters" if designer else ""), "" if designer else by))
    if imgs:
        return imgs, None
    print(f"  No picture for {title}. Tried: " + "; ".join(tried), file=sys.stderr)
    return [], None


def add_design_art(archive, previous):
    """Fills in "art" (and "images") on design picks that lack them.

    Picks that still have no picture are tried again on every build, so a
    picture turns up as soon as any source has one."""
    known = {}
    for week in ((previous or {}).get("editions", {}).get("design") or {}).get("weeks", []):
        for it in week.get("items", []):
            if it.get("art"):
                known[(it.get("title"), it.get("by"))] = it
    found = tried = 0
    missing = []
    for week in archive["design"]["weeks"]:
        for it in week["items"]:
            if it.get("art"):
                continue
            old = known.get((it.get("title"), it.get("by")))
            if old and old.get("art_checked") is None and not image_ok(old["art"]):
                print(f"  Saved picture for {it.get('title')} failed the size check; looking again.")
                old = None
            if old:
                it["art_checked"] = True
                for k in ("art", "images", "link"):
                    if old.get(k) and (k != "link" or old.get("link_fixed")):
                        it[k] = old[k]
                https = lambda u: "https://" + u[7:] if u.startswith("http://") else u  # noqa: E731
                it["art"] = https(it["art"])
                if it.get("images"):
                    it["images"] = [https(u) for u in it["images"]]
                if old.get("link_fixed"):
                    it["link_fixed"] = True
                continue
            tried += 1
            pics, better = find_pictures(it)
            if pics:
                it["art"], it["art_checked"] = pics[0], True
                if len(pics) > 1:
                    it["images"] = pics
                if better and better != it.get("link"):
                    print(f"  Link moved for {it['title']}: {better}")
                    it["link"], it["link_fixed"] = better, True
                found += 1
            else:
                missing.append(it.get("title"))
    if tried:
        print(f"Design pictures: {found} of {tried} new pick(s) filled in.")
    if missing:
        print("::warning title=Design picks without a picture::" + " | ".join(missing))


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
    add_design_art(archive, previous_archive)

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
