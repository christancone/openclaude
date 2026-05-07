"""Unit tests for graph_dal._phase_tag — pure Python, no Neo4j."""
from __future__ import annotations

import pytest

from graph_dal._phase_tag import (
    phase, set_phase, clear_phase, current_phase,
)


pytestmark = pytest.mark.unit


def test_default_is_none():
    """A fresh import sees no phase set."""
    clear_phase()
    assert current_phase() is None


def test_set_and_clear():
    """Low-level setters round-trip."""
    set_phase("phase4_components")
    try:
        assert current_phase() == "phase4_components"
    finally:
        clear_phase()
    assert current_phase() is None


def test_context_manager_sets_then_clears():
    """The `phase()` context manager sets on enter, restores None on exit."""
    clear_phase()
    with phase("phase7_investigation"):
        assert current_phase() == "phase7_investigation"
    assert current_phase() is None


def test_context_manager_nested_save_and_restore():
    """Nested `with phase(...)` blocks restore the outer value, not None."""
    clear_phase()
    with phase("outer"):
        assert current_phase() == "outer"
        with phase("inner"):
            assert current_phase() == "inner"
        # Inner cleared → outer restored, NOT None
        assert current_phase() == "outer"
    assert current_phase() is None


def test_context_manager_restores_on_exception():
    """If the body raises, the phase is still cleared on exit."""
    clear_phase()
    with pytest.raises(RuntimeError):
        with phase("phase5_events"):
            assert current_phase() == "phase5_events"
            raise RuntimeError("boom")
    assert current_phase() is None


def test_set_phase_rejects_empty_string():
    """Empty / non-string phase ids are a programming error."""
    with pytest.raises(ValueError):
        set_phase("")


def test_set_phase_rejects_non_string():
    with pytest.raises(ValueError):
        set_phase(None)              # type: ignore[arg-type]
    with pytest.raises(ValueError):
        set_phase(42)                # type: ignore[arg-type]


def test_phase_context_manager_does_not_leak_across_scopes():
    """Two consecutive `with phase(...)` blocks don't interfere."""
    clear_phase()
    with phase("a"):
        pass
    with phase("b"):
        assert current_phase() == "b"
    assert current_phase() is None
