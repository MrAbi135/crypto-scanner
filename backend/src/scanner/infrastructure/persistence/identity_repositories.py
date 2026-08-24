"""PostgreSQL persistence for T20 tenants and T21 users."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from scanner.application.ports.identity import TenantRecord, UserRecord
from scanner.infrastructure.persistence.identity_models import TenantRow, UserRow


def _tenant(row: TenantRow) -> TenantRecord:
    return TenantRecord(
        tenant_id=row.tenant_id,
        name=row.name,
        status=row.status,
        created_at=row.created_at,
    )


def _user(row: UserRow) -> UserRecord:
    return UserRecord(
        user_id=row.user_id,
        tenant_id=row.tenant_id,
        email=row.email,
        password_hash=row.password_hash,
        role=row.role,
        status=row.status,
        created_at=row.created_at,
        deleted_at=row.deleted_at,
    )


class PgTenantRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert(self, tenant: TenantRecord) -> bool:
        stmt = (
            pg_insert(TenantRow)
            .values(
                tenant_id=tenant.tenant_id,
                name=tenant.name,
                status=tenant.status,
                created_at=tenant.created_at,
            )
            .on_conflict_do_nothing(index_elements=[TenantRow.tenant_id])
            .returning(TenantRow.tenant_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def get(self, tenant_id: str) -> TenantRecord | None:
        async with self._sessions() as session:
            row = await session.get(TenantRow, tenant_id)

        return _tenant(row) if row is not None else None


class PgUserRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(self, user: UserRecord) -> bool:
        """Insert once. False when the email or id is already taken.

        `ON CONFLICT DO NOTHING` with no `index_elements`, so it covers both
        the primary key and the unique email index. Naming one would let a
        collision on the other surface as a driver error, and the caller
        cannot tell that from a database being down.
        """
        stmt = (
            pg_insert(UserRow)
            .values(
                user_id=user.user_id,
                tenant_id=user.tenant_id,
                email=user.email,
                password_hash=user.password_hash,
                role=user.role,
                status=user.status,
                created_at=user.created_at,
                deleted_at=user.deleted_at,
            )
            .on_conflict_do_nothing()
            .returning(UserRow.user_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None

    async def get_by_email(self, email: str) -> UserRecord | None:
        stmt = select(UserRow).where(UserRow.email == email)

        async with self._sessions() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()

        return _user(row) if row is not None else None

    async def get(self, user_id: str) -> UserRecord | None:
        async with self._sessions() as session:
            row = await session.get(UserRow, user_id)

        return _user(row) if row is not None else None

    async def list_all(self) -> tuple[UserRecord, ...]:
        stmt = select(UserRow).order_by(UserRow.created_at.asc(), UserRow.user_id.asc())

        async with self._sessions() as session:
            rows = (await session.execute(stmt)).scalars().all()

        return tuple(_user(row) for row in rows)

    async def set_password_hash(self, user_id: str, password_hash: str) -> bool:
        stmt = (
            update(UserRow)
            .where(UserRow.user_id == user_id)
            .values(password_hash=password_hash)
            .returning(UserRow.user_id)
        )

        async with self._sessions() as session:
            written = (await session.execute(stmt)).scalar_one_or_none()

            await session.commit()

            return written is not None
