"""Run ledger: state.json, resume, reuse (SPEC §9).

Two levels — nodes and steps. Written only by the engine, only atomically
(tmp + os.replace). On load, running → pending (crash recovery).
"""
