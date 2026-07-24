# illustrator → paperbanana MCP server

The `illustrator` agent (`library/agents/illustrator`) generates figures by calling
the **published** paperbanana MCP server — no wrapper to write. It declares
`needs: [read, edit, "mcp:paperbanana"]`, which keeps the run reproducible and
workdir-confined (I1): the agent calls MCP tools, exactly like `arch_probe` →
`tavily-remote` or `confluence_publisher` → `mcp-atlassian`.

**refract needs zero engine changes for this** — MCP is the built-in extension
point. You only register the server in `~/.refract/mcp.yaml`.

- Catalog: https://mcpservers.org/servers/llmsresearch/paperbanana
- Source: https://github.com/llmsresearch/paperbanana

## Register the server (`~/.refract/mcp.yaml`)

```yaml
servers:
  paperbanana:
    command: ["uvx", "--from", "paperbanana[mcp]", "paperbanana-mcp"]
    env: {}   # see keys below — prefer inheriting from the run env, not inlining
```

**Keys (I8):** the paperbanana MCP's default image backend is Gemini and it
**requires `GOOGLE_API_KEY`** (observed at runtime: with only `OPENAI_API_KEY`
set, `generate_diagram` fails with "missing API key … obtain a Google API key").
To use the OpenAI image backend instead, configure paperbanana's provider via its
own env (e.g. `OPENAI_BASE_URL` and related overrides — see the paperbanana repo)
in the `env:` block below. Do NOT inline secret values — refract runs the step
with the run-level env and the stdio MCP server inherits it, so exporting the
right key in the run environment is enough; reference an env var if you must set
one under `env:`.

## Tools the server exposes (illustrator uses these)

`generate_diagram`, `generate_plot`, `continue_diagram`, `continue_plot`,
`evaluate_diagram`, `evaluate_plot`, `orchestrate_figures`, `batch_diagrams`,
`batch_plots`, `continue_run`, `download_references`.

The agent uses `generate_diagram` (architecture/flow figures), `generate_plot`
(data charts), and `continue_*` for revisions. Tools save the image to a path
(`outputs/run_<ts>/final_output.png`) and return it; the agent reads that file and
copies it into its own `output/fig-<n>.png` so all artifacts stay in the step
workdir.

## What lives outside refract

The paperbanana package, its image-model API key, and the reference dataset live
wherever the MCP server process runs (`uvx` fetches the package on first use).
That's the intended split: reproducible, isolated agent in refract; the heavy
image-gen dependency in its own published server.
