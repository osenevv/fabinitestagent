"""Stdlib JSON Schema 2020-12 subset for vyhodnoceni.schema.json. Unknown keywords error."""

from __future__ import annotations

from typing import Any

# Anotace bez vlivu na validaci.
_ANNOTATIONS = {"$schema", "$id", "title", "description", "$defs", "default"}
_SUPPORTED = _ANNOTATIONS | {
    "type",
    "enum",
    "required",
    "properties",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minimum",
    "maximum",
    "minLength",
    "$ref",
}

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(Exception):
    """Schéma samo je neplatné / používá nepodporované klíčové slovo."""


def _resolve(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith("#/"):
        raise SchemaError(f"podporovány jen lokální $ref, ne {ref!r}")
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            raise SchemaError(f"nerozřešitelný $ref {ref!r}")
        node = node[part]
    return node


def validate(instance: Any, schema: dict[str, Any]) -> list[str]:
    """Vrátí seznam chyb (prázdný = validní). Cesty ve formátu `a.b[0].c`."""
    errors: list[str] = []
    _validate(instance, schema, schema, "$", errors)
    return errors


def _validate(inst: Any, sch: dict[str, Any], root: dict[str, Any], path: str, errs: list[str]) -> None:
    unknown = set(sch) - _SUPPORTED
    if unknown:
        raise SchemaError(f"{path}: nepodporovaná klíčová slova {sorted(unknown)}")

    if "$ref" in sch:
        _validate(inst, _resolve(root, sch["$ref"]), root, path, errs)
        return

    if "type" in sch:
        types = sch["type"] if isinstance(sch["type"], list) else [sch["type"]]
        if not any(_TYPE_CHECKS[t](inst) for t in types):
            errs.append(f"{path}: očekáván typ {'|'.join(types)}, je {type(inst).__name__}")
            return

    if "enum" in sch and inst not in sch["enum"]:
        errs.append(f"{path}: hodnota {inst!r} není v {sch['enum']}")

    if isinstance(inst, (int, float)) and not isinstance(inst, bool):
        if "minimum" in sch and inst < sch["minimum"]:
            errs.append(f"{path}: {inst} < minimum {sch['minimum']}")
        if "maximum" in sch and inst > sch["maximum"]:
            errs.append(f"{path}: {inst} > maximum {sch['maximum']}")

    if isinstance(inst, str) and "minLength" in sch and len(inst) < sch["minLength"]:
        errs.append(f"{path}: řetězec kratší než {sch['minLength']}")

    if isinstance(inst, dict):
        for key in sch.get("required", []):
            if key not in inst:
                errs.append(f"{path}: chybí povinné pole {key!r}")
        props = sch.get("properties", {})
        for key, val in inst.items():
            sub = f"{path}.{key}"
            if key in props:
                _validate(val, props[key], root, sub, errs)
            elif sch.get("additionalProperties", True) is False:
                errs.append(f"{sub}: nepovolené pole")

    if isinstance(inst, list):
        if "minItems" in sch and len(inst) < sch["minItems"]:
            errs.append(f"{path}: méně než {sch['minItems']} položek")
        if "maxItems" in sch and len(inst) > sch["maxItems"]:
            errs.append(f"{path}: více než {sch['maxItems']} položek")
        if "items" in sch:
            for i, item in enumerate(inst):
                _validate(item, sch["items"], root, f"{path}[{i}]", errs)
