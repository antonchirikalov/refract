"""The ONE step lifecycle (SPEC §10.2).

Materialize inputs → assemble prompt → run runtime → HITL check → gate →
done/ok. Gate retries with feedback, attempts/<n>/ archival, full outcome
taxonomy. Meta-nodes and map reuse this; never duplicate it.
"""
