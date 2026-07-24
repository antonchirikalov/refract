# illustrator → paperbanana MCP server

The `illustrator` agent (`library/agents/illustrator`) generates figures by calling
an MCP tool, declared as `needs: [read, edit, "mcp:paperbanana"]`. This keeps the
run reproducible and workdir-confined (I1): the agent never shells out to a local
script — it calls a tool, exactly like `arch_probe` → `tavily-remote` or
`confluence_publisher` → `mcp-atlassian`.

**refract needs zero engine changes for this** — MCP is the built-in extension
point. You only (1) build the server as a **separate project**, (2) register it in
`~/.refract/mcp.yaml`, (3) keep the agent's `needs` as-is.

## 1. Register the server (`~/.refract/mcp.yaml`)

```yaml
servers:
  paperbanana:
    command: ["python", "-m", "paperbanana_mcp"]   # however your server launches
    env: {}                                          # e.g. { OPENAI_API_KEY_ENV_ALREADY_IN_RUN_ENV }
```

refract passes the run env down (I8: provider keys + used agents' MCP tokens); put
any secret the server needs behind an env var, never inline it here.

## 2. Tool contract the agent expects

The agent calls one tool per figure and writes the result into its own `output/`
(so all files stay inside the step workdir — I1). Return the image as data, not a
path, so the server's filesystem is irrelevant to the agent:

```
tool: generate_illustration
  input:  { "prompt": string, "style": string (optional) }
  output: { "png_base64": string, "revised_prompt": string }
```

The agent decodes `png_base64` → `output/fig-<n>.png` and records `revised_prompt`
in `output/manifest.json`. (If you prefer the server to return several candidates,
return an array and let the agent pick — but keep one call per figure.)

## 3. Starter skeleton (goes in YOUR separate repo, not in refract)

Minimal stdio MCP server wrapping paperbanana. This is a starting point — wire the
actual paperbanana pipeline (planner → stylist → visualizer → critic) inside
`generate_illustration`.

```python
# paperbanana_mcp/__main__.py  — separate project; deps: mcp, paperbanana
import base64
from mcp.server.fastmcp import FastMCP

app = FastMCP("paperbanana")


@app.tool()
def generate_illustration(prompt: str, style: str = "") -> dict:
    """Generate one publication figure and return it as base64 PNG."""
    # from paperbanana import generate  # your real call
    # png_bytes, revised = generate(prompt=prompt, style=style, provider="openai")
    png_bytes, revised = b"", prompt  # <-- replace with paperbanana
    return {
        "png_base64": base64.b64encode(png_bytes).decode("ascii"),
        "revised_prompt": revised,
    }


if __name__ == "__main__":
    app.run()  # stdio transport
```

## What still lives outside refract

The server's runtime deps — the `paperbanana` package, its image-API key, and the
`~/.cache/paperbanana/` reference dataset — must exist wherever the MCP server
runs. MCP relocates that heavy dependency behind a clean interface; it does not
remove it. That's the intended tradeoff (reproducible, isolated agent; infra lives
in its own project).
