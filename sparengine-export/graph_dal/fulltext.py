"""Lucene-syntax wrappers around the :Page.text fulltext index.

Phase 7.5 verification re-searches the corpus to find evidence the OCR
or Phase 4-6 missed (batch-range Form 1s, alternate PNs, sibling SNs,
etc.). The index is created in phases/schema.cypher on
``:Page.text + :Page.title``.

Lucene quick reference:
    "exact phrase"     — phrase search
    foo AND bar        — both terms
    foo OR bar         — either term
    foo NOT bar        — exclude
    foo*               — prefix wildcard
    field:value        — field-restricted (use sparingly; index spans 2 fields)
"""

from __future__ import annotations

from typing import Any


_SEARCH_PAGES_CYPHER = """
CALL db.index.fulltext.queryNodes("page_text", $lucene_query)
YIELD node, score
WHERE node.asset_id = $asset_id
RETURN node.value AS page_uid, node.page_index AS page_index, score
ORDER BY score DESC
LIMIT $limit
"""


def search_pages(
    session: Any, *, asset_id: str, query: str, limit: int = 25,
) -> list[dict]:
    """Run a Lucene query against :Page.text.

    Returns list of {"page_uid", "page_index", "score"} sorted by score desc.

    Note: the parameter name in Cypher is `lucene_query` (not `query`)
    because Neo4j's ``Session.run()`` reserves `query` as the first
    positional arg.
    """
    return [
        {"page_uid": r["page_uid"], "page_index": r["page_index"], "score": r["score"]}
        for r in session.run(_SEARCH_PAGES_CYPHER,
                              lucene_query=query, asset_id=asset_id, limit=limit)
    ]


def escape_lucene(s: str) -> str:
    """Escape Lucene special characters for use in a phrase query.

    Special chars (per Lucene 9.x): + - && || ! ( ) { } [ ] ^ " ~ * ? : \\ /
    """
    out = []
    for ch in s:
        if ch in r'+-&|!(){}[]^"~*?:\/':
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)
