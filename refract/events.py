"""events.jsonl append-only writer (SPEC §9).

Single writer — an asyncio task with a queue that also assigns ``seq``. Event
types and payloads per §9.
"""
