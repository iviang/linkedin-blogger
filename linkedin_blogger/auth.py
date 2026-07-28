"""LinkedIn OAuth 2.0 (three-legged) for a local script.

The member has to grant access once in a browser. We capture the redirect on a
short-lived localhost server, exchange the code for tokens, and cache them. Member
access tokens are long lived (about 60 days) but not permanent, so we also store the
refresh token when the app is enabled for it and refresh transparently on expiry.
"""

import json
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from . import config

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"


def _load_tokens():
    if config.TOKENS_FILE.exists():
        return json.loads(config.TOKENS_FILE.read_text(encoding="utf-8"))
    return {}


def _save_tokens(tokens: dict):
    # Stamp an absolute expiry so we can decide when to refresh without guessing.
    if "expires_in" in tokens:
        tokens["expires_at"] = int(time.time()) + int(tokens["expires_in"])
    config.TOKENS_FILE.write_text(json.dumps(tokens, indent=2), encoding="utf-8")


class _CallbackHandler(BaseHTTPRequestHandler):
    """Captures the single OAuth redirect, then the server shuts down."""

    code = None
    state = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.code = params.get("code", [None])[0]
        _CallbackHandler.state = params.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"LinkedIn authorization received. You can close this tab.")

    def log_message(self, *args):
        # Silence the default per-request stderr logging.
        pass


def login():
    """Run the full browser consent flow and cache the resulting tokens."""
    config.require("LINKEDIN_CLIENT_ID", config.LINKEDIN_CLIENT_ID)
    config.require("LINKEDIN_CLIENT_SECRET", config.LINKEDIN_CLIENT_SECRET)

    # A random state value guards against the callback being forged.
    state = str(int(time.time()))
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": config.LINKEDIN_CLIENT_ID,
            "redirect_uri": config.LINKEDIN_REDIRECT_URI,
            "state": state,
            "scope": config.LINKEDIN_SCOPES,
        }
    )
    auth_link = f"{AUTH_URL}?{query}"

    parsed_redirect = urllib.parse.urlparse(config.LINKEDIN_REDIRECT_URI)
    host = parsed_redirect.hostname or "localhost"
    port = parsed_redirect.port or 8000

    print("Opening LinkedIn authorization in your browser.")
    print(f"If it does not open, paste this URL manually:\n{auth_link}\n")
    webbrowser.open(auth_link)

    server = HTTPServer((host, port), _CallbackHandler)
    # handle_request blocks until exactly one request (the redirect) arrives.
    server.handle_request()
    server.server_close()

    if not _CallbackHandler.code:
        raise SystemExit("No authorization code received. Try the login flow again.")
    if _CallbackHandler.state != state:
        raise SystemExit("State mismatch on the OAuth callback. Aborting for safety.")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.code,
            "redirect_uri": config.LINKEDIN_REDIRECT_URI,
            "client_id": config.LINKEDIN_CLIENT_ID,
            "client_secret": config.LINKEDIN_CLIENT_SECRET,
        },
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    print("Authorized. Tokens saved to tokens.json.")
    return tokens


def _refresh(tokens: dict):
    """Use the refresh token to get a new access token. Only works if the app has
    member-token refresh enabled; otherwise the member must log in again."""
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise SystemExit("Access token expired and no refresh token is available. Run: python blogger.py login")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": config.LINKEDIN_CLIENT_ID,
            "client_secret": config.LINKEDIN_CLIENT_SECRET,
        },
        timeout=30,
    )
    resp.raise_for_status()
    new_tokens = resp.json()
    # LinkedIn may not return a fresh refresh token, so keep the old one if absent.
    new_tokens.setdefault("refresh_token", refresh_token)
    _save_tokens(new_tokens)
    return new_tokens


def get_access_token() -> str:
    """Return a valid access token, refreshing if it is close to expiry."""
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        raise SystemExit("Not authorized yet. Run: python blogger.py login")

    # Refresh a minute early to avoid racing the expiry boundary.
    if tokens.get("expires_at", 0) <= int(time.time()) + 60:
        tokens = _refresh(tokens)
    return tokens["access_token"]


def get_member_urn() -> str:
    """Return the author URN (urn:li:person:{id}) required by the Posts API.

    Cached in state.json after the first lookup because it never changes for a member.
    """
    state = {}
    if config.STATE_FILE.exists():
        state = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
    if state.get("member_urn"):
        return state["member_urn"]

    token = get_access_token()
    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    # OpenID Connect returns the member id as `sub`.
    member_id = resp.json()["sub"]
    urn = f"urn:li:person:{member_id}"

    state["member_urn"] = urn
    config.STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return urn
