#!/usr/bin/env python3
"""One-time local script: obtains a LinkedIn access token (and your person
URN) for this pipeline's two human-gated posting paths to use — see
ARCHITECTURE.md "LinkedIn posting" and src/storage/linkedin.py.

Why this exists instead of an official SDK: LinkedIn does not publish a
first-party Python SDK, so this implements its 3-legged OAuth 2.0
authorization-code flow by hand, using only the standard library's
`http.server` for the local redirect catcher plus `requests` and
`webbrowser` (both already dependencies of this repo). This script runs the
interactive consent flow ONCE, in your own browser, on your own machine, and
prints an access token + person URN that src/storage/linkedin.py then uses
non-interactively.

Unlike scripts/authorize_google_drive.py's refresh token (which doesn't
expire on its own), LinkedIn's access token expires after ~60 days. There is
no unattended refresh mechanism — re-run this script to get a fresh token
when it expires. This is intentional and consistent with both LinkedIn
posting paths requiring a human in the loop (see CLAUDE.md's "Rules").

Usage (see docs/SETUP.md step 3c for how to create a LinkedIn developer app
first). Either set LINKEDIN_CLIENT_ID/LINKEDIN_CLIENT_SECRET in your .env
file (this script loads it automatically, same as src/main.py's main()), or
export them directly:

    export LINKEDIN_CLIENT_ID=...
    export LINKEDIN_CLIENT_SECRET=...
    python scripts/authorize_linkedin.py

A browser window opens, you sign in and approve access, and the script
prints LINKEDIN_ACCESS_TOKEN and LINKEDIN_PERSON_URN — save those as env
vars (.env, locally) or GitHub Actions secrets are NOT needed since neither
LinkedIn posting path ever runs in CI (see CLAUDE.md's "Rules").
"""

from __future__ import annotations

import http.server
import os
import secrets
import sys
import urllib.parse
import webbrowser
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REDIRECT_URI = "http://localhost:8765/callback"
_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
_SCOPE = "openid profile w_member_social"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Captures the `code`/`state` query params from LinkedIn's redirect,
    stores them on the server instance, then tells the browser it can be
    closed. One request is all this ever needs to handle."""

    def do_GET(self) -> None:  # noqa: N802 - http.server's naming convention
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.callback_code = params.get("code", [None])[0]
        self.server.callback_state = params.get("state", [None])[0]
        self.server.callback_error = params.get("error_description", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><body>You can close this tab and return to the terminal.</body></html>"
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - silence default access logging
        pass


def _await_callback() -> tuple[str | None, str | None, str | None]:
    server = http.server.HTTPServer(("localhost", 8765), _CallbackHandler)
    server.callback_code = None
    server.callback_state = None
    server.callback_error = None
    server.handle_request()  # blocks for exactly one request, then returns
    server.server_close()
    return server.callback_code, server.callback_state, server.callback_error


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")  # no-op if the file doesn't exist
    except ImportError:
        pass  # python-dotenv ships with the pipeline's own requirements.txt;
        # if it's missing in whatever environment runs this standalone
        # script, just fall through to already-exported env vars below.

    import requests

    client_id = os.environ.get("LINKEDIN_CLIENT_ID")
    client_secret = os.environ.get("LINKEDIN_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Set LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET first (see\n"
            "docs/SETUP.md step 3c for how to create a LinkedIn developer\n"
            "app and find these on its Auth tab). These are setup-only\n"
            "values -- not read by the pipeline itself.",
            file=sys.stderr,
        )
        return 1

    state = secrets.token_urlsafe(16)
    auth_url = f"{_AUTH_URL}?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": _REDIRECT_URI,
            "scope": _SCOPE,
            "state": state,
        }
    )

    print("Opening your browser for one-time LinkedIn authorization...")
    print("Sign in and approve access for the app you created in docs/SETUP.md step 3c.\n")
    webbrowser.open(auth_url)

    code, returned_state, error = _await_callback()

    if error:
        print(f"LinkedIn returned an error: {error}", file=sys.stderr)
        return 1
    if not code:
        print("No authorization code was returned. Try again.", file=sys.stderr)
        return 1
    if returned_state != state:
        print(
            "State mismatch on the OAuth redirect (possible CSRF) -- refusing to "
            "proceed. Try again.",
            file=sys.stderr,
        )
        return 1

    try:
        token_resp = requests.post(
            _TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _REDIRECT_URI,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        token_resp.raise_for_status()
        token_payload = token_resp.json()
        access_token = token_payload["access_token"]
        expires_in = token_payload.get("expires_in")
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"Token exchange failed: {exc}", file=sys.stderr)
        return 1

    try:
        userinfo_resp = requests.get(
            _USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        userinfo_resp.raise_for_status()
        member_id = userinfo_resp.json()["sub"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        print(f"Looking up your member ID failed: {exc}", file=sys.stderr)
        return 1

    days = f"{expires_in // 86400}" if expires_in else "~60"
    print(f"\nSuccess. Token expires in ~{days} days. Save these as:")
    print("(in .env locally -- no GitHub Actions secret needed, this never runs in CI)\n")
    print(f"LINKEDIN_ACCESS_TOKEN={access_token}")
    print(f"LINKEDIN_PERSON_URN=urn:li:person:{member_id}")
    print(
        "\nRe-run this script when the token expires -- there is no unattended "
        "refresh (both LinkedIn posting paths are human-gated by design)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
