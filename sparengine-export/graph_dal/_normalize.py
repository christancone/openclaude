"""Shared identifier normalisation helper.

One canonical function for folding OCR variants of PartNumber / SerialNumber
strings into a comparable form. Used by:

  * connector.py    — at write time, populates ``n.normalized`` on every
                      :PartNumber / :SerialNumber so downstream phases can
                      join alias-resiliently without re-deriving.
  * phase 6 aliases — groups nodes whose normalised form matches to MERGE
                      ALIAS_OF edges (kind='ocr_separator' / 'ocr_variant').
  * phase 7 search  — pre-folds Lucene queries so a search for
                      `MAY18-4688` also returns pages where the OCR wrote
                      `MAY184688` or `MAY18_4688`.

Two strictness levels are provided. Use ``normalize_identifier`` for high-
confidence folding (whitespace, unicode hyphens, separator characters,
case). Use ``normalize_identifier_aggressive`` only for second-pass alias
detection (also folds OCR letter-digit confusables O→0, I→1, S→5); this is
prone to false positives and must be tagged with lower confidence.
"""

from __future__ import annotations

import re

# Unicode hyphen/dash variants the OCR commonly produces.
_HYPHENS = "‐‑‒–—―−"

# Characters treated as separators that vanish under high-confidence folding.
_SEPARATORS = " _/.\t"

_WS_RE = re.compile(r"\s+")


def normalize_identifier(value: str | None) -> str | None:
    """Strict, separator-only normalisation.

    Returns None for None / empty input. Otherwise:
      1. Strip and uppercase.
      2. Fold unicode hyphens to ASCII '-'.
      3. Strip all whitespace.
      4. Replace '_', '/', '.', ASCII '-' with empty (so `MAY18-4688`,
         `MAY18_4688`, `MAY184688` all collapse to `MAY184688`).

    This is the safe default — used for write-time `n.normalized` and for
    join-time comparison.
    """
    if not value:
        return None
    s = value.strip().upper()
    for h in _HYPHENS:
        s = s.replace(h, "-")
    s = _WS_RE.sub("", s)
    for sep in _SEPARATORS:
        s = s.replace(sep, "")
    s = s.replace("-", "")
    return s or None


def normalize_identifier_aggressive(value: str | None) -> str | None:
    """Second-pass folding that additionally collapses OCR confusables.

    O → 0, I → 1, l → 1, S → 5, B → 8. Use ONLY for surfacing candidate
    aliases for review — never as the canonical form. Confidence must be
    tagged 'medium' or 'ambiguous' on any edge derived from this.
    """
    base = normalize_identifier(value)
    if base is None:
        return None
    confusables = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8"})
    return base.translate(confusables)


_DATE_DD_MMM_YYYY = re.compile(r"^\s*(\d{1,2})[/\-\s](\w{3,9})[/\-\s](\d{4})\s*$")
_MONTHS = {
    "jan":"01","feb":"02","mar":"03","apr":"04","may":"05","jun":"06",
    "jul":"07","aug":"08","sep":"09","sept":"09","oct":"10","nov":"11","dec":"12",
    "january":"01","february":"02","march":"03","april":"04","june":"06","july":"07",
    "august":"08","september":"09","october":"10","november":"11","december":"12",
}


def normalize_date(value: str | None) -> str | None:
    """Convert common Form 1 date formats to ISO yyyy-mm-dd.

    Handles ``22/Oct/2019``, ``22-Oct-2019``, ``22 Oct 2019`` (block 14e
    EASA/FAA convention), and passes through already-ISO ``2019-10-22``.
    """
    if not value:
        return None
    s = value.strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    m = _DATE_DD_MMM_YYYY.match(s)
    if m:
        dd, mmm, yyyy = m.group(1), m.group(2), m.group(3)
        mm = _MONTHS.get(mmm.lower())
        if mm:
            return f"{yyyy}-{mm}-{int(dd):02d}"
    return None


def normalize_for_fulltext(value: str | None) -> str | None:
    """Normalisation specifically for fulltext-search queries.

    Same as ``normalize_identifier`` but preserves a few characters that
    Lucene tokenisers split on but humans expect to match: digits stay
    grouped without separators, letters stay uppercased.
    """
    return normalize_identifier(value)


# =============================================================================
#  Noise-identifier blocklist
# =============================================================================
#
# Per Lukas Audit Cheatsheet §13 and `references/data_quality_rules.md`, the
# OCR commonly fills empty cells with placeholder text. These are NOT real
# identifiers and writing them as :SerialNumber or :PartNumber nodes creates
# the classic noise-fanout bug — a single phantom node attracts every PN/SN
# co-mentioned with the placeholder, exploding into thousands of false
# Components.
#
# The CL650-6134 case study: 90 :Component nodes attached to one
# :SerialNumber{value:'N/A'} after the previous build. None were real
# physical parts — all bulk-cert or non-serialised OEM hardware.
#
# This is the universal blocklist applied by:
#   - connector.py:write_serial_number / write_part_number  (write-time gate)
#   - Phase 1 row processing                                (don't even attempt the write)
#   - Phase 4 Component materialisation                     (no Component for noise SNs)

# Exact case-insensitive sentinels.
_NOISE_LITERALS = frozenset({
    "", "N/A", "NA", "N\\A", "N.A.", "N.A",
    "NONE", "NIL", "NULL",
    "TBD", "TBA", "TO BE DETERMINED", "TO BE ASSIGNED",
    "UNKNOWN", "UNK", "UNDETERMINED",
    "-", "--", "---", "_", "__", "___",
    "VARIOUS", "SEE BLOCK 12", "SEE BLOCK 11",
    "SEE REMARKS", "SEE NOTES", "SEE REVERSE",
    "REFER TO BLOCK 12", "REFER TO REMARKS",
    "BATCH", "LOT",
    "NOT APPLICABLE", "NOT AVAILABLE", "NOT ASSIGNED",
    "NA / NA", "X", "XX", "XXX",
    "?", "??", "???",
    "0", "00", "000",                       # placeholder zeros (real SNs are longer)
})

# Regex patterns for noise — applied to UPPERCASED, stripped value.
_NOISE_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}$"),     # date strings
    re.compile(r"^\d{2}/\d{2}/\d{2,4}$"),   # alt date format
    re.compile(r"^(19|20)\d{2}$"),          # year strings 1900-2099
    re.compile(r"^\d+\.\d+$"),              # decimal numbers (likely measurements)
    re.compile(r"^\s+$"),                   # all whitespace
    re.compile(r"^[\W_]+$"),                # all non-word chars
]


def is_noise_identifier(value: str | None) -> bool:
    """Return True if ``value`` is OCR placeholder/sentinel text, not a real identifier.

    Used as a write-time gate in connector.py and Phase 1/4 to prevent
    fanout from phantom :SerialNumber/:PartNumber nodes. Lukas Cheatsheet
    §13 lists these as the "never flag" sentinels — they're system
    placeholders for "no SN" or "no PN", not data.

    Examples that return True:
        "N/A", "n/a", "NONE", "TBD", "Unknown", "-", "--", "X",
        "2019-08-27", "08/14/22", "2020",
        "", "  ", "?", "see remarks"

    Examples that return False (real identifiers):
        "024453-000", "AA441157", "090520021095E", "4FM4CT", "3990",
        "MAY18-4688", "MS29513-219", "GG468-4054-15", "Lot: 11518"
        (lot numbers are real — they go to :BatchNumber, not :SerialNumber)
    """
    if value is None:
        return True
    s = str(value).strip()
    if not s:
        return True
    s_up = s.upper()
    if s_up in _NOISE_LITERALS:
        return True
    # Length-2 single-letter prefixes etc. are too generic to be real SNs.
    if len(s) <= 2 and not s.isdigit():
        return True
    if len(s) <= 1:
        return True
    for rx in _NOISE_PATTERNS:
        if rx.match(s):
            return True
    # "Challenger" / "see ..." / "part ..." prefixes carried over from v7 rules
    if any(s_up.startswith(p) for p in ("SEE ", "REFER ", "PART ", "SERIAL ", "BATCH ", "LOT ")):
        # but "Lot: 11518" is real — only flag as noise if there's no digit afterwards
        if not re.search(r"\d", s):
            return True
    if "CHALLENGER" in s_up:
        return True
    return False
