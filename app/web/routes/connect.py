"""Safe HTTPS redirect route for VPN client deep-link buttons."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.bot.utils.formatting import CONNECT_APP_SCHEMES, verify_connect_signature

router = APIRouter(tags=["connect"])


@router.get("/connect/{app_slug}")
async def redirect_to_vpn_client(
    app_slug: str,
    request: Request,
    url: str = Query(..., min_length=8, max_length=2048),
    sig: str = Query(..., min_length=16, max_length=128),
) -> RedirectResponse:
    """Safely redirect HTTPS Telegram button click to allowlisted VPN client deep link."""
    scheme_entry = CONNECT_APP_SCHEMES.get(app_slug)
    if scheme_entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unsupported client app")

    clean_url = url.strip()
    if not (clean_url.startswith("https://") or clean_url.startswith("http://")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid subscription URL")
    if any(ch in clean_url for ch in ("\r", "\n", " ", "<", ">", '"')):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsafe characters in URL")

    settings = request.app.state.settings
    secret = settings.BOT_TOKEN.get_secret_value()
    if not verify_connect_signature(secret, app_slug, clean_url, sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid redirect signature")

    _, template = scheme_entry
    deep_link = template.format(url=clean_url)
    return RedirectResponse(url=deep_link, status_code=status.HTTP_302_FOUND)
