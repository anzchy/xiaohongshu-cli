"""Tests for xhs_cli.download — the note download/archiving module (seam A)."""

from __future__ import annotations

import httpx
import pytest

from xhs_cli.download import DownloadOptions, download_note

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 20_000
HEIC = b"\x00\x00\x00\x18ftypheic" + b"h" * 20_000


def image(file_id: str) -> dict:
    return {
        "file_id": file_id,
        "info_list": [
            {"image_scene": "WB_PRV", "url": f"http://sns-webpic-qc.xhscdn.com/sig/{file_id}!nd_prv_wlteh_webp_3"},
            {"image_scene": "WB_DFT", "url": f"http://sns-webpic-qc.xhscdn.com/sig/{file_id}!nd_dft_wlteh_webp_3"},
        ],
    }


def note_card(**overrides) -> dict:
    card = {
        "note_id": "note1",
        "title": "好用的锅/工程师",
        "desc": "正文",
        "type": "normal",
        "user": {"user_id": "author1", "nickname": "Lee"},
        "interact_info": {"liked_count": "1", "collected_count": "2", "comment_count": "3", "share_count": "4"},
        "image_list": [image("notes_pre_post/aaa")],
    }
    card.update(overrides)
    return card


class FakeClient:
    def __init__(self, card: dict, comments: list | None = None, replies: dict | None = None):
        self.card = card
        self.comments = comments or []
        self.replies = replies or {}
        self.calls: list[tuple] = []

    def get_note_detail(self, note_id, xsec_token="", xsec_source=""):
        self.calls.append(("detail", note_id, xsec_token))
        return {"items": [{"id": note_id, "note_card": self.card}]}

    def get_all_comments(self, note_id, xsec_token="", xsec_source="", max_pages=20):
        self.calls.append(("comments", note_id, xsec_token))
        return {"comments": self.comments, "has_more": False}

    def get_sub_comments(self, note_id, comment_id, cursor="", xsec_token="", num=30):
        self.calls.append(("sub", comment_id, cursor, xsec_token))
        return self.replies[(comment_id, cursor)]


def media_http(routes: dict[str, tuple[int, bytes, str]], seen: list[str] | None = None) -> httpx.Client:
    """Fake CDN: maps URL -> (status, body, content-type); anything else is 404."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        status, body, ctype = routes.get(url, (404, b"", "text/plain"))
        return httpx.Response(status, content=body, headers={"content-type": ctype})

    return httpx.Client(transport=httpx.MockTransport(handler))


ORIGINAL = "https://ci.xiaohongshu.com/notes_pre_post/aaa"


class TestImages:
    def test_downloads_original_image_into_note_folder(self, tmp_path):
        http = media_http({ORIGINAL: (200, JPEG, "image/jpeg")})

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        folder = tmp_path / "note1_好用的锅_工程师"
        assert result.folder == folder
        assert (folder / "images" / "01.jpg").read_bytes() == JPEG
        assert result.media == [folder / "images" / "01.jpg"]

    def test_falls_back_to_web_default_then_preview_when_original_unavailable(self, tmp_path):
        dft = "http://sns-webpic-qc.xhscdn.com/sig/notes_pre_post/aaa!nd_dft_wlteh_webp_3"
        prv = "http://sns-webpic-qc.xhscdn.com/sig/notes_pre_post/aaa!nd_prv_wlteh_webp_3"
        seen: list[str] = []
        http = media_http({dft: (403, b"", "text/plain"), prv: (200, b"RIFF" + b"w" * 20_000, "image/webp")}, seen)

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert seen == [ORIGINAL, dft, prv]
        assert [p.name for p in result.media] == ["01.webp"]

    def test_heic_original_is_converted_to_jpg_by_default(self, tmp_path):
        http = media_http({ORIGINAL: (200, HEIC, "image/heic")})

        def fake_convert(src):
            dst = src.with_suffix(".jpg")
            dst.write_bytes(JPEG)
            return dst

        result = download_note(
            FakeClient(note_card()), "note1", out_dir=tmp_path, http=http, convert_heic=fake_convert
        )

        images = result.folder / "images"
        assert [p.name for p in result.media] == ["01.jpg"]
        assert sorted(p.name for p in images.iterdir()) == ["01.jpg"]

    def test_original_format_keeps_heic(self, tmp_path):
        http = media_http({ORIGINAL: (200, HEIC, "image/heic")})

        def must_not_convert(src):
            raise AssertionError("conversion requested for --format original")

        result = download_note(
            FakeClient(note_card()), "note1", out_dir=tmp_path, http=http,
            options=DownloadOptions(fmt="original"), convert_heic=must_not_convert,
        )

        assert [p.name for p in result.media] == ["01.heic"]
        assert result.media[0].read_bytes() == HEIC

    def test_rerun_skips_images_already_on_disk(self, tmp_path):
        images = tmp_path / "note1_好用的锅_工程师" / "images"
        images.mkdir(parents=True)
        (images / "01.jpg").write_bytes(JPEG)
        seen: list[str] = []
        http = media_http({}, seen)

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert seen == []
        assert result.media == [images / "01.jpg"]
        assert result.skipped == 1

    def test_image_with_no_working_url_is_reported_and_others_still_download(self, tmp_path):
        card = note_card(image_list=[image("notes_pre_post/dead"), image("notes_pre_post/aaa")])
        http = media_http({ORIGINAL: (200, JPEG, "image/jpeg")})

        result = download_note(FakeClient(card), "note1", out_dir=tmp_path, http=http)

        assert [p.name for p in result.media] == ["02.jpg"]
        assert result.failed == ["image 01"]


MP4 = b"\x00\x00\x00\x20ftypisom" + b"v" * 50_000
VIDEO_CARD = {
    "type": "video",
    "image_list": [],
    "video": {
        "consumer": {"origin_video_key": "pre_post/vvv"},
        "media": {"stream": {"h264": [{
            "master_url": "http://sns-video-qc.xhscdn.com/stream/h264.mp4",
            "backup_urls": ["http://sns-video-bk.xhscdn.com/stream/h264.mp4"],
        }]}},
    },
}


class TestVideo:
    def test_video_note_saves_original_stream(self, tmp_path):
        http = media_http({"https://sns-video-bd.xhscdn.com/pre_post/vvv": (200, MP4, "video/mp4")})

        result = download_note(FakeClient(note_card(**VIDEO_CARD)), "note1", out_dir=tmp_path, http=http)

        assert result.media == [result.folder / "video.mp4"]
        assert result.media[0].read_bytes() == MP4

    def test_video_falls_back_to_h264_master_then_backup(self, tmp_path):
        seen: list[str] = []
        http = media_http({"http://sns-video-bk.xhscdn.com/stream/h264.mp4": (200, MP4, "video/mp4")}, seen)

        result = download_note(FakeClient(note_card(**VIDEO_CARD)), "note1", out_dir=tmp_path, http=http)

        assert seen == [
            "https://sns-video-bd.xhscdn.com/pre_post/vvv",
            "http://sns-video-qc.xhscdn.com/stream/h264.mp4",
            "http://sns-video-bk.xhscdn.com/stream/h264.mp4",
        ]
        assert [p.name for p in result.media] == ["video.mp4"]


def comment(cid, text, *, user="u", uid="uid", subs=None, has_more=False, cursor="", target=None):
    c = {
        "id": cid, "content": text, "create_time": 1741343625000, "like_count": "2",
        "ip_location": "浙江", "user_info": {"user_id": uid, "nickname": user},
        "sub_comments": subs or [], "sub_comment_has_more": has_more, "sub_comment_cursor": cursor,
        "sub_comment_count": str(len(subs or [])),
    }
    if target:
        c["target_comment"] = {"user_info": {"nickname": target}}
    return c


class TestComments:
    def test_comments_option_saves_full_reply_threads(self, tmp_path):
        import json

        top = comment("c1", "问题", subs=[comment("r1", "回复1")], has_more=True, cursor="r1")
        client = FakeClient(
            note_card(image_list=[]),
            comments=[top],
            replies={("c1", "r1"): {"comments": [comment("r2", "回复2")], "has_more": False, "cursor": ""}},
        )

        result = download_note(
            client, "note1", xsec_token="tok", out_dir=tmp_path, http=media_http({}),
            options=DownloadOptions(comments=True),
        )

        saved = json.loads((result.folder / "comments.json").read_text())
        assert [c["id"] for c in saved] == ["c1"]
        assert [r["id"] for r in saved[0]["sub_comments"]] == ["r1", "r2"]
        assert ("sub", "c1", "r1", "tok") in client.calls
        assert (result.comments, result.replies) == (1, 2)

    def test_comments_not_fetched_unless_requested(self, tmp_path):
        client = FakeClient(note_card(image_list=[]), comments=[comment("c1", "x")])

        result = download_note(client, "note1", out_dir=tmp_path, http=media_http({}))

        assert not any(call[0] == "comments" for call in client.calls)
        assert not (result.folder / "comments.json").exists()


class TestMarkdown:
    def test_markdown_note_has_body_images_and_comment_tree(self, tmp_path):
        reply = comment("r1", "答", user="Lee", uid="author1")
        client = FakeClient(note_card(), comments=[comment("c1", "问题", user="小明", subs=[reply])])
        http = media_http({ORIGINAL: (200, JPEG, "image/jpeg")})

        result = download_note(
            client, "note1", out_dir=tmp_path, http=http,
            options=DownloadOptions(comments=True, markdown=True),
        )

        md = (result.folder / "note.md").read_text()
        assert md.startswith("# 好用的锅/工程师\n")
        assert "- 作者：Lee" in md
        assert "- 链接：https://www.xiaohongshu.com/explore/note1" in md
        assert "\n正文\n" in md
        assert "![01](images/01.jpg)" in md
        assert "## 评论（1 条主评论，1 条回复）" in md
        assert "    - **Lee 【作者】**" in md

    def test_note_json_always_saved(self, tmp_path):
        import json

        result = download_note(FakeClient(note_card(image_list=[])), "note1", out_dir=tmp_path, http=media_http({}))

        assert json.loads((result.folder / "note.json").read_text())["title"] == "好用的锅/工程师"


def test_comment_tree_requests_every_page(tmp_path):
    class Recording(FakeClient):
        def get_all_comments(self, note_id, xsec_token="", xsec_source="", max_pages=20):
            self.calls.append(("comments-max-pages", max_pages))
            return {"comments": []}

    client = Recording(note_card(image_list=[]))
    download_note(client, "note1", out_dir=tmp_path, http=media_http({}), options=DownloadOptions(comments=True))

    assert ("comments-max-pages", None) in client.calls


class TestPathSafety:
    @pytest.mark.parametrize("bad_id", ["../../escape", "..", "a/b", "http://xhslink.com/a/x", ""])
    def test_unsafe_note_id_is_rejected_before_any_request_or_write(self, tmp_path, bad_id):
        from xhs_cli.exceptions import XhsApiError

        out = tmp_path / "out"
        client = FakeClient(note_card())
        with pytest.raises(XhsApiError, match="note id"):
            download_note(client, bad_id, out_dir=out, http=media_http({}))
        assert client.calls == []
        assert not out.exists() or list(out.iterdir()) == []


class TestMediaValidation:
    def test_html_200_is_not_saved_as_image_and_next_candidate_is_tried(self, tmp_path):
        dft = "http://sns-webpic-qc.xhscdn.com/sig/notes_pre_post/aaa!nd_dft_wlteh_webp_3"
        http = media_http({
            ORIGINAL: (200, b"<html>blocked</html>" * 100, "text/html"),
            dft: (200, b"RIFF" + b"w" * 20_000, "image/webp"),
        })

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert [p.name for p in result.media] == ["01.webp"]
        assert result.failed == []

    def test_octet_stream_is_typed_by_magic_bytes(self, tmp_path):
        http = media_http({ORIGINAL: (200, JPEG, "application/octet-stream")})

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert [p.name for p in result.media] == ["01.jpg"]

    def test_octet_stream_with_unknown_bytes_is_rejected(self, tmp_path):
        http = media_http({ORIGINAL: (200, b"garbage" * 1000, "application/octet-stream")})

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert result.media == []
        assert result.failed == ["image 01"]


class TestInterruptedDownloads:
    def test_leftover_part_file_is_not_treated_as_done(self, tmp_path):
        images = tmp_path / "note1_好用的锅_工程师" / "images"
        images.mkdir(parents=True)
        (images / "01.jpg.part").write_bytes(JPEG[:100])
        http = media_http({ORIGINAL: (200, JPEG, "image/jpeg")})

        result = download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        assert [p.name for p in result.media] == ["01.jpg"]
        assert result.skipped == 0
        assert sorted(p.name for p in images.iterdir()) == ["01.jpg"]

    def test_write_failure_leaves_no_final_file(self, tmp_path, monkeypatch):
        from pathlib import Path

        real_replace = Path.replace

        def crash_on_replace(self, target):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "replace", crash_on_replace)
        http = media_http({ORIGINAL: (200, JPEG, "image/jpeg")})

        with pytest.raises(OSError):
            download_note(FakeClient(note_card()), "note1", out_dir=tmp_path, http=http)

        monkeypatch.setattr(Path, "replace", real_replace)
        images = tmp_path / "note1_好用的锅_工程师" / "images"
        assert not (images / "01.jpg").exists()
