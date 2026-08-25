"""API Spec §8, §9 and §10: pagination, filtering and sorting, in one grammar.

Built once and shared, because the spec's rules are only worth anything if
every endpoint obeys the same ones. Three of them do real work:

* **"unknown filter fields ⇒ 422, never silent ignoring: a filter the server
  didn't apply is a lie the client believes"** (§9). A closed set per endpoint,
  enforced here.
* **"OFFSET pagination does not exist in this API"** (§8). There is no `offset`
  parameter to accidentally support.
* **rank-ordered resources "fix their sort ... client sort parameters are
  rejected there rather than silently overridden"** (§10). A `fixed` sort is a
  refusal, not a default.

One rule this module *cannot* enforce and each endpoint must: §9's "filters
narrow only — nothing filterable can surface below-floor or suppressed
content". That is a property of the query the endpoint builds, not of the
grammar it parses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

# §8: "Default `limit` 50, max 200".
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# §8: "cursors are opaque, signed, and expire after 24 h".
CURSOR_TTL = timedelta(hours=24)


class FilterOp(str, Enum):
    """§9's four forms. Anything else is not in the grammar."""

    EQ = "eq"
    IN = "in"
    GTE = "gte"
    LTE = "lte"


@dataclass(frozen=True, slots=True)
class Filter:
    field: str
    op: FilterOp
    # Always a tuple, even for EQ. A caller that had to check the op before
    # knowing whether it held one value or several would get it wrong once.
    values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SortKey:
    field: str
    descending: bool = False

    def __str__(self) -> str:
        return f"-{self.field}" if self.descending else self.field


class QueryRejectedError(Exception):
    """A §9/§10 violation: the caller asked for something not on the menu.

    Carries the field so the endpoint can build §7's field-precise `details`.
    Raised rather than returned because every call site would otherwise have
    to remember to check, and the one that forgets serves an unfiltered list.
    """

    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)

        self.message = message
        self.field = field


@dataclass(frozen=True, slots=True)
class CursorCodec:
    """§8's opaque, signed, expiring cursor.

    **Signed, not encrypted.** The position it carries is a row's own sort key
    — a timestamp and an id the client already has. Signing stops a client
    inventing a position and walking somebody else's ordering; hiding it would
    buy nothing and cost a key-management story.

    **Domain-separated from the access-token secret it shares.** One secret,
    two purposes is a real smell; a second required environment variable for a
    pagination cursor is friction nobody will thank us for. HMAC over a
    prefixed message is the standard resolution: a cursor can never be
    presented as a token or the reverse, because the signatures are over
    different inputs.
    """

    secret: str

    _PREFIX = "scanner-cursor-v1"

    def encode(self, position: Mapping[str, str], *, now: datetime) -> str:
        payload = json.dumps(
            {"p": dict(position), "iat": int(now.timestamp())},
            sort_keys=True,
            separators=(",", ":"),
        )

        body = _b64(payload.encode("utf-8"))

        return f"{body}.{self._sign(body)}"

    def decode(self, raw: str, *, now: datetime) -> dict[str, str] | None:
        """The position, or None for anything wrong with the cursor.

        One answer for tampered, malformed and expired. A caller that could
        tell them apart would be telling a client whether its forgery had the
        right shape.
        """
        body, _, signature = raw.partition(".")

        if not body or not signature:
            return None

        if not hmac.compare_digest(signature, self._sign(body)):
            return None

        try:
            payload = json.loads(_unb64(body))
            issued = datetime.fromtimestamp(payload["iat"], tz=now.tzinfo)
            position = payload["p"]
        except (ValueError, KeyError, TypeError, OSError):
            return None

        if not isinstance(position, dict):
            return None

        if now - issued > CURSOR_TTL:
            return None

        return {str(k): str(v) for k, v in position.items()}

    def _sign(self, body: str) -> str:
        message = f"{self._PREFIX}.{body}".encode()

        return _b64(hmac.new(self.secret.encode("utf-8"), message, hashlib.sha256).digest())


def parse_limit(raw: str | None) -> int:
    """§8's limit, clamped by refusal rather than silently.

    A limit of 5,000 quietly served as 200 teaches a client that its page size
    is respected, and the first time it matters is when someone paginates by
    assuming the count they asked for.
    """
    if raw is None:
        return DEFAULT_LIMIT

    try:
        value = int(raw)
    except ValueError:
        raise QueryRejectedError("limit must be an integer", field="limit") from None

    if value < 1:
        raise QueryRejectedError("limit must be at least 1", field="limit")

    if value > MAX_LIMIT:
        raise QueryRejectedError(f"limit must be at most {MAX_LIMIT}", field="limit")

    return value


def parse_filters(
    params: Mapping[str, str],
    *,
    allowed: Mapping[str, frozenset[FilterOp]],
) -> tuple[Filter, ...]:
    """§9's grammar over the raw query parameters.

    `allowed` is the endpoint's closed set: field to the operators that field
    supports. A field absent from it, or present with an operator it does not
    support, is a 422 — §9 is explicit that silently ignoring either "is a lie
    the client believes".
    """
    found: list[Filter] = []

    for key, raw in params.items():
        if not key.startswith("filter["):
            continue

        field, op = _split_filter_key(key)

        if field not in allowed:
            raise QueryRejectedError(
                f"unknown filter field: {field}",
                field=key,
            )

        if op not in allowed[field]:
            raise QueryRejectedError(
                f"filter field {field} does not support the {op.value} operator",
                field=key,
            )

        values = tuple(v for v in raw.split(",") if v) if op is FilterOp.IN else (raw,)

        if not values:
            raise QueryRejectedError(f"filter {field} has no value", field=key)

        found.append(Filter(field=field, op=op, values=values))

    return tuple(found)


def parse_sort(
    raw: str | None,
    *,
    allowed: frozenset[str],
    default: tuple[SortKey, ...],
    fixed: bool = False,
) -> tuple[SortKey, ...]:
    """§10's sort, or a refusal.

    `fixed=True` is for the rank-ordered resources §10 names — `/rankings` and
    the signal feed. Their order is SLS §9.2's deterministic ranking, and a
    client sort would not merely reorder the page, it would reorder a *ranking*
    and make the numbers beside the rows wrong. §10 says to reject rather than
    silently override, so a sort parameter there is a 422 even when the field
    would otherwise be sortable.
    """
    if raw is None or not raw.strip():
        return default

    if fixed:
        raise QueryRejectedError(
            "this collection is rank-ordered and does not accept a sort parameter",
            field="sort",
        )

    keys: list[SortKey] = []

    for token in raw.split(","):
        token = token.strip()

        if not token:
            continue

        descending = token.startswith("-")
        field = token[1:] if descending else token

        if field not in allowed:
            raise QueryRejectedError(f"unknown sort field: {field}", field="sort")

        if any(k.field == field for k in keys):
            raise QueryRejectedError(f"sort field repeated: {field}", field="sort")

        keys.append(SortKey(field=field, descending=descending))

    # `?sort=` with only separators. Falling back to the default would be the
    # silent tolerance §12 forbids.
    if not keys:
        raise QueryRejectedError("sort was given with no fields", field="sort")

    return tuple(keys)


def _split_filter_key(key: str) -> tuple[str, FilterOp]:
    """`filter[grade]` or `filter[grade][in]` into its parts."""

    inner = key[len("filter[") :]

    if not inner.endswith("]"):
        raise QueryRejectedError(f"malformed filter parameter: {key}", field=key)

    inner = inner[:-1]

    if "][" not in inner:
        return inner, FilterOp.EQ

    field, _, op_name = inner.partition("][")

    try:
        return field, FilterOp(op_name)
    except ValueError:
        raise QueryRejectedError(
            f"unknown filter operator: {op_name}",
            field=key,
        ) from None


def _b64(raw: bytes) -> str:
    # Unpadded url-safe: a cursor travels in a query string, and `=` there is
    # legal but invites a client to re-encode it and change the signature.
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(raw: str) -> bytes:
    return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
