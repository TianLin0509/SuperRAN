"""Temporary, explicit compatibility boundary for the SuperRAN rename.

New code and documentation use only ``SUPERRAN_*``.  Existing developer
machines may still have the former product's environment variables set; an
in-place rename must not silently change data roots or physics backends.  We
therefore copy a legacy value only when the corresponding SuperRAN key is
absent.  The mapping is inspectable and can be removed after the announced
migration window.
"""
from __future__ import annotations

import os
import warnings

_LEGACY_PREFIX = "SUPER" + "WIRELESS"
_SUFFIXES = (
    "ARTIFACTS",
    "CDL_SPEC",
    "CHANNELHUB",
    "DEBUG",
    "NO_BROWSER",
    "NO_SERVE",
    "PRESETS",
    "SCENES",
)

_MIGRATED: list[dict[str, str]] = []


def migrate_legacy_environment() -> list[dict[str, str]]:
    """Map legacy environment keys to ``SUPERRAN_*`` without overwriting.

    Returns a copy of the migration audit. Calling the function repeatedly is
    idempotent; each key is recorded at most once.
    """
    recorded = {item["new_key"] for item in _MIGRATED}
    for suffix in _SUFFIXES:
        old_key = f"{_LEGACY_PREFIX}_{suffix}"
        new_key = f"SUPERRAN_{suffix}"
        if new_key not in os.environ and old_key in os.environ:
            os.environ[new_key] = os.environ[old_key]
            if new_key not in recorded:
                _MIGRATED.append(
                    {"legacy_key": old_key, "new_key": new_key, "action": "copied"}
                )
                recorded.add(new_key)
    if _MIGRATED:
        warnings.warn(
            "Legacy wireless-simulator environment variables were mapped to "
            "SUPERRAN_*; update the host configuration.",
            DeprecationWarning,
            stacklevel=2,
        )
    return legacy_environment_audit()


def legacy_environment_audit() -> list[dict[str, str]]:
    """Return the immutable-in-practice migration audit for this process."""
    return [dict(item) for item in _MIGRATED]
