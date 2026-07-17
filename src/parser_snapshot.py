"""Compatibility facade for core immutable source snapshots."""

import os as os
import stat as stat

from src.karst_core.parser.snapshots import read_snapshot as read_snapshot

__all__ = ("read_snapshot",)
