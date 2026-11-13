from __future__ import annotations

import os

os.environ.setdefault("OUTPUT", "rich")

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.xiaohongshu-cli (cookies, token & index caches)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
