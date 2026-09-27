"""YouTube downloads with isolated cookies and resumable transfer handling."""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
import hashlib
import importlib
import json
import re
import sys
import subprocess
import errno
import os
import shutil
import tempfile
from typing import Any
from urllib.parse import urlparse

from .core import Config, Cancelled
from .session import Auth, authentication_rejected
from .core import AuthenticationError, LoginRequired, History, Result, now, error_code, file_hash
from .core import transfer

MAX_FILENAME_STEM_LENGTH = 24

def progress_hook(status: dict[str, Any]) -> None:
    state = status.get("status")

    if state == "downloading":
        percent = status.get("_percent_str", "").strip()
        speed = status.get("_speed_str", "").strip()
        eta = status.get("_eta_str", "").strip()
        filename = status.get("filename", "")
        print(
            f"\rDownloading: {percent} | {speed} | ETA {eta} | {Path(filename).name}",
            end="",
        )

    elif state == "finished":
        print("\nDownload finished. Merging/converting if needed...")


def sanitized_video_stem(title: str) -> str:
    stem = re.sub(r"\s+", "_", title.strip().lower())
    stem = re.sub(r"[^A-Za-z0-9_]", "", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    stem = stem[:MAX_FILENAME_STEM_LENGTH].rstrip("_")
    return stem or "video"


def unique_sanitized_path(output_dir: Path, stem: str, suffix: str) -> Path:
    candidate = output_dir / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate

    for index in range(2, 1000):
        suffix_marker = f"_{index}"
        available_length = MAX_FILENAME_STEM_LENGTH - len(suffix_marker)
        indexed_stem = f"{stem[:available_length].rstrip('_')}{suffix_marker}"
        candidate = output_dir / f"{indexed_stem}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Could not create a unique sanitized filename in {output_dir}")


def sanitize_downloaded_path(
    downloaded_path: Path,
    info: dict[str, Any],
    output_dir: Path,
) -> Path:
    title = str(info.get("title") or downloaded_path.stem)
    stem = sanitized_video_stem(title)
    target_path = unique_sanitized_path(output_dir, stem, downloaded_path.suffix)
    if downloaded_path.resolve() == target_path.resolve():
        return downloaded_path

    finalize_file(downloaded_path, target_path)
    print(f"Sanitized downloaded filename: {target_path.name}")
    return target_path


def finalize_file(source: Path, target: Path):
    """Finalize on another drive without exposing a partially copied output."""
    try:
        source.rename(target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".duty_", suffix=".partial", dir=target.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with source.open("rb") as original:
                shutil.copyfileobj(original, stream)
            stream.flush()
            os.fsync(stream.fileno())
        if file_hash(source) != file_hash(temporary):
            raise OSError("Output verification failed after cross-drive copy; original retained")
        temporary.rename(target)
        source.unlink()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def provider_options(config):
    root = config.path("vendor", "bgutil")
    for path in (config.tool("deno"), root / "plugin/yt_dlp_plugins/extractor/getpot_bgutil_script.py",
                 config.path("provider/server/src/generate_once.ts"), config.path("provider/server/node_modules")):
        if not path.exists():
            raise FileNotFoundError(f"Missing provider dependency: {path}. Run python src/main.py setup.")
    plugin = str(root / "plugin")
    if plugin not in sys.path:
        sys.path.insert(0, plugin)
    return {"extractor_args": {"youtubepot-bgutilscript": {"server_home": [str(config.path("provider/server"))]},
                               "youtubepot-bgutilhttp": {"disable": ["true"]}},
            "js_runtimes": {"deno": {"path": str(config.tool("deno"))}}}


@contextmanager
def youtube_cookie_options(config=None):
    config = config or Config()
    with Auth(config).cookie_copy() as path:
        yield {"cookiefile": str(path), "cachedir": str(config.path("cache", "yt-dlp")),
               **provider_options(config)}


def probe(config, path: Path, *, require_video=True):
    output = subprocess.run([str(config.tool("ffprobe")), "-v", "error", "-show_streams",
                             "-show_format", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    payload = json.loads(output.stdout)
    if require_video and not any(s.get("codec_type") == "video" for s in payload.get("streams", [])):
        raise RuntimeError("Downloaded output has no video stream")
    return payload


def validate_output(config, path, container, exact_4k_only=False, *, exact_1080_only=False):
    payload = probe(config, path, require_video=container != "mp3")
    streams = payload["streams"]
    if container == "mp3":
        if (not any(s.get("codec_type") == "audio" and s.get("codec_name") == "mp3" for s in streams)
                or any(s.get("codec_type") == "video" for s in streams)):
            raise RuntimeError("Output must contain MP3 audio only")
        return payload
    if not (
        any(s.get("codec_type") == "video" for s in streams)
        and any(s.get("codec_type") == "audio" for s in streams)
    ):
        raise RuntimeError("Output must contain both video and audio")
    requested_height = 2160 if exact_4k_only else 1080 if exact_1080_only else None
    if requested_height and not any(s.get("codec_type") == "video" and s.get("height") == requested_height for s in streams):
        raise RuntimeError(f"Output did not meet the requested exact {requested_height}p height")
    return payload


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"} or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com",
    }:
        raise ValueError("Provide a YouTube video URL")
    return url.strip()


def resolve_downloaded_path(info, output_dir: Path, container: str):
    # Only paths reported for this extraction are eligible, never arbitrary directory files.
    candidates = [info.get("filepath"), info.get("_filename")]
    candidates += [item.get("filepath") for item in info.get("requested_downloads", [])]
    for value in candidates:
        if not value:
            continue
        path = Path(value)
        for candidate in (path.with_suffix("." + container), path):
            if candidate.is_file() and candidate.suffix.lower() == "." + container:
                candidate.resolve().relative_to(output_dir.resolve())
                return candidate
    raise FileNotFoundError("yt-dlp did not report a completed output file")


@transfer
def download_video(url: str, download_path: str | Path | None = None,
                   container: str = "mp4", exact_4k_only: bool = False, *, config=None,
                   exact_1080_only: bool = False) -> Result:
    config = config or Config()
    url = validate_url(url)
    if container not in {"mp4", "mkv", "mp3"}:
        raise ValueError("Output format must be mp4, mkv or mp3")
    audio_only = container == "mp3"
    if audio_only and (exact_4k_only or exact_1080_only):
        raise ValueError("Video resolution options do not apply to MP3 audio downloads")
    if exact_4k_only and exact_1080_only:
        raise ValueError("Choose either exact 1080p or exact 4K")
    output_dir = Path(download_path or config.path("downloads")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    identity = f"{url}|{container}|{exact_4k_only}|{output_dir}"
    if container == "mp4":
        # Earlier MP4 jobs restricted codecs; do not reuse them for best-quality requests.
        identity += "|unrestricted-codecs-v1"
    if exact_1080_only:
        identity += "|1080p"
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    job = config.path("work", "download_" + key)
    job.mkdir(parents=True, exist_ok=True)
    history = History(config)
    record = {"job_id": "download_" + key, "operation": "download", "source": url,
              "status": "started", "created_at": now()}
    for previous in history.records():
        if previous.get("job_id") == record["job_id"] and previous.get("status") == "completed":
            if Path(previous["local_path"]).is_file():
                try:
                    validate_output(config, Path(previous["local_path"]), container, exact_4k_only,
                                    exact_1080_only=exact_1080_only)
                except Exception as exc:
                    result = Result("download", "failed", local_path=previous["local_path"], source=url,
                                    error=str(exc), error_code=error_code(exc), job_id=record["job_id"], stage="cached_output")
                    record.update(result.to_dict())
                    history.write(record)
                    return result
                return Result("download", "completed", local_path=previous["local_path"], source=url,
                              video_id=previous.get("video_id"), job_id=record["job_id"], stage="cached_output")
    history.write(record)
    def report_progress(event):
        config.check_cancel()
        config.emit("progress", downloaded=event.get("downloaded_bytes", 0),
                    total=event.get("total_bytes") or event.get("total_bytes_estimate"),
                    message=event.get("status", "downloading"))
        if not config.on_event:
            progress_hook(event)
    auth = Auth(config)
    try:
        auth.ensure()
        # Product's on-demand plugin discovery is retained.
        provider_options(config)
        yt_dlp = importlib.import_module("yt_dlp")
        height = "[height=2160]" if exact_4k_only else "[height=1080]" if exact_1080_only else ""
        format_selector = "bestaudio/best" if audio_only else f"bestvideo{height}+bestaudio/best{height}"
        for attempt in range(2):
            try:
                with youtube_cookie_options(config) as auth_options:
                    options = {"ignoreconfig": True, "outtmpl": str(job / "%(id)s.%(ext)s"),
                               "format": format_selector,
                               "noplaylist": True, "progress_hooks": [report_progress], "quiet": False,
                               "no_warnings": False, "retries": 10, "fragment_retries": 10,
                               "continuedl": True, "windowsfilenames": True,
                               "ffmpeg_location": str(config.tool("ffmpeg").parent),
                               "socket_timeout": 30, **auth_options}
                    if audio_only:
                        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                                      "preferredquality": "0"}]
                    else:
                        options["merge_output_format"] = container
                        options["postprocessors"] = [{"key": "FFmpegVideoRemuxer", "preferedformat": container}]
                    with yt_dlp.YoutubeDL(options) as ydl:
                        info = ydl.extract_info(url, download=True)
                break
            except yt_dlp.utils.DownloadError as exc:
                config.check_cancel()
                if attempt == 0 and authentication_rejected(str(exc)):
                    auth.login(refresh=True)
                    continue
                if authentication_rejected(str(exc)):
                    raise AuthenticationError(str(exc)) from exc
                raise RuntimeError(f"Download failed: {exc}") from exc
        config.check_cancel()
        downloaded = resolve_downloaded_path(info, job, container)
        validate_output(config, downloaded, container, exact_4k_only, exact_1080_only=exact_1080_only)
        downloaded = sanitize_downloaded_path(downloaded, info, output_dir)
        result = Result("download", "completed", str(downloaded), url, info.get("id"),
                        info.get("webpage_url"), job_id=record["job_id"])
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            exc = Cancelled("Download cancelled")
        result = Result("download", "cancelled" if isinstance(exc, Cancelled) else "login_required" if isinstance(exc, LoginRequired) else "failed", source=url, error=str(exc), job_id=record["job_id"],
                        stop_batch=isinstance(exc, (AuthenticationError, FileNotFoundError)), error_code=error_code(exc))
    record.update(result.to_dict())
    history.write(record)
    return result


