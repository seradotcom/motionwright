"""Closed immutable profiles for retained Motion Canvas development evidence.

A profile selects fixed custody bounds; it does not admit an execution, grant
Broker permissions, or confer formal acceptance. Callers retain those checks.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetainedScope:
    selection: str
    stages: tuple[str, ...]
    jobs: int
    hosts: int
    preflight_prefix: str
    owner_prefix: str
    evidence_directory: str
    public_directory: str
    sdk_directory: str

    @property
    def full_frames(self) -> int:
        return self.jobs * 60


RETAINED_SCOPES = frozenset({"editable-source-cycle", "out-cubic-xy"})

_EDITABLE_SOURCE_CYCLE = RetainedScope(
    selection="editable-source-cycle",
    stages=("original", "reopened", "edited", "edited_reopened"),
    jobs=5,
    hosts=4,
    preflight_prefix="mw-mc-cycle-preflight-",
    owner_prefix="mw-mc-cycle-",
    evidence_directory="native-editable-private",
    public_directory="native-editable-public",
    sdk_directory="verification/mc-cycle-sdk",
)

_OUT_CUBIC_XY = RetainedScope(
    selection="out-cubic-xy",
    stages=("original",),
    jobs=2,
    hosts=1,
    preflight_prefix="mw-mc-xy-preflight-",
    owner_prefix="mw-mc-xy-",
    evidence_directory="native-out-cubic-xy-private",
    public_directory="native-out-cubic-xy-public",
    sdk_directory="verification/mc-xy-sdk",
)


def retained_scope(selection: str) -> RetainedScope:
    """Select one complete source-defined profile, without budget overrides."""
    if type(selection) is not str:
        raise TypeError("retained scope selection must be an exact str")
    if selection == "editable-source-cycle":
        return _EDITABLE_SOURCE_CYCLE
    if selection == "out-cubic-xy":
        return _OUT_CUBIC_XY
    raise ValueError("unsupported retained Motion Canvas scope")
