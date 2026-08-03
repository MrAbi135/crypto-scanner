# ADR-000 — Repository Conventions

- **Status:** Accepted
- **Date:** 2026-08-02
- **Deciders:** Project owner (lead engineer)
- **Governance reference:** Constitution §10–§13; TAD §30–§33; TDR §22–§25; Roadmap S0; Sprint S0.1 Implementation Guide §14–§23

## Context

Sprint S0.1 establishes the repository foundation for the Institutional AI Crypto
Scanner. The governance stack fixes the technology (TDR) and the architecture
(TAD §30 tree, §27 layering), but a set of concrete, repository-level conventions
must be recorded once so every later sprint inherits them unambiguously. This ADR
captures those conventions and the small number of additive extensions to the
TAD §30 tree that this sprint's deliverables require. It creates no application
logic; it records decisions.

## Decision

### 1. Monorepo layout (TAD §30)
We use one repository with workspaces `backend/` (Python 3.12 + uv), `frontend/`
(React 18 + TS strict + pnpm + Vite), `ops/` (compose, Caddy, monitoring,
Terraform), `docs/`, and `.github/`. The TAD §30 tree is laid down verbatim with
five **additive** extensions, none of which move or rename a TAD entry:
`scripts/`, `assets/`, `ops/docker/`, `ops/env/`, and `docs/governance/` +
`docs/setup.md`. These live within TAD latitude and are recorded here as required
by the S0.1 guide §2 provenance note.

### 2. Git branch strategy (guide §16)
Trunk-based. `main` is always green and deployable; protected (PR-only, CI
required, no force-push). Work branches are short-lived and named
`<type>/<sprint>-<slug>` with the closed type set `feat · fix · chore · docs ·
test · refactor`. Commits follow Conventional Commits (`type(scope): summary`,
scope = package area). PRs squash-merge and must carry the governance-reference
line. Releases are SemVer tags `vX.Y.Z`; `algo_version` moves independently in
code, never via git tags. No `develop` or long-lived feature branches.

### 3. Git ignore law (guide §17)
`dev.env` and decrypted secrets are ignored; `.env.example`, `uv.lock`,
`pnpm-lock.yaml`, encrypted `*.enc.env`, and curated golden datasets are
committed. Market data, DB volumes, and raw golden captures never enter git.

### 4. Naming & file conventions (guide §18, §20)
Python `snake_case` modules, `PascalCase` classes with role suffixes
(`*Repository`, `*Port`, `*Adapter`, `*Service`, `*Engine`), `SCREAMING_SNAKE`
constants. TS components `PascalCase.tsx`, everything else `camelCase.ts`, types
without an `I` prefix. Env vars `SCANNER_<AREA>_<NAME>`. Governance docs
`SCREAMING_SNAKE.md`; working docs `kebab-case.md`; ADRs `NNN-kebab-slug.md`.
`utils.py`/`helpers.ts` at package roots are forbidden (dumping-ground detector).
**Vocabulary law:** a domain term in code means exactly what the SLS defines.

### 5. Import conventions (guide §19)
Backend: absolute imports rooted at `scanner.`; ruff-enforced import order
stdlib → third-party → `scanner.shared` → `scanner.domain` →
`scanner.application` → `scanner.infrastructure`/`scanner.interfaces`; no
import-time side effects; `TYPE_CHECKING` for type-only cross-layer imports.
Frontend: path aliases `@app/@features/@entities/@services/@shared`; features
never import another feature; generated client code is imported, never edited.

### 6. Code organization (guide §21)
One concept per file (~400-line smell threshold, not a hard law). Layer content
law: domain = pure logic, application = orchestration + ports, infrastructure =
adapters with zero business rules, interfaces = thin translation, runtime =
wiring only. Code enters `shared/` only when used from ≥3 places AND is
domain-agnostic. Tests mirror source 1:1. No dead code, no commented-out blocks,
no `TODO(` without an issue reference.

### 7. Module boundaries — import-linter contracts (guide §22, TAD §27)
Six contracts govern the backend: (1) Layered, (2) Domain purity, (3) Ports
independence, (4) Runtime exclusivity, (5) Engine acyclicity, (6) Context
isolation. Frontend boundaries are enforced by eslint-plugin-boundaries. A
violating import is a build failure, not a review comment. Contracts are
configured and locally runnable now; CI activation is S0.2.

### 8. Dependency rules (guide §23)
Lockfiles are law (`uv sync --locked`, `pnpm install --frozen-lockfile`). Adding
a direct dependency requires a one-line PR justification and the TDR §25 check;
anything architectural requires its own ADR. Version floors for backend direct
deps; `main` vs `dev` groups strictly separated (production images install
`main` only). No direct HTTP/DB clients outside `infrastructure/`.

## Consequences

- Every later sprint inherits an unambiguous structure and can be reviewed
  against these conventions mechanically (pre-commit + import-linter + eslint).
- The additive tree extensions are documented, so the tree cannot drift from
  TAD §30 without a visible ADR.
- Because this repository was reconstituted around pre-existing Sprint S1 code,
  the empty package markers created in S0.1 also make the pre-existing
  import-linter contracts resolvable (e.g., `scanner.runtime`, the domain-engine
  packages) — a beneficial side effect, not a scope expansion.

## Alternatives considered

- **Polyrepo (separate backend/frontend repos):** rejected — the governance
  stack governs one codebase and frontend types are generated from backend
  OpenAPI in CI, which wants atomic cross-stack commits (guide §1).
- **`develop` + release branches (git-flow):** rejected — ceremony without value
  at solo+AI team size (guide §16); revisit only via a new ADR if the team grows.
