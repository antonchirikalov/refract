# PROGRESS

Tracks the current phase and per-section status. Updated in every commit that
closes an item. Phase table from SPEC §17.

## Current phase

**Phase 0 complete** — engine + CLI core done (all §18 Phase-0 tests green, mypy/ruff
clean). Remaining before the phase can be *signed off*: manual smoke on real opencode
(the OpencodeRuntime execution half + `docs/opencode-smoke.md` are Phase 1). **Next: Phase
1** — metanodes (loop/select), map_over, winner_model binding, rerun/reuse. (SPEC §17)

## Phase status

| Phase | Scope (SPEC §17) | Status |
|---|---|---|
| 0 | pyproject+scaffolding, PROGRESS.md, models, registry (+builtin types), graph+validator (all §8.3 codes), scheduler (no loop/select), map (+aggregation, provider semaphores), scanner, steps (full §10.2), state (nodes+steps, resume, force-step), snapshot, prompt, runtime base+mock+opencode-compile, CLI (validate/run/status/resume), examples/demo-project, migrate source_processor + requirements_writer | **done** (real-opencode manual smoke pending in Phase 1) |
| 1 | metanodes (loop/select), map_over.models, winner_model binding, rerun/reuse + `refract rerun` **done** (test_loop/test_select/test_map_over/test_reuse green, spec-audited); 3 library templates (extract/discovery/solution_design, validate + run E2E on MockRuntime) **done**; remaining: remaining spectra agents (arch/effort/illustrator/publisher/word_form), docs/opencode-smoke.md, OpencodeRuntime exec + manual smoke | in progress |
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
| §10.5 | scheduler (no loop/select) | done |
| §10.3 | map (+aggregation, provider semaphores) | done (map:; map_over is Phase 1) |
| §13 | builtin/scanner | done |
| §10.2 | steps: single lifecycle (gate retries+feedback, attempts, outcomes, timeout, infra retries) | done |
| §9 | state ledger (nodes+steps, atomic write, crash recovery, retry-failed) | done |
| §9 | snapshot | done |
| §9 | events.jsonl writer (single writer, seq, append-only) | done |
| §10.1/§10.4 | artifacts: link_or_copy, materialization, gate | done |
| §11 | prompt assembly (jinja2 templates) | done |
| §12 | runtime base (protocol, StepSpec/StepResult) + MockRuntime | done |
| §12 | opencode compile (agent-md + opencode.json) | done (compile only; runtime exec + smoke doc are Phase 1) |
| §14 | CLI (validate/run/status/resume) + agents list | done (rerun is Phase 1) |
| §4 | examples/demo-project | done (demo_writer agent + scanner→map demo) |
| §17 | migrate source_processor + requirements_writer | done (+ extract@v1 type/schema; refract-native prompts per I5) |
| §18 | Phase 0 tests | done (models/registry/graph/steps/state/snapshot/scheduler/events/scanner/map/cli+E2E-golden/opencode_compile covered) |

