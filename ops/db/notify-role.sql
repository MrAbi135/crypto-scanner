-- The interim notifier's role: read two tables, nothing else.
--
-- INTERIM. This role exists only for `ops/notify/`, which is deleted when the
-- S18 alert engine ships (DEVELOPMENT_ROADMAP.md, Sprint S18). Drop the role
-- in the same change: `DROP ROLE scanner_notify;`.
--
-- Why a third role rather than reusing one that exists:
--
-- * `scanner` is the table owner and a SUPERUSER (rolsuper = t). Running an
--   unattended per-minute cron as it would give a notification script the
--   ability to drop the sealed signal record, disable the migration-018
--   append-only triggers, or read `identity.users`. The hourly invariants
--   check runs as `scanner` under a human's eye; a forever-loop is not that.
--
-- * `scanner_app` is least-privilege for the *application*, which is a wider
--   job than this one: it holds DELETE and UPDATE on `detection.setups` and
--   can read every schema. The notifier needs neither.
--
-- The grant is also how the notifier's contract is enforced. `ops/notify/`
-- must never push a candidate that skipped the §15.3 publication gates, and
-- the table behind `GET /api/v1/rankings` is `detection.setups` — below-floor
-- rows included, by its own design. A python-side path whitelist states that
-- intention; this states the capability. Postgres answers "permission denied"
-- to a mistake the whitelist could only ask nicely about.
--
-- No password is set, deliberately. The container's pg_hba.conf is
-- `local all all trust` for the unix socket and `scram-sha-256` for host
-- connections, so a passwordless role is reachable by `docker exec ... psql`
-- and by nothing on the network.
--
-- Run as the owner (`scanner`), after migrations. Idempotent. Carries no
-- secret. One file, read by two things: the operator applying it and the
-- integration test that attacks it
-- (backend/tests/integration/test_notify_role_pg.py). A second copy of this
-- SQL in either place would be a second definition, free to drift.
--
-- The role itself is created separately, once, exactly as `scanner_app` is
-- (docs/runbooks/deploy-p1b.md). Postgres has no CREATE ROLE IF NOT EXISTS and
-- the DO block that emulates one contains semicolons, which would split wrong
-- in every reader that runs this file statement by statement. So the file
-- holds grants, and creation is the operator's line:
--
--     CREATE ROLE scanner_notify
--         LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
--
-- No password, deliberately — see the pg_hba note above.

GRANT USAGE ON SCHEMA detection TO scanner_notify;

GRANT SELECT ON
    detection.signals,
    detection.signal_transitions
TO scanner_notify;

-- §10.1 caps push at "Tier 1-3", and the tier lives nowhere else: it is not in
-- the sealed §15.2 payload, and two symbols in the scanned set are INELIGIBLE
-- (LISTAUSDT, LITEBUSDT, present to prove G1b's unselected-symbol criterion).
-- Without this the notifier would push them.
--
-- Hardcoding the eligible symbols instead was considered and rejected: the
-- daily universe job promotes and demotes tiers every midnight, so a frozen
-- list is wrong within weeks and wrong silently.
--
-- COLUMN-SCOPED on purpose. `market.symbols` also carries liquidity figures,
-- wash-risk state and the delisting record, none of which this job needs.
-- A notifier that could read them would eventually be asked to report them,
-- and then the role would be the API. Three columns, and `USAGE` on the
-- schema buys nothing else: every other table in `market` stays unreadable
-- because none of them is granted below.
GRANT USAGE ON SCHEMA market TO scanner_notify;

GRANT SELECT (exchange_symbol, tier, status)
    ON market.symbols
TO scanner_notify;

-- No ALTER DEFAULT PRIVILEGES. `least-privilege-role.sql` grants defaults so a
-- new application table does not break the API; the opposite is wanted here.
-- A table added by a future migration must stay unreadable to the notifier
-- until someone names it above, on purpose.
