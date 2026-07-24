# opencode smoke test (manual)

The `OpencodeRuntime` (`refract/runtime/opencode.py`) drives a **real** `opencode`
process. It is deliberately **not** in the automated suite — every automated test
uses `MockRuntime` (SPEC §18: no network, no real LLMs). This recipe is how you
verify the real adapter end to end after touching it.

## Pinned version

`OPENCODE_PINNED_VERSION` in `refract/runtime/opencode.py`. The adapter checks
`opencode --version` once per run and logs a warning on mismatch (it does not
abort). If the real interface differs from what the adapter assumes (see
[Assumptions](#assumptions-to-confirm) below), bump the constant and adjust the
adapter in the same commit.

## One-time setup

1. Install opencode and confirm it is on `PATH`:
   ```bash
   opencode --version
   ```
2. Create `~/.refract/providers.yaml` (or point `$REFRACT_HOME` elsewhere):
   ```yaml
   providers:
     kimi: { api_key_env: MOONSHOT_API_KEY, max_concurrent: 2 }
   library_path: /abs/path/to/refract/library
   ```
   Optionally `~/.refract/mcp.yaml` if any agent `needs` an `mcp:<server>`:
   ```yaml
   servers:
     pdf-reader: { command: ["npx", "-y", "@mcp/pdf-reader"], env: {} }
   ```
3. Export the provider key referenced above:
   ```bash
   export MOONSHOT_API_KEY=sk-...
   ```
4. Point the model in `examples/demo-project/project.yaml` at a provider you have
   a key for (default is `kimi/kimi-k3`).

## Run the demo pipeline

```bash
uv run refract validate examples/demo-project      # expect: OK
uv run refract run      examples/demo-project       # real opencode
uv run refract status   examples/demo-project/runs/run_<TS>
```

Expected:
- run status `completed`;
- `runs/run_<TS>/steps/write/_out/requirements/_collection.json` with `stats.ok == 2`;
- each element under `steps/write/<slug>/` has `output/requirements.md`, plus the
  per-attempt trace files `prompt.md`, `raw.txt`, `agent.events.jsonl` (I9);
- `steps/write/<slug>/.opencode/agent/demo_writer.md` and `opencode.json` compiled
  into the workdir (I1: the agent ran with `cwd` = that workdir);
- `events.jsonl` contains `heartbeat` events for any step that ran longer than the
  heartbeat interval.

Then exercise the larger shapes with the library templates (copy one into a
project's `pipelines/`): `extract` (loop), `solution_design` (map_over → select →
winner-model-bound loop).

## What to check

- **I1** — the agent only touched files under its step workdir. Inspect that no
  paths outside the workdir appear in `raw.txt` / the produced files.
- **Auto-approve** — the run did not block on a permission prompt (the generated
  `opencode.json` sets every `permission` to `allow`).
- **Process hygiene** — no `opencode serve` processes survive the run:
  ```bash
  pgrep -laf "opencode serve"   # expect: nothing
  ```
  The adapter kills each per-step serve in a `finally` and again in `close()`.
- **Secrets (I8)** — grep the run dir for your key; it must NOT appear. Only the
  `{env:MOONSHOT_API_KEY}` placeholder should be in `opencode.json`.
  ```bash
  grep -r "$MOONSHOT_API_KEY" runs/ ; echo "exit=$?"   # expect: no match
  ```

## Interface (verified against opencode 1.18.4, no LLM)

Confirmed by probing a real `opencode serve` (health, agent discovery, session
create) — everything except the LLM message send, whose response shape is taken
from the server's own OpenAPI spec at `GET /doc`:

- `opencode serve --port <p>` starts a localhost HTTP server; `GET /global/health`
  returns 200 when ready. ✓ verified
- `POST /session {"title": ...}` returns `{"id": ...}`. ✓ verified
- `<workdir>/.opencode/agent/<name>.md` (`AGENTS_SUBDIR`) is discovered — the
  compiled agent shows up in `GET /agent` alongside global agents. ✓ verified
- `POST /session/{id}/message {"agent": <name>, "model": {"providerID","modelID"},
  "parts": [{"type":"text","text": <prompt>}]}` runs the turn and returns
  `{"info": {"error"?, "cost", "tokens", ...}, "parts": [{"type":"text","text"}]}`.
  (from OpenAPI `/doc`; the adapter reads `info.error` / `info.{cost,tokens}` and
  concatenates the text parts) — **not yet exercised end to end** (needs a key).

Still to confirm on YOUR setup, and adjust `refract/runtime/opencode.py` +
`OPENCODE_PINNED_VERSION` if they differ:

- The message send actually completes and the agent writes files under
  `output/` (the whole point — a live LLM run).
- Custom providers (e.g. `kimi`) may additionally need `baseURL`/`npm` in
  `opencode.json`'s provider block; `providers.yaml` (SPEC §7) currently only
  carries `api_key_env`, so extend both if your provider needs more.

Any drift is expected — fix the adapter, bump `OPENCODE_PINNED_VERSION`, note it here.
