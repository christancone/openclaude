"""``:Date`` node materialiser.

Implements the Q5 contract: every dated entity in the graph also has a
``-[:ON_DATE {role}]->(:Date {asset_id, iso, year, month, day, dow})`` edge
in addition to keeping the date as a property on the originating node
(belt + suspenders).

The single helper ``link_date()`` is called from every other DAL writer
that has dated arguments. It MERGEs the ``:Date`` node and the ``:ON_DATE``
edge in one round-trip; idempotent on re-runs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .errors import GoldenRuleViolation
from ._phase_tag import current_phase


_LINK_DATE_CYPHER = """
MATCH (src {asset_id: $asset_id, value: $source_uid})
WHERE $source_label IN labels(src)
WITH src
MERGE (d:Date {asset_id: $asset_id, iso: $iso})
ON CREATE SET d.year = $year, d.month = $month, d.day = $day, d.dow = $dow,
              d.created_in_phase = $created_in_phase
MERGE (src)-[r:ON_DATE {role: $role}]->(d)
RETURN d.iso AS iso
"""

# Special form when the source node uses `iso` as its key (i.e. another Date
# node — won't happen in practice). Kept symmetric for completeness; not used.


def parse_iso_date(s: str) -> date:
    """Parse a date string into a ``datetime.date``, tolerating partial inputs.

    Aviation OCR data routinely carries partial / messy dates:
      "2024-03-15"            → date(2024, 3, 15)
      "2024-03-15T12:34:56"   → date(2024, 3, 15)   (time dropped — Q5 day-only)
      "2024-03-15 12:34:56"   → date(2024, 3, 15)
      "2024-03-15T12:34:56Z"  → date(2024, 3, 15)
      "2024-03"               → date(2024, 3, 1)    (assume first of month)
      "2024"                  → date(2024, 1, 1)    (assume first of year)
      "03/15/2024"            → date(2024, 3, 15)   (US slash-form)
      "15-MAR-2024"           → date(2024, 3, 15)   (aviation logbook form)

    Raises ``ValueError`` only when no plausible date can be extracted
    at all. Empty strings raise.
    """
    if not s:
        raise ValueError("empty date string")
    s = s.strip()

    # Drop trailing 'Z' and split T / space (timezone + time discarded).
    head = s.split("T", 1)[0].split(" ", 1)[0].rstrip("Z")

    # Full ISO YYYY-MM-DD.
    try:
        return date.fromisoformat(head)
    except ValueError:
        pass

    # Year-month "2024-03"
    if len(head) == 7 and head[4] == "-":
        try:
            y, m = head.split("-")
            return date(int(y), int(m), 1)
        except (ValueError, TypeError):
            pass

    # Year-only "2024"
    if len(head) == 4 and head.isdigit():
        return date(int(head), 1, 1)

    # US slash form "MM/DD/YYYY" or "M/D/YYYY"
    if "/" in head:
        parts = head.split("/")
        if len(parts) == 3:
            try:
                m, d_, y = (int(p) for p in parts)
                if y < 100:
                    y += 2000 if y < 50 else 1900
                return date(y, m, d_)
            except (ValueError, TypeError):
                pass

    # Aviation form "15-MAR-2024" or "01-JAN-20"
    months = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    if "-" in head:
        parts = head.upper().split("-")
        if len(parts) == 3 and parts[1] in months:
            try:
                d_, mon, y = parts
                y_int = int(y)
                if y_int < 100:
                    y_int += 2000 if y_int < 50 else 1900
                return date(y_int, months[mon], int(d_))
            except (ValueError, TypeError):
                pass

    raise ValueError(f"unparseable date: {s!r}")


def link_date(
    tx: Any,
    *,
    asset_id: str,
    source_uid: str,
    source_label: str,
    role: str,
    date_iso: str | None,
) -> str | None:
    """MERGE :Date and :ON_DATE edge from a source node.

    Parameters
    ----------
    tx
        Active Neo4j transaction.
    asset_id
        Per-asset isolation key.
    source_uid
        The originating node's natural-key value (the ``value`` property
        for most nodes; ``iso`` for ``:Date``-to-``:Date`` chains, but
        that's not a supported case here).
    source_label
        The label of the originating node (e.g. ``"Form1"``, ``"Event"``).
        Used as a label-filter inside the MERGE so the wrong node type is
        not matched by accident.
    role
        Closed-enum ``DateRole`` value (e.g. ``"block_13"``, ``"event"``).
        Pass the enum's ``.value`` string.
    date_iso
        ``YYYY-MM-DD`` (other ISO 8601 forms with time are tolerated and
        truncated to date). If ``None`` or empty, the call is a no-op
        and returns ``None`` — date is genuinely unknown.

    Returns
    -------
    The ISO date string written, or ``None`` if no date was supplied.
    """
    if not date_iso:
        return None
    try:
        d = parse_iso_date(date_iso)
    except ValueError:
        # Unparseable date — non-fatal. The originating node already has
        # the raw string as a property (belt + suspenders, Q5). We just
        # don't materialise a :Date node for it. Log to stderr so a phase
        # operator can audit the data-quality if they care.
        import sys as _sys
        print(
            f"link_date: skipping unparseable date_iso={date_iso!r} "
            f"for {source_label}(value={source_uid!r}, role={role!r})",
            file=_sys.stderr,
        )
        return None

    iso = d.isoformat()
    result = tx.run(
        _LINK_DATE_CYPHER,
        asset_id=asset_id,
        source_uid=source_uid,
        source_label=source_label,
        iso=iso,
        year=d.year,
        month=d.month,
        day=d.day,
        dow=d.isoweekday(),  # 1 = Monday ... 7 = Sunday (ISO weekday)
        role=role,
        created_in_phase=current_phase(),
    )
    record = result.single()
    if record is None:
        # The source node didn't exist when we tried to link a date to it.
        # That's a programming error in the calling phase — write the
        # source node first, then call link_date.
        raise GoldenRuleViolation(
            label=source_label,
            value=source_uid,
            missing=f"source node not found (cannot link :Date {iso!r}, role={role!r})",
        )
    return record["iso"]
