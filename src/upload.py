"""YouTube Studio uploader adapted from Product's YouTubeUploader.

Field editing and click/scroll helpers are retained from the existing implementation.
Browser lifecycle and journaling belong to DUTY.
"""
from __future__ import annotations
import json
import logging
from pathlib import Path
import re
import time
import uuid
import subprocess

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import HTTPError
from .session import Auth
from .session import BrowserSession, until, verify_channel
from .core import Config, Cancelled
from .core import transfer
from .download import probe
from .core import (AuthenticationError, ChannelMismatch, LoginRequired, History, Result, UploadLimit,
                    UnresolvedUpload, file_hash, now, error_code)

class Constant:
    USER_WAITING_TIME = 0.3

class YouTubeUploader:
    def __init__(self, session, video_path, metadata, record, history):
        self.session = session
        self.browser = session.browser
        self.driver = session.driver
        self.config = session.config
        self.video_path = video_path
        self.metadata_dict = metadata
        self.record = record
        self.history = history
        self.is_mac = False
        self.logger = logging.getLogger("DUTY-Uploader")
        self.deadline = None

    def wait(self, predicate, timeout, message):
        remaining = timeout if self.deadline is None else min(timeout, max(0, self.deadline - time.monotonic()))
        return until(predicate, remaining, f"{self.record.get('stage', 'upload')}: {message}", self.config.check_cancel)

    def __clear_field(self, field):
        field.click()
        time.sleep(Constant.USER_WAITING_TIME)
        if self.is_mac:
            field.send_keys(Keys.COMMAND + "a")
        else:
            field.send_keys(Keys.CONTROL + "a")
        time.sleep(Constant.USER_WAITING_TIME)
        field.send_keys(Keys.BACKSPACE)

    def checkpoint(self, **values):
        self.record.update(values)
        self.history.write(self.record)
        self.config.emit("progress", message=self.record.get("stage", "upload"))

    def video_id(self):
        for element in self.driver.find_elements(By.CSS_SELECTOR, "ytcp-video-info a[href], ytcp-video-share-dialog a[href]"):
            href = element.get_attribute("href") or ""
            match = re.search(r"(?:youtu\.be/|[?&]v=|/shorts/)([\w-]{11})(?:[?&#/]|$)", href)
            if match:
                return match.group(1)
        return None

    def errors(self):
        video_id = self.video_id()
        if video_id and video_id != self.record.get("video_id"):
            self.checkpoint(video_id=video_id, youtube_url=f"https://www.youtube.com/watch?v={video_id}")
        for element in self.driver.find_elements(By.CSS_SELECTOR, "#error-message, .error-message"):
            if element.is_displayed() and element.text.strip():
                text = element.text.strip()
                if "limit" in text.lower():
                    raise UploadLimit(text)
                raise RuntimeError(text)

    def element(self, selector):
        def find():
            self.errors()
            matches = self.driver.find_elements(By.CSS_SELECTOR, selector)
            return next((e for e in matches if e.is_displayed()), None)
        return self.wait(find, self.config.timeout("element"), f"Studio control not found: {selector}")

    def choose_radio(self, name):
        element = self.element(f'[name="{name}"]')
        self.__click(element)
        self.wait(lambda: element.get_attribute("aria-checked") == "true" or element.get_attribute("checked") in {"true", ""},
              self.config.timeout("element"), f"Studio did not select {name}")

    def upload(self):
        self.deadline = time.monotonic() + self.config.timeout("upload")
        self.checkpoint(stage="attachment")
        self.driver.get("https://www.youtube.com/upload")
        file_input = self.wait(lambda: self.driver.find_elements(By.CSS_SELECTOR, "input[type=file]"),
                           self.config.timeout("element"), "Studio upload input not found")[0]
        # Persist BEFORE attaching: a crash after this point must not resubmit the file.
        self.config.check_cancel()
        self.checkpoint(status="attaching")
        file_input.send_keys(str(self.video_path))
        self.checkpoint(status="submitted", stage="metadata")
        title = self.element("#title-textarea #textbox")
        description = self.element("#description-textarea #textbox")
        self.__write_in_field(title, self.metadata_dict["title"], select_all=True)
        self.__write_in_field(description, self.metadata_dict["description"].replace("\n", Keys.ENTER), select_all=True)
        audience = "VIDEO_MADE_FOR_KIDS_MFK" if self.metadata_dict["made_for_kids"] else "VIDEO_MADE_FOR_KIDS_NOT_MFK"
        self.checkpoint(stage="audience")
        self.choose_radio(audience)
        for _ in range(3):
            self.__click(self.element("#next-button"))
        visibility = self.metadata_dict["visibility"].upper()
        self.checkpoint(stage="visibility")
        self.choose_radio(visibility)

        def ready():
            self.errors()
            video_id = self.video_id()
            if video_id and video_id != self.record.get("video_id"):
                self.checkpoint(video_id=video_id, youtube_url=f"https://www.youtube.com/watch?v={video_id}")
            progress = self.driver.find_elements(By.CSS_SELECTOR, "ytcp-video-upload-progress[uploading]")
            buttons = self.driver.find_elements(By.CSS_SELECTOR, "#done-button")
            if not progress and buttons and buttons[0].is_displayed() and buttons[0].is_enabled() and buttons[0].get_attribute("aria-disabled") != "true":
                return buttons[0] if self.record.get("video_id") else None
            return None

        self.checkpoint(stage="processing")
        done = self.wait(ready, self.config.timeout("upload"), "Upload did not become ready for completion")
        # Require the dialog to exist before saving; its subsequent disappearance
        # confirms the save transition even when no share dialog/toast is shown.
        if not any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, "ytcp-uploads-dialog")):
            raise RuntimeError("Upload dialog disappeared before save")
        self.checkpoint(stage="save_requested", save_requested_at=now())
        self.session.close_after_save(seconds=5)
        self.driver.command_executor.set_timeout(1)
        try:
            self.__click(done)
            self.wait(self.save_completed, 1, "Studio has not yet confirmed completion")
        except (TimeoutError, WebDriverException, HTTPError):
            # Close Firefox first. A slower read-only check belongs outside its session.
            self.checkpoint(stage="confirming_saved_video")
        else:
            self.checkpoint(status="completed", stage="completed")
        return self.record["video_id"]

    def save_completed(self):
        self.errors()
        if not self.record.get("video_id") or not self.record.get("save_requested_at"):
            return False
        from urllib.parse import urlparse
        if urlparse(self.driver.current_url).hostname != "studio.youtube.com":
            return False
        if not any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, "ytcp-app")):
            return False
        if any(e.is_displayed() for e in self.driver.find_elements(By.CSS_SELECTOR, "ytcp-video-share-dialog")):
            return True
        dialogs = self.driver.find_elements(By.CSS_SELECTOR, "ytcp-uploads-dialog")
        return not any(e.is_displayed() for e in dialogs)

    def __write_in_field(self, field, string, select_all=False):
        self.config.check_cancel()
        if select_all:
            self.__clear_field(field)
        else:
            field.click()
            time.sleep(Constant.USER_WAITING_TIME)

        field.send_keys(string)

    def __scroll_into_view(self, element):
        self.browser.driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});",
            element,
        )
        time.sleep(Constant.USER_WAITING_TIME)

    def __click(self, element):
        self.config.check_cancel()
        self.__scroll_into_view(element)
        try:
            element.click()
        except Exception:
            self.browser.driver.execute_script("arguments[0].click();", element)
        time.sleep(Constant.USER_WAITING_TIME)


def load_metadata(video: Path, path: Path | None, config: Config, *, visibility=None, made_for_kids=None,
                  title=None, description=None):
    if not video.is_file() or video.suffix.lower() not in {".mp4", ".mkv"}:
        raise ValueError(f"Expected an existing MP4 or MKV: {video}")
    payload = json.loads(path.read_text(encoding="utf-8-sig")) if path is not None else {}
    if not isinstance(payload, dict):
        raise ValueError("Metadata must be a JSON object")
    unsupported = set(payload) - {"title", "description", "visibility", "made_for_kids"}
    if unsupported:
        raise ValueError("Unsupported metadata fields: " + ", ".join(sorted(unsupported)))
    metadata = {**config.values["upload"], "title": video.stem, "description": "", **payload}
    metadata.pop("headless")
    if title is not None:
        metadata["title"] = title
    if description is not None:
        metadata["description"] = description
    if visibility is not None:
        metadata["visibility"] = visibility
    if made_for_kids is not None:
        metadata["made_for_kids"] = made_for_kids
    if isinstance(metadata["title"], str) and not metadata["title"].strip():
        metadata["title"] = video.stem
    for key, limit in (("title", 100), ("description", 5000)):
        if not isinstance(metadata[key], str) or len(metadata[key]) > limit:
            raise ValueError(f"{key} must be a string of at most {limit} characters")
    if not isinstance(metadata["visibility"], str) or metadata["visibility"] not in {"public", "private", "unlisted"}:
        raise ValueError("visibility must be public, private or unlisted")
    if not isinstance(metadata["made_for_kids"], bool):
        raise ValueError("made_for_kids must be a JSON boolean")
    return metadata


def validate_upload_media(config, video):
    if video.stat().st_size == 0:
        raise ValueError("The input video is empty")
    try:
        data = probe(config, video)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise ValueError("The input is not a readable video") from exc
    formats = data.get("format", {}).get("format_name", "").split(",")
    expected = {".mp4": "mp4", ".mkv": "matroska"}.get(video.suffix.lower())
    if expected is None or expected not in formats:
        raise ValueError("The input container does not match its MP4/MKV extension")


def _metadata_request(options, url, connection):
    """A disposable child process bounds the complete network extraction, not just one socket."""
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        connection.send({name: info.get(name) for name in ("id", "channel_id", "title", "description", "availability")})
    except Exception as exc:
        connection.send({"error": str(exc)})
    finally:
        connection.close()


def _bounded_metadata(config, options, url, deadline):
    import multiprocessing
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    process = context.Process(target=_metadata_request, args=(options, url, send))
    try:
        process.start()
        send.close()
        while process.is_alive() and time.monotonic() < deadline:
            config.check_cancel()
            if receive.poll(min(.2, max(0, deadline - time.monotonic()))):
                break
        return receive.recv() if receive.poll() else {}
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        receive.close()
        send.close()


def confirm_saved_video(config, record, metadata):
    """Check only this saved video; no login, upload, or publish retry is allowed."""
    if not record.get("save_requested_at") or not re.fullmatch(r"[\w-]{11}", record.get("video_id") or ""):
        return False
    from .download import youtube_cookie_options
    deadline = time.monotonic() + 120
    expected = {"id": record["video_id"], "channel_id": record["channel_id"],
                "title": metadata["title"], "description": metadata["description"],
                "availability": metadata["visibility"]}
    while time.monotonic() < deadline:
        config.check_cancel()
        started = time.monotonic()
        try:
            with youtube_cookie_options(config) as auth_options:
                options = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                           "socket_timeout": 5, "retries": 0, "extractor_retries": 0, **auth_options}
                info = _bounded_metadata(config, options, f"https://www.youtube.com/watch?v={record['video_id']}",
                                         min(deadline, started + 10))
                if all(info.get(key) == value for key, value in expected.items()):
                    return True
        except Cancelled:
            raise
        except Exception as exc:
            record["confirmation_error"] = str(exc)
        wait = max(0, min(deadline, started + 10) - time.monotonic())
        if config.cancel_event.wait(wait):
            config.check_cancel()
    return False


@transfer
def run_single_upload(video_path, metadata_path=None, *, config=None, headless=None,
                      visibility=None, made_for_kids=None, title=None, description=None):
    config = config or Config()
    video = Path(video_path).resolve()
    history = History(config)
    auth = Auth(config)
    record = {"operation": "upload", "job_id": "upload_" + uuid.uuid4().hex,
              "status": "prepared", "stage": "input", "local_path": str(video), "created_at": now()}
    history.write(record)
    try:
        # An explicit missing metadata file is an error; an absent adjacent file is optional.
        path = Path(metadata_path) if metadata_path is not None else video.parent / "metadata.json"
        metadata = load_metadata(video, path if metadata_path is not None or path.is_file() else None, config,
                                 visibility=visibility, made_for_kids=made_for_kids, title=title, description=description)
        validate_upload_media(config, video)
        config.check_cancel()
        record.update(metadata=metadata, stage="authentication")
        history.write(record)
        current = auth.ensure()
        channel_id = current[1]["channel_id"]
        fingerprint = file_hash(video)
        duplicate = history.duplicate(fingerprint, channel_id)
        if duplicate:
            # This attempt never attached anything. Keep the existing authoritative record.
            record.update(status="cancelled", stage="duplicate", duplicate_of=duplicate["job_id"])
            history.write(record)
            outcome = "completed" if duplicate["status"] == "completed" else "unresolved"
            return Result("upload", outcome, str(video), video_id=duplicate.get("video_id"),
                          youtube_url=duplicate.get("youtube_url"), job_id=duplicate["job_id"],
                          error="Already recorded; file was not submitted again", stop_batch=outcome == "unresolved", stage="duplicate")
        record.update(channel_id=channel_id, fingerprint=fingerprint)
        history.write(record)
        for attempt in range(2):
            config.check_cancel()
            profile = auth.working_profile()
            session = None
            try:
                with BrowserSession(config, profile, headless=config.values["upload"]["headless"] if headless is None else headless) as session:
                    verify_channel(session, channel_id, config.timeout("element"))
                    uploader = YouTubeUploader(session, video, metadata, record, history)
                    uploader.upload()
                if session.shutdown_warning:
                    record.setdefault("warnings", []).append(session.shutdown_warning)
                record["browser_closed_at"] = now()
                history.write(record)
                if record["status"] != "completed" and record.get("save_requested_at"):
                    config.emit("progress", message="Firefox closed. Confirming the saved video…")
                    if not confirm_saved_video(config, record, metadata):
                        raise UnresolvedUpload("Firefox is closed; saved video metadata did not confirm completion")
                    record.update(status="completed", stage="completed", confirmation_source="authenticated video metadata")
                    history.write(record)
                break
            except ChannelMismatch:
                raise
            except AuthenticationError:
                if attempt == 0 and record["status"] == "prepared":
                    auth.login(refresh=True)
                    continue
                raise
            finally:
                if record["status"] == "completed" and session is not None and not session.shutdown_warning:
                    try:
                        auth.capture(profile, channel_id)
                    except Exception as exc:
                        record.setdefault("warnings", []).append(f"Session was not renewed: {exc}")
                auth.cleanup(profile)
    except BaseException as exc:
        if record["status"] != "completed":
            ambiguous = record["status"] in {"attaching", "submitted"}
            record["status"] = ("unresolved" if ambiguous else "cancelled" if isinstance(exc, (Cancelled, KeyboardInterrupt))
                                else "login_required" if isinstance(exc, LoginRequired) else "failed")
            record["error"] = str(exc) or type(exc).__name__
            record["error_code"] = "cancelled" if isinstance(exc, (Cancelled, KeyboardInterrupt)) else "timeout" if isinstance(exc, TimeoutError) else error_code(exc)
            record["stop_batch"] = ambiguous or isinstance(exc, (AuthenticationError, UploadLimit, KeyboardInterrupt, Cancelled))
        else:
            record.setdefault("warnings", []).append(str(exc))
        if isinstance(exc, SystemExit):
            history.write(record)
            raise
    record.setdefault("warnings", []).extend(auth.warnings)
    history.write(record)
    return Result("upload", record["status"], str(video), video_id=record.get("video_id"),
                  youtube_url=record.get("youtube_url"), error=record.get("error"),
                  job_id=record["job_id"], stop_batch=record.get("stop_batch", False),
                  error_code=record.get("error_code"), stage=record.get("stage"), warnings=record.get("warnings", []))


upload_video = run_single_upload
