from __future__ import annotations

import http.cookiejar
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from src.auth import Auth, authentication_rejected
from src.browser import studio_channel, verify_channel
from src.cli import parser, run_batch
from src.config import Config
from src.download import resolve_downloaded_path, sanitized_video_stem, unique_sanitized_path
from src.state import AuthenticationError, ChannelMismatch, History, Result, atomic_json
from src.upload import load_metadata, run_single_upload, YouTubeUploader

CHANNEL = "UC" + "a" * 22


def cookie(expires=None):
    return http.cookiejar.Cookie(0, "SAPISID", "test-only", None, False, ".youtube.com", True,
                                True, "/", True, True, expires or int(time.time()) + 3600,
                                False, None, None, {}, False)


class DutyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = Config(self.root)
        for name in ("work", "history", "permanent/auth/generations"):
            self.config.path(name).mkdir(parents=True)
        self.auth = Auth(self.config)
        self.video = self.root / "sample.mp4"
        self.video.write_bytes(b"test video content")
        self.meta = self.root / "metadata.json"
        self.meta.write_text("{}")
        media = patch("src.upload.probe", return_value={"streams": [{"codec_type": "video"}], "format": {"format_name": "mov,mp4"}})
        media.start()
        self.addCleanup(media.stop)

    def tearDown(self):
        self.temp.cleanup()

    def session(self):
        profile = self.root / "work/browser_test/profile"
        profile.mkdir(parents=True, exist_ok=True)
        (profile / "prefs.js").write_text("// test profile")
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=[cookie()]):
            self.auth.capture(profile, CHANNEL)
        return self.auth.current()

    def test_cookie_copy_cannot_change_saved_cookie_file(self):
        path, _ = self.session()
        original = (path / "cookies.txt").read_bytes()
        with self.auth.cookie_copy() as copy:
            self.assertNotEqual(copy, path / "cookies.txt")
            copy.write_text("modified by downloader")
        self.assertFalse(copy.exists())
        self.assertEqual((path / "cookies.txt").read_bytes(), original)

    def test_cookie_copy_cleanup_after_failure(self):
        self.session()
        with self.assertRaises(RuntimeError):
            with self.auth.cookie_copy() as copy:
                raise RuntimeError("network failed")
        self.assertFalse(copy.exists())

    def test_failed_capture_retains_saved_generation(self):
        original, _ = self.session()
        profile = self.auth.working_profile()
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=[]):
            with self.assertRaises(AuthenticationError):
                self.auth.capture(profile, CHANNEL)
        self.assertEqual(self.auth.current()[0], original)

    def test_interrupted_generation_does_not_replace_pointer(self):
        original, _ = self.session()
        profile = self.auth.working_profile()
        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=[cookie()]), \
             patch("src.auth.atomic_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.auth.capture(profile, CHANNEL)
        self.assertEqual(self.auth.current()[0], original)

    def test_wrong_channel_cannot_replace_authentication(self):
        original, _ = self.session()
        with self.assertRaises(ChannelMismatch):
            self.auth.capture(self.auth.working_profile(), "UC" + "b" * 22)
        self.assertEqual(self.auth.current()[0], original)

    def test_refresh_creates_immutable_generations(self):
        first, _ = self.session()
        second, _ = self.session()
        self.assertNotEqual(first, second)
        self.assertTrue((first / "cookies.txt").is_file())
        self.assertFalse(self.auth.stale())

    def test_expired_cookie_requires_refresh(self):
        path, _ = self.session()
        jar = http.cookiejar.MozillaCookieJar(str(path / "cookies.txt"))
        jar.set_cookie(cookie(expires=1))
        jar.save(ignore_discard=True, ignore_expires=True)
        self.assertTrue(self.auth.stale())

    def test_explicit_auth_rejection_only(self):
        self.assertTrue(authentication_rejected("Your cookies are no longer valid"))
        for error in ("HTTP Error 403", "Sign in to confirm you're not a bot", "Video unavailable", "connection timeout"):
            self.assertFalse(authentication_rejected(error))

    def test_studio_channel_requires_real_studio_host(self):
        driver = MagicMock()
        driver.current_url = "https://studio.youtube.com/channel/" + CHANNEL
        driver.find_elements.return_value = [object()]
        self.assertEqual(studio_channel(driver), CHANNEL)
        driver.current_url = "https://example.com/channel/" + CHANNEL
        self.assertIsNone(studio_channel(driver))

    def test_wrong_channel_rejected_before_attachment(self):
        session = MagicMock()
        session.driver.current_url = "https://studio.youtube.com/channel/UC" + "b" * 22
        with self.assertRaises(ChannelMismatch):
            verify_channel(session, CHANNEL, .1)

    def test_metadata_defaults_and_overrides(self):
        data = load_metadata(self.video, self.meta, self.config)
        self.assertEqual(data, {"title": "sample", "description": "", "visibility": "public", "made_for_kids": False})
        data = load_metadata(self.video, self.meta, self.config, visibility="private", made_for_kids=True)
        self.assertEqual(data["visibility"], "private")
        self.assertTrue(data["made_for_kids"])

    def test_unsupported_metadata_rejected_before_browser(self):
        self.meta.write_text('{"schedule": "tomorrow"}')
        with patch("src.upload.BrowserSession") as browser, self.assertRaises(ValueError):
            run_single_upload(self.video, config=self.config)
        browser.assert_not_called()

    def test_invalid_audience_rejected(self):
        self.meta.write_text('{"made_for_kids": "false"}')
        with self.assertRaises(ValueError):
            load_metadata(self.video, self.meta, self.config)

    def test_missing_metadata_rejected_before_authentication(self):
        self.meta.unlink()
        with patch("src.upload.Auth") as auth, self.assertRaises(FileNotFoundError):
            run_single_upload(self.video, config=self.config)
        auth.assert_not_called()

    def test_upload_crash_preserves_id_and_blocks_resubmission(self):
        self.session()
        initial = self.video.read_bytes()
        def crash(uploader):
            uploader.checkpoint(status="submitted", video_id="abcdefghijk")
            raise RuntimeError("browser disconnected")
        with patch("src.upload.BrowserSession"), patch("src.upload.verify_channel"), \
             patch.object(YouTubeUploader, "upload", crash):
            result = run_single_upload(self.video, config=self.config)
        self.assertEqual(result.status, "unresolved")
        self.assertEqual(result.video_id, "abcdefghijk")
        with patch("src.upload.BrowserSession") as browser:
            repeated = run_single_upload(self.video, config=self.config)
        browser.assert_not_called()
        self.assertEqual(repeated.status, "unresolved")
        self.assertEqual(self.video.read_bytes(), initial)

    def test_completion_survives_session_capture_failure(self):
        self.session()
        def complete(uploader):
            uploader.checkpoint(status="completed", video_id="abcdefghijk")
        with patch("src.upload.BrowserSession"), patch("src.upload.verify_channel"), \
             patch.object(YouTubeUploader, "upload", complete), \
             patch.object(Auth, "capture", side_effect=OSError("cannot save session")):
            result = run_single_upload(self.video, config=self.config)
        self.assertEqual(result.status, "completed")
        self.assertTrue(any("cannot save session" in warning for warning in result.warnings))
        self.assertTrue(self.video.is_file())

    def test_only_one_pre_attachment_refresh(self):
        self.session()
        profiles_before = set(self.config.path("work").glob("browser_*"))
        with patch("src.upload.BrowserSession"), \
             patch("src.upload.verify_channel", side_effect=AuthenticationError("expired")), \
             patch.object(Auth, "login", return_value=self.auth.current()) as login:
            result = run_single_upload(self.video, config=self.config)
        self.assertEqual(result.status, "failed")
        self.assertEqual(sum(call.kwargs.get("refresh", False) for call in login.call_args_list), 1)
        self.assertEqual(set(self.config.path("work").glob("browser_*")), profiles_before)

    def test_mixed_batch_continues_invalid_input_stops_uncertain_upload(self):
        visited = []
        def worker(item):
            visited.append(item)
            if item == 1:
                raise ValueError("bad metadata")
            return Result("upload", "unresolved" if item == 3 else "completed", stop_batch=item == 3)
        result = run_batch([1, 2, 3, 4], worker, "upload")
        self.assertEqual(visited, [1, 2, 3])
        self.assertEqual([r["status"] for r in result["results"]], ["failed", "completed", "unresolved", "pending"])

    def test_history_corruption_blocks_duplicate_check(self):
        self.config.path("history/broken.json").write_text("bad")
        with self.assertRaises(RuntimeError):
            History(self.config).duplicate("x", CHANNEL)

    def test_no_unrelated_download_fallback(self):
        (self.root / "old.mp4").write_bytes(b"old")
        with self.assertRaises(FileNotFoundError):
            resolve_downloaded_path({"filepath": str(self.root / "missing.mp4")}, self.root, "mp4")

    def test_download_path_cannot_escape_job_folder(self):
        job = self.root / "job"
        job.mkdir()
        with self.assertRaises(ValueError):
            resolve_downloaded_path({"filepath": str(self.video)}, job, "mp4")

    def test_filename_collision_preserves_existing_file(self):
        self.assertEqual(unique_sanitized_path(self.root, "sample", ".mp4").name, "sample_2.mp4")
        self.assertEqual(sanitized_video_stem("Hello - World!"), "hello_world")

    def test_cli_selection(self):
        cli = parser()
        self.assertEqual(cli.parse_args(["upload", str(self.video), "--visibility", "private"]).visibility, "private")
        self.assertEqual(cli.parse_args(["download", "https://youtu.be/abcdefghijk"]).format, "mp4")


if __name__ == "__main__":
    unittest.main()
