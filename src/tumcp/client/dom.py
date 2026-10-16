"""HTML helpers shared by the TUWEL and TISS parsers (selectolax, Lexbor backend).

selectolax 1.0 removed the old Modest backend (`selectolax.parser` raises on import).

Lexbor trap: `css("a, b")` returns a node once per comma-separated selector it
matches, so an element matching both comes back twice. Use `select()` for lists;
`css_first()` is unaffected. Decomposing a duplicate twice is not safe either.
"""
from __future__ import annotations

import re

from selectolax.lexbor import LexborHTMLParser, LexborNode

_WS = re.compile(r"\s+")


def parse(html: str | None) -> LexborHTMLParser:
    return LexborHTMLParser(html or "")


def select(node, query: str) -> list[LexborNode]:
    """`node.css(query)` without duplicates, in document order."""
    seen: set[int] = set()
    out: list[LexborNode] = []
    for found in node.css(query):
        if found.mem_id not in seen:
            seen.add(found.mem_id)
            out.append(found)
    return out


def drop(node, query: str) -> None:
    """Remove every element matching `query` below `node`."""
    for found in select(node, query):
        found.decompose()


def ancestor(node: LexborNode | None, tag: str) -> LexborNode | None:
    node = node.parent if node is not None else None
    while node is not None and node.tag != tag:
        node = node.parent
    return node


def clip(text: str, limit: int = 0) -> str:
    return text[:limit] + "…" if limit and len(text) > limit else text


def text(node: LexborNode | None, limit: int = 0) -> str:
    """Whitespace-normalised text of `node` ("" for None), clipped to `limit` chars."""
    if node is None:
        return ""
    return clip(_WS.sub(" ", node.text(separator=" ")).strip(), limit)


def strip_html(fragment: str | None, limit: int = 0) -> str:
    """Plain text of an HTML fragment (Moodle descriptions, notification bodies)."""
    return text(parse(fragment).body, limit)
