# Runbooks

Operational procedures for running and repairing the platform. Each runbook is a
numbered-step, copy-pasteable procedure with explicit exit criteria — written for
the person on call at 3am, not for the author (Roadmap §12).

## Format
Every runbook states, in order:
1. **Purpose** — what it fixes / achieves, in one sentence.
2. **Preconditions** — access, environment, and safety checks before starting.
3. **Steps** — numbered, deterministic, with expected output per step.
4. **Reading the output** — how to interpret results and exit codes.
5. **Escalation** — what to do when a step fails or the outcome is ambiguous.

## Index
| Runbook | Purpose | Owning sprint |
|---|---|---|
| [backfill.md](backfill.md) | Fill/repair the historical candle record for one series | S1 |
| _staging-provision.md_ | Provision + harden the staging host | S0.3 (planned) |
| _feed-incidents.md_ | Diagnose and recover the live ingest feed | S2 (planned) |
| _secret-rotation.md_ | Quarterly secret rotation procedure | S21 (planned) |
| _disaster-recovery.md_ | Backup restore + PITR drills (RPO ≤5m / RTO ≤60m) | S21 (planned) |

Rules: no manual production data writes (Constitution §33.2 — rerun the pipeline
after a fix); every runbook grows from a real incident or a sprint DoD, never
speculation.
