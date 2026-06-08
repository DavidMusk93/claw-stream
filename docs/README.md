# claw-stream Documentation

> Domain-organized knowledge base. Future development experiences should be auto-archived here.

---

## `docs/design/` — Architecture & Design

System architecture, module design, and algorithm specifications.

| Document | Content |
|----------|---------|
| [architecture.md](design/architecture.md) | System architecture, playback flow, state machine, component interaction, troubleshooting |
| [cache-architecture.md](design/cache-architecture.md) | Cache module architecture (first principle: smooth playback), lifecycle, eviction strategy, best practices |
| [tiered-cache.md](design/tiered-cache.md) | Four-tier cache policy & scoring formula |
| [bootstrap-first.md](design/bootstrap-first.md) | Skip-recheck verification mechanism |
| [piece-tracker.md](design/piece-tracker.md) | Bitmap state machine architecture |
| [deletion-design.md](design/deletion-design.md) | Safe actor deletion flow design |
| [diff-sync-design.md](design/diff-sync-design.md) | Diff-Sync incremental sync algorithm design |
| [ui-design.md](design/ui-design.md) | Frontend design spec |

## `docs/analysis/` — Root Cause Analysis & Investigations

Post-mortems, performance analysis, and debugging case studies.

| Document | Content |
|----------|---------|
| [safari-code4.md](analysis/safari-code4.md) | Safari code=4 playback failure root cause analysis |
| [timeout-debug.md](analysis/timeout-debug.md) | Caddy 502 upstream timeout troubleshooting case |
| [piece-tracker-optimization.md](analysis/piece-tracker-optimization.md) | Bitwise optimization records & benchmarks |

## `docs/ops/` — Operations & Deployment

systemd, Caddy, HTTPS, logging, and production runbooks.

| Document | Content |
|----------|---------|
| [process-lifecycle.md](ops/process-lifecycle.md) | systemd service config & process lifecycle |
| [https-setup.md](ops/https-setup.md) | HTTPS architecture, Caddy + TLS, maintenance |
| [tracing-logging.md](ops/tracing-logging.md) | Logging system, trace_id flow, per-module log files |

## `docs/skill/` — Best Practices & Experience

Refactoring experiences, coding conventions, and lessons learned.

| Document | Content |
|----------|---------|
| [project-refactor.md](skill/project-refactor.md) | Layout migration, terminology unification, english-ification log |

---

## Entry Points

- **[AGENTS.md](../AGENTS.md)** — AI coding assistant guide (build, run, code style, tests, ops)
- **[README.md](../README.md)** — Project overview & quick start

---

## Auto-Archive Convention

When developing new features or fixing bugs, agents should archive experiences here:

- New architecture decisions → `docs/design/`
- Post-mortem / RCA → `docs/analysis/`
- Deployment / ops changes → `docs/ops/`
- Refactoring lessons → `docs/skill/`
