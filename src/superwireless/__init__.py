"""Deprecated import bridge for the former package name.

All implementation lives in :mod:`superran`.  This package remains only so
existing notebooks can migrate without silently changing their data roots or
simulation behaviour.  New code must import :mod:`superran` directly.
"""
from __future__ import annotations

import warnings

import superran as _superran

warnings.warn(
    "The former package name is deprecated; import 'superran' instead.",
    FutureWarning,
    stacklevel=2,
)

# Only the former top-level public API is bridged.  Internal submodules are not
# mirrored: retaining a second module namespace would duplicate module-level
# state and make simulations depend on which spelling was imported first.
__version__ = _superran.__version__

Dataset = _superran.Dataset
load = _superran.load

__all__ = ["Dataset", "load", "__version__"]
