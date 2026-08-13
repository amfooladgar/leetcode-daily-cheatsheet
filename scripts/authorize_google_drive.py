#!/usr/bin/env python3
"""One-time local script: obtains a Google Drive OAuth refresh token for
this pipeline to use headlessly (GitHub Actions, or any unattended run).

Why this exists instead of a service account: see ARCHITECTURE.md "Why
OAuth instead of a service account" — a service account has no Drive
storage quota of its own and cannot upload file *content* into a personal
(non-Google-Workspace) Drive folder, only create empty folders. This script
runs the interactive OAuth consent flow ONCE, in your own browser, on your
own machine, and prints a long-lived refresh token that
src/storage/google_drive.py then uses non-interactively forever after
(until you revoke it).

Usage (see docs/SETUP.md step 3 for how to get a client ID/secret first).
Either set GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET in your .env
file (this script loads it automatically, same as src/main.py's main()),
or export them directly:

    pip install google-auth-oauthlib   # one-time, only needed for this script
    export GOOGLE_OAUTH_CLIENT_ID=...
    export GOOGLE_OAUTH_CLIENT_SECRET=...
    python scripts/authorize_google_drive.py

A browser window opens, you sign in as the Google account that owns (or
has Editor access to) the Drive folder you want to archive into, and
approve access. The script then prints GOOGLE_OAUTH_REFRESH_TOKEN — save
that alongside the client ID/secret as env vars (.env, locally) or GitHub
Actions secrets (CI). This script itself does not need to run again unless
you revoke access or rotate the client secret.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_SCOPES = ["https://www.googleapis.com/auth/drive"]
_REPO_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")  # no-op if the file doesn't exist
    except ImportError:
        pass  # python-dotenv ships with the pipeline's own requirements.txt;
        # if it's missing in whatever environment runs this standalone
        # script, just fall through to already-exported env vars below.

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "google-auth-oauthlib is not installed. This is a one-time,\n"
            "setup-only dependency (not needed by the pipeline itself):\n\n"
            "    pip install google-auth-oauthlib\n",
            file=sys.stderr,
        )
        return 1

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET first\n"
            "(see docs/SETUP.md step 3 for how to create an OAuth Client ID\n"
            "of type 'Desktop app' in Google Cloud Console).",
            file=sys.stderr,
        )
        return 1

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, scopes=_SCOPES)
    print("Opening your browser for one-time Google Drive authorization...")
    print("Sign in as the account that owns (or has Editor access to) the")
    print("Drive folder you want this pipeline to archive into.\n")
    creds = flow.run_local_server(port=0)

    if not creds.refresh_token:
        print(
            "No refresh_token was returned. This usually means you've\n"
            "authorized this app before and Google only issues a\n"
            "refresh_token on first consent. Revoke prior access at\n"
            "https://myaccount.google.com/permissions and re-run this\n"
            "script.",
            file=sys.stderr,
        )
        return 1

    print("\nSuccess. Save this as GOOGLE_OAUTH_REFRESH_TOKEN")
    print("(in .env locally, and as a GitHub Actions secret for CI):\n")
    print(creds.refresh_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
