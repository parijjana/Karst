"""Compatibility facade for the Karst core project-summary query interface."""

from src.karst_core.query.summary import (
    ProjectSummary,
    ProjectSummaryService,
    TrackedFileRow,
)

__all__ = ["ProjectSummary", "ProjectSummaryService", "TrackedFileRow"]
