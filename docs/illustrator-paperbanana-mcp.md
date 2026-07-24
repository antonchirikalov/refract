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
    # NOTE the [openai] extra — without it paperbanana lacks the `openai` module
    # and falls back to Gemini (then demands GOOGLE_API_KEY).
    command: ["uvx", "--from", "paperbanana[mcp,openai]", "paperbanana-mcp"]
    env:
      # opencode launches the MCP subprocess with ONLY this declared environment
      # (it does not forward the whole parent env), so pass the key explicitly.
      # `{env:VAR}` is resolved by opencode from the run env — the literal secret
      # is never written to the file (I8).
      OPENAI_API_KEY: "{env:OPENAI_API_KEY}"
      # paperbanana's default model names are Gemini's, which 404 against OpenAI.
      # Pin real OpenAI models (verified working): VLM gpt-4o, image gpt-image-1.
      OPENAI_VLM_MODEL: "gpt-4o"
      OPENAI_IMAGE_MODEL: "gpt-image-1"
```

Also select the OpenAI providers in paperbanana's own config (`config.yaml`:
`vlm.provider: openai`, `image.provider: openai_imagen`) — its default provider is
the free Gemini tier.

**Keys (I8):** paperbanana runs on `OPENAI_API_KEY` once the `[openai]` extra is
installed and OpenAI models are pinned. Verified directly (CLI, `--vlm-provider
openai --vlm-model gpt-4o --image-provider openai_imagen --image-model
gpt-image-1`): it ran its retriever→planner→stylist→visualizer→critic loop and
produced real PNGs (~2 MB, 2 iterations, ≈$0.12/figure). The key reaches the MCP
subprocess only via the `{env:OPENAI_API_KEY}` placeholder above (the literal is
never written to disk). Pitfalls that cost us a few runs: (1) omitting `[openai]`
→ `ModuleNotFoundError: openai` → silent Gemini fallback → "get a Google API key";
(2) leaving default model names → 404 (`gemini-2.5-flash`/`gpt-image-1.5` don't
exist on OpenAI) — pin `gpt-4o` / `gpt-image-1`.

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
