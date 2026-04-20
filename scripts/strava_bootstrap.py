"""One-time Strava OAuth bootstrap.

Produces a long-lived `refresh_token` that the main app uses to pull activities
without re-prompting. Refresh tokens don't expire unless you revoke the app,
so this script should only be needed once.

Usage:

    python scripts/strava_bootstrap.py

Prerequisites:

1. Register an API app at https://www.strava.com/settings/api.
   Set **Authorization Callback Domain** to exactly ``localhost`` (no port, no
   scheme). Strava validates the callback domain before redirecting.
2. Copy the Client ID and Client Secret; paste them when this script prompts
   (or pre-set STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET in .env).

Flow:
- Start a one-shot HTTP server on localhost:8000 to catch the redirect.
- Open the Strava authorize URL in your default browser.
- You click Authorize in Strava.
- Strava redirects to http://localhost:8000/callback?code=…
- We exchange the code for an access+refresh token pair.
- Refresh token is written back to .env under STRAVA_REFRESH_TOKEN.
"""

from __future__ import annotations

import http.server
import os
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> None:  # type: ignore[misc]
        return None


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

CALLBACK_PORT = 8000
CALLBACK_PATH = "/callback"
CALLBACK_URL = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"
SCOPE = "activity:read_all"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"


class _Catcher(http.server.BaseHTTPRequestHandler):
    # Class attributes so we can read the result after handle_request() returns.
    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler dispatch uses this casing
        parsed = urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return
        q = parse_qs(parsed.query)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        if "code" in q:
            _Catcher.code = q["code"][0]
            body = (
                "<!doctype html><meta charset='utf-8'>"
                "<h1>Autoriseerimine õnnestus ✅</h1>"
                "<p>Võid selle akna sulgeda. Terminalis jätkab skript refresh_tokeni vahetust.</p>"
            )
        else:
            _Catcher.error = q.get("error", ["tundmatu"])[0]
            body = (
                "<!doctype html><meta charset='utf-8'>"
                "<h1>Autoriseerimine ebaõnnestus ❌</h1>"
                f"<p>Viga: {_Catcher.error}</p>"
            )
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args, **kwargs) -> None:  # silence the default access log
        return


def _prompt(label: str, existing: str | None, *, secret: bool = False) -> str:
    if existing:
        print(f"{label}: (loetud .env-ist)")
        return existing
    if secret:
        import getpass
        return getpass.getpass(f"{label}: ").strip()
    return input(f"{label}: ").strip()


def _write_env(key: str, value: str) -> None:
    lines: list[str] = []
    if ENV_FILE.exists():
        lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    updated = False
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            updated = True
            break
    if not updated:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    load_dotenv()

    client_id = _prompt("STRAVA_CLIENT_ID", os.getenv("STRAVA_CLIENT_ID"))
    client_secret = _prompt(
        "STRAVA_CLIENT_SECRET", os.getenv("STRAVA_CLIENT_SECRET"), secret=True
    )

    if not client_id or not client_secret:
        print("client_id ja client_secret on mõlemad kohustuslikud.", file=sys.stderr)
        return 1

    try:
        server = http.server.HTTPServer(("localhost", CALLBACK_PORT), _Catcher)
    except OSError as exc:
        print(
            f"Ei saa kuulata localhost:{CALLBACK_PORT} — sulge muu teenus või vaheta port. {exc}",
            file=sys.stderr,
        )
        return 1

    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    auth_url = (
        f"{AUTHORIZE_URL}"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&redirect_uri={CALLBACK_URL}"
        f"&approval_prompt=force"
        f"&scope={SCOPE}"
    )
    print()
    print("Avan brauseri autoriseerimiseks...")
    print(f"  Kui brauser ei avane, ava käsitsi: {auth_url}")
    print("  Strava app'i callback-domeen peab olema täpselt: localhost")
    print()
    webbrowser.open(auth_url)

    thread.join(timeout=300)
    server.server_close()

    if _Catcher.error:
        print(f"Autoriseerimine ebaõnnestus: {_Catcher.error}", file=sys.stderr)
        return 1
    if not _Catcher.code:
        print("Ei saanud autoriseerimise koodi (aegus või katkestati).", file=sys.stderr)
        return 1

    print("Kood saadud, vahetan refresh_tokeni vastu...")
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _Catcher.code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if not response.ok:
        print(f"Token-vahetus ebaõnnestus: {response.status_code} {response.text}", file=sys.stderr)
        return 1
    token = response.json()

    refresh_token = token["refresh_token"]
    _write_env("STRAVA_CLIENT_ID", client_id)
    _write_env("STRAVA_CLIENT_SECRET", client_secret)
    _write_env("STRAVA_REFRESH_TOKEN", refresh_token)

    athlete = token.get("athlete", {}) or {}
    first = athlete.get("firstname", "")
    last = athlete.get("lastname", "")
    print()
    print(f"✅ Sportlane: {first} {last}".rstrip())
    print(f"✅ STRAVA_REFRESH_TOKEN salvestatud {ENV_FILE}-i.")
    print("Käivita Streamlit uuesti (preview_stop + preview_start) — nüüd ilmub sidebari 'Strava API' valik.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
