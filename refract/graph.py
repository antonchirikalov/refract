"""Pipeline load + validation and topological sort (SPEC §8).

All validation errors are collected as structured ``{code, node_id?, message}``
records (closed enum in §8.3), never raised one-by-one. Check order per §8.3.
"""
