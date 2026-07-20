"""OpencodeRuntime — real opencode adapter (SPEC §12).

Compiles the agent package into a per-step workdir, confines file access to the
workdir (I1), emits heartbeats, auto-approves permissions. Processes started
here are killed in close() even on crash paths.
"""
