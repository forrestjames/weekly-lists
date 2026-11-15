#!/usr/bin/env python3
"""
Second half of the weekly run: takes the picks files Claude wrote (one per
list, in incoming/) and puts each one where the site and Spotify expect it.

  python scripts/publish.py LISTS_JSON DATE

LISTS_JSON is the list of ids that were due, e.g. '["rob","juno"]'.
Writes .weekly/status.json, which the email step reads, and prints the
music picks files saved (space separated) for the Spotify step.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lists as list_config  # noqa: E402

ROOT = list_config.ROOT
INCOMING = ROOT / "incoming"
STATUS = ROOT / ".weekly" / "status.json"


def main(due_json, day):
    due = json.loads(due_json)
    status, music_files = [], []
    for lid in due:
        item = list_config.get(lid)
        entry = {"id": lid, "name": item["name"], "type": item["type"], "ok": False}
        src = INCOMING / f"{lid}.json"
        if not src.exists():
            entry["problem"] = "the curation step didn't produce any picks"
            status.append(entry)
            continue
        try:
            data = json.loads(src.read_text())
        except json.JSONDecodeError as e:
            entry["problem"] = f"the picks file wasn't valid JSON ({e})"
            status.append(entry)
            continue
        entry["intro"] = (data.pop("email_intro", "") or "").strip()
        data["list"] = lid
        data["date"] = data.get("date") or day
        dest = ROOT / list_config.folder(item) / f"{data['date']}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        rel = str(dest.relative_to(ROOT))
        entry.update(ok=True, file=rel, count=len(data.get("items", [])))
        if item["type"] == "music":
            music_files.append(rel)
        status.append(entry)

    STATUS.parent.mkdir(exist_ok=True)
    STATUS.write_text(json.dumps({"date": day, "lists": status}, indent=2))
    for e in status:
        print(f"{e['id']}: {'saved ' + e['file'] if e['ok'] else 'FAILED, ' + e['problem']}", file=sys.stderr)
    print(" ".join(music_files))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
