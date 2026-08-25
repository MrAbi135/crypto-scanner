"""TAD §20's access token, and whose clock decides."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scanner.application.identity.tokens import (
    ACCESS_TTL,
    AccessTokens,
    AccessTokenSecretError,
)

SECRET = "a-signing-secret-of-entirely-sufficient-length"
NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


def tokens(secret: str = SECRET) -> AccessTokens:
    return AccessTokens(secret)


def mint(at: datetime = NOW, *, secret: str = SECRET) -> str:
    return tokens(secret).mint(
        user_id="u-1",
        tenant_id="default",
        session_id="s-1",
        role="user",
        now=at,
    )


def test_a_token_round_trips_and_carries_its_session() -> None:
    """`sid` travels so a verified request knows which family authorised it.

    Without it, "revoke this session" could not be enforced on anything but
    the refresh path even once TAD §20's bitmap exists.
    """
    claims = tokens().verify(mint(), now=NOW)

    assert claims is not None
    assert (claims.user_id, claims.tenant_id, claims.session_id) == ("u-1", "default", "s-1")
    assert claims.expires_at == NOW + ACCESS_TTL


def test_the_injected_clock_is_the_only_authority_on_time() -> None:
    """The bug this test exists for cost an hour.

    Every identity path here takes `now` as an argument. PyJWT validates `exp`,
    `iat` and `nbf` against the *system* clock, so a token minted at an
    injected time ahead of the wall clock was refused as issued in the future
    — and the symptom was a bare 401 with no reason, because the verifier
    deliberately reports nothing.

    `exp` was turned off first and `iat` was missed. Pinned at an injected time
    far in the future *and* far in the past, so neither direction can regress.
    """
    far_future = datetime(2099, 1, 1, tzinfo=UTC)
    far_past = datetime(2001, 1, 1, tzinfo=UTC)

    assert tokens().verify(mint(far_future), now=far_future) is not None
    assert tokens().verify(mint(far_past), now=far_past) is not None


def test_a_token_expires_against_the_injected_clock() -> None:
    token = mint()

    assert tokens().verify(token, now=NOW + ACCESS_TTL - timedelta(seconds=1)) is not None
    assert tokens().verify(token, now=NOW + ACCESS_TTL) is None
    assert tokens().verify(token, now=NOW + ACCESS_TTL + timedelta(hours=1)) is None


def test_a_token_signed_with_another_key_is_refused() -> None:
    forged = mint(secret="a-different-secret-of-quite-sufficient-length")

    assert tokens().verify(forged, now=NOW) is None


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "eyJhbGciOiJub25lIn0..", "..."])
def test_a_malformed_token_is_refused_without_a_reason(token: str) -> None:
    """One answer for every failure: a caller who could tell them apart could
    probe the token format."""

    assert tokens().verify(token, now=NOW) is None


def test_the_algorithm_is_pinned() -> None:
    """`alg: none` and algorithm confusion, in one assertion.

    A token the caller declares unsigned must not verify, whatever its header
    says.
    """
    import jwt

    unsigned = jwt.encode(
        {
            "sub": "u-1",
            "tid": "default",
            "sid": "s-1",
            "role": "user",
            "iss": "scanner",
            "aud": "scanner-api",
            "iat": int(NOW.timestamp()),
            "exp": int((NOW + ACCESS_TTL).timestamp()),
        },
        key="",
        algorithm="none",
    )

    assert tokens().verify(unsigned, now=NOW) is None


def test_a_token_for_another_audience_or_issuer_is_refused() -> None:
    """A token minted by some other system with the same secret is not ours."""

    import jwt

    for claim in ({"aud": "someone-else"}, {"iss": "someone-else"}):
        stranger = jwt.encode(
            {
                "sub": "u-1",
                "tid": "default",
                "sid": "s-1",
                "role": "user",
                "iss": "scanner",
                "aud": "scanner-api",
                "iat": int(NOW.timestamp()),
                "exp": int((NOW + ACCESS_TTL).timestamp()),
                **claim,
            },
            SECRET,
            algorithm="HS256",
        )

        assert tokens().verify(stranger, now=NOW) is None


@pytest.mark.parametrize("missing", ["sub", "tid", "sid", "role", "exp", "iat"])
def test_a_token_missing_a_required_claim_is_refused(missing: str) -> None:
    """Turning off PyJWT's time checks must not turn off its *presence* checks.

    A token with no `exp` would otherwise verify forever.
    """
    import jwt

    claims = {
        "sub": "u-1",
        "tid": "default",
        "sid": "s-1",
        "role": "user",
        "iss": "scanner",
        "aud": "scanner-api",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + ACCESS_TTL).timestamp()),
    }

    del claims[missing]

    assert tokens().verify(jwt.encode(claims, SECRET, algorithm="HS256"), now=NOW) is None


@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_a_secret_too_short_to_be_one_is_refused_at_construction(secret: str) -> None:
    """Below 32 characters, signing is theatre.

    Raised at construction so the process dies at boot rather than issuing
    forgeable tokens.
    """
    with pytest.raises(AccessTokenSecretError):
        AccessTokens(secret)
