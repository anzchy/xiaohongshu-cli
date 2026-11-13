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
