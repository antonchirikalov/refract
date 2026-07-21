# PROGRESS

Tracks the current phase and per-section status. Updated in every commit that
closes an item. Phase table from SPEC §17.

## Current phase

**Phase 0** — engine + CLI scaffolding and core. (SPEC §17)

## Phase status

| Phase | Scope (SPEC §17) | Status |
|---|---|---|
| 0 | pyproject+scaffolding, PROGRESS.md, models, registry (+builtin types), graph+validator (all §8.3 codes), scheduler (no loop/select), map (+aggregation, provider semaphores), scanner, steps (full §10.2), state (nodes+steps, resume, force-step), snapshot, prompt, runtime base+mock+opencode-compile, CLI (validate/run/status/resume), examples/demo-project, migrate source_processor + requirements_writer | **in progress** |
| 1 | metanodes (loop/select), map_over.models, winner_model binding, rerun/reuse, `refract rerun`, remaining spectra agents, 3 library templates, docs/opencode-smoke.md | remaining |
| 2 | api/ + WS; frontend UI spec separate | remaining |
| 3 | HITL, capability tiers, confirmations | remaining |
| 4 | graph patch ops, builder-LLM catalog (out of this spec) | remaining |

## Phase 0 — SPEC sections checklist

| SPEC § | Item | Status |
|---|---|---|
| §3 | pyproject.toml | done |
| §4 | repo scaffolding + PROGRESS.md | done |
| §5–§9 | models — all file formats (types, agent, config, pipeline, ledger/events) | done |
| §5 | registry (artifact types + builtin types, rules, edge compat, slugify) | done |
| §8 | graph load + validator (all §8.3 codes) + toposort | done |
| §13 | builtin registry metadata (ports/params; run deferred) | done |
| §10.5 | scheduler (no loop/select) | remaining |
| §10.3 | map (+aggregation, provider semaphores) | remaining |
| §13 | builtin/scanner | remaining |
| §10.2 | steps (gate retries+feedback, attempts, outcome taxonomy) | remaining |
| §9 | state ledger (nodes+steps, atomic write, crash recovery, retry-failed) | done |
| §9 | snapshot | remaining |
| §10.1/§10.4 | artifacts: link_or_copy, materialization, gate | done |
| §11 | prompt assembly (jinja2 templates) | done |
| §12 | runtime base (protocol, StepSpec/StepResult) + MockRuntime | done |
| §12 | opencode compile (agent-md + opencode.json) | remaining |
| §14 | CLI (validate/run/status/resume) | remaining |
| §4 | examples/demo-project | remaining |
| §17 | migrate source_processor + requirements_writer | remaining |
| §18 | Phase 0 tests | remaining |
