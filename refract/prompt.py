"""Prompt assembly (SPEC §11).

Concatenates agent prompt.md + generated inputs/outputs sections (from the
contract, I5) + context additions (revision, gate_feedback). Uses the jinja2
templates in refract/templates/.
"""
