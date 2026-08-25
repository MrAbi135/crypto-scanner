"""API Spec §8, §9 and §10."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from scanner.interfaces.api.query import (
    CURSOR_TTL,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    CursorCodec,
    FilterOp,
    QueryRejectedError,
    SortKey,
    parse_filters,
    parse_limit,
    parse_sort,
)

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
SECRET = "a-signing-secret-of-entirely-sufficient-length"

GRADES = frozenset({FilterOp.EQ, FilterOp.IN})
SCORES = frozenset({FilterOp.GTE, FilterOp.LTE})

ALLOWED = {"grade": GRADES, "archetype": GRADES, "confidence": SCORES}


# ------------------------------------------------------------------ cursors


def test_a_cursor_round_trips() -> None:
    codec = CursorCodec(SECRET)

    token = codec.encode({"published_at": "2026-08-25T00:00:00Z", "id": "s-1"}, now=NOW)

    assert codec.decode(token, now=NOW) == {
        "published_at": "2026-08-25T00:00:00Z",
        "id": "s-1",
    }


def test_a_cursor_is_opaque_in_the_sense_that_matters() -> None:
    """§8 says opaque and signed, not encrypted.

    The position is the row's own sort key, which the client already has.
    Signing is what stops it inventing one; hiding it would buy nothing. So
    this asserts the *signature* binds, not that the payload is unreadable.
    """
    codec = CursorCodec(SECRET)

    token = codec.encode({"id": "s-1"}, now=NOW)
    body, _, signature = token.partition(".")

    assert body and signature
    # Same body, signature from a different key: refused.
    forged = f"{body}.{CursorCodec('a-different-secret-of-sufficient-length')._sign(body)}"

    assert codec.decode(forged, now=NOW) is None


@pytest.mark.parametrize(
    "token",
    ["", "no-separator", ".", "abc.", ".xyz", "!!!.???", "YWJj.YWJj"],
)
def test_a_malformed_cursor_is_refused_without_saying_why(token: str) -> None:
    """One answer for tampered, malformed and expired.

    A caller who could tell them apart would learn whether a forgery had the
    right shape.
    """
    assert CursorCodec(SECRET).decode(token, now=NOW) is None


def test_a_cursor_expires_after_twenty_four_hours() -> None:
    codec = CursorCodec(SECRET)

    token = codec.encode({"id": "s-1"}, now=NOW)

    assert codec.decode(token, now=NOW + CURSOR_TTL - timedelta(minutes=1)) is not None
    assert codec.decode(token, now=NOW + CURSOR_TTL + timedelta(minutes=1)) is None


def test_a_cursor_carries_no_padding() -> None:
    """`=` in a query string is legal and invites a client to re-encode it.

    A re-encoded cursor is a different string, and the signature would fail on
    a cursor nobody tampered with.
    """
    token = CursorCodec(SECRET).encode({"id": "s-1", "at": "2026-08-25"}, now=NOW)

    assert "=" not in token


def test_the_cursor_signature_is_domain_separated_from_the_token_secret() -> None:
    """One secret, two purposes — resolved by signing different messages.

    Without the prefix, a value signed as one could be presented as the other.
    Asserted by showing a bare HMAC over the same body does not verify.
    """
    import base64
    import hashlib
    import hmac

    codec = CursorCodec(SECRET)

    token = codec.encode({"id": "s-1"}, now=NOW)
    body, _, _ = token.partition(".")

    bare = (
        base64.urlsafe_b64encode(hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())
        .decode()
        .rstrip("=")
    )

    assert codec.decode(f"{body}.{bare}", now=NOW) is None


# ------------------------------------------------------------------- limits


def test_the_limit_defaults_and_bounds() -> None:
    assert parse_limit(None) == DEFAULT_LIMIT
    assert parse_limit("1") == 1
    assert parse_limit(str(MAX_LIMIT)) == MAX_LIMIT


@pytest.mark.parametrize("raw", ["0", "-1", "201", "10000", "abc", "1.5", ""])
def test_a_limit_outside_the_range_is_refused_not_clamped(raw: str) -> None:
    """Clamping teaches a client that its page size was respected.

    The first time that matters is when someone paginates by assuming the
    count they asked for.
    """
    with pytest.raises(QueryRejectedError) as raised:
        parse_limit(raw)

    assert raised.value.field == "limit"


# ------------------------------------------------------------------ filters


def test_the_four_filter_forms_parse() -> None:
    found = parse_filters(
        {
            "filter[grade]": "A",
            "filter[archetype][in]": "A1,A4",
            "filter[confidence][gte]": "70",
            "filter[confidence][lte]": "95",
            # Not a filter; must be ignored rather than rejected.
            "limit": "50",
        },
        allowed=ALLOWED,
    )

    by_key = {(f.field, f.op): f.values for f in found}

    assert by_key[("grade", FilterOp.EQ)] == ("A",)
    assert by_key[("archetype", FilterOp.IN)] == ("A1", "A4")
    assert by_key[("confidence", FilterOp.GTE)] == ("70",)
    assert by_key[("confidence", FilterOp.LTE)] == ("95",)


def test_an_unknown_filter_field_is_refused() -> None:
    """§9: "unknown filter fields ⇒ 422 SEMANTIC_REJECTION, never silent
    ignoring: a filter the server didn't apply is a lie the client believes"."""

    with pytest.raises(QueryRejectedError, match="unknown filter field: colour"):
        parse_filters({"filter[colour]": "red"}, allowed=ALLOWED)


def test_a_known_field_with_an_unsupported_operator_is_refused() -> None:
    """The closed set is per field *and* per operator.

    `confidence[in]` and `grade[gte]` are both nonsense, and accepting either
    and ignoring it is the same lie.
    """
    with pytest.raises(QueryRejectedError, match="does not support"):
        parse_filters({"filter[grade][gte]": "A"}, allowed=ALLOWED)

    with pytest.raises(QueryRejectedError, match="does not support"):
        parse_filters({"filter[confidence][in]": "70,80"}, allowed=ALLOWED)


def test_an_unknown_operator_is_refused() -> None:
    with pytest.raises(QueryRejectedError, match="unknown filter operator: like"):
        parse_filters({"filter[grade][like]": "A%"}, allowed=ALLOWED)


@pytest.mark.parametrize("key", ["filter[grade", "filter[", "filter[]x"])
def test_a_malformed_filter_key_is_refused(key: str) -> None:
    with pytest.raises(QueryRejectedError):
        parse_filters({key: "A"}, allowed=ALLOWED)


def test_an_empty_in_list_is_refused() -> None:
    """`filter[grade][in]=` matches nothing, and an endpoint would return an
    empty page that looks like an answer."""

    with pytest.raises(QueryRejectedError, match="no value"):
        parse_filters({"filter[grade][in]": ",,"}, allowed=ALLOWED)


def test_no_filters_is_not_an_error() -> None:
    assert parse_filters({"limit": "50"}, allowed=ALLOWED) == ()


# -------------------------------------------------------------------- sorts


DEFAULT_SORT = (SortKey("published_at", descending=True),)
SORTABLE = frozenset({"published_at", "confidence", "grade"})


def test_sorting_parses_direction_and_order() -> None:
    keys = parse_sort(
        "-confidence,grade",
        allowed=SORTABLE,
        default=DEFAULT_SORT,
    )

    assert keys == (SortKey("confidence", descending=True), SortKey("grade"))
    # §10: "applied left-to-right".
    assert [str(k) for k in keys] == ["-confidence", "grade"]


def test_the_default_sort_applies_when_none_is_given() -> None:
    assert parse_sort(None, allowed=SORTABLE, default=DEFAULT_SORT) == DEFAULT_SORT


def test_an_unknown_sort_field_is_refused() -> None:
    with pytest.raises(QueryRejectedError, match="unknown sort field: colour"):
        parse_sort("colour", allowed=SORTABLE, default=DEFAULT_SORT)


def test_a_repeated_sort_field_is_refused() -> None:
    """`?sort=grade,-grade` has no meaning, and picking one is a guess."""

    with pytest.raises(QueryRejectedError, match="repeated"):
        parse_sort("grade,-grade", allowed=SORTABLE, default=DEFAULT_SORT)


def test_a_sort_parameter_with_no_fields_is_refused() -> None:
    """Falling back to the default would be §12's silent tolerance."""

    with pytest.raises(QueryRejectedError, match="no fields"):
        parse_sort(",,", allowed=SORTABLE, default=DEFAULT_SORT)


def test_a_rank_ordered_collection_rejects_any_sort() -> None:
    """§10: rank-ordered resources "fix their sort ... client sort parameters
    are rejected there rather than silently overridden".

    A client sort on a ranking does not merely reorder the page — it reorders
    a *ranking*, and the position numbers beside the rows become wrong.
    Refused even for a field that would otherwise be sortable.
    """
    with pytest.raises(QueryRejectedError, match="rank-ordered"):
        parse_sort("confidence", allowed=SORTABLE, default=DEFAULT_SORT, fixed=True)

    # And the fixed order is still what a caller gets without one.
    assert parse_sort(None, allowed=SORTABLE, default=DEFAULT_SORT, fixed=True) == DEFAULT_SORT


# ------------------------------------------------- the 422 the parsers raise


def test_a_query_rejection_becomes_a_422_with_the_field_named() -> None:
    """§7's envelope, §12(4)'s "field-precise details".

    Registered as an app-level handler rather than caught per endpoint: the
    parsers raise from inside a dependency, and an endpoint that forgot the
    `try` would turn a caller's typo into a 500 — which reads as our bug and
    hides theirs.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from scanner.interfaces.api.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/probe")
    def probe() -> dict[str, str]:
        parse_sort("colour", allowed=SORTABLE, default=DEFAULT_SORT)

        return {"unreachable": "yes"}

    response = TestClient(app, raise_server_exceptions=False).get("/probe")

    assert response.status_code == 422

    body = response.json()

    assert body["error"]["code"] == "SEMANTIC_REJECTION"
    assert body["error"]["details"][0]["field"] == "sort"
    assert "colour" in body["error"]["details"][0]["message"]
    # §7: every error carries one, so a client can quote it in a bug report.
    assert body["error"]["correlation_id"]
