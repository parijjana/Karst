from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


ToolHandler = Callable[..., str]


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Bind one stable public tool name to its transport-neutral handler."""

    name: str
    handler: ToolHandler

    @classmethod
    def from_handler(cls, handler: ToolHandler) -> ToolContract:
        return cls(name=handler.__name__, handler=handler)


def define_tool_contracts(*handlers: ToolHandler) -> tuple[ToolContract, ...]:
    """Create a validated, ordered public tool contract."""
    contracts = tuple(ToolContract.from_handler(handler) for handler in handlers)
    names = [contract.name for contract in contracts]
    if len(names) != len(set(names)):
        raise ValueError("MCP tool contract names must be unique.")
    return contracts
