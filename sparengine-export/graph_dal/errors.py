"""Exception types raised by the graph DAL.

Two exception classes — both descend from `Exception`. Both are intended to
**stop the phase**: if either is raised mid-phase, the phase script must not
proceed to the next phase. This is the SPARENGINE "MANDATORY VERIFICATION"
discipline at the code level: don't fake a successful run.
"""

from __future__ import annotations


class GoldenRuleViolation(Exception):
    """Raised by a fact-write helper when the caller did not supply page evidence.

    The "golden rule" (Q7 of the migration grill, mirrored in GRAPH.md):
    every fact-bearing node must trace to (file_name, page_index, verbatim_quote).
    In Neo4j we enforce this at the DAL chokepoint — every write_event,
    write_component, write_form1, write_finding (etc.) has required keyword
    args `evidence_page_uid` and `evidence_quote`. If either is missing or
    empty, the helper raises this exception **before** the MERGE runs.

    The DAL never writes a fact node without page evidence. There is no
    other path to write fact nodes.

    Attributes
    ----------
    label
        The Neo4j label the caller was trying to write (e.g. "Event").
    value
        The natural-key value of the would-be node (for diagnostics).
    missing
        Which evidence field was missing — "evidence_page_uid" or
        "evidence_quote" (both is reported as "evidence_page_uid").
    """

    def __init__(self, *, label: str, value: str | None, missing: str) -> None:
        self.label = label
        self.value = value
        self.missing = missing
        super().__init__(
            f"Golden rule violation: cannot write :{label} "
            f"(value={value!r}) without {missing}. "
            f"Every fact-bearing node must cite (page_uid, quote)."
        )


class VerificationFailed(Exception):
    """Raised by `graph_dal.verify.verify_phase_N(...)` when a rule fails.

    Phase scripts call the verifier at the end of their phase, before
    declaring done. If the verifier finds rule violations (missing evidence
    edges, zero counts where >0 was expected, orphaned nodes), it raises
    this exception with a structured payload. The phase script must STOP.

    Attributes
    ----------
    phase
        The phase number / id (e.g. "1", "7.5").
    counts
        The counts dict the verifier was building — surface for the
        progress.log entry.
    rule_violations
        List of dicts describing each violated rule:
        ``{"rule": str, "expected": str, "actual": int, "detail": str}``
    """

    def __init__(
        self,
        *,
        phase: str,
        counts: dict[str, int] | None = None,
        rule_violations: list[dict] | None = None,
    ) -> None:
        self.phase = phase
        self.counts = counts or {}
        self.rule_violations = rule_violations or []
        rule_summary = "; ".join(
            f"{rv['rule']} expected {rv['expected']}, got {rv['actual']}"
            for rv in self.rule_violations
        )
        super().__init__(
            f"Phase {phase} verification failed: {rule_summary or 'no rules listed'}"
        )
