-- DDD layer (a): the application role cannot rewrite the sealed signal record.
--
-- DDD asks for three layers on detection.signals, signal_transitions and
-- signal_outcomes: (a) no UPDATE grants to the application role, (b) trigger
-- guards, (c) hash seals. Migration 018 installs (b); (c) is in the code. (a)
-- cannot be installed by a migration, because migrations run as the owner and
-- an owner's privileges cannot be revoked from itself -- so it lives here, and
-- is applied to a second role the application connects as.
--
-- One file, read by two things: the operator applying it to a real database
-- (docs/runbooks/deploy-p1b.md#least-privilege-role) and the integration test
-- that attacks it (backend/tests/integration/test_grant_layer_pg.py). A second
-- copy of this SQL in either place would be a second definition of the layer,
-- free to drift from the one that was tested.
--
-- Run as the owner (`scanner`), AFTER migrations. Idempotent. Carries no
-- secret: the role and its password are created separately.
--
-- Every schema the application reads or writes must appear below. `identity`
-- arrived in migration 019 and is easy to miss -- the first draft of the
-- runbook's SQL granted only detection, market and ops, and a cut-over on that
-- version would have left the API unable to read a single user.

GRANT USAGE ON SCHEMA detection, market, ops, identity TO scanner_app;

GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA detection, market, ops, identity TO scanner_app;

-- The point of the exercise: the crown jewels are insert-and-read only.
REVOKE UPDATE, DELETE, TRUNCATE ON
  detection.signals,
  detection.signal_transitions,
  detection.signal_outcomes
FROM scanner_app;

-- Tables added later inherit the broad grant, so re-run the REVOKE above
-- whenever a migration adds an immutable table. Default privileges cannot
-- express "everything except these three".
ALTER DEFAULT PRIVILEGES IN SCHEMA detection, market, ops, identity
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO scanner_app;
