"""MockRuntime — scripted runtime for tests (SPEC §12).

Scenario dict[pattern, list[ScriptedResponse]]; pattern = fnmatch over step_id.
Writes stub raw.txt and minimal agent.events.jsonl. No network, no real LLMs.
"""
