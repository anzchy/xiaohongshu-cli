"""Render a comment tree (top-level comments with ``sub_comments``) as Markdown or CSV."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import datetime
from typing import Any

CSV_FIELDS = ["id", "parent_id", "user", "user_id", "is_author", "reply_to", "time", "ip_location", "likes", "content"]


def _time(comment: dict[str, Any]) -> str:
    ms = comment.get("create_time")
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M") if ms else ""


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _reply_to(comment: dict[str, Any]) -> str:
    return (comment.get("target_comment") or {}).get("user_info", {}).get("nickname", "")


def _is_author(comment: dict[str, Any], author_id: str) -> bool:
    if "is_author" in (comment.get("show_tags") or []):
        return True
    return bool(author_id) and comment.get("user_info", {}).get("user_id") == author_id


def _walk(comments: list[dict[str, Any]]) -> Iterator[tuple[dict[str, Any], str]]:
    for top in comments:
        yield top, ""
        for reply in top.get("sub_comments") or []:
            yield reply, top.get("id", "")


def _md_line(comment: dict[str, Any], author_id: str) -> str:
    user = comment.get("user_info", {})
    name = user.get("nickname", "")
    if _is_author(comment, author_id):
        name += " 【作者】"
    target = _reply_to(comment)
    to = f" → @{target}" if target else ""
    meta = f"（{comment.get('ip_location', '')} · {_time(comment)} · 👍{comment.get('like_count', 0)}）"
    pics = "".join(
        f" ![]({p.get('url_default') or p.get('url_pre') or p.get('url', '')})" for p in comment.get("pictures") or []
    )
    return f"**{name}**{to}{meta}：{_one_line(comment.get('content', ''))}{pics}"


def render_comments_md(comments: list[dict[str, Any]], *, author_id: str = "") -> str:
    """Nested Markdown list: top-level comments with their replies indented beneath."""
    lines = []
    for comment, parent in _walk(comments):
        indent = "    " if parent else ""
        lines.append(f"{indent}- {_md_line(comment, author_id)}")
    return "\n".join(lines) + ("\n" if lines else "")


def render_comments_csv(comments: list[dict[str, Any]], *, author_id: str = "") -> str:
    """Flat CSV, one row per comment or reply, replies linked by ``parent_id``."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for comment, parent in _walk(comments):
        user = comment.get("user_info", {})
        writer.writerow({
            "id": comment.get("id", ""),
            "parent_id": parent,
            "user": user.get("nickname", ""),
            "user_id": user.get("user_id", ""),
            "is_author": "true" if _is_author(comment, author_id) else "false",
            "reply_to": _reply_to(comment),
            "time": _time(comment),
            "ip_location": comment.get("ip_location", ""),
            "likes": comment.get("like_count", 0),
            "content": comment.get("content", ""),
        })
    return buf.getvalue()
