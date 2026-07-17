"""Compatibility facade for core source discovery."""

from src.karst_core.parser.discovery import (
    DiscoveryLimits as DiscoveryLimits,
    DiscoveryResult as DiscoveryResult,
    discover_snapshots as discover_snapshots,
)

__all__ = ("DiscoveryLimits", "DiscoveryResult", "discover_snapshots")
