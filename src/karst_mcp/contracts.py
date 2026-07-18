from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Mapping


ToolHandler = Callable[..., str]


@dataclass(frozen=True, slots=True)
class ToolAnnotationPolicy:
    """Immutable, transport-neutral MCP safety hints for one public tool."""

    read_only: bool
    destructive: bool
    idempotent: bool
    open_world: bool


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Bind one stable public tool name to its transport-neutral handler."""

    name: str
    handler: ToolHandler
    annotations: ToolAnnotationPolicy

    @classmethod
    def from_handler(
        cls, handler: ToolHandler, annotations: ToolAnnotationPolicy
    ) -> ToolContract:
        return cls(name=handler.__name__, handler=handler, annotations=annotations)


def define_tool_contracts(
    *handlers: ToolHandler,
    annotations: Mapping[str, ToolAnnotationPolicy],
) -> tuple[ToolContract, ...]:
    """Create a validated, ordered public tool contract."""
    handler_names = {handler.__name__ for handler in handlers}
    annotation_names = set(annotations)
    if handler_names != annotation_names:
        missing = sorted(handler_names - annotation_names)
        unexpected = sorted(annotation_names - handler_names)
        raise ValueError(
            "MCP annotations must exactly match tool handlers; "
            f"missing={missing}, unexpected={unexpected}."
        )
    contracts = tuple(
        ToolContract.from_handler(handler, annotations[handler.__name__])
        for handler in handlers
    )
    names = [contract.name for contract in contracts]
    if len(names) != len(set(names)):
        raise ValueError("MCP tool contract names must be unique.")
    return contracts
