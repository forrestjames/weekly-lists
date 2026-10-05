#!/usr/bin/env python3
"""
One-time Spotify setup for the weekly lists.

Run this once on your Mac:  python3 get_token.py

It will:
  1. Ask for your app's Client ID and Client Secret
  2. Open Spotify in your browser so you can click Agree
  3. Print your refresh token (the long-lasting key)
  4. Find "Rob's List, Weekly" and create "Juno Weekly" if it doesn't exist
  5. Print the five values to paste into GitHub

Nothing is saved to disk. Uses only the Python that ships with macOS.
"""

import base64
import getpass
import http.server
import json
import secrets
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REDIRECT_URI = "http://127.0.0.1:8888/callback"
SCOPES = "playlist-read-private playlist-modify-private playlist-modify-public"
ROB_NAME = "Rob's List, Weekly"
JUNO_NAME = "Juno Weekly"


def request(method, url, token=None, data=None, headers=None, form=False):
    headers = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        sys.exit(f"\nSpotify said no ({e.code}): {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        if isinstance(e.reason, ssl.SSLError):
            sys.exit(
                "\nYour Python can't verify secure connections yet.\n"
                "Fix: open Finder > Applications > Python 3.x and double-click\n"
                "'Install Certificates.command', then run this script again.\n"
                "Or run it with Apple's Python instead: /usr/bin/python3 get_token.py"
            )
        raise


def wait_for_code(expected_state):
    result = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "All done. You can close this tab and go back to Terminal."
            if "error" in result:
                msg = f"Spotify returned an error: {result['error']}. Check Terminal."
            self.wfile.write(
                f"<html><body style='font-family:sans-serif;padding:40px'>"
                f"<h2>{msg}</h2></body></html>".encode()
            )

        def log_message(self, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", 8888), Handler)
    except OSError:
        sys.exit("\nSomething else is using port 8888. Close it and try again.")
    while "code" not in result and "error" not in result:
        server.handle_request()
    server.server_close()

    if "error" in result:
        sys.exit(f"\nSpotify returned an error: {result['error']}")
    if result.get("state") != expected_state:
        sys.exit("\nSecurity check failed (state mismatch). Please run it again.")
    return result["code"]


def all_my_playlists(token):
    url = "https://api.spotify.com/v1/me/playlists?limit=50"
    while url:
        page = request("GET", url, token)
        for p in page.get("items", []):
            if p:
                yield p
        url = page.get("next")


def main():
    print("\nSpotify setup for the weekly lists\n")
    client_id = input("Paste your Client ID: ").strip()
    client_secret = getpass.getpass("Paste your Client Secret (hidden as you type): ").strip()
    if not client_id or not client_secret:
        sys.exit("Need both values. Find them in your app's Settings on the Spotify dashboard.")
    if client_secret == client_id:
        sys.exit("\nThe secret you pasted is the Client ID. Click 'View client secret' and copy that one.")
    if len(client_secret) != 32:
        sys.exit(
            f"\nThat secret is {len(client_secret)} characters long, but Spotify secrets are 32.\n"
            "It was probably pasted twice or only partly copied. Run the script again\n"
            "and paste it just once (nothing shows as you type, that's normal)."
        )
    print(f"Secret looks right, ending in ...{client_secret[-4:]}")

    state = secrets.token_urlsafe(16)
    auth_url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
        "show_dialog": "true",
    })

    print("\nOpening Spotify in your browser. Log in if asked, then click Agree.")
    print("If nothing opens, copy this link into your browser:\n")
    print(auth_url + "\n")
    webbrowser.open(auth_url)

    code = wait_for_code(state)

    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    tokens = request(
        "POST",
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
        headers={"Authorization": f"Basic {basic}"},
        form=True,
    )
    access = tokens["access_token"]
    refresh = tokens.get("refresh_token")
    if not refresh:
        sys.exit("\nSpotify didn't return a refresh token. Please run it again.")

    me = request("GET", "https://api.spotify.com/v1/me", access)
    print(f"\nConnected as: {me.get('display_name') or me.get('id')}")

    rob_id = juno_id = None
    for p in all_my_playlists(access):
        name = (p.get("name") or "").strip()
        if name == ROB_NAME and not rob_id:
            rob_id = p["id"]
        if name == JUNO_NAME and not juno_id:
            juno_id = p["id"]

    if rob_id:
        print(f"Found '{ROB_NAME}'.")
    else:
        print(f"Couldn't find '{ROB_NAME}' in your playlists. Check the name in Spotify.")

    if juno_id:
        print(f"Found '{JUNO_NAME}'.")
    else:
        created = request(
            "POST",
            "https://api.spotify.com/v1/me/playlists",
            access,
            data={
                "name": JUNO_NAME,
                "public": False,
                "description": "New releases picked from Juno every Monday.",
            },
        )
        juno_id = created["id"]
        print(f"Created '{JUNO_NAME}'.")

    print("\n" + "=" * 60)
    print("Paste these into GitHub as repository secrets")
    print("(Settings > Secrets and variables > Actions > New repository secret)")
    print("=" * 60)
    print(f"\nSPOTIFY_CLIENT_ID\n{client_id}")
    print("\nSPOTIFY_CLIENT_SECRET\n(the secret you pasted above)")
    print(f"\nSPOTIFY_REFRESH_TOKEN\n{refresh}")
    print(f"\nROB_PLAYLIST_ID\n{rob_id or '(not found)'}")
    print(f"\nJUNO_PLAYLIST_ID\n{juno_id}")
    print("\nKeep the refresh token private. It works like a password for your playlists.\n")


if __name__ == "__main__":
    main()
