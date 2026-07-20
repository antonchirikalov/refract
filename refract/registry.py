"""Artifact type registry (SPEC §5).

Loads ``library/types/artifact_types.yaml`` and injects the built-in control
types (verdict@v1, selection@v1, question@v1, answer@v1). Owns slugify and the
collection<X> type constructor.
"""
