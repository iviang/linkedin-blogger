"""Publish posts to the member's own feed via LinkedIn's official Posts API.

Docs: https://learn.microsoft.com/linkedin/marketing/community-management/shares/posts-api
This is the sanctioned way to post programmatically. Browser automation or scraping
would violate LinkedIn's User Agreement and risk the account, so we do not do that.
"""

from pathlib import Path

import requests

from . import auth, config

POSTS_URL = "https://api.linkedin.com/rest/posts"
IMAGES_URL = "https://api.linkedin.com/rest/images?action=initializeUpload"

_IMAGE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
}


class PublishError(Exception):
    """LinkedIn rejected the publish request or did not return a post URN."""

    def __init__(self, status_code: int | None, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _api_headers() -> dict:
    return {
        "Authorization": f"Bearer {auth.get_access_token()}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
        "LinkedIn-Version": config.LINKEDIN_API_VERSION,
    }


def _media_type(path: Path) -> str:
    media_type = _IMAGE_TYPES.get(path.suffix.lower())
    if not media_type:
        allowed = ", ".join(sorted(_IMAGE_TYPES))
        raise PublishError(None, f"Unsupported media type {path.suffix}. Use one of: {allowed}")
    return media_type


def upload_image(image_path: Path) -> str:
    """Upload an image and return its urn:li:image URN."""
    author = auth.get_member_urn()
    headers = _api_headers()

    init_body = {"initializeUploadRequest": {"owner": author}}
    resp = requests.post(IMAGES_URL, headers=headers, json=init_body, timeout=30)
    if resp.status_code >= 400:
        raise PublishError(resp.status_code, resp.text)

    value = resp.json().get("value") or {}
    upload_url = value.get("uploadUrl")
    image_urn = value.get("image")
    if not upload_url or not image_urn:
        raise PublishError(resp.status_code, f"Image upload init missing fields: {resp.text}")

    data = image_path.read_bytes()
    upload_resp = requests.put(
        upload_url,
        data=data,
        headers={"Content-Type": _media_type(image_path)},
        timeout=120,
    )
    if upload_resp.status_code >= 400:
        raise PublishError(upload_resp.status_code, upload_resp.text)

    return image_urn


def publish_post(text: str, media_path: Path | None = None, alt_text: str = "") -> str:
    """Publish a post. Returns the post URN or raises PublishError."""
    author = auth.get_member_urn()
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

    if media_path:
        image_urn = upload_image(media_path)
        media = {"id": image_urn}
        if alt_text.strip():
            media["altText"] = alt_text.strip()
        body["content"] = {"media": media}

    resp = requests.post(POSTS_URL, headers=_api_headers(), json=body, timeout=60)
    if resp.status_code >= 400:
        raise PublishError(resp.status_code, resp.text)

    urn = resp.headers.get("x-restli-id", "").strip()
    if not urn or urn == "(unknown urn)":
        raise PublishError(resp.status_code, "LinkedIn did not return a post URN in x-restli-id.")
    return urn


def publish_text_post(text: str) -> str:
    """Backward-compatible wrapper for text-only posts."""
    return publish_post(text)
