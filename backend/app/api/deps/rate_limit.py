from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from app.api.deps.auth import get_current_user, get_optional_user
from app.core.config import Settings, get_settings
from app.models.user import User
from app.services.rate_limit_service import RateLimitService


def _get_client_ip(request: Request) -> str:
    """Extract client IP from the rightmost X-Forwarded-For entry.

    On Render (and most single-hop reverse proxies), the load balancer
    appends the real client IP as the LAST entry. Taking the first entry
    would allow any client to spoof their IP by setting the header themselves
    and bypass all IP-based rate limits (CWE-290).
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ips = [ip.strip() for ip in forwarded_for.split(",")]
        return ips[-1]  # rightmost entry is set by the trusted proxy
    if request.client:
        return request.client.host
    return "unknown"


async def _check_verification_limit(
    request: Request,
    current_user: User | None = Depends(get_optional_user),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Enforce daily verification quotas before the route handler runs.
    Authenticated users get DAILY_VERIFICATIONS_USER attempts per day.
    Guests get DAILY_VERIFICATIONS_GUEST (1) and are prompted to sign up.
    Raises HTTP 429 when the quota is exceeded; fails open if Redis is down.
    """
    redis = getattr(request.app.state, "redis", None)
    rl = RateLimitService(redis)

    if current_user:
        allowed, _, reset_at = await rl.check_user(
            current_user.id, "verifications", settings.DAILY_VERIFICATIONS_USER
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Daily verification limit reached ({settings.DAILY_VERIFICATIONS_USER} per day). "
                    f"Your allowance {rl.format_reset_message(reset_at)}."
                ),
            )
    else:
        ip = _get_client_ip(request)
        allowed, _, reset_at = await rl.check_guest(
            ip, "verifications", settings.DAILY_VERIFICATIONS_GUEST
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Guest verification limit reached ({settings.DAILY_VERIFICATIONS_GUEST} per day). "
                    f"Your allowance {rl.format_reset_message(reset_at)}. "
                    "Sign up for a free account to get more daily verifications."
                ),
            )


VerificationRateLimit = Annotated[None, Depends(_check_verification_limit)]


async def _check_batch_limit(
    request: Request,
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Enforce daily batch upload quota before the route handler runs.
    Raises HTTP 429 when the quota is exceeded; fails open if Redis is down.
    """
    redis = getattr(request.app.state, "redis", None)
    rl = RateLimitService(redis)
    allowed, _, reset_at = await rl.check_user(
        current_user.id, "batch", settings.DAILY_BATCH_UPLOADS_USER
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Daily batch upload limit reached ({settings.DAILY_BATCH_UPLOADS_USER} per day). "
                f"Your allowance {rl.format_reset_message(reset_at)}."
            ),
        )


BatchRateLimit = Annotated[None, Depends(_check_batch_limit)]
