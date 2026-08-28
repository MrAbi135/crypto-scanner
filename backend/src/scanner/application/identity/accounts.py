"""Creating and authenticating an account.

**Registration is not here, and that is the scope decision.** §18.1's
`POST /auth/register` "triggers verification email (≤ 60 s)", and the roadmap
says plainly that "a registration flow without email is untestable". There is
no transactional email provider chosen for this deployment and choosing one is
not a code decision. So the account is provisioned by CLI — `scanner users
create` — which is a real, exercisable path for a single-operator instance, and
`/auth/register` lands with the email adapter.

`authenticate` returns one value for every failure. The endpoint above it maps
that to §18.1's `AUTH_REQUIRED`, which the spec marks "deliberately same code,
no user-enumeration".
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from scanner.application.identity.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
)
from scanner.application.ports.identity import (
    TenantRecord,
    TenantRepository,
    UserRecord,
    UserRepository,
)

DEFAULT_TENANT_ID = "default"

# Every account created by this build. Roles above `user` exist in T21's check
# constraint for staff flows that S10-minimal does not implement; minting one
# here would create a privileged account no code path can currently police.
DEFAULT_ROLE = "user"


def fold_email(email: str) -> str:
    """T21's "unique, case-folded", in the one place that decides it.

    NFKC first: `ﬁ` and `fi` are the same address to a mail server and two
    different strings to Postgres, so folding case alone would let one address
    hold two accounts. `casefold` rather than `lower` because `lower` leaves
    `ß` alone while `casefold` maps it to `ss`, and a German address should not
    get a second account for free.

    Whitespace is stripped, not rejected: a pasted address carries a trailing
    space far more often than a user means one.
    """
    return unicodedata.normalize("NFKC", email).strip().casefold()


def user_id_for(email: str) -> str:
    """Derived from the folded email, so the same address is the same id.

    A random id would let a replayed create make a second row for one address
    — the unique index would refuse it, but the caller could not tell that
    from a database error. Deriving it makes the collision explicit.
    """
    return hashlib.sha256(fold_email(email).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class AccountService:
    users: UserRepository
    tenants: TenantRepository

    async def ensure_default_tenant(self, *, now: datetime) -> TenantRecord:
        """One tenant, created on demand.

        T20 exists because the platform is multi-tenant by design; this build
        has one operator. Creating the row rather than making `tenant_id`
        nullable keeps the foreign key real, so the day tenancy matters
        nothing has to be backfilled.
        """
        existing = await self.tenants.get(DEFAULT_TENANT_ID)

        if existing is not None:
            return existing

        tenant = TenantRecord(
            tenant_id=DEFAULT_TENANT_ID,
            name="default",
            status="ACTIVE",
            created_at=now,
        )

        await self.tenants.upsert(tenant)

        return tenant

    async def create(self, email: str, password: str, *, now: datetime) -> UserRecord | None:
        """Provision one account. None when the email is already taken.

        Raises `PasswordPolicyError` for a password that cannot be accepted —
        that is a caller mistake worth a message, unlike a login failure.
        """
        await self.ensure_default_tenant(now=now)

        folded = fold_email(email)

        user = UserRecord(
            user_id=user_id_for(folded),
            tenant_id=DEFAULT_TENANT_ID,
            email=folded,
            password_hash=hash_password(password),
            role=DEFAULT_ROLE,
            status="ACTIVE",
            created_at=now,
        )

        if not await self.users.create(user):
            return None

        return user

    async def set_password(self, email: str, password: str) -> UserRecord | None:
        """Replace an account's password. None when there is no such account.

        Separate from `create`, which refuses an address that already exists --
        and rightly, because silently overwriting a live account's credential
        on a re-run is how a provisioning script locks its owner out. This is
        the deliberate version of the same act.

        Raises `PasswordPolicyError` for a password that cannot be accepted,
        like `create`: the same policy and the same hashing, because a password
        set here has to be one `authenticate` will take.

        **It does not revoke sessions, and the caller must.** The service owns
        no session store, and a method that silently left them standing would
        be the more dangerous shape -- so the omission is stated here and the
        CLI does the revoking where the store is in hand.
        """
        user = await self.users.get_by_email(fold_email(email))

        if user is None:
            return None

        # Hashed before the write and outside the repository, so a policy
        # refusal happens before anything is stored.
        if not await self.users.set_password_hash(user.user_id, hash_password(password)):
            return None

        return user

    async def authenticate(self, email: str, password: str) -> UserRecord | None:
        """The credential check. None for every failure, without saying which.

        The hash is verified even when no user matched. Skipping it would make
        a miss return in microseconds and a hit in the ~50 ms Argon2 costs,
        which is a timing oracle for whether an address is registered — the
        exact enumeration §18.1 is written to prevent.
        """
        user = await self.users.get_by_email(fold_email(email))

        if user is None:
            verify_password(password, _DUMMY_HASH)

            return None

        if not verify_password(password, user.password_hash):
            return None

        if not user.can_authenticate:
            return None

        if needs_rehash(user.password_hash):
            # The password is known correct at this point, so this is the only
            # moment a cost increase can be applied without a reset. Failure
            # to store it must not fail the login -- the credential is valid
            # either way.
            await self.users.set_password_hash(user.user_id, hash_password(password))

        return user


# A real Argon2 hash of a value nobody holds, so the no-such-user path spends
# the same time as the wrong-password path. Computed once at import: doing it
# per call would itself be slower than a verify and reintroduce the skew.
_DUMMY_HASH = hash_password("timing-equalizer-not-a-credential")
