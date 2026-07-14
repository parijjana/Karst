from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pytest

from src.index_models import (
    CancellationSignal,
    DiagnosticSeverity,
    IndexBudget,
    IndexCounts,
    IndexDiagnostic,
    IndexResult,
    IndexStatus,
)


MANIFEST_HASH = "b" * 64


def diagnostic(
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> IndexDiagnostic:
    return IndexDiagnostic(
        severity=severity,
        code="index_state",
        message="The indexing state changed.",
    )


def test_index_counts_have_exact_fields_and_account_for_current_discovery() -> None:
    assert tuple(field.name for field in fields(IndexCounts)) == (
        "discovered_files",
        "indexed_files",
        "unchanged_files",
        "skipped_files",
        "deleted_files",
        "renamed_files",
        "failed_files",
        "symbol_count",
        "edge_count",
        "diagnostic_count",
    )
    assert IndexCounts(deleted_files=2).processed_files == 0
    rename_only = IndexCounts(discovered_files=2, renamed_files=2)
    assert rename_only.processed_files == 2
    assert IndexCounts(
        discovered_files=5,
        indexed_files=1,
        unchanged_files=1,
        skipped_files=1,
        renamed_files=1,
        failed_files=1,
    ).processed_files == 5
    with pytest.raises(ValueError, match="nonnegative"):
        IndexCounts(indexed_files=-1)
    with pytest.raises(ValueError, match="processed files"):
        IndexCounts(renamed_files=1)


def test_budget_fields_are_nonnegative_and_diagnostics_are_never_disabled() -> None:
    budget = IndexBudget(
        max_files=100,
        max_file_bytes=1_000,
        max_total_bytes=10_000,
        max_depth=20,
        max_diagnostics=10,
        max_symbols=1_000,
        max_edges=2_000,
        max_update_files=50,
        max_duration_ms=30_000,
    )

    assert not hasattr(budget, "__dict__")
    with pytest.raises(FrozenInstanceError):
        budget.max_files = 1  # type: ignore[misc]
    with pytest.raises(ValueError, match="max_diagnostics"):
        IndexBudget(max_diagnostics=0)
    with pytest.raises(ValueError, match="nonnegative"):
        IndexBudget(max_diagnostics=1, max_symbols=-1)


def test_cancellation_signal_is_runtime_checkable() -> None:
    class Signal:
        def is_cancelled(self) -> bool:
            return True

    assert isinstance(Signal(), CancellationSignal)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("discovered_files", 1),
        ("indexed_files", 1),
        ("unchanged_files", 1),
        ("skipped_files", 1),
        ("deleted_files", 1),
        ("renamed_files", 1),
        ("failed_files", 1),
        ("symbol_count", 1),
        ("edge_count", 1),
    ],
)
def test_preflight_rejection_requires_zero_workload_counts(
    field: str, value: int
) -> None:
    values = {"diagnostic_count": 1, field: value}
    if field in {
        "indexed_files",
        "unchanged_files",
        "skipped_files",
        "renamed_files",
        "failed_files",
    }:
        values["discovered_files"] = value

    with pytest.raises(ValueError, match="zero workload"):
        IndexResult(
            IndexStatus.REJECTED,
            IndexCounts(**values),
            diagnostics=(diagnostic(),),
        )


def test_lifecycle_supports_preflight_rejection_and_staged_failure() -> None:
    error = diagnostic()
    rejected = IndexResult(
        IndexStatus.REJECTED,
        IndexCounts(diagnostic_count=1),
        diagnostics=(error,),
    )
    failed = IndexResult(
        IndexStatus.FAILED,
        IndexCounts(diagnostic_count=1),
        diagnostics=(error,),
        generation_id=2,
    )

    assert rejected.generation_id is None
    assert failed.counts.failed_files == 0
    with pytest.raises(ValueError, match="rejected result"):
        replace(rejected, generation_id=1)
    with pytest.raises(ValueError, match="generation"):
        replace(failed, generation_id=None)
    with pytest.raises(ValueError, match="error or fatal"):
        replace(failed, diagnostics=(diagnostic(DiagnosticSeverity.WARNING),))


@pytest.mark.parametrize(
    "severity", [DiagnosticSeverity.INFO, DiagnosticSeverity.WARNING]
)
def test_cancellation_accepts_nonterminal_reason(
    severity: DiagnosticSeverity,
) -> None:
    cancelled = IndexResult(
        IndexStatus.CANCELLED,
        IndexCounts(diagnostic_count=1),
        diagnostics=(diagnostic(severity),),
        generation_id=3,
    )

    assert cancelled.counts.failed_files == 0
    with pytest.raises(ValueError, match="diagnostic"):
        replace(cancelled, diagnostics=(), counts=IndexCounts())


def test_in_progress_and_completed_lifecycle_are_unambiguous() -> None:
    warning = diagnostic(DiagnosticSeverity.WARNING)
    progress = IndexResult(
        IndexStatus.IN_PROGRESS,
        IndexCounts(diagnostic_count=1),
        diagnostics=(warning,),
        generation_id=4,
    )
    completed = IndexResult(
        IndexStatus.COMPLETED,
        IndexCounts(discovered_files=1, indexed_files=1),
        generation_id=4,
        manifest_sha256=MANIFEST_HASH,
        promoted=True,
    )

    assert progress.status.is_terminal is False
    assert completed.succeeded is True
    with pytest.raises(ValueError, match="terminal diagnostic"):
        replace(progress, diagnostics=(diagnostic(),))
    with pytest.raises(ValueError, match="must be promoted"):
        replace(completed, promoted=False)
    with pytest.raises(ValueError, match="only completed"):
        replace(progress, promoted=True)
    with pytest.raises(ValueError, match="every discovered"):
        IndexResult(
            IndexStatus.COMPLETED,
            IndexCounts(discovered_files=2, indexed_files=1),
            generation_id=4,
            manifest_sha256=MANIFEST_HASH,
            promoted=True,
        )
