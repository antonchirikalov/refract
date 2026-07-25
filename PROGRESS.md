# PROGRESS

Tracks the current phase and per-section status. Updated in every commit that
closes an item. Phase table from SPEC §17.

## Current phase

**Phase 5 in progress.** Phases 0–3 done and live-validated on real opencode (see below).
Phase 4 landed: authoring catalog (§19.1) + safe pipeline write (§19.2); patch operations
were considered and rejected (§19.2 CHANGED). Phase 5 landed the `discover` node (§20) —
the research archetype: a brief instead of a documents folder, sources found on the
network. The UI (SPEC-UI.md) landed as v0.1: React SPA in `web/` over the API, served by
`refract serve`. Scope per the owner's call — templates are hand-authored YAML, the UI
shows and runs them; no pipeline editor. Next: run it on live LLMs once provider quota
returns (`rerun --from discover` finishes Discovery; research pipeline untested live).

## Live Extract validation (2026-07-25)

First real Extract run (`examples/extract-project`, 2 synthetic sources, real
opencode 1.18.4): scan → map(2) → loop worked end to end on the engine level —
type gates, verdicts, round derivation from the ledger, snapshot isolation, reuse.
Six defects it exposed, each fixed with a test:

| Fix | What the run showed |
|---|---|
| `fix(events)` | `resume` restarted `seq` at 1 → duplicate seq + WS `?from_seq=` replay silently hid every post-resume event |
| `fix(scanner)` | `init`'s `input/.gitkeep` became a source; map spent an LLM call extracting an empty file |
| `feat(cli)` | §14 promised stdout heartbeats; `run` was silent for minutes and looked hung |
| `fix(library)` | critic got no `extracts` (traceability unverifiable) and invented structural demands → loop could never converge |
| `fix(opencode)` | `POST /session/../message` is not a completion signal (it outlived a finished turn); trace was never written on the timeout path (I9) |
| `fix(tests)` | fixtures copied `examples/**/runs`, so a developer's real run broke 13 tests |

Extract then completed on `kimi/k3`: `refine` approved in round 1, extract steps
reused (no LLM calls), 27 FR/NFR in the final document, I8 clean, no surviving
`opencode serve`.

**Discovery** (`examples/discovery-project`, same two sources) ran scan → map(2) →
loop → probe on real opencode; `arch_probe` produced 41 workshop questions anchored
to concrete FR/constraint numbers. `discover` (arch_critic) then failed on the
provider's billing limit, so the last node is **not yet verified live** — its two
fixes below are covered by the MockRuntime template e2e test only:

| Fix | What the run showed |
|---|---|
| `fix(discovery)` arch_critic | curates against "the source" but was wired with the draft only — now consumes `requirements@v1` too |
| `fix(discovery)` error summary | opencode's provider error (whole HTTP response, `set-cookie` included) was persisted in `state.json` and printed by `refract status` |

Provider note: both providers hit limits during this session — `openai` returns
`429 insufficient_quota` (opencode **hangs** on it rather than failing, so a stalled
step with an empty `output/` is a provider symptom, not an engine bug), `kimi`
returns `403` with a billing message (that one surfaces correctly as
`failed_agent`). Probe the provider directly before suspecting the engine.

## Phase status

| Phase | Scope (SPEC §17) | Status |
|---|---|---|
| 0 | pyproject+scaffolding, PROGRESS.md, models, registry (+builtin types), graph+validator (all §8.3 codes), scheduler (no loop/select), map (+aggregation, provider semaphores), scanner, steps (full §10.2), state (nodes+steps, resume, force-step), snapshot, prompt, runtime base+mock+opencode-compile, CLI (validate/run/status/resume), examples/demo-project, migrate source_processor + requirements_writer | **done** (real-opencode manual smoke pending in Phase 1) |
| 1 | metanodes (loop/select), map_over.models, winner_model binding, rerun/reuse + `refract rerun` **done** (test_loop/test_select/test_map_over/test_reuse green, spec-audited); 3 library templates, OpencodeRuntime execution (serve-per-step, I1/heartbeats/auto-approve/kill) + docs/opencode-smoke.md **done** (verified vs real opencode 1.18.4 to the LLM boundary); spectra agents migrated (arch_probe→tavily-remote, arch_critic, illustrator→mcp:paperbanana, confluence_publisher→mcp-atlassian) **done** (effort_estimator + word_form_builder dropped per user; illustrator uses a paperbanana MCP wrapper — separate project, see docs/illustrator-paperbanana-mcp.md; no engine change needed). Phase 1 engine + content complete; live-LLM smoke run PASSED (demo pipeline on real opencode 1.18.4 + openai: completed, gate-retry recovered a bad output, I8 secrets clean, no leaked processes) | done |
| 2 | REST/WS API (§15): projects/pipelines/validate/runs/artifacts/cancel/resume/models/fs + WS events **done**; `/answers` (HITL + capability approvals → background resume) landed with phase 3; frontend UI spec separate | done |
| 3 | HITL (question@v1 → waiting_human → `refract answer` / API answers → resume) **done** for plain agent nodes (guarded inside map/loop); capability tiers (safe<moderate<dangerous) + confirmations **done** — a project `confirm` / `confirm_tier` policy parks a plain agent step at `waiting_human` before it runs, reusing the HITL answer machinery (`refract answer <run> <node> approve` → `confirm/approved.json` → resume). Confluence publisher left untouched per user | done |
| checkpoints | §21: pipeline-level `checkpoints: [node]` + run-scoped `--stop-after` / `stop_after`; the run parks at `waiting_human` after the node's outputs are assembled, a human may EDIT them in place, then `answer … continue` + resume runs the rest (`reject` → cancelled). `refract status` prints the parked node and its outputs; UI shows a banner with artifact links. Merged `requirements_to_design` template uses it | done (MockRuntime + API tests; not yet run live) |
| UI | SPEC-UI v0.1: projects/new-project/templates/project/run screens; template gallery with derived metadata; documents copied into the project or a brief typed instead; graph derived server-side (`GET .../graph`) so the client never parses YAML; live run view over WS + ledger; HITL answer box; `refract serve` serves API + built SPA | done (API tested; SPA smoke-run, no e2e suite) |
| 5 | `discover` node (§20): network source of `collection<source@v1>` — agent produces ONE dir, the ENGINE assembles the collection (I6 intact); `builtin/brief` + `brief@v1`/`found_sources@v1` types; `input_mode: documents\|brief` on the pipeline; `source_finder@1` agent + `research` template; reuse = ordinary agent step (fresh search is explicit, §20.3); slug/source_hash by the scanner's rules. **done** (test_discover + template e2e on MockRuntime; not yet run on live LLMs — no provider quota) | done |
| 4 | authoring catalog (§19.1: agents/builtins/types/node kinds/constraints keyed by validator codes; `GET /api/catalog`, `refract catalog`) **done**; safe pipeline write (§19.2: validate-before-commit, atomic write, `base_hash` optimistic locking, `allow_invalid` drafts) **done**. Graph patch ops were specified and then **rejected before implementation** — see the §19.2 CHANGED note: full rewrite + verification covers the editor case at a fraction of the complexity. UI itself: separate spec, in design | in progress |

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
| §14 | CLI (validate/run/status/resume) + agents list | done (rerun is Phase 1; `answer` in Phase 3; `init`/`templates` authoring ergonomics added) |
| §4 | examples/demo-project | done (demo_writer agent + scanner→map demo) |
| §17 | migrate source_processor + requirements_writer | done (+ extract@v1 type/schema; refract-native prompts per I5) |
| §18 | Phase 0 tests | done (models/registry/graph/steps/state/snapshot/scheduler/events/scanner/map/cli+E2E-golden/opencode_compile covered) |

