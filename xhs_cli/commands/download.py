"""`xhs download` — save notes' media, comments and markdown to disk."""

from __future__ import annotations

from pathlib import Path

import click

from ..command_normalizers import resolve_current_user_id
from ..cookies import cache_note_context, load_note_index
from ..download import DownloadOptions, download_note, export_notes
from ..exceptions import NeedVerifyError, XhsApiError
from ..formatter import maybe_print_structured, print_error, print_info, print_success
from ..note_refs import resolve_note_reference
from ._common import exit_for_error, run_client_action, structured_output_options


def _parse_index_spec(spec: str, total: int) -> list[int]:
    """'all' or '1-3,5' -> 1-based indexes."""
    if spec.strip().lower() == "all":
        return list(range(1, total + 1))
    indexes: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
                indexes += range(lo, hi + 1)
            else:
                indexes.append(int(part))
        except ValueError:
            raise click.BadParameter(f"Invalid index range: {part!r}", param_hint="--from-index") from None
    return indexes


def _collect_targets(refs, from_index: str | None, url_file: str | None) -> list[tuple[str, str, str]]:
    targets = [resolve_note_reference(ref) for ref in refs]
    if from_index:
        entries = load_note_index()
        if not entries:
            raise click.UsageError("No previous listing — run search / feed / favorites / user-posts first")
        for i in _parse_index_spec(from_index, len(entries)):
            if not 1 <= i <= len(entries):
                raise click.BadParameter(f"Index {i} out of range 1-{len(entries)}", param_hint="--from-index")
            entry = entries[i - 1]
            targets.append((entry["note_id"], entry.get("xsec_token", ""), entry.get("xsec_source", "")))
    if url_file:
        for line in Path(url_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                targets.append(resolve_note_reference(line))
    return targets


@click.command()
@click.argument("refs", nargs=-1)
@click.option("--from-index", default=None, help="Download from the last listing: 'all' or e.g. '1-5,8'")
@click.option("-f", "--file", "url_file", type=click.Path(exists=True, dir_okay=False), default=None,
              help="File with one note URL/ID per line (# comments allowed)")
@click.option("-o", "--output", "out_dir", type=click.Path(file_okay=False), default=".", help="Output directory")
@click.option(
    "--format", "fmt", type=click.Choice(["jpg", "original"]), default="jpg",
    help="jpg: convert HEIC originals to JPG; original: keep source format",
)
@click.option("--comments", "with_comments", is_flag=True, help="Also save all comments with full reply threads")
@click.option("--md", "markdown", is_flag=True, help="Write note.md (body, media, comments)")
@structured_output_options
@click.pass_context
def download(ctx, refs, from_index, url_file, out_dir, fmt, with_comments, markdown, as_json, as_yaml):
    """Download notes (ID, URL, or short index) with original-quality media."""
    targets = _collect_targets(refs, from_index, url_file)
    if not targets:
        raise click.UsageError("Give at least one note ID, URL, or short index")
    options = DownloadOptions(fmt=fmt, comments=with_comments, markdown=markdown)

    def _run(client):
        results = []
        aborted = None
        for note_id, token, source in targets:
            if aborted:
                results.append({"note_id": note_id, "ok": False, "error": f"skipped: batch aborted ({aborted})"})
                continue
            if token:
                cache_note_context(note_id, token, source or "pc_feed")
            try:
                res = download_note(
                    client, note_id, xsec_token=token, xsec_source=source,
                    out_dir=Path(out_dir), options=options,
                )
            except NeedVerifyError as exc:
                # Pushing on after a captcha risks escalating the account's risk score.
                results.append({"note_id": note_id, "ok": False, "error": str(exc)})
                aborted = "captcha"
                continue
            except XhsApiError as exc:
                results.append({"note_id": note_id, "ok": False, "error": str(exc)})
                continue
            results.append({
                "note_id": note_id,
                "ok": True,
                "folder": str(res.folder),
                "media": [str(p) for p in res.media],
                "skipped": res.skipped,
                "failed": res.failed,
                "comments": res.comments,
                "replies": res.replies,
            })
        return {"results": results, "aborted": aborted}

    try:
        data = run_client_action(ctx, _run)
    except XhsApiError as exc:
        exit_for_error(exc, as_json=as_json, as_yaml=as_yaml)
    if not maybe_print_structured(data, as_json=as_json, as_yaml=as_yaml):
        for entry in data["results"]:
            if entry["ok"]:
                extra = f", {entry['comments']} comments + {entry['replies']} replies" if with_comments else ""
                print_success(f"{entry['note_id']}: {len(entry['media'])} media{extra} → {entry['folder']}")
                for item in entry["failed"]:
                    print_error(f"  {item}: no working URL")
            else:
                print_error(f"{entry['note_id']}: {entry['error']}")
        print_info(f"{sum(e['ok'] for e in data['results'])}/{len(data['results'])} notes downloaded")
        if data["aborted"]:
            print_error("Stopped at a captcha. Solve it in the browser (open the note), wait a while, then re-run "
                        "(media already saved is skipped). Consider a larger --delay.")
    if not all(e["ok"] for e in data["results"]):
        raise SystemExit(1)


def _export_command(ctx, fetcher, user_id, out_dir, fmt, with_comments, markdown, limit, as_json, as_yaml):
    options = DownloadOptions(fmt=fmt, comments=with_comments, markdown=markdown)

    def _run(client):
        uid = user_id or resolve_current_user_id(client.get_self_info())
        if not uid:
            raise click.UsageError("Cannot determine current user_id. Please specify user_id explicitly.")
        res = export_notes(
            client, lambda cursor: fetcher(client, uid, cursor), Path(out_dir), options=options, limit=limit
        )
        return {
            "user_id": uid,
            "out_dir": str(out_dir),
            "downloaded": res.downloaded,
            "already_archived": res.already_archived,
            "failed": res.failed,
            "aborted": res.aborted,
        }

    try:
        data = run_client_action(ctx, _run)
    except XhsApiError as exc:
        exit_for_error(exc, as_json=as_json, as_yaml=as_yaml)
    if not maybe_print_structured(data, as_json=as_json, as_yaml=as_yaml):
        print_success(f"{len(data['downloaded'])} new notes → {out_dir} ({data['already_archived']} already archived)")
        for note_id, err in data["failed"].items():
            print_error(f"{note_id}: {err}")
        if data["aborted"]:
            print_error("Stopped at a captcha — wait, then re-run to resume. Consider a larger --delay.")
    if data["failed"]:
        raise SystemExit(1)


def _export_options(command):
    command = structured_output_options(command)
    command = click.option("--limit", type=int, default=None, help="Download at most N new notes")(command)
    command = click.option("--md/--no-md", "markdown", default=True, show_default=True,
                           help="Write note.md per note")(command)
    command = click.option("--comments", "with_comments", is_flag=True,
                           help="Also archive full comment threads")(command)
    command = click.option("--format", "fmt", type=click.Choice(["jpg", "original"]), default="jpg")(command)
    command = click.option("-o", "--output", "out_dir", type=click.Path(file_okay=False), required=True,
                           help="Archive directory (holds manifest.json)")(command)
    return command


@click.command("export-favorites")
@click.argument("user_id", required=False, default=None)
@_export_options
@click.pass_context
def export_favorites(ctx, user_id, out_dir, fmt, with_comments, markdown, limit, as_json, as_yaml):
    """Archive all favorited notes (incremental; defaults to current user)."""
    _export_command(
        ctx, lambda client, uid, cur: client.get_user_favorites(uid, cursor=cur),
        user_id, out_dir, fmt, with_comments, markdown, limit, as_json, as_yaml,
    )


@click.command("export-user")
@click.argument("user_id")
@_export_options
@click.pass_context
def export_user(ctx, user_id, out_dir, fmt, with_comments, markdown, limit, as_json, as_yaml):
    """Archive all notes posted by a user (incremental)."""
    _export_command(
        ctx, lambda client, uid, cur: client.get_user_notes(uid, cursor=cur),
        user_id, out_dir, fmt, with_comments, markdown, limit, as_json, as_yaml,
    )
