"""Unit tests for cookie management (no network required)."""


import time

import pytest

from xhs_cli.cookies import (
    NOTE_CONTEXT_TTL_SECONDS,
    cache_note_context,
    clear_cookies,
    cookies_to_string,
    get_cached_note_context,
    get_cached_xsec_token,
    get_cookies,
    get_index_cache_path,
    get_note_by_index,
    get_token_cache_path,
    load_saved_cookies,
    load_token_cache,
    save_cookies,
    save_note_index,
)


@pytest.fixture
def tmp_config_dir(tmp_path, monkeypatch):
    """Override config dir to use temp directory."""
    monkeypatch.setattr("xhs_cli.cookies.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("xhs_cli.cookies.get_cookie_path", lambda: tmp_path / "cookies.json")
    monkeypatch.setattr("xhs_cli.cookies._TOKEN_CACHE_MEMORY", None)
    monkeypatch.setattr("xhs_cli.cookies._TOKEN_CACHE_PATH", None)
    return tmp_path


class TestSaveCookies:
    def test_save_and_load(self, tmp_config_dir):
        cookies = {"a1": "test_value", "web_session": "sess123"}
        save_cookies(cookies)

        loaded = load_saved_cookies()
        assert loaded is not None
        assert loaded["a1"] == "test_value"
        assert loaded["web_session"] == "sess123"

    def test_file_permissions(self, tmp_config_dir):
        cookies = {"a1": "test"}
        save_cookies(cookies)

        cookie_file = tmp_config_dir / "cookies.json"
        stat = cookie_file.stat()
        assert stat.st_mode & 0o777 == 0o600


class TestLoadSavedCookies:
    def test_no_file(self, tmp_config_dir):
        assert load_saved_cookies() is None

    def test_invalid_json(self, tmp_config_dir):
        (tmp_config_dir / "cookies.json").write_text("not json")
        assert load_saved_cookies() is None

    def test_missing_a1(self, tmp_config_dir):
        (tmp_config_dir / "cookies.json").write_text('{"web_session": "x"}')
        assert load_saved_cookies() is None


class TestClearCookies:
    def test_clear_existing(self, tmp_config_dir):
        save_cookies({"a1": "test"})
        clear_cookies()
        assert load_saved_cookies() is None

    def test_clear_nonexistent(self, tmp_config_dir):
        # Should not raise
        clear_cookies()


class TestCookiesToString:
    def test_format(self):
        result = cookies_to_string({"a1": "v1", "web_session": "v2"})
        assert "a1=v1" in result
        assert "web_session=v2" in result
        assert "; " in result


class TestGetCookies:
    def test_prefers_saved_cookies_by_default(self, monkeypatch):
        monkeypatch.setattr("xhs_cli.cookies.load_saved_cookies", lambda: {"a1": "saved"})
        monkeypatch.setattr(
            "xhs_cli.cookies.extract_browser_cookies",
            lambda source: ("chrome", {"a1": "fresh"}),
        )

        browser, cookies = get_cookies("chrome")
        assert browser == "saved"
        assert cookies == {"a1": "saved"}

    def test_force_refresh_bypasses_saved_cookies(self, monkeypatch):
        monkeypatch.setattr("xhs_cli.cookies.load_saved_cookies", lambda: {"a1": "saved"})
        monkeypatch.setattr(
            "xhs_cli.cookies.extract_browser_cookies",
            lambda source: ("chrome", {"a1": "fresh"}),
        )
        saved = []
        monkeypatch.setattr("xhs_cli.cookies.save_cookies", lambda cookies: saved.append(cookies))

        browser, cookies = get_cookies("chrome", force_refresh=True)
        assert browser == "chrome"
        assert cookies == {"a1": "fresh"}
        assert saved == [{"a1": "fresh"}]


class TestNoteContextCache:
    def test_cache_persists_token_and_source(self, tmp_config_dir):
        cache_note_context("note-1", "token-1", "pc_search", context="search")

        assert get_cached_xsec_token("note-1") == "token-1"
        context = get_cached_note_context("note-1")
        assert context["token"] == "token-1"
        assert context["source"] == "pc_search"
        assert context["context"] == "search"

    def test_load_token_cache_keeps_legacy_entries_compatible(self, tmp_config_dir):
        get_token_cache_path().write_text('{"note-1":"legacy-token"}')

        cache = load_token_cache()
        assert cache["note-1"]["token"] == "legacy-token"
        assert cache["note-1"]["source"] == ""

    def test_expired_note_context_is_not_returned(self, tmp_config_dir):
        stale_ts = time.time() - NOTE_CONTEXT_TTL_SECONDS - 10
        get_token_cache_path().write_text(
            f'{{"note-1":{{"token":"stale-token","source":"pc_search","ts":{stale_ts}}}}}'
        )

        assert get_cached_note_context("note-1") == {}


class TestNoteIndexCache:
    def test_save_and_resolve_index_with_source(self, tmp_config_dir):
        save_note_index([
            {
                "note_id": "note-1",
                "xsec_token": "token-1",
                "xsec_source": "pc_search",
            }
        ])

        assert get_note_by_index(1) == {
            "note_id": "note-1",
            "xsec_token": "token-1",
            "xsec_source": "pc_search",
        }

    def test_save_empty_index_clears_previous_entries(self, tmp_config_dir):
        save_note_index([
            {
                "note_id": "note-1",
                "xsec_token": "token-1",
                "xsec_source": "pc_search",
            }
        ])
        save_note_index([])

        assert get_note_by_index(1) is None
        assert get_index_cache_path().read_text() == "[]"

    def test_index_file_permissions(self, tmp_config_dir):
        save_note_index([{"note_id": "note-1", "xsec_token": "", "xsec_source": ""}])

        stat = get_index_cache_path().stat()
        assert stat.st_mode & 0o777 == 0o600

    def test_index_normalizes_missing_optional_fields(self, tmp_config_dir):
        get_index_cache_path().write_text('[{"note_id":"note-1"}]')

        assert get_note_by_index(1) == {
            "note_id": "note-1",
            "xsec_token": "",
            "xsec_source": "",
        }


class _Cookie:
    def __init__(self, name, value):
        self.name, self.value, self.domain = name, value, ".xiaohongshu.com"


class TestBrowserProfiles:
    """`login --profile`: read cookies from a specific (or every) Chromium profile."""

    def _chrome_dir(self, tmp_path, monkeypatch, profiles_with_a1, profiles_without=()):
        from pathlib import Path

        monkeypatch.setattr("sys.platform", "darwin")
        root = Path(tmp_path) / "Library/Application Support/Google/Chrome"
        for name in [*profiles_with_a1, *profiles_without]:
            (root / name).mkdir(parents=True)
            (root / name / "Cookies").write_bytes(b"")
        opened = []

        def fake_chrome(cookie_file=None, domain_name=""):
            opened.append(cookie_file)
            profile = Path(cookie_file).parent.name
            return [_Cookie("a1", f"a1-{profile}")] if profile in profiles_with_a1 else [_Cookie("webId", "x")]

        monkeypatch.setattr("xhs_cli.cookies._get_browser_loader", lambda source: fake_chrome)
        monkeypatch.setattr("xhs_cli.cookies._extract_via_subprocess", lambda *a, **k: None)
        return root, opened

    def test_named_profile_reads_that_profiles_cookie_db(self, tmp_path, monkeypatch):
        from xhs_cli.cookies import extract_browser_cookies

        root, opened = self._chrome_dir(tmp_path, monkeypatch, ["Profile 1"], ["Default"])

        browser, cookies = extract_browser_cookies("chrome", profile="Profile 1")

        assert opened == [str(root / "Profile 1" / "Cookies")]
        assert (browser, cookies["a1"]) == ("chrome:Profile 1", "a1-Profile 1")

    def test_auto_profile_tries_each_profile_until_a1_found(self, tmp_path, monkeypatch):
        from xhs_cli.cookies import extract_browser_cookies

        root, opened = self._chrome_dir(tmp_path, monkeypatch, ["Profile 2"], ["Default", "Profile 1"])

        browser, cookies = extract_browser_cookies("chrome", profile="auto")

        assert opened[0] == str(root / "Default" / "Cookies")
        assert (browser, cookies["a1"]) == ("chrome:Profile 2", "a1-Profile 2")

    def test_unknown_profile_returns_none(self, tmp_path, monkeypatch):
        from xhs_cli.cookies import extract_browser_cookies

        self._chrome_dir(tmp_path, monkeypatch, ["Default"])

        assert extract_browser_cookies("chrome", profile="Nope") is None

    def test_auto_source_falls_back_to_non_default_profiles(self, tmp_path, monkeypatch):
        from xhs_cli.cookies import extract_browser_cookies

        self._chrome_dir(tmp_path, monkeypatch, ["Profile 1"], ["Default"])
        monkeypatch.setattr("xhs_cli.cookies._available_browsers", lambda: ("chrome",))
        monkeypatch.setattr("xhs_cli.cookies._extract_in_process", _only_with_cookie_file)

        browser, cookies = extract_browser_cookies("auto")

        assert (browser, cookies["a1"]) == ("chrome:Profile 1", "a1-Profile 1")

    def test_get_cookies_passes_profile_and_saves_result(self, monkeypatch):
        from xhs_cli.cookies import get_cookies

        seen = {}

        def fake_extract(source, profile=None):
            seen.update(source=source, profile=profile)
            return "chrome:Profile 1", {"a1": "x"}

        monkeypatch.setattr("xhs_cli.cookies.extract_browser_cookies", fake_extract)

        assert get_cookies("chrome", force_refresh=True, profile="Profile 1") == ("chrome:Profile 1", {"a1": "x"})
        assert seen == {"source": "chrome", "profile": "Profile 1"}
        assert load_saved_cookies()["a1"] == "x"


def _only_with_cookie_file(source, cookie_file=None):
    """Default-path extraction finds nothing; per-profile extraction finds a1 only in Profile 1."""
    from pathlib import Path

    if cookie_file and Path(cookie_file).parent.name == "Profile 1":
        return {"a1": "a1-Profile 1"}
    return None
