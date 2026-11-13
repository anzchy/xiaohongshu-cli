"""Tests for comment tree export renderers (seam E)."""

from __future__ import annotations

import csv
import io
from datetime import datetime

from xhs_cli.comment_export import render_comments_csv, render_comments_md

TS = 1741343625000  # local-time rendering asserted via datetime below
TS_TEXT = datetime.fromtimestamp(TS / 1000).strftime("%Y-%m-%d %H:%M")


def c(cid, text, user, uid, *, likes="0", ip="浙江", subs=None, target=None, pictures=None):
    item = {
        "id": cid, "content": text, "create_time": TS, "like_count": likes, "ip_location": ip,
        "user_info": {"user_id": uid, "nickname": user}, "sub_comments": subs or [],
    }
    if target:
        item["target_comment"] = {"id": "x", "user_info": {"nickname": target}}
    if pictures:
        item["pictures"] = pictures
    return item


TREE = [
    c("c1", "电压力锅怎么选", "小明", "u1", likes="5", subs=[
        c("r1", "看内盖", "Lee", "author", ip="广东", target="小明"),
    ]),
    c("c2", "图里这款呢", "Amy", "u2", pictures=[{"url_default": "https://img/1.jpg"}]),
]


class TestMarkdown:
    def test_nested_markdown_marks_author_and_reply_target(self):
        md = render_comments_md(TREE, author_id="author")

        assert md.splitlines() == [
            f"- **小明**（浙江 · {TS_TEXT} · 👍5）：电压力锅怎么选",
            f"    - **Lee 【作者】** → @小明（广东 · {TS_TEXT} · 👍0）：看内盖",
            f"- **Amy**（浙江 · {TS_TEXT} · 👍0）：图里这款呢 ![](https://img/1.jpg)",
        ]

    def test_multiline_comment_stays_inside_its_list_item(self):
        md = render_comments_md([c("c1", "第一行\n第二行", "A", "u")], author_id="x")

        assert md.splitlines() == [f"- **A**（浙江 · {TS_TEXT} · 👍0）：第一行 第二行"]


class TestCsv:
    def test_flat_csv_has_one_row_per_comment_with_parent_id(self):
        rows = list(csv.DictReader(io.StringIO(render_comments_csv(TREE, author_id="author"))))

        assert [(r["id"], r["parent_id"], r["is_author"]) for r in rows] == [
            ("c1", "", "false"),
            ("r1", "c1", "true"),
            ("c2", "", "false"),
        ]
        assert rows[1]["reply_to"] == "小明"
        assert rows[0]["likes"] == "5"
        assert rows[0]["time"] == TS_TEXT
        assert rows[2]["content"] == "图里这款呢"


def test_author_detected_from_show_tags_without_author_id():
    tagged = c("c9", "作者回复", "Lee", "author")
    tagged["show_tags"] = ["is_author", "user_top"]

    assert "**Lee 【作者】**" in render_comments_md([tagged])
    assert "true" in render_comments_csv([tagged]).splitlines()[1]
