"""QR payload + image generation (Plan #9, #10).

Payload is an immutable reference only — never the full record:
    <FRONTEND_URL>/a/<asset_uuid>   (preferred)
    <FRONTEND_URL>/asset/<asset_code>
"""

from __future__ import annotations

import io
import os
from urllib.parse import urlparse

# QR payload format version (Phase 3): bumped when the payload shape changes
# so scanners can detect stale codes. Returned in GET /api/v1/assets/<ref>.
QR_VERSION = 1


def frontend_base_url() -> str:
    return (os.getenv("FRONTEND_URL") or "http://localhost:3000").rstrip("/")


def frontend_host() -> str:
    """Lower-cased host[:port] of the site whose QR payloads we mint."""
    return urlparse(frontend_base_url()).netloc.lower()


class ForeignHostError(ValueError):
    """Scanned URL points at a host other than this installation."""


def qr_payload(asset_uuid: str | None = None, asset_code: str | None = None) -> str:
    base = frontend_base_url()
    if asset_uuid:
        return f"{base}/a/{asset_uuid}"
    if asset_code:
        return f"{base}/asset/{asset_code}"
    raise ValueError("asset_uuid or asset_code is required")


def parse_qr_payload(payload: str, allowed_hosts: set[str] | None = None) -> dict:
    """Decode a scanned QR string into {asset_uuid} or {asset_code}.

    Absolute URLs are accepted only when their host is one this
    installation trusts — by default FRONTEND_URL's host, plus whatever the
    caller passes (e.g. the request host). Any other host raises
    :class:`ForeignHostError` so a sticker minted by a different deployment
    is rejected instead of being resolved here. Bare refs (UUID / asset
    code) need no host and always pass.
    """
    text = (payload or "").strip()
    if not text:
        raise ValueError("Empty QR payload")
    # Raw UUID or code pasted manually
    if "/" not in text and " " not in text:
        if len(text) >= 32 and text.replace("-", "").isalnum():
            return {"asset_uuid": text} if "-" in text else {"asset_code": text}
        return {"asset_code": text}
    try:
        parsed = urlparse(text)
    except ValueError:
        raise ValueError("Unrecognized QR payload") from None
    if parsed.netloc:
        allowed = {frontend_host()} if allowed_hosts is None else {h.lower() for h in allowed_hosts}
        allowed.discard("")
        if parsed.netloc.lower() not in allowed:
            raise ForeignHostError(f"QR payload host not allowed: {parsed.netloc}")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[-2] in {"a", "asset", "assets"}:
        ref = parts[-1]
        if parts[-2] == "a":
            return {"asset_uuid": ref}
        return {"asset_code": ref}
    # Fallback: last segment is the code
    if parts:
        return {"asset_code": parts[-1]}
    raise ValueError("Unrecognized QR payload")


def qr_png_bytes(payload: str, size: int | None = None) -> bytes:
    """PNG of the payload. ``size`` requests the edge length in pixels
    (clamped to 128–2048, rounded down to whole QR modules); omit for the
    library default box size."""
    import qrcode

    if size is None:
        img = qrcode.make(payload)
    else:
        edge = max(128, min(2048, int(size)))
        qr = qrcode.QRCode(border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        # Whole modules only: image = (modules_count + 2*border) * box_size,
        # never above the requested edge.
        total = qr.modules_count + 2 * qr.border
        qr.box_size = max(1, edge // total)
        img = qr.make_image()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_svg_text(payload: str) -> str:
    import qrcode
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgImage
    img = qrcode.make(payload, image_factory=factory)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8", "ignore")
