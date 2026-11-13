"""CLI tests for `xhs download` (seam C) — fake client, no network."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.cookies import save_note_index
from xhs_cli.exceptions import NeedVerifyError, XhsApiError

runner = CliRunner()


class FakeClient:
    """Notes without media, so no CDN traffic; records detail calls."""

    def __init__(self, fail: dict[str, Exception] | None = None):
        self.fail = fail or {}
        self.detail_calls: list[tuple[str, str]] = []

    def get_note_detail(self, note_id, xsec_token="", xsec_source=""):
        self.detail_calls.append((note_id, xsec_token))
        if note_id in self.fail:
            raise self.fail[note_id]
        return {"items": [{"note_card": {"title": f"T {note_id}", "image_list": [], "user": {}}}]}


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr("xhs_cli.commands.download.run_client_action", lambda ctx, action: action(client))
    return client


def run_json(args):
    result = runner.invoke(cli, ["download", *args, "--json"])
    return result, json.loads(result.output)


class TestDownloadCommand:
    def test_downloads_note_from_url_into_output_dir(self, fake_client, tmp_path):
        url = "https://www.xiaohongshu.com/explore/n1?xsec_token=tok1&xsec_source=app_share"

        result, payload = run_json([url, "-o", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert fake_client.detail_calls == [("n1", "tok1")]
        assert (tmp_path / "n1_T_n1" / "note.json").exists()
        [entry] = payload["data"]["results"]
        assert entry["note_id"] == "n1"
        assert entry["ok"] is True
        assert entry["folder"] == str(tmp_path / "n1_T_n1")


class TestBatch:
    def test_from_index_downloads_selected_entries_of_last_listing(self, fake_client, tmp_path):
        save_note_index([{"note_id": f"n{i}", "xsec_token": f"t{i}", "xsec_source": "pc_search"} for i in range(1, 6)])

        result, payload = run_json(["--from-index", "1-2,4", "-o", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert fake_client.detail_calls == [("n1", "t1"), ("n2", "t2"), ("n4", "t4")]

    def test_from_index_all(self, fake_client, tmp_path):
        save_note_index([{"note_id": f"n{i}", "xsec_token": "", "xsec_source": ""} for i in range(1, 4)])

        result, _ = run_json(["--from-index", "all", "-o", str(tmp_path)])

        assert [c[0] for c in fake_client.detail_calls] == ["n1", "n2", "n3"]

    def test_url_file_skips_blank_and_comment_lines(self, fake_client, tmp_path):
        urls = tmp_path / "urls.txt"
        urls.write_text(
            "# my list\n"
            "https://www.xiaohongshu.com/explore/a1?xsec_token=ta\n"
            "\n"
            "b2\n"
        )

        result, _ = run_json(["-f", str(urls), "-o", str(tmp_path)])

        assert result.exit_code == 0, result.output
        assert fake_client.detail_calls == [("a1", "ta"), ("b2", "")]

    def test_failed_note_is_reported_and_batch_continues(self, fake_client, tmp_path):
        fake_client.fail["b"] = XhsApiError("note gone")

        result, payload = run_json(["a", "b", "c", "-o", str(tmp_path)])

        assert result.exit_code == 1
        assert [(e["note_id"], e["ok"]) for e in payload["data"]["results"]] == [("a", True), ("b", False), ("c", True)]


class TestRateLimiting:
    def test_captcha_aborts_remaining_batch(self, fake_client, tmp_path):
        fake_client.fail["b"] = NeedVerifyError("captcha", "uuid")

        result, payload = run_json(["a", "b", "c", "-o", str(tmp_path)])

        assert result.exit_code == 1
        assert [c[0] for c in fake_client.detail_calls] == ["a", "b"]
        entries = payload["data"]["results"]
        assert [(e["note_id"], e["ok"]) for e in entries] == [("a", True), ("b", False), ("c", False)]
        assert entries[2]["error"].startswith("skipped")
        assert payload["data"]["aborted"] == "captcha"

    def test_global_delay_option_reaches_client(self, monkeypatch, tmp_path):
        created = {}

        class SpyClient:
            def __init__(self, cookies, **kwargs):
                created.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get_note_detail(self, note_id, **kw):
                return {"items": [{"note_card": {"title": "t", "image_list": []}}]}

        monkeypatch.setattr("xhs_cli.commands._common.get_cookies", lambda *a, **k: ("saved", {"a1": "x"}))
        monkeypatch.setattr("xhs_cli.commands._common.XhsClient", SpyClient)

        result = runner.invoke(cli, ["--delay", "3.5", "download", "n1", "-o", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        assert created["request_delay"] == 3.5
