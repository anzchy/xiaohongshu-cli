"""Download a note's media (and optionally comments / markdown) to disk."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .comment_export import render_comments_md
from .constants import USER_AGENT
from .exceptions import NeedVerifyError, XhsApiError

logger = logging.getLogger(__name__)

_EXT_BY_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heic",
    "image/gif": ".gif",
    "video/mp4": ".mp4",
}
_MEDIA_HEADERS = {"user-agent": USER_AGENT, "referer": "https://www.xiaohongshu.com/"}


@dataclass
class DownloadOptions:
    fmt: str = "jpg"  # "jpg" converts HEIC to JPG; "original" keeps the source format
    comments: bool = False
    markdown: bool = False


def convert_heic_to_jpg(src: Path) -> Path:
    """Convert a HEIC file to JPG next to it (macOS ``sips``, else Pillow + pillow-heif).

    Returns the JPG path, or *src* unchanged when no converter is available.
    """
    dst = src.with_suffix(".jpg")
    if shutil.which("sips"):
        subprocess.run(
            ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "92", str(src), "--out", str(dst)],
            check=True,
            capture_output=True,
        )
    else:
        try:
            from PIL import Image  # type: ignore[import-not-found]
            from pillow_heif import register_heif_opener  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("No HEIC converter (install pillow-heif); keeping %s", src.name)
            return src
        register_heif_opener()
        Image.open(src).convert("RGB").save(dst, "JPEG", quality=92)
    return dst


@dataclass
class DownloadResult:
    note_id: str
    folder: Path
    media: list[Path] = field(default_factory=list)
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    comments: int = 0
    replies: int = 0


def _safe_name(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("._")
    return cleaned[:limit]


def _note_card(detail: dict[str, Any]) -> dict[str, Any]:
    items = detail.get("items")
    if items:
        return items[0].get("note_card", {})
    return detail


def _image_candidates(img: dict[str, Any]) -> list[str]:
    candidates = []
    if img.get("file_id"):
        candidates.append(f"https://ci.xiaohongshu.com/{img['file_id']}")
    by_scene = {info.get("image_scene"): info.get("url") for info in img.get("info_list", [])}
    candidates += [by_scene[scene] for scene in ("WB_DFT", "WB_PRV") if by_scene.get(scene)]
    return candidates


def _video_candidates(video: dict[str, Any]) -> list[str]:
    candidates = []
    origin_key = video.get("consumer", {}).get("origin_video_key")
    if origin_key:
        candidates.append(f"https://sns-video-bd.xhscdn.com/{origin_key}")
    streams = video.get("media", {}).get("stream", {})
    for codec in ("h264", "h265", "av1"):
        for stream in streams.get(codec) or []:
            candidates += [u for u in [stream.get("master_url"), *stream.get("backup_urls", [])] if u]
    return candidates


def fetch_comment_tree(client, note_id: str, *, xsec_token: str = "", xsec_source: str = "") -> list[dict]:
    """All top-level comments, each with its complete ``sub_comments`` list."""
    data = client.get_all_comments(note_id, xsec_token=xsec_token, xsec_source=xsec_source)
    comments = data.get("comments", []) if isinstance(data, dict) else []
    for top in comments:
        replies = list(top.get("sub_comments") or [])
        seen = {r.get("id") for r in replies}
        cursor, more = top.get("sub_comment_cursor", ""), top.get("sub_comment_has_more", False)
        while more:
            page = client.get_sub_comments(note_id, top["id"], cursor=cursor, xsec_token=xsec_token)
            for reply in page.get("comments", []):
                if reply.get("id") not in seen:
                    seen.add(reply.get("id"))
                    replies.append(reply)
            next_cursor = page.get("cursor", "")
            more = page.get("has_more", False) and next_cursor and next_cursor != cursor
            cursor = next_cursor
        top["sub_comments"] = replies
    return comments


def _fetch(http: httpx.Client, url: str, stem: Path) -> Path | None:
    try:
        resp = http.get(url, headers=_MEDIA_HEADERS)
    except httpx.HTTPError as exc:
        logger.debug("Media fetch failed for %s: %s", url[:80], exc)
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    ctype = resp.headers.get("content-type", "").split(";")[0].strip()
    path = stem.with_suffix(_EXT_BY_TYPE.get(ctype, ".jpg"))
    path.write_bytes(resp.content)
    return path


def download_note(
    client,
    note_id: str,
    *,
    xsec_token: str = "",
    xsec_source: str = "",
    out_dir: Path,
    options: DownloadOptions | None = None,
    http: httpx.Client | None = None,
    convert_heic: Callable[[Path], Path] = convert_heic_to_jpg,
) -> DownloadResult:
    """Download one note into ``out_dir/<note_id>_<title>/``."""
    options = options or DownloadOptions()
    card = _note_card(client.get_note_detail(note_id, xsec_token=xsec_token, xsec_source=xsec_source))
    folder = Path(out_dir) / f"{note_id}_{_safe_name(card.get('title', ''))}".rstrip("_")
    images_dir = folder / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (folder / "note.json").write_text(json.dumps(card, ensure_ascii=False, indent=1))
    result = DownloadResult(note_id=note_id, folder=folder)

    own_http = http is None
    http = http or httpx.Client(timeout=60, follow_redirects=True)
    try:
        for index, img in enumerate(card.get("image_list", []), 1):
            existing = sorted(images_dir.glob(f"{index:02d}.*"))
            if existing:
                result.media.append(existing[0])
                result.skipped += 1
                continue
            for url in _image_candidates(img):
                path = _fetch(http, url, images_dir / f"{index:02d}")
                if path:
                    if path.suffix == ".heic" and options.fmt == "jpg":
                        converted = convert_heic(path)
                        if converted != path:
                            path.unlink(missing_ok=True)
                            path = converted
                    result.media.append(path)
                    break
            else:
                result.failed.append(f"image {index:02d}")
        video = card.get("video")
        if video:
            existing = sorted(folder.glob("video.*"))
            if existing:
                result.media.append(existing[0])
                result.skipped += 1
            else:
                for url in _video_candidates(video):
                    path = _fetch(http, url, folder / "video")
                    if path:
                        result.media.append(path.rename(path.with_suffix(".mp4")))
                        break
                else:
                    result.failed.append("video")
    finally:
        if own_http:
            http.close()

    comments: list[dict] | None = None
    if options.comments:
        comments = fetch_comment_tree(client, note_id, xsec_token=xsec_token, xsec_source=xsec_source)
        (folder / "comments.json").write_text(json.dumps(comments, ensure_ascii=False, indent=1))
        result.comments = len(comments)
        result.replies = sum(len(c.get("sub_comments") or []) for c in comments)
    if options.markdown:
        (folder / "note.md").write_text(render_note_md(card, note_id, result, comments))
    return result


def render_note_md(card: dict[str, Any], note_id: str, result: DownloadResult, comments: list[dict] | None) -> str:
    user = card.get("user", {})
    stats = card.get("interact_info", {})
    lines = [
        f"# {card.get('title', '')}",
        "",
        f"- 作者：{user.get('nickname', '')}",
        f"- 链接：https://www.xiaohongshu.com/explore/{note_id}",
        f"- 👍{stats.get('liked_count', 0)} ⭐{stats.get('collected_count', 0)} "
        f"💬{stats.get('comment_count', 0)} ↗{stats.get('share_count', 0)}",
        "",
        "## 正文",
        "",
        card.get("desc", ""),
        "",
        "## 媒体",
        "",
    ]
    for path in result.media:
        rel = path.relative_to(result.folder).as_posix()
        lines.append(f"![{path.stem}]({rel})" if path.suffix != ".mp4" else f"[{path.name}]({rel})")
    if comments is not None:
        lines += ["", f"## 评论（{result.comments} 条主评论，{result.replies} 条回复）", ""]
        lines.append(render_comments_md(comments, author_id=user.get("user_id", "")))
    return "\n".join(lines).rstrip() + "\n"


@dataclass
class ExportResult:
    downloaded: list[str] = field(default_factory=list)
    already_archived: int = 0
    failed: dict[str, str] = field(default_factory=dict)
    aborted: str | None = None


def export_notes(
    client,
    fetch_page: Callable[[str], dict[str, Any]],
    out_dir: Path,
    *,
    options: DownloadOptions | None = None,
    limit: int | None = None,
    http: httpx.Client | None = None,
    convert_heic: Callable[[Path], Path] = convert_heic_to_jpg,
) -> ExportResult:
    """Archive every note of a paged listing into *out_dir*, skipping notes in ``manifest.json``.

    The manifest is rewritten after each note so an interrupted export resumes where it stopped.
    Stops at the first captcha rather than pushing on.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest: dict[str, Any] = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    result = ExportResult()

    own_http = http is None
    http = http or httpx.Client(timeout=60, follow_redirects=True)
    try:
        cursor = ""
        while True:
            page = fetch_page(cursor)
            for note in page.get("notes", []):
                note_id = str(note.get("note_id") or note.get("id") or "")
                if not note_id:
                    continue
                if note_id in manifest:
                    result.already_archived += 1
                    continue
                if limit is not None and len(result.downloaded) >= limit:
                    return result
                try:
                    res = download_note(
                        client, note_id, xsec_token=note.get("xsec_token", ""), out_dir=out_dir,
                        options=options, http=http, convert_heic=convert_heic,
                    )
                except NeedVerifyError as exc:
                    result.failed[note_id] = str(exc)
                    result.aborted = "captcha"
                    return result
                except XhsApiError as exc:
                    result.failed[note_id] = str(exc)
                    continue
                manifest[note_id] = {"folder": res.folder.name, "title": note.get("display_title", "")}
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
                result.downloaded.append(note_id)
            cursor = page.get("cursor", "")
            if not page.get("has_more") or not cursor:
                return result
    finally:
        if own_http:
            http.close()
