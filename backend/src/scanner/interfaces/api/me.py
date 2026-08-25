"""§18.2's profile group, for the one row S10-minimal implements.

`GET /me` is "the SPA bootstrap call" — the first request S13's terminal makes
after login, to learn who it is talking as.

**What §18.2 asks for and this does not return: plan, capabilities, usage
meters.** Those are TAD §21's entitlement layer, which S10-minimal does not
build — there are no plans, so there is nothing to resolve. The fields are
absent rather than present-and-empty: an empty `capabilities` list reads as "no
capabilities", which a client would correctly render as a locked interface, and
`ENTITLEMENTS_ENFORCED` already says in one place that nothing is being
enforced. A missing field is a question; an empty one is a wrong answer.

The other §18.2 rows — profile update, email change, password change, linked
channels, export, delete — are deferred with the rest of S10: the first three
need the verified-email flow, channels need the Telegram bot (S18), and export
and delete are the GDPR workflows.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from scanner.application.ports.identity import UserRepository
from scanner.interfaces.api.deps import get_accounts
from scanner.interfaces.api.errors import not_found
from scanner.interfaces.api.security import CurrentUser, require_user

router = APIRouter(prefix="/api/v1/me", tags=["profile"])


class Profile(BaseModel):
    """Identity only. See the module docstring for what is deliberately absent."""

    user_id: str
    tenant_id: str
    email: str
    role: str
    # Stated in the payload rather than left for a client to infer from the
    # missing plan: a terminal that unlocks everything should be able to say
    # *why*, and the day entitlements land this flips rather than appears.
    entitlements_enforced: bool = False


@router.get("", response_model=Profile)
async def get_profile(
    request: Request,
    caller: Annotated[CurrentUser, Depends(require_user)],
    accounts: Annotated[object, Depends(get_accounts)],
) -> Profile:
    """The caller's own record, read back from the database.

    Read rather than reflected from the token. The token is a signed snapshot
    from up to fifteen minutes ago; this row is what a client renders as "you",
    and showing a stale email after a change would be a small lie that is hard
    to notice.

    It also means a token for an account that has since been deleted gets a
    404 here instead of a cheerful profile.
    """
    users: UserRepository = accounts.users  # type: ignore[attr-defined]

    user = await users.get(caller.user_id)

    if user is None or not user.can_authenticate:
        raise not_found(request, "No such account.")

    return Profile(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        email=user.email,
        role=user.role,
    )
