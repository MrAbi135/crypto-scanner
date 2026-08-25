"""TAD §20's access token: "access JWT (≤15 min)".

**HS256, not RS256.** Asymmetric signing exists so a verifier can check a token
without being able to mint one — which matters when several services verify
and one issues. Here `api` is both, in one process. RS256 would add key
management and rotation for a separation that does not exist yet, and the
symmetric secret is one env var the deployment already knows how to carry. The
day a second service verifies these, this is the thing to change, and the
`algorithms=` list below is where.

**Fifteen minutes is a ceiling, not a target.** The access token cannot be
revoked — that is what makes it fast, and what makes its lifetime the true
blast radius of a revoked session until TAD §20's Redis bitmap exists. Piece 2
said the same from the other side: a revoked family is refused on its next
refresh, and until then any live access token still works.

**The token carries identity, not entitlements.** TAD §21 allows entitlement
claims as a fast path with the Redis record as truth. There are no entitlements
in S10-minimal, and putting an empty claim in would create a field that reads
as "no capabilities" rather than "not implemented".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError

ACCESS_TTL = timedelta(minutes=15)

_ALGORITHM = "HS256"
_ISSUER = "scanner"
_AUDIENCE = "scanner-api"

# Below this a secret is guessable enough that signing is theatre. 32 bytes of
# entropy, hex- or base64-encoded, lands comfortably above it.
MIN_SECRET_LENGTH = 32


class AccessTokenSecretError(ValueError):
    """The signing secret is missing or too short to be one."""


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """What a verified access token asserts."""

    user_id: str
    tenant_id: str
    session_id: str
    role: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class AccessTokens:
    """Mint and verify access tokens against one secret.

    A class rather than module functions so the secret is supplied once at
    composition and cannot be read from the environment deep inside a request
    — which is how a process ends up verifying against a secret nobody
    configured.
    """

    secret: str

    def __post_init__(self) -> None:
        if len(self.secret) < MIN_SECRET_LENGTH:
            raise AccessTokenSecretError(
                f"access-token secret must be at least {MIN_SECRET_LENGTH} "
                f"characters; got {len(self.secret)}"
            )

    def mint(
        self,
        *,
        user_id: str,
        tenant_id: str,
        session_id: str,
        role: str,
        now: datetime,
    ) -> str:
        """One access token, expiring `ACCESS_TTL` from `now`.

        `session_id` travels in the token so a verified request knows which
        family authorised it. Without it, "revoke this session" could not be
        enforced on anything but the refresh path even once the bitmap exists.
        """
        return jwt.encode(
            {
                "sub": user_id,
                "tid": tenant_id,
                "sid": session_id,
                "role": role,
                "iss": _ISSUER,
                "aud": _AUDIENCE,
                "iat": int(now.timestamp()),
                "exp": int((now + ACCESS_TTL).timestamp()),
            },
            self.secret,
            algorithm=_ALGORITHM,
        )

    def verify(self, token: str, *, now: datetime) -> AccessClaims | None:
        """Claims, or None. Never an exception, and never a reason.

        The algorithm is pinned to a one-element list. Accepting whatever the
        header declares is the `alg: none` family of attacks, and passing
        `algorithms=None` to PyJWT is how that happens by accident.

        Issuer and audience are verified for the same reason: a token minted
        by some other system with the same secret should not authenticate
        here, and a token minted for a different audience of *this* system
        should not either.
        """
        try:
            payload = jwt.decode(
                token,
                self.secret,
                algorithms=[_ALGORITHM],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={
                    "require": ["sub", "tid", "sid", "role", "exp", "iat"],
                    # **Every time-based check here is off, and the injected
                    # `now` is the only authority.**
                    #
                    # Each identity path in this codebase takes `now` as an
                    # argument -- sessions, revocation, rotation. Any check
                    # PyJWT performs uses the system clock instead, so the
                    # token layer and the session layer would disagree about
                    # whether the same moment had passed.
                    #
                    # `exp` was turned off first and `iat` was missed, which
                    # cost an hour: a token minted at an injected time *ahead*
                    # of the wall clock was refused as issued in the future,
                    # and the symptom was a bare 401 with no reason. Both are
                    # still *required* to be present; what changes is which
                    # clock reads them. `iat` is carried for audit and is not
                    # gated -- there is one process, so a token from its own
                    # future is a clock bug, not an attack.
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except InvalidTokenError:
            return None

        expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)

        # The only expiry check, and it runs on the caller's clock. See the
        # `verify_exp` note above.
        if expires_at <= now:
            return None

        return AccessClaims(
            user_id=payload["sub"],
            tenant_id=payload["tid"],
            session_id=payload["sid"],
            role=payload["role"],
            expires_at=expires_at,
        )
