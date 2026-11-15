#!/usr/bin/env python3
"""
Last step of the weekly run: one email with every list that ran today.

Reads .weekly/status.json (what ran), .weekly/spotify.json (how the
playlists went) and site/data/archive.json (picks with their Spotify links
and pictures filled in).

Sends through Gmail when these secrets are set:
  SMTP_USER      the Gmail address it sends from
  SMTP_PASSWORD  a Gmail app password for that address
  EMAIL_TO       where it goes
Without them it opens a GitHub issue instead, which GitHub emails to you.
Either way the same text goes in the run summary.
"""

import html
import json
import os
import smtplib
import subprocess
import sys
from datetime import date
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lists as list_config  # noqa: E402

ROOT = list_config.ROOT
WEEKLY = ROOT / ".weekly"
RUN_URL = os.environ.get("RUN_URL", "")
SITE_URL = os.environ.get("SITE_URL", "")


def load(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def nice_date(iso):
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def week_items(archive, lid, day):
    for w in archive.get("editions", {}).get(lid, {}).get("weeks", []):
        if w.get("date") == day:
            return w.get("items", [])
    return []


def picks_from_file(rel):
    return load(ROOT / rel, {}).get("items", []) if rel else []


def playlist_line(entry, conf, spotify):
    """One plain line on whether the playlist update worked."""
    res = next((r for r in spotify if r.get("file") == entry.get("file")), None)
    url = conf.get("playlist_url", "")
    if res and res.get("playlist_id") and not url:
        url = f"https://open.spotify.com/playlist/{res['playlist_id']}"
    if not res:
        return "The playlist wasn't updated this week. The run log has the details.", url
    if res.get("error"):
        return f"The playlist update didn't work: {res['error']}", url
    line = f"Playlist updated, {res['added']} new tracks added."
    if res.get("missing"):
        line += " Not on Spotify, so not in the playlist: " + "; ".join(res["missing"]) + "."
    return line, url


def build(status, spotify, archive):
    text, body = [], []
    day = status["date"]
    ran = [e for e in status["lists"]]
    for entry in ran:
        conf = list_config.get(entry["id"])
        name = entry["name"]
        text.append(f"{name.upper()}\n")
        body.append(f'<h2 style="font-family:Georgia,serif;margin:28px 0 6px">{html.escape(name)}</h2>')
        if not entry["ok"]:
            msg = f"This one didn't run this week: {entry.get('problem', 'unknown problem')}."
            if RUN_URL:
                msg += f" The run log: {RUN_URL}"
            text.append(msg + "\n")
            body.append(f"<p>{html.escape(msg)}</p>")
            continue

        if conf["type"] == "music":
            line, url = playlist_line(entry, conf, spotify)
            if url:
                text.append(f"Playlist: {url}")
                body.append(f'<p><a href="{html.escape(url)}">Open the playlist</a></p>')
            text.append(line)
            body.append(f'<p style="color:#555">{html.escape(line)}</p>')
        if entry.get("intro"):
            text.append(entry["intro"])
            body.append(f"<p>{html.escape(entry['intro'])}</p>")
        text.append("")

        items = week_items(archive, entry["id"], day) or picks_from_file(entry.get("file"))
        for n, it in enumerate(items, 1):
            if conf["type"] == "design":
                text += design_text(n, it)
                body.append(design_html(n, it))
            else:
                text += music_text(n, it)
                body.append(music_html(n, it))

    if SITE_URL:
        text.append(f"Everything's on the site too: {SITE_URL}")
        body.append(f'<p style="margin-top:28px">Everything\'s on the site too: <a href="{html.escape(SITE_URL)}">Weekly Lists</a></p>')
    text.append("Enjoy.")
    body.append("<p>Enjoy.</p>")
    page = ('<div style="font-family:Georgia,serif;font-size:16px;line-height:1.5;color:#1b1b1f;max-width:640px">'
            + "\n".join(body) + "</div>")
    return "\n".join(text), page


def listen_link(it):
    return it.get("spotify_url") or it.get("link") or ""


def music_text(n, it):
    tags = ", ".join(it.get("tags", []))
    src = it.get("source", "")
    lines = [f"{n}. {it.get('title', '')}, by {it.get('artist', '')}" + (" (single)" if it.get("kind") == "track" else "")]
    if it.get("note"):
        lines.append(it["note"])
    meta = ". ".join(x for x in [tags and f"Tags: {tags}", src and f"Source: {src}"] if x)
    if meta:
        lines.append(meta + ".")
    if listen_link(it):
        lines.append(f"Listen: {listen_link(it)}")
    return lines + [""]


def music_html(n, it):
    e = html.escape
    single = " <span style='color:#777'>(single)</span>" if it.get("kind") == "track" else ""
    parts = [f"<p style='margin:18px 0 4px'><b>{n}. {e(it.get('title', ''))}</b>{single}<br>{e(it.get('artist', ''))}</p>"]
    if it.get("note"):
        parts.append(f"<p style='margin:4px 0'>{e(it['note'])}</p>")
    if it.get("tags"):
        parts.append(f"<p style='margin:4px 0'><i>{e(', '.join(it['tags']))}</i></p>")
    src = e(it.get("source", ""))
    if it.get("source_url") and src:
        src = f"<a style='color:#777' href='{e(it['source_url'])}'>{src}</a>"
    link = listen_link(it)
    listen = f"<a href='{e(link)}'>Listen</a>" if link else ""
    meta = " · ".join(x for x in [src, listen] if x)
    if meta:
        parts.append(f"<p style='margin:4px 0;color:#777;font-size:14px'>{meta}</p>")
    return "\n".join(parts)


def design_text(n, it):
    lines = [f"{n}. {it.get('title', '')}" + (f", {it['by']}" if it.get("by") else "")]
    if it.get("tags"):
        lines.append(", ".join(it["tags"]))
    if it.get("note"):
        lines.append(it["note"])
    if it.get("instagram"):
        lines.append("Instagram: " + ", ".join(it["instagram"]))
    shop = ", ".join(x for x in [it.get("shop", ""), it.get("price", "")] if x)
    if shop:
        lines.append(shop)
    if it.get("link"):
        lines.append(it["link"])
    return lines + [""]


def design_html(n, it):
    e = html.escape
    parts = [f"<p style='margin:22px 0 4px'><b>{n}. {e(it.get('title', ''))}</b>"
             + (f"<br>{e(it['by'])}" if it.get("by") else "") + "</p>"]
    if it.get("tags"):
        parts.append(f"<p style='margin:4px 0'><i>{e(', '.join(it['tags']))}</i></p>")
    pics = [it["art"]] if it.get("art") else []
    pics += [u for u in it.get("images", []) if u not in pics]
    if pics:
        imgs = "".join(f"<img src='{e(u)}' alt='' style='height:180px;margin:0 6px 6px 0;border-radius:4px'>" for u in pics[:3])
        parts.append(f"<div style='margin:8px 0'>{imgs}</div>")
    if it.get("note"):
        parts.append(f"<p style='margin:4px 0'>{e(it['note'])}</p>")
    if it.get("instagram"):
        handles = ", ".join(f"<a href='https://www.instagram.com/{e(h.lstrip('@'))}/'>{e(h)}</a>" for h in it["instagram"])
        parts.append(f"<p style='margin:4px 0'>{handles}</p>")
    shop = e(", ".join(x for x in [it.get("shop", ""), it.get("price", "")] if x))
    if it.get("link"):
        shop = f"{shop} · <a href='{e(it['link'])}'>{'See their work' if it.get('kind') == 'designer' else 'View'}</a>"
    if shop:
        parts.append(f"<p style='margin:4px 0;color:#777;font-size:14px'>{shop}</p>")
    return "\n".join(parts)


def subject(status):
    names = [e["name"] for e in status["lists"]]
    title = names[0] if len(names) == 1 else (list_config.load().get("email_subject") or "Weekly Lists")
    broken = [e for e in status["lists"] if not e["ok"]]
    flag = " (one didn't run)" if len(broken) == 1 else (f" ({len(broken)} didn't run)" if broken else "")
    return f"{title}: {nice_date(status['date'])}{flag}"


def send(subj, text, page):
    user, password, to = (os.environ.get(k, "").strip() for k in ("SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"))
    if user and password and to:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subj, user, to
        msg.set_content(text)
        msg.add_alternative(page, subtype="html")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
        print(f"Emailed: {subj}")
        return True
    # No email set up: a GitHub issue, which GitHub emails to the repo owner.
    md = text + (f"\n\nRun: {RUN_URL}" if RUN_URL else "")
    r = subprocess.run(["gh", "issue", "create", "--title", subj, "--body", md],
                       capture_output=True, text=True)
    print(r.stdout or r.stderr)
    return r.returncode == 0


def main():
    status = load(WEEKLY / "status.json", None)
    if not status or not status.get("lists"):
        print("Nothing ran, so no email.")
        return 0
    spotify = load(WEEKLY / "spotify.json", [])
    archive = load(ROOT / "site" / "data" / "archive.json", {})
    text, page = build(status, spotify, archive)
    subj = subject(status)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(f"## {subj}\n\n```\n{text}\n```\n")
    if os.environ.get("DRY_RUN") == "true":
        print(subj, "\n", text)
        Path(WEEKLY / "email.html").write_text(page)
        return 0
    return 0 if send(subj, text, page) else 1


if __name__ == "__main__":
    sys.exit(main())
