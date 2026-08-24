"""Argon2id, per DDD T21.

Not in `domain/`: TAD §2.3 forbids the domain importing third-party packages,
and a KDF written by hand to satisfy a layering rule would be the worst trade
in this codebase. It sits in `application/` beside the services that use it.

**Parameters are RFC 9106's second recommendation** (the memory-constrained
one): 64 MiB, three passes, one lane. The first recommendation asks for 2 GiB,
which the staging host — 12 GB total, shared with Postgres, Redis and four
services — cannot spend on a login. 64 MiB times the handful of concurrent logins
this system will ever see is affordable; 2 GiB is not, and a parameter set the
host cannot run is not security, it is an outage.

They live here as named constants rather than inside the hasher call so the
parameter registry can read them and so a change is visible in a diff. Argon2
encodes its parameters into the hash string, so raising them later re-hashes
each password on next login rather than invalidating anyone.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# RFC 9106 §4, second recommended option.
ARGON2_MEMORY_KIB = 65536
ARGON2_TIME_COST = 3
ARGON2_PARALLELISM = 1
ARGON2_HASH_LEN = 32
ARGON2_SALT_LEN = 16

# Long enough that the KDF is not the weakest part, short enough that it is not
# a denial-of-service vector: Argon2 hashes whatever it is handed, so a
# megabyte password is a megabyte of work per login attempt.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024

_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=ARGON2_HASH_LEN,
    salt_len=ARGON2_SALT_LEN,
)


class PasswordPolicyError(ValueError):
    """The password cannot be accepted. Raised on creation, never on login."""


def hash_password(password: str) -> str:
    """Hash a new password, enforcing the length bounds.

    Length only. A composition rule ("one digit, one symbol") narrows the
    search space it claims to widen and pushes people toward `Password1!`;
    NIST 800-63B dropped them for that reason.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")

    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")

    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Whether the password matches. False for every failure, never an exception.

    A caller that had to distinguish "wrong password" from "corrupt hash"
    would be one refactor away from telling the client which — and the
    difference between those two answers is a user-enumeration oracle. §18.1
    makes the same point about the login endpoint returning `AUTH_REQUIRED`
    for bad credentials "deliberately same code, no user-enumeration".
    """
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether this hash predates the current parameters.

    Argon2 stores its parameters in the hash string, so raising the cost is a
    re-hash on next successful login rather than a forced reset. Without this
    check a cost increase applies only to accounts created after it.
    """
    return _HASHER.check_needs_rehash(password_hash)
