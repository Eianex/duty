"""Standalone regression tests. No network or live YouTube browser interactions."""
import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from filelock import FileLock, Timeout
from src.auth import Auth
from src.cli import main, run_batch
from src.config import Config
from src.download import download_video, finalize_file, validate_output
from src.operation import operation_context
from src.search import youtube_video_search
from src.state import AuthenticationError, ChannelMismatch, History, Result, atomic_json, error_code
from src.upload import YouTubeUploader, load_metadata, run_single_upload, validate_upload_media

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_support import promote, write_manifest, installation_lock
from package import release_files

CHANNEL = "UC" + "a" * 22


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root)
        self.config.path("work").mkdir()
        self.video = self.root / "video.mp4"
        self.video.write_bytes(b"fixture content")
        self.meta = self.root / "metadata.json"
        self.meta.write_text('{}')

    def tearDown(self):
        self.temp.cleanup()

    def test_nested_operation_restores_environment_after_exception(self):
        old = dict(os.environ)
        old_temp = tempfile.tempdir
        with self.assertRaisesRegex(ValueError, "deliberate"):
            with operation_context(self.config), operation_context(self.config):
                self.assertEqual(os.environ["DENO_DIR"], str(self.root / "cache/deno"))
                raise ValueError("deliberate")
        self.assertEqual(dict(os.environ), old)
        self.assertEqual(tempfile.tempdir, old_temp)

    def test_conflicting_operation_does_not_modify_environment(self):
        old = dict(os.environ)
        with FileLock(str(self.root / "work/duty.lock")), self.assertRaises(Timeout):
            with operation_context(self.config):
                self.fail("lock acquired")
        self.assertEqual(dict(os.environ), old)

    def test_build_lock_conflicts_with_transfer(self):
        with operation_context(self.config), self.assertRaises(RuntimeError):
            with installation_lock(self.root):
                self.fail("build acquired transfer lock")

    def test_cancelled_login_cleans_working_profile(self):
        profile = self.root / "work/browser_cancel/profile"
        profile.mkdir(parents=True)
        with patch.object(Auth, "working_profile", return_value=profile), patch("src.auth.LoginWindow"), \
             patch("src.auth.until", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            Auth(self.config).login()
        self.assertFalse(profile.parent.exists())

    def test_timed_out_login_cleans_profile(self):
        profile = self.root / "work/browser_timeout/profile"
        profile.mkdir(parents=True)
        with patch.object(Auth, "working_profile", return_value=profile), patch("src.auth.LoginWindow"), \
             patch("src.auth.until", side_effect=TimeoutError("expired")), self.assertRaises(AuthenticationError):
            Auth(self.config).login()
        self.assertFalse(profile.parent.exists())

    def test_saved_channel_mismatch_rejected(self):
        atomic_json(self.root / "permanent/channel.json", {"channel_id": CHANNEL})
        atomic_json(self.root / "permanent/auth/current.json", {"generation": "abc"})
        atomic_json(self.root / "permanent/auth/generations/abc/session.json", {"channel_id": "UC" + "b" * 22})
        with self.assertRaises(ChannelMismatch):
            Auth(self.config).current()

    def test_cross_drive_finalization_verifies_then_removes_source(self):
        output = self.root / "output.mp4"
        rename = Path.rename
        def cross_drive(path, destination):
            if path == self.video:
                raise OSError(errno.EXDEV, "different device")
            return rename(path, destination)
        with patch.object(Path, "rename", cross_drive):
            finalize_file(self.video, output)
        self.assertEqual(output.read_bytes(), b"fixture content")
        self.assertFalse(self.video.exists())
        self.assertFalse(list(self.root.glob('.duty_*')))

    def test_interrupted_copy_preserves_source_and_hides_partial_output(self):
        output = self.root / "output.mp4"
        with patch.object(Path, "rename", side_effect=OSError(errno.EXDEV, "other drive")), \
             patch("src.download.shutil.copyfileobj", side_effect=OSError("disk full")), self.assertRaises(OSError):
            finalize_file(self.video, output)
        self.assertTrue(self.video.exists())
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob('.duty_*')))

    def test_output_validation_rejects_wrong_codec_or_resolution(self):
        streams = [{"codec_type": "video", "codec_name": "vp9", "height": 720}, {"codec_type": "audio", "codec_name": "aac"}]
        with patch("src.download.probe", return_value={"streams": streams}):
            with self.assertRaisesRegex(RuntimeError, "H.264"):
                validate_output(self.config, self.video, "mp4")
            with self.assertRaisesRegex(RuntimeError, "2160p"):
                validate_output(self.config, self.video, "mkv", True)

    def test_download_resume_options_and_mkv_postprocessor(self):
        captured = []
        def extract(url, download):
            raise RuntimeError("connection interrupted")
        def ydl(options):
            captured.append(options)
            instance = MagicMock()
            instance.__enter__.return_value.extract_info.side_effect = extract
            return instance
        with patch("src.download.Auth.ensure"), patch("src.download.provider_options", return_value={}), \
             patch("src.download.youtube_cookie_options") as cookies, patch("yt_dlp.YoutubeDL", side_effect=ydl):
            cookies.return_value.__enter__.return_value = {}
            first = download_video("https://youtu.be/abcdefghijk", container="mkv", config=self.config)
            second = download_video("https://youtu.be/abcdefghijk", container="mkv", config=self.config)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(first.error_code, "network")
        self.assertEqual(captured[0]["outtmpl"], captured[1]["outtmpl"])
        self.assertTrue(captured[0]["continuedl"])
        self.assertEqual(captured[0]["postprocessors"], [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}])

    def test_error_categories(self):
        for message, code in (("Requested format is not available", "format_unavailable"),
                              ("Video unavailable", "video_unavailable"), ("BgUtil provider failed", "provider"),
                              ("HTTP Error 503", "network")):
            self.assertEqual(error_code(RuntimeError(message)), code)
        self.assertEqual(error_code(OSError("disk full")), "filesystem")
        self.assertEqual(error_code(AuthenticationError("expired")), "authentication")

    def test_long_filename_title_rejected_after_empty_title_fallback(self):
        video = self.root / ("x" * 101 + ".mp4")
        video.write_bytes(b"x")
        self.meta.write_text('{"title":" "}')
        with self.assertRaisesRegex(ValueError, "title"):
            load_metadata(video, self.meta, self.config)

    def test_invalid_media_rejected_before_authentication(self):
        with patch("src.upload.probe", side_effect=RuntimeError("no video")), \
             patch("src.upload.Auth") as auth, self.assertRaises(ValueError):
            run_single_upload(self.video, config=self.config)
        auth.assert_not_called()

    def test_non_mp4_container_rejected(self):
        with patch("src.upload.probe", return_value={"format": {"format_name": "matroska,webm"}}), self.assertRaises(ValueError):
            validate_upload_media(self.config, self.video)

    def uploader(self):
        session = MagicMock()
        session.config = self.config
        session.driver.current_url = "https://studio.youtube.com/channel/" + CHANNEL
        uploader = YouTubeUploader(session, self.video, {}, {"video_id": "abcdefghijk", "save_requested_at": "now"}, MagicMock())
        uploader.errors = MagicMock()
        app = MagicMock()
        app.is_displayed.return_value = True
        session.driver.find_elements.side_effect = lambda by, selector: [app] if selector == "ytcp-app" else []
        return uploader

    def test_save_completion_without_toast_or_share_dialog(self):
        self.assertTrue(self.uploader().save_completed())

    def test_open_dialog_or_navigation_is_not_completion(self):
        uploader = self.uploader()
        dialog = MagicMock()
        dialog.is_displayed.return_value = True
        uploader.driver.find_elements.side_effect = lambda by, selector: [dialog] if selector in {"ytcp-app", "ytcp-uploads-dialog"} else []
        self.assertFalse(uploader.save_completed())
        uploader = self.uploader()
        uploader.driver.current_url = "https://accounts.google.com/"
        self.assertFalse(uploader.save_completed())
        uploader = self.uploader()
        uploader.record.pop("save_requested_at")
        self.assertFalse(uploader.save_completed())

    def test_mixed_batch_is_persisted_with_remaining_jobs(self):
        def worker(item):
            if item == 1:
                raise ValueError("bad input")
            return Result("upload", "unresolved", stop_batch=True)
        result = run_batch([1, 2, 3], worker, "upload", self.config)
        records = History(self.config).records()
        self.assertEqual(len(records), 4)
        self.assertEqual(result["summary"]["pending"], 1)
        self.assertEqual(next(r for r in records if r["operation"] == "batch")["results"], result["results"])

    def test_invalid_history_schema_and_path_are_rejected(self):
        history = History(self.config)
        atomic_json(history.folder / "upload_bad.json", [])
        with self.assertRaises(RuntimeError):
            history.records()
        with self.assertRaises(ValueError):
            history.write({"job_id": "../escape", "operation": "upload", "status": "failed"})

    def test_wrapper_global_flags_and_core_status(self):
        output = io.StringIO()
        with patch("src.cli.Config", return_value=self.config), contextlib.redirect_stdout(output):
            code = main(["login", "--json", "--non-interactive"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "login_required")
        from src.status import status
        self.assertFalse(status(self.config)["firefox_authentication"]["saved"])

    def test_search_refreshes_once_and_keeps_long_videos(self):
        from yt_dlp.utils import DownloadError
        with patch("src.search.Auth") as auth, patch("src.search.youtube_cookie_options") as cookies, \
             patch("src.search.yt_dlp.YoutubeDL") as ydl:
            cookies.return_value.__enter__.return_value = {}
            ydl.return_value.__enter__.return_value.extract_info.side_effect = [
                DownloadError("cookies have expired"), {"entries": [{"id": "abcdefghijk", "duration": 99999}]}]
            result = youtube_video_search("fixture", config=self.config)
        auth.return_value.ensure.assert_called_once()
        auth.return_value.login.assert_called_once_with(refresh=True)
        self.assertEqual(result[0]["duration"], 99999)

    def test_manifest_excludes_itself_and_includes_vendor(self):
        (self.root / "runtime").mkdir()
        (self.root / "runtime/tool.exe").write_bytes(b"tool")
        (self.root / "vendor").mkdir()
        (self.root / "vendor/native.node").write_bytes(b"native")
        (self.root / "runtime.lock.json").write_text('{}')
        write_manifest(self.root, release_files(self.root))
        first = (self.root / "runtime/manifest.json").read_bytes()
        write_manifest(self.root, release_files(self.root))
        self.assertEqual(first, (self.root / "runtime/manifest.json").read_bytes())
        self.assertEqual(set(json.loads(first)["files"]), {"runtime/tool.exe", "vendor/native.node"})

    def test_failed_promotion_restores_previous_installation(self):
        stage = self.root / "cache/stage_test"
        for base in (self.root, stage):
            for relative in ("runtime/tool.exe", "vendor/bgutil/server/node_modules/native.node", "requirements.txt", "runtime.lock.json"):
                path = base / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("new" if base == stage else "old")
        rename = Path.rename
        def fail_once(path, destination):
            if path == stage / "requirements.txt":
                raise OSError("simulated failure")
            return rename(path, destination)
        with patch.object(Path, "rename", fail_once), self.assertRaises(OSError):
            promote(stage, self.root)
        for relative in ("runtime/tool.exe", "vendor/bgutil/server/node_modules/native.node", "requirements.txt", "runtime.lock.json"):
            self.assertEqual((self.root / relative).read_text(), "old")


if __name__ == "__main__":
    unittest.main()
