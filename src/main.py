"""DUTY's Python CLI: python src/main.py --help."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.pycache_prefix = str(ROOT / "local/cache/pycache")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import Config, History, Result, Cancelled, LoginRequired, error_code, now, operation_context, setup, status


def parser():
    cli = argparse.ArgumentParser(description="DUTY — download and upload YouTube videos")
    cli.add_argument("--json", action="store_true", help="Machine-readable output; progress goes to stderr")
    cli.add_argument("--non-interactive", action="store_true", help="Return login_required instead of opening login")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Install missing project-local tools and dependencies")
    check = sub.add_parser("status", help="Show installed tools, saved session and provider readiness")
    check.add_argument("--online", action="store_true", help="Establish or refresh saved login when needed; no Studio verification")
    login = sub.add_parser("login", help="Sign in through Firefox")
    login.add_argument("--refresh", action="store_true")
    download = sub.add_parser("download", help="Download one YouTube URL at best available quality")
    download.add_argument("url")
    download.add_argument("--output", type=Path)
    download.add_argument("--format", choices=["mkv", "mp4", "mp3"], default="mp4",
                          help="Video with audio: mkv/mp4; audio only: mp3")
    quality = download.add_mutually_exclusive_group()
    quality.add_argument("--exact-1080", action="store_true")
    quality.add_argument("--exact-4k", action="store_true")
    upload = sub.add_parser("upload", help="Upload one local MP4/MKV; defaults to public")
    upload.add_argument("video", type=Path)
    upload.add_argument("--metadata", type=Path)
    upload.add_argument("--title")
    upload.add_argument("--description")
    upload.add_argument("--visibility", choices=["public", "unlisted", "private"])
    upload.add_argument("--made-for-kids", action=argparse.BooleanOptionalAction, default=None)
    upload.add_argument("--headless", action=argparse.BooleanOptionalAction, default=None)
    history = sub.add_parser("history", help="Read or reconcile transfer history")
    history.add_argument("--resolve", metavar="JOB_ID")
    history.add_argument("--status", choices=["completed", "failed"])
    history.add_argument("--video-id")
    return cli


def reconcile(config, job_id, outcome, video_id=None):
    import re
    history = History(config)
    row = next((r for r in history.records() if r["job_id"] == job_id), None)
    if not row or row["operation"] != "upload" or row["status"] not in {"attaching", "submitted", "unresolved"}:
        raise ValueError("Only an uncertain upload can be reconciled")
    if outcome not in {"completed", "failed"}:
        raise ValueError("Reconciliation requires --status completed or failed")
    video_id = video_id or row.get("video_id")
    if outcome == "completed" and (not video_id or not re.fullmatch(r"[\w-]{11}", video_id)):
        raise ValueError("A completed upload requires its YouTube video ID")
    row.update(status=outcome, reconciled_at=now(), error=None, error_code=None, stop_batch=False,
               stage="reconciled", confirmation_source="user-confirmed outcome")
    if video_id:
        row.update(video_id=video_id, youtube_url=f"https://www.youtube.com/watch?v={video_id}")
    history.write(row)
    return row


def dispatch(args, config):
    if args.command == "setup":
        return setup(config)
    if args.command == "status":
        return status(config, online=args.online)
    if args.command == "history":
        if args.resolve:
            return {"ok": True, "record": reconcile(config, args.resolve, args.status, args.video_id)}
        if args.status or args.video_id:
            raise ValueError("--status and --video-id require --resolve")
        return {"ok": True, "records": History(config).records()}
    if args.command == "login":
        from src.session import Auth
        current = Auth(config).login(refresh=args.refresh)
        return {"ok": True, "channel_id": current[1]["channel_id"], "captured_at": current[1]["captured_at"]}
    if args.command == "download":
        from src.download import download_video
        return download_video(args.url, args.output, args.format, args.exact_4k,
                              config=config, exact_1080_only=args.exact_1080)
    if args.command == "upload":
        from src.upload import run_single_upload
        return run_single_upload(args.video, args.metadata, config=config, title=args.title,
                                 description=args.description, visibility=args.visibility,
                                 made_for_kids=args.made_for_kids, headless=args.headless)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    flags = {"--json", "--non-interactive"}
    args = parser().parse_args([x for x in argv if x in flags] + [x for x in argv if x not in flags])
    portable = ROOT / "local/runtime/python/python.exe"
    if args.command != "setup" and portable.is_file() and Path(sys.executable).resolve() != portable.resolve():
        import subprocess
        return subprocess.call([str(portable), str(Path(__file__).resolve()), *argv])
    try:
        config = Config()
        config.non_interactive = args.non_interactive
        with operation_context(config), redirect_stdout(sys.stderr):
            payload = dispatch(args, config)
        if isinstance(payload, Result):
            payload = {**payload.to_dict(), "ok": payload.status == "completed"}
        code = 2 if payload.get("status") == "login_required" else 130 if payload.get("status") == "cancelled" else 0 if payload.get("ok") else 1
    except (KeyboardInterrupt, Cancelled):
        payload, code = {"ok": False, "status": "cancelled", "error": "Operation cancelled", "error_code": "cancelled"}, 130
    except Exception as exc:
        payload = {"ok": False, "status": "login_required" if isinstance(exc, LoginRequired) else "failed", "error": str(exc), "error_code": error_code(exc)}
        code = 2 if isinstance(exc, LoginRequired) else 1
    if args.json or "checks" in payload or "records" in payload or "record" in payload:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(payload.get("message") or payload.get("status") or ("Ready" if payload.get("ok") else "Failed"))
        for field in ("local_path", "youtube_url", "channel_id", "job_id", "error"):
            if payload.get(field):
                print(f"{field}: {payload[field]}")
        for warning in payload.get("warnings", []):
            print(f"Warning: {warning}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
