"""Builtin-node registry (SPEC §13).

BUILTINS: dict[str, BuiltinDef]; BuiltinDef = {params_model, produces, run}.
The validator reads node ports from here.
"""
