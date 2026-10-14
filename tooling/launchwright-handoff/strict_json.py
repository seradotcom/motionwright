"""Strict JSON decoding shared by immutable handoff export and receiver.

Reject ambiguous duplicate names at every object depth and non-JSON numeric
constants. A SHA-256 only authenticates bytes, not their unambiguous meaning.
"""
from __future__ import annotations

import json


class AmbiguousJSON(ValueError):
    """JSON cannot be interpreted as one canonical document."""


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise AmbiguousJSON("duplicate JSON object key")
        result[key] = value
    return result


def _finite_json(_value: str) -> object:
    raise AmbiguousJSON("non-finite JSON numeric constant")


def parse_strict_json(source: bytes) -> object:
    try:
        return json.loads(
            source.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_finite_json,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise AmbiguousJSON("invalid UTF-8 or JSON document") from error
