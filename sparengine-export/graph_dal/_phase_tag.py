"""Thread-local helper for tagging DAL writes with their owning phase.

Used by the per-phase scoring system (Q10): every node / edge a phase
writes carries a ``created_in_phase`` property so the benchmark archive
can compute phase-scoped metrics later (e.g. "how many components did
phase4 create on this archetype?").

Design
------

Phase scripts wrap their main work in either:

    from graph_dal._phase_tag import phase
    with phase("phase4_components"):
        ... DAL writer calls ...

or call the lower-level setters:

    from graph_dal._phase_tag import set_phase, clear_phase
    set_phase("phase4_components")
    try:
        ... DAL writer calls ...
    finally:
        clear_phase()

Either way, ``current_phase()`` returns the active id while the writer
runs, or ``None`` outside any wrapped block. DAL writers call
``current_phase()`` and include the result as a property on the MERGE.

We use ``contextvars.ContextVar`` rather than threading.local — it works
correctly under asyncio (each coroutine gets its own value) and across
``concurrent.futures`` worker threads.

The helper is internal — phase scripts use the public ``with phase(...)``
context manager; DAL writers use ``current_phase()``. No other usage.
"""
from __future__ import annotations

import contextlib
import contextvars
from typing import Iterator


__all__ = ["phase", "set_phase", "clear_phase", "current_phase"]


# Sentinel: ``None`` means "no phase set" — the writer should still proceed
# but the ``created_in_phase`` property is omitted (not written as null).
_current_phase: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "graph_dal._phase_tag.current_phase",
    default=None,
)


def set_phase(phase_id: str) -> None:
    """Set the active phase id on the current context.

    Pairs with ``clear_phase()``. Most callers should use the ``phase()``
    context manager instead, which guarantees cleanup on exception.
    """
    if not isinstance(phase_id, str) or not phase_id:
        raise ValueError(
            f"set_phase: phase_id must be a non-empty string, got {phase_id!r}"
        )
    _current_phase.set(phase_id)


def clear_phase() -> None:
    """Clear the active phase id on the current context."""
    _current_phase.set(None)


def current_phase() -> str | None:
    """Return the active phase id, or ``None`` if no phase is set."""
    return _current_phase.get()


@contextlib.contextmanager
def phase(phase_id: str) -> Iterator[None]:
    """Context manager: set the active phase id for the duration of the block.

    Restores the previous value on exit (so nested ``with phase(...)`` blocks
    behave correctly — useful in tests that simulate sub-phases).

    Example::

        from graph_dal._phase_tag import phase
        from graph_dal.component import write_component

        with phase("phase4_components"):
            write_component(tx, asset_id=aid, value=cid, ...)
            # The :Component node carries created_in_phase = "phase4_components".
    """
    token = _current_phase.set(phase_id)
    try:
        yield
    finally:
        _current_phase.reset(token)
