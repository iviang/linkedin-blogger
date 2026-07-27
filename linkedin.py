"""Publish a text post to the member's own feed via LinkedIn's official Posts API.

Docs: https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api
This is the sanctioned way to post programmatically. Browser automation or scraping
would violate LinkedIn's User Agreement and risk the account, so we do not do that.
"""

import requests

import auth
import config

POSTS_URL = "https://api.linkedin.com/rest/posts"


def publish_text_post(text: str) -> str:
    """Publish `text` as a public post. Returns the created post's URN."""
    token = auth.get_access_token()
    author = auth.get_member_urn()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": config.LINKEDIN_API_VERSION,
    }
    body = {
        "author": author,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(POSTS_URL, headers=headers, json=body, timeout=30)
    if resp.status_code >= 400:
        # Surface LinkedIn's error body; it is far more useful than a bare status code.
        raise SystemExit(f"LinkedIn API error {resp.status_code}: {resp.text}")

    # On success the post URN comes back in a response header, not the (empty) body.
    return resp.headers.get("x-restli-id", "(unknown urn)")
