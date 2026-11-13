"""Tests for export_notes — incremental archive of a paged note listing (seam D)."""

from __future__ import annotations

import json

import httpx
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.download import export_notes
from xhs_cli.exceptions import NeedVerifyError

PAGES = {
    "": {"notes": [{"note_id": "n1", "xsec_token": "t1"}, {"note_id": "n2", "xsec_token": "t2"}],
         "has_more": True, "cursor": "p2"},
    "p2": {"notes": [{"note_id": "n3", "xsec_token": "t3"}], "has_more": False, "cursor": ""},
}


class FakeClient:
    def __init__(self, captcha_on: str = ""):
        self.captcha_on = captcha_on
        self.details: list[tuple[str, str]] = []

    def get_note_detail(self, note_id, xsec_token="", xsec_source=""):
        self.details.append((note_id, xsec_token))
        if note_id == self.captcha_on:
            raise NeedVerifyError("captcha", "u")
        return {"items": [{"note_card": {"title": note_id.upper(), "image_list": []}}]}


_RealClient = httpx.Client


def no_cdn(**_kwargs) -> httpx.Client:
    return _RealClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))


def run(client, tmp_path):
    return export_notes(client, lambda cursor: PAGES[cursor], tmp_path, http=no_cdn())


class TestExportNotes:
    def test_exports_every_page_with_each_notes_token(self, tmp_path):
        client = FakeClient()

        result = run(client, tmp_path)

        assert client.details == [("n1", "t1"), ("n2", "t2"), ("n3", "t3")]
        assert result.downloaded == ["n1", "n2", "n3"]
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert sorted(manifest) == ["n1", "n2", "n3"]
        assert manifest["n1"]["folder"] == "n1_N1"

    def test_rerun_only_fetches_notes_missing_from_manifest(self, tmp_path):
        (tmp_path / "manifest.json").write_text(json.dumps({"n1": {"folder": "n1_N1"}, "n3": {"folder": "n3_N3"}}))
        client = FakeClient()

        result = run(client, tmp_path)

        assert client.details == [("n2", "t2")]
        assert (result.downloaded, result.already_archived) == (["n2"], 2)

    def test_captcha_stops_export_and_keeps_progress(self, tmp_path):
        client = FakeClient(captcha_on="n2")

        result = run(client, tmp_path)

        assert result.aborted == "captcha"
        assert [c[0] for c in client.details] == ["n1", "n2"]
        assert sorted(json.loads((tmp_path / "manifest.json").read_text())) == ["n1"]


class TestExportCommands:
    def test_export_user_pages_through_user_posts(self, monkeypatch, tmp_path):
        calls = []

        class Client(FakeClient):
            def get_user_notes(self, user_id, cursor=""):
                calls.append((user_id, cursor))
                return PAGES[cursor]

        client = Client()
        monkeypatch.setattr("xhs_cli.commands.download.run_client_action", lambda ctx, action: action(client))
        monkeypatch.setattr("xhs_cli.download.httpx.Client", no_cdn)

        result = CliRunner().invoke(cli, ["export-user", "u42", "-o", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        assert calls == [("u42", ""), ("u42", "p2")]
        assert json.loads(result.output)["data"]["downloaded"] == ["n1", "n2", "n3"]

    def test_export_favorites_defaults_to_current_user(self, monkeypatch, tmp_path):
        calls = []

        class Client(FakeClient):
            def get_self_info(self):
                return {"user_id": "me"}

            def get_user_favorites(self, user_id, cursor=""):
                calls.append((user_id, cursor))
                return PAGES[cursor]

        client = Client()
        monkeypatch.setattr("xhs_cli.commands.download.run_client_action", lambda ctx, action: action(client))
        monkeypatch.setattr("xhs_cli.download.httpx.Client", no_cdn)

        result = CliRunner().invoke(cli, ["export-favorites", "-o", str(tmp_path), "--json"])

        assert result.exit_code == 0, result.output
        assert calls[0] == ("me", "")


def test_note_with_failed_media_is_not_archived_and_is_retried(tmp_path):
    class WithImage(FakeClient):
        def get_note_detail(self, note_id, xsec_token="", xsec_source=""):
            self.details.append((note_id, xsec_token))
            images = [{"file_id": f"f/{note_id}"}] if note_id == "n2" else []
            return {"items": [{"note_card": {"title": note_id.upper(), "image_list": images}}]}

    client = WithImage()
    result = run(client, tmp_path)  # CDN returns 404 for everything

    assert result.downloaded == ["n1", "n3"]
    assert result.failed == {"n2": "media failed: image 01"}
    assert sorted(json.loads((tmp_path / "manifest.json").read_text())) == ["n1", "n3"]

    client.details.clear()
    run(client, tmp_path)
    assert [c[0] for c in client.details] == ["n2"]


def test_corrupt_manifest_is_backed_up_and_export_continues(tmp_path):
    (tmp_path / "manifest.json").write_text('{"n1": {"fol')

    result = run(FakeClient(), tmp_path)

    assert result.downloaded == ["n1", "n2", "n3"]
    assert (tmp_path / "manifest.json.corrupt").read_text() == '{"n1": {"fol'
    assert sorted(json.loads((tmp_path / "manifest.json").read_text())) == ["n1", "n2", "n3"]


def test_failed_manifest_write_keeps_previous_manifest_intact(tmp_path, monkeypatch):
    from pathlib import Path

    import pytest

    previous = '{"n0": {"folder": "n0_X"}}'
    (tmp_path / "manifest.json").write_text(previous)

    def crash(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", crash)
    with pytest.raises(OSError):
        export_notes(FakeClient(), lambda c: PAGES[c], tmp_path, http=no_cdn())

    assert (tmp_path / "manifest.json").read_text() == previous


def test_repeating_cursor_stops_export(tmp_path):
    calls = []

    def stuck(cursor):
        calls.append(cursor)
        if len(calls) > 10:
            raise AssertionError("export did not stop on a repeating cursor")
        return {"notes": [], "has_more": True, "cursor": "same"}

    result = export_notes(FakeClient(), stuck, tmp_path, http=no_cdn())

    assert result.aborted == "pagination loop"
    assert calls == ["", "same"]


def test_export_command_exits_nonzero_and_names_reason_on_pagination_loop(monkeypatch, tmp_path):
    class Stuck(FakeClient):
        def get_user_notes(self, user_id, cursor=""):
            return {"notes": [], "has_more": True, "cursor": "same"}

    client = Stuck()
    monkeypatch.setattr("xhs_cli.commands.download.run_client_action", lambda ctx, action: action(client))
    monkeypatch.setattr("xhs_cli.download.httpx.Client", no_cdn)
    monkeypatch.setenv("OUTPUT", "rich")

    result = CliRunner().invoke(cli, ["export-user", "u1", "-o", str(tmp_path)])

    assert result.exit_code == 1
    assert "pagination loop" in result.output
    assert "captcha" not in result.output
