"""Typer CLI (SPEC §14).

Commands: validate, run, status, resume, rerun, agents list. The synchronous
CLI calls asyncio.run(...) at the boundary. Exposes ``app`` (entry point).
"""
