import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from src.auth import Auth
from src.browser import LoginWindow
from src.browser_upload import prepare, checkpoint, active_job, status
from src.cli import dispatch, main, parser
from src.config import Config
from src.state import ChannelMismatch, History, LoginRequired

CHANNEL = "UC" + "a" * 22


class BrowserUploadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.config = Config(Path(self.temporary.name))
        self.config.path("work").mkdir()
        self.video = self.config.path("test.mp4")
        self.video.write_bytes(b"synthetic fixture")
        self.config.path("metadata.json").write_text('{}')

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, **kwargs):
        return prepare(self.config, self.video, CHANNEL, **kwargs)["record"]

    def test_private_upload_journal_and_duplicate_prevention(self):
        original = self.video.read_bytes()
        row = self.prepare(visibility="private")
        job = row["job_id"]
        self.assertEqual(status(self.config)["channel_id"], CHANNEL)
        self.assertFalse(status(self.config)["firefox_authentication"]["saved"])
        self.assertTrue(checkpoint(self.config, job, "attach", channel_id=CHANNEL)["attach_allowed"])
        with self.assertRaises(ValueError):
            checkpoint(self.config, job, "attach", channel_id=CHANNEL)
        checkpoint(self.config, job, "checkpoint", video_id="abcdefghijk")
        checkpoint(self.config, job, "complete", channel_id=CHANNEL, video_id="abcdefghijk",
                   visibility="private", made_for_kids=False, confirmation="Saved privately")
        duplicate = prepare(self.config, self.video, CHANNEL)
        self.assertFalse(duplicate["attach_allowed"])
        self.assertEqual(duplicate["record"]["job_id"], job)
        self.assertEqual(self.video.read_bytes(), original)

    def test_default_visibility_remains_public(self):
        self.assertEqual(self.prepare()["metadata"]["visibility"], "public")

    def test_wrong_channel_rejected_before_attachment(self):
        job = self.prepare()["job_id"]
        with self.assertRaises(ChannelMismatch):
            checkpoint(self.config, job, "attach", channel_id="UC" + "b" * 22)
        self.assertEqual(active_job(self.config)["status"], "prepared")

    def test_browser_binding_rejects_wrong_firefox_capture(self):
        self.prepare()
        with self.assertRaises(ChannelMismatch):
            Auth(self.config).capture(self.config.path("unused"), "UC" + "b" * 22)

    def test_modified_input_rejected_before_attachment(self):
        job = self.prepare()["job_id"]
        self.video.write_bytes(b"different file")
        with self.assertRaises(ValueError):
            checkpoint(self.config, job, "attach", channel_id=CHANNEL)

    def test_pending_job_reserves_installation(self):
        self.prepare()
        with self.assertRaises(RuntimeError):
            self.prepare()
        with self.assertRaises(RuntimeError):
            dispatch(parser().parse_args(["login"]), self.config)

    def test_unresolved_retains_id_and_cannot_cancel(self):
        job = self.prepare()["job_id"]
        checkpoint(self.config, job, "attach", channel_id=CHANNEL)
        checkpoint(self.config, job, "checkpoint", video_id="abcdefghijk")
        checkpoint(self.config, job, "unresolved", reason="Lost browser")
        with self.assertRaises(ValueError):
            checkpoint(self.config, job, "cancel")
        duplicate = prepare(self.config, self.video, CHANNEL)
        self.assertFalse(duplicate["ok"])
        self.assertEqual(duplicate["record"]["video_id"], "abcdefghijk")

    def test_completion_requires_matching_metadata_id_and_evidence(self):
        job = self.prepare(visibility="private")["job_id"]
        checkpoint(self.config, job, "attach", channel_id=CHANNEL)
        checkpoint(self.config, job, "checkpoint", video_id="abcdefghijk")
        values = dict(channel_id=CHANNEL, video_id="abcdefghijk", visibility="private",
                      made_for_kids=False, confirmation="Saved")
        for override in ({"visibility": "public"}, {"made_for_kids": True},
                         {"confirmation": ""}, {"video_id": "12345678901"}):
            with self.assertRaises(ValueError):
                checkpoint(self.config, job, "complete", **{**values, **override})
        self.assertEqual(active_job(self.config)["status"], "submitted")

    def test_noninteractive_auth_never_launches_firefox(self):
        self.config.non_interactive = True
        with patch("src.auth.LoginWindow") as window, self.assertRaises(LoginRequired):
            Auth(self.config).ensure()
        window.assert_not_called()

    def test_noninteractive_cli_returns_json_and_code_two(self):
        output = io.StringIO()
        with patch("src.cli.Config", return_value=self.config), patch.object(self.config, "prepare"), \
             contextlib.redirect_stdout(output):
            code = main(["--json", "--non-interactive", "login"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "login_required")

    def test_noninteractive_download_returns_code_two_without_provider(self):
        output = io.StringIO()
        with patch("src.cli.Config", return_value=self.config), patch.object(self.config, "prepare"), \
             patch("src.download.provider_options") as provider, contextlib.redirect_stdout(output):
            code = main(["--json", "--non-interactive", "download", "https://youtu.be/abcdefghijk"])
        self.assertEqual(code, 2)
        provider.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["results"][0]["status"], "login_required")

    def test_login_observation_requires_new_studio_history_and_unexpired_cookie(self):
        profile = self.config.path("work/profile")
        profile.mkdir()
        window = LoginWindow.__new__(LoginWindow)
        window.profile = profile
        window.started = time.time()
        window.process = MagicMock()
        window.process.poll.return_value = None
        with contextlib.closing(sqlite3.connect(profile / "cookies.sqlite")) as db, db:
            db.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, expiry INTEGER)")
            db.execute("INSERT INTO moz_cookies VALUES (?,?,?)", (".youtube.com", "SAPISID", int(time.time()) + 1000))
        with contextlib.closing(sqlite3.connect(profile / "places.sqlite")) as db, db:
            db.execute("CREATE TABLE moz_places (url TEXT, last_visit_date INTEGER)")
            db.execute("INSERT INTO moz_places VALUES (?,?)", ("https://studio.youtube.com/channel/" + CHANNEL,
                       int((window.started - 100) * 1_000_000)))
        self.assertIsNone(window.channel())
        with contextlib.closing(sqlite3.connect(profile / "places.sqlite")) as db, db:
            db.execute("UPDATE moz_places SET last_visit_date=?", (int((window.started + 1) * 1_000_000),))
        self.assertEqual(window.channel(), CHANNEL)
        with contextlib.closing(sqlite3.connect(profile / "cookies.sqlite")) as db, db:
            db.execute("UPDATE moz_cookies SET expiry=1")
        self.assertIsNone(window.channel())


if __name__ == "__main__":
    unittest.main()
