"""Compatibility facade for core generation planning."""

from src.karst_core.indexing.plan import (
    IndexCounts as IndexCounts,
    IndexPlan as IndexPlan,
    ManifestRecord as ManifestRecord,
    PlanAction as PlanAction,
    PlanItem as PlanItem,
    build_manifest_plan as build_manifest_plan,
)

__all__ = (
    "IndexCounts",
    "IndexPlan",
    "ManifestRecord",
    "PlanAction",
    "PlanItem",
    "build_manifest_plan",
)
