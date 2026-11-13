"""Tests for CLI commands using Click's test runner."""

import pytest
import yaml
from click.testing import CliRunner

from xhs_cli.cli import cli
from xhs_cli.exceptions import NoCookieError, SessionExpiredError, UnsupportedOperationError

runner = CliRunner()

FAKE_NOTE_RESPONSE = {
    "items": [
        {
            "note_card": {
                "title": "Test Note",
                "desc": "body",
                "user": {"nickname": "Author"},
                "interact_info": {
                    "liked_count": "100",
                    "collected_count": "50",
                    "comment_count": "10",
                    "share_count": "5",
                },
                "tag_list": [],
                "image_list": [],
            }
        }
    ]
}


class TestCliBasic:
    """Test CLI basics without requiring cookies."""

    def test_version(self):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0." in result.output  # dynamic version from importlib.metadata

    def test_help(self):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "xhs" in result.output
        assert "search" in result.output
        assert "read" in result.output

    def test_search_help(self):
        result = runner.invoke(cli, ["search", "--help"])
        assert result.exit_code == 0
        assert "keyword" in result.output.lower() or "KEYWORD" in result.output

    def test_read_help(self):
        result = runner.invoke(cli, ["read", "--help"])
        assert result.exit_code == 0

    def test_login_help(self):
        result = runner.invoke(cli, ["login", "--help"])
        assert result.exit_code == 0

    def test_status_help(self):
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0

    def test_all_commands_registered(self):
        result = runner.invoke(cli, ["--help"])
        commands_expected = [
            # Auth
            "login", "status", "logout", "whoami",
            # Reading
            "search", "read", "comments", "sub-comments", "user", "user-posts",
            "feed", "hot", "topics", "search-user", "my-notes",
            "notifications", "unread",
            # Interactions
            "like", "favorite", "unfavorite", "comment", "reply", "delete-comment",
            # Social
            "follow", "unfollow", "favorites",
            # Creator
            "post", "delete",
        ]
        for cmd in commands_expected:
            assert cmd in result.output, f"Command '{cmd}' not found in CLI help"

    def test_whoami_help(self):
        result = runner.invoke(cli, ["whoami", "--help"])
        assert result.exit_code == 0

    def test_hot_help(self):
        result = runner.invoke(cli, ["hot", "--help"])
        assert result.exit_code == 0
        assert "category" in result.output.lower()

    def test_unread_help(self):
        result = runner.invoke(cli, ["unread", "--help"])
        assert result.exit_code == 0

    def test_my_notes_help(self):
        result = runner.invoke(cli, ["my-notes", "--help"])
        assert result.exit_code == 0

    def test_status_auto_yaml_when_stdout_is_not_tty(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "auto")
        monkeypatch.setattr(
            "xhs_cli.commands.auth.run_client_action",
            lambda ctx, action: {"nickname": "Alice", "red_id": "alice001"},
        )

        result = runner.invoke(cli, ["status"])

        assert result.exit_code == 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is True
        assert payload["schema_version"] == "1"
        assert payload["data"]["authenticated"] is True
        assert payload["data"]["user"]["name"] == "Alice"

    def test_whoami_auto_yaml_when_stdout_is_not_tty(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "auto")
        monkeypatch.setattr(
            "xhs_cli.commands.auth.run_client_action",
            lambda ctx, action: {"nickname": "Alice", "red_id": "alice001", "user_id": "u-1"},
        )

        result = runner.invoke(cli, ["whoami"])

        assert result.exit_code == 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is True
        assert payload["data"]["user"]["username"] == "alice001"

    def test_read_error_yaml_when_not_logged_in(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "auto")
        monkeypatch.setattr(
            "xhs_cli.commands._common.get_cookies",
            lambda source, force_refresh=False: (_ for _ in ()).throw(NoCookieError(source)),
        )

        result = runner.invoke(cli, ["read", "abc", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "not_authenticated"

    def test_status_reports_not_authenticated_when_session_expired(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "auto")

        def fake_run_client_action(ctx, action):
            raise SessionExpiredError()

        monkeypatch.setattr("xhs_cli.commands.auth.run_client_action", fake_run_client_action)

        result = runner.invoke(cli, ["status", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "not_authenticated"

    def test_logout_supports_structured_output(self):
        from xhs_cli.commands import auth

        original_clear_cookies = auth.clear_cookies
        auth.clear_cookies = lambda: None
        try:
            result = runner.invoke(cli, ["logout", "--yaml"])
        finally:
            auth.clear_cookies = original_clear_cookies

        assert result.exit_code == 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is True
        assert payload["data"]["logged_out"] is True

    def test_delete_reports_unsupported_operation(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.commands.creator.run_client_action",
            lambda ctx, action: (_ for _ in ()).throw(
                UnsupportedOperationError("Delete note is currently unavailable from the public web API.")
            ),
        )

        result = runner.invoke(cli, ["delete", "note-123", "--yes", "--yaml"])

        assert result.exit_code != 0
        payload = yaml.safe_load(result.output)
        assert payload["ok"] is False
        assert payload["error"]["code"] == "unsupported_operation"

    def test_comments_rich_output_handles_string_reply_counts(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        monkeypatch.setattr(
            "xhs_cli.commands.reading.run_client_action",
            lambda ctx, action: {
                "comments": [
                    {
                        "user_info": {"nickname": "tester"},
                        "content": "hello",
                        "like_count": "12",
                        "sub_comment_count": "2",
                    }
                ]
            },
        )

        result = runner.invoke(cli, ["comments", "note-123"])

        assert result.exit_code == 0
        assert "tester" in result.output
        assert "2 replies" in result.output

    def test_search_rich_output_shortens_visible_links(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        monkeypatch.setattr(
            "xhs_cli.commands.reading.handle_command",
            lambda ctx, action, render, as_json, as_yaml: render({
                "items": [
                    {
                        "id": "69ad061d000000002603326d",
                        "xsec_token": "very-long-token-value",
                        "note_card": {
                            "title": "测试标题",
                            "user": {"nickname": "tester"},
                            "interact_info": {"liked_count": "12"},
                            "type": "normal",
                        },
                    }
                ],
                "has_more": False,
            }),
        )

        result = runner.invoke(cli, ["search", "openclaw"])

        assert result.exit_code == 0
        assert "search_result/69ad061d" in result.output
        assert "very-long-token-value" not in result.output

    def test_feed_rich_output_shortens_visible_links(self, monkeypatch):
        monkeypatch.setenv("OUTPUT", "rich")
        monkeypatch.setattr(
            "xhs_cli.commands.reading.handle_command",
            lambda ctx, action, render, as_json, as_yaml: render({
                "items": [
                    {
                        "id": "69ad061d000000002603326d",
                        "xsec_token": "another-very-long-token",
                        "note_card": {
                            "title": "推荐内容",
                            "user": {"nickname": "tester"},
                            "interact_info": {"liked_count": "9"},
                        },
                    }
                ]
            }),
        )

        result = runner.invoke(cli, ["feed"])

        assert result.exit_code == 0
        assert "explore/69ad061d" in result.output
        assert "another-very-long-token" not in result.output

    def test_read_help_mentions_short_index(self):
        result = runner.invoke(cli, ["read", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output.lower()

    def test_comments_help_mentions_short_index(self):
        result = runner.invoke(cli, ["comments", "--help"])
        assert result.exit_code == 0
        assert "index" in result.output.lower()

    def test_read_index_resolves_note_context(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.note_refs.get_note_by_index",
            lambda idx: {
                "note_id": "note-abc",
                "xsec_token": "token-abc",
                "xsec_source": "pc_search",
            } if idx == 1 else None,
        )

        called = {}

        class FakeClient:
            def get_note_detail(self, note_id, **kwargs):
                called["note_id"] = note_id
                called["kwargs"] = kwargs
                return FAKE_NOTE_RESPONSE

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            action(FakeClient())
            return None

        monkeypatch.setattr("xhs_cli.commands.reading.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["read", "1"])

        assert result.exit_code == 0
        assert called["note_id"] == "note-abc"
        assert called["kwargs"]["xsec_token"] == "token-abc"
        assert called["kwargs"]["xsec_source"] == "pc_search"

    def test_comments_index_resolves_note_context(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.note_refs.get_note_by_index",
            lambda idx: {
                "note_id": "note-abc",
                "xsec_token": "token-abc",
                "xsec_source": "pc_search",
            } if idx == 1 else None,
        )

        called = {}

        class FakeClient:
            def get_comments(self, note_id, cursor="", **kwargs):
                called["note_id"] = note_id
                called["cursor"] = cursor
                called["kwargs"] = kwargs
                return {"comments": []}

        def fake_run_client_action(ctx, action):
            return action(FakeClient())

        monkeypatch.setattr("xhs_cli.commands.reading.run_client_action", fake_run_client_action)

        result = runner.invoke(cli, ["comments", "1", "--yaml"])

        assert result.exit_code == 0
        assert called["note_id"] == "note-abc"
        assert called["kwargs"]["xsec_token"] == "token-abc"
        assert called["kwargs"]["xsec_source"] == "pc_search"

    def test_read_index_not_found_returns_usage_error(self, monkeypatch):
        monkeypatch.setattr("xhs_cli.note_refs.get_note_by_index", lambda idx: None)

        result = runner.invoke(cli, ["read", "999"])

        assert result.exit_code != 0
        assert "999" in result.output

    def test_search_empty_results_clear_previous_index(self, monkeypatch):
        from xhs_cli.note_refs import save_index_from_items

        saved = []
        monkeypatch.setattr("xhs_cli.note_refs.save_note_index", lambda items: saved.append(items))

        save_index_from_items({"items": []}, xsec_source="pc_search")

        assert saved == [[]]

    def test_user_posts_saves_index_entries(self, monkeypatch):
        saved = []
        monkeypatch.setattr("xhs_cli.note_refs.save_note_index", lambda items: saved.append(items))

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            class FakeClient:
                def get_user_notes(self, user_id, cursor=""):
                    return {
                        "notes": [
                            {"note_id": "note-1"},
                            {"note_id": "note-2", "xsec_token": "ignored"},
                        ],
                        "has_more": False,
                        "cursor": "",
                    }

            data = action(FakeClient())
            render(data)
            return None

        monkeypatch.setattr("xhs_cli.commands.reading.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["user-posts", "user-1"])

        assert result.exit_code == 0
        assert saved == [[
            {"note_id": "note-1", "xsec_token": "", "xsec_source": ""},
            {"note_id": "note-2", "xsec_token": "ignored", "xsec_source": ""},
        ]]

    @pytest.mark.parametrize(
        ("command", "extra_args", "method_name"),
        [
            ("like", [], "like_note"),
            ("like", ["--undo"], "unlike_note"),
            ("favorite", [], "favorite_note"),
            ("unfavorite", [], "unfavorite_note"),
        ],
    )
    def test_short_index_resolves_for_note_actions(self, monkeypatch, command, extra_args, method_name):
        monkeypatch.setattr(
            "xhs_cli.note_refs.get_note_by_index",
            lambda idx: {
                "note_id": "note-abc",
                "xsec_token": "token-abc",
                "xsec_source": "pc_search",
            } if idx == 1 else None,
        )

        called = {}

        class FakeClient:
            def __getattr__(self, name):
                if name != method_name:
                    raise AttributeError(name)

                def _call(note_id):
                    called["method"] = name
                    called["note_id"] = note_id
                    return {"ok": True}

                return _call

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            action(FakeClient())
            return None

        monkeypatch.setattr("xhs_cli.commands.interactions.handle_command", fake_handle_command)

        result = runner.invoke(cli, [command, "1", *extra_args])

        assert result.exit_code == 0
        assert called == {"method": method_name, "note_id": "note-abc"}

    def test_short_index_resolves_for_comment(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.note_refs.get_note_by_index",
            lambda idx: {
                "note_id": "note-abc",
                "xsec_token": "token-abc",
                "xsec_source": "pc_search",
            } if idx == 1 else None,
        )

        called = {}

        class FakeClient:
            def post_comment(self, note_id, content):
                called["note_id"] = note_id
                called["content"] = content
                return {"ok": True}

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            action(FakeClient())
            return None

        monkeypatch.setattr("xhs_cli.commands.interactions.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["comment", "1", "-c", "hello"])

        assert result.exit_code == 0
        assert called == {"note_id": "note-abc", "content": "hello"}

    def test_short_index_resolves_for_reply(self, monkeypatch):
        monkeypatch.setattr(
            "xhs_cli.note_refs.get_note_by_index",
            lambda idx: {
                "note_id": "note-abc",
                "xsec_token": "token-abc",
                "xsec_source": "pc_search",
            } if idx == 1 else None,
        )

        called = {}

        class FakeClient:
            def reply_comment(self, note_id, comment_id, content):
                called["note_id"] = note_id
                called["comment_id"] = comment_id
                called["content"] = content
                return {"ok": True}

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            action(FakeClient())
            return None

        monkeypatch.setattr("xhs_cli.commands.interactions.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["reply", "1", "--comment-id", "c-1", "-c", "hello"])

        assert result.exit_code == 0
        assert called == {"note_id": "note-abc", "comment_id": "c-1", "content": "hello"}

    def test_favorites_saves_index_entries(self, monkeypatch):
        saved = []
        monkeypatch.setattr("xhs_cli.note_refs.save_note_index", lambda items: saved.append(items))
        monkeypatch.setattr("xhs_cli.commands.social._resolve_user_id", lambda ctx, user_id: "user-1")

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            class FakeClient:
                def get_user_favorites(self, user_id, cursor=""):
                    return {
                        "notes": [
                            {"note_id": "note-1"},
                            {"id": "note-2", "xsec_token": "token-2"},
                        ],
                        "has_more": False,
                        "cursor": "",
                    }

            data = action(FakeClient())
            render(data)
            return None

        monkeypatch.setattr("xhs_cli.commands.social.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["favorites"])

        assert result.exit_code == 0
        assert saved == [[
            {"note_id": "note-1", "xsec_token": "", "xsec_source": ""},
            {"note_id": "note-2", "xsec_token": "token-2", "xsec_source": ""},
        ]]

    def test_my_notes_saves_index_entries(self, monkeypatch):
        saved = []
        monkeypatch.setattr("xhs_cli.note_refs.save_note_index", lambda items: saved.append(items))

        def fake_handle_command(ctx, action, render, as_json, as_yaml):
            class FakeClient:
                def get_creator_note_list(self, page=0):
                    return {
                        "note_list": [
                            {"note_id": "note-1"},
                            {"id": "note-2"},
                        ]
                    }

            data = action(FakeClient())
            render(data)
            return None

        monkeypatch.setattr("xhs_cli.commands.creator.handle_command", fake_handle_command)

        result = runner.invoke(cli, ["my-notes"])

        assert result.exit_code == 0
        assert saved == [[
            {"note_id": "note-1", "xsec_token": "", "xsec_source": ""},
            {"note_id": "note-2", "xsec_token": "", "xsec_source": ""},
        ]]


class TestSubCommentsCommand:
    def _fake_client(self, monkeypatch):
        called = {}

        class FakeClient:
            def get_sub_comments(self, note_id, comment_id, cursor="", **kwargs):
                called.update(note_id=note_id, comment_id=comment_id, cursor=cursor, kwargs=kwargs)
                return {"comments": []}

        monkeypatch.setattr(
            "xhs_cli.commands._common.run_client_action", lambda ctx, action: action(FakeClient())
        )
        return called

    def test_sub_comments_accepts_note_url_and_passes_its_token(self, monkeypatch):
        called = self._fake_client(monkeypatch)
        url = "https://www.xiaohongshu.com/explore/note-abc?xsec_token=tok-url&xsec_source=app_share"

        result = runner.invoke(cli, ["sub-comments", url, "cmt-1", "--json"])

        assert result.exit_code == 0, result.output
        assert called["note_id"] == "note-abc"
        assert called["comment_id"] == "cmt-1"
        assert called["kwargs"]["xsec_token"] == "tok-url"

    def test_sub_comments_accepts_explicit_xsec_token(self, monkeypatch):
        called = self._fake_client(monkeypatch)

        result = runner.invoke(cli, ["sub-comments", "note-abc", "cmt-1", "--xsec-token", "tok-x", "--json"])

        assert result.exit_code == 0, result.output
        assert called["kwargs"]["xsec_token"] == "tok-x"


class TestCommentsExport:
    URL = "https://www.xiaohongshu.com/explore/note-abc?xsec_token=tok-url&xsec_source=app_share"

    def _fake(self, monkeypatch):
        calls = []

        class FakeClient:
            def get_all_comments(self, note_id, **kwargs):
                calls.append(("all", note_id, kwargs.get("xsec_token")))
                return {"comments": [{
                    "id": "c1", "content": "问", "user_info": {"nickname": "A", "user_id": "u"},
                    "sub_comments": [], "sub_comment_has_more": True, "sub_comment_cursor": "",
                }]}

            def get_sub_comments(self, note_id, comment_id, cursor="", xsec_token="", **kw):
                calls.append(("sub", comment_id, xsec_token))
                return {"comments": [{"id": "r1", "content": "答", "user_info": {"nickname": "B"},
                                      "show_tags": ["is_author"]}], "has_more": False, "cursor": ""}

        monkeypatch.setattr("xhs_cli.commands.reading.run_client_action", lambda ctx, action: action(FakeClient()))
        return calls

    def test_comments_md_prints_full_tree(self, monkeypatch):
        calls = self._fake(monkeypatch)

        result = runner.invoke(cli, ["comments", self.URL, "--md"])

        assert result.exit_code == 0, result.output
        assert "- **A**" in result.output
        assert "    - **B 【作者】**" in result.output
        assert ("sub", "c1", "tok-url") in calls

    def test_comments_csv_writes_output_file(self, monkeypatch, tmp_path):
        self._fake(monkeypatch)
        out = tmp_path / "c.csv"

        result = runner.invoke(cli, ["comments", self.URL, "--csv", "-o", str(out)])

        assert result.exit_code == 0, result.output
        lines = out.read_text().splitlines()
        assert lines[0].startswith("id,parent_id")
        assert lines[2].startswith("r1,c1,")


def test_login_profile_option_is_passed_to_cookie_extraction(monkeypatch):
    seen = {}

    def fake_get_cookies(source, force_refresh=False, profile=None):
        seen.update(source=source, profile=profile)
        return "chrome:Profile 1", {"a1": "x"}

    class FakeClient:
        def __init__(self, cookies): ...
        def __enter__(self): return self
        def __exit__(self, *a): ...
        def get_self_info(self):
            return {"basic_info": {"nickname": "Me", "red_id": "1"}, "guest": False}

    monkeypatch.setattr("xhs_cli.commands.auth.get_cookies", fake_get_cookies)
    monkeypatch.setattr("xhs_cli.commands.auth.XhsClient", FakeClient)

    result = runner.invoke(cli, ["login", "--cookie-source", "chrome", "--profile", "Profile 1"])

    assert result.exit_code == 0, result.output
    assert seen == {"source": "chrome", "profile": "Profile 1"}


class TestTokenCommand:
    FULL = "https://www.xiaohongshu.com/discovery/item/note-abc?xsec_token=tok-1&xsec_source=app_share&type=normal"

    def _mock_short_links(self, monkeypatch, seen):
        import httpx

        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": self.FULL})

        monkeypatch.setattr(
            "xhs_cli.note_refs._redirect_client",
            lambda: httpx.Client(transport=httpx.MockTransport(handler)),
        )

    def test_full_url_prints_note_context_and_caches_token(self):
        from xhs_cli.cookies import get_cached_note_context

        result = runner.invoke(cli, ["token", self.FULL, "--json"])

        assert result.exit_code == 0, result.output
        data = yaml.safe_load(result.output)["data"]
        assert data == {"note_id": "note-abc", "xsec_token": "tok-1", "xsec_source": "app_share",
                        "url": "https://www.xiaohongshu.com/explore/note-abc?xsec_token=tok-1&xsec_source=app_share"}
        assert get_cached_note_context("note-abc")["token"] == "tok-1"

    def test_short_link_inside_share_text_is_expanded(self, monkeypatch):
        seen = []
        self._mock_short_links(monkeypatch, seen)
        share = "88 小明发布了一篇小红书笔记，快来看吧！ 😆 abc 😆 http://xhslink.com/a/AbCd123 复制本条信息"

        result = runner.invoke(cli, ["token", share, "--json"])

        assert result.exit_code == 0, result.output
        assert seen == ["http://xhslink.com/a/AbCd123"]
        assert yaml.safe_load(result.output)["data"]["xsec_token"] == "tok-1"

    def test_url_without_token_is_an_error(self):
        result = runner.invoke(cli, ["token", "https://www.xiaohongshu.com/explore/note-abc"])

        assert result.exit_code != 0
        assert "xsec_token" in result.output
