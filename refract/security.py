"""Secret minimization and run-level env assembly (SPEC §12, I8).

Runner env = union of provider keys for models in resolved.yaml + MCP tokens
from the needs of used agents. Secrets never enter project folders, artifacts,
or prompts.
"""
