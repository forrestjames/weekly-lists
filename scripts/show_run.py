#!/usr/bin/env python3
"""Prints Claude's messages and any blocked actions from a weekly run's log,
so a run that went wrong is easy to read in the Actions page."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    events = json.load(open(path))
except Exception as e:
    sys.exit(f"No run log to show ({e})")
for ev in events if isinstance(events, list) else [events]:
    if ev.get("type") == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "text" and block["text"].strip():
                print("CLAUDE:", block["text"].strip()[:1500])
            elif block.get("type") == "tool_use":
                print("TOOL:", block.get("name"), json.dumps(block.get("input", {}))[:300])
    elif ev.get("type") == "result":
        for d in ev.get("permission_denials") or []:
            print("BLOCKED:", json.dumps(d)[:500])
        print("RESULT:", str(ev.get("result", ""))[:1500])
