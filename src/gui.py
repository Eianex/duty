"""Minimal desktop interface. Launch with DUTY's portable Python after setup."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.pycache_prefix = str(ROOT / "local/cache/pycache")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

python_name = (
    "pythonw.exe"
    if Path(sys.executable).name.lower() == "pythonw.exe"
    else "python.exe"
)
portable = ROOT / "local/runtime/python" / python_name
if (
    __name__ == "__main__"
    and portable.is_file()
    and Path(sys.executable).resolve() != portable.resolve()
):
    import subprocess

    raise SystemExit(
        subprocess.call([str(portable), str(Path(__file__).resolve()), *sys.argv[1:]])
    )

try:
    from PySide6.QtCore import QThread, Signal, QTimer, QUrl, Qt
    from PySide6.QtGui import QDesktopServices, QIcon
    from PySide6.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QButtonGroup,
        QComboBox,
        QTabWidget,
        QFormLayout,
        QPlainTextEdit,
        QCheckBox,
        QGroupBox,
        QFileDialog,
        QProgressBar,
        QMessageBox,
    )
except ImportError:
    raise SystemExit(
        "DUTY's GUI dependency is missing. Run python src/main.py setup with an installed Python 3.11, "
        "then local/runtime/python/python.exe src/gui.py."
    )

from src.core import (
    Config,
    Result,
    Cancelled,
    now,
    error_code,
    operation_context,
    status,
)


class Worker(QThread):
    event = Signal(dict)
    outcome = Signal(dict)

    def __init__(self, config, action, values):
        super().__init__()
        self.config, self.action, self.values = config, action, values

    def run(self):
        self.config.on_event = self.event.emit
        try:
            with operation_context(self.config):
                with self.config.path("logs/gui.log").open(
                    "a", encoding="utf-8"
                ) as log, redirect_stdout(log), redirect_stderr(log):
                    if self.action == "check":
                        result = status(self.config, online=False)
                    elif self.action == "login":
                        result = status(self.config, online=True, refresh=True)
                    elif self.action == "download":
                        from src.download import download_video

                        result = download_video(config=self.config, **self.values)
                    elif self.action == "convert":
                        from src.download import convert_video

                        result = convert_video(config=self.config, **self.values)
                    else:
                        from src.upload import run_single_upload

                        result = run_single_upload(config=self.config, **self.values)
            if isinstance(result, Result):
                result = {**result.to_dict(), "ok": result.status == "completed"}
            self.outcome.emit(result)
        except Exception as exc:
            self.outcome.emit(
                {
                    "ok": False,
                    "status": "cancelled" if isinstance(exc, Cancelled) else "failed",
                    "error": str(exc),
                    "error_code": error_code(exc),
                }
            )
        finally:
            self.config.on_event = None


class Window(QMainWindow):
    def __init__(self, config):
        super().__init__()
        self.config, self.worker = config, None
        self.ready = False
        self.convert_ready = False
        self.close_pending = False
        self.last_result = {}
        self.studio_completed = False
        self.setWindowTitle("DUTY · Download & Upload To Youtube")
        self.resize(700, 780)
        body = QWidget()
        self.setCentralWidget(body)
        layout = QVBoxLayout(body)
        heading = QLabel("DUTY")
        heading.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(heading)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.download_page(), "Download")
        self.tabs.addTab(self.upload_page(), "Upload")
        self.tabs.addTab(self.convert_page(), "Convert")
        self.tabs.currentChanged.connect(self.change_tab)
        layout.addWidget(self.tabs)

        transfer_actions = QHBoxLayout()
        self.go = QPushButton("Download")
        self.go.setEnabled(False)
        self.go.clicked.connect(self.transfer)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        transfer_actions.addWidget(self.go)
        transfer_actions.addWidget(self.cancel_button)
        layout.addLayout(transfer_actions)
        result_box = QGroupBox("Result")
        result_layout = QVBoxLayout(result_box)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        result_layout.addWidget(self.progress)
        self.message = QLabel("Checking your setup…")
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result_layout.addWidget(self.message)
        self.open_button = QPushButton("Open result")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_result)
        result_layout.addWidget(self.open_button)
        layout.addWidget(result_box)

        self.health = {}
        installed = [
            ("python", "Python"),
            ("firefox", "Firefox"),
            ("geckodriver", "GeckoDriver"),
            ("deno", "Deno"),
            ("ffmpeg", "FFmpeg"),
            ("ffprobe", "ffprobe"),
            ("dependencies", "Python packages"),
            ("provider", "yt-dlp / provider"),
        ]
        connection = [
            ("account", "Account / channel"),
            ("cookies", "Cookies"),
            ("youtube", "Transfer error"),
            ("studio", "YouTube Studio"),
        ]
        self.installed_keys = {key for key, _ in installed}
        health_box = QGroupBox("Connection and tools")
        health_layout = QHBoxLayout(health_box)
        for heading_text, entries in (
            ("Installed", installed),
            ("Connection", connection),
        ):
            column = QVBoxLayout()
            label = QLabel(heading_text)
            label.setStyleSheet("font-weight: 600;")
            column.addWidget(label)
            for key, name in entries:
                label = QLabel(f"○ {name}")
                label.setWordWrap(True)
                self.health[key] = (label, name)
                column.addWidget(label)
                if key in {"studio", "youtube"}:
                    label.hide()
            column.addStretch()
            health_layout.addLayout(column, 1)
        layout.addWidget(health_box)
        actions = QHBoxLayout()
        self.check_button = QPushButton("Check status")
        self.login_button = QPushButton("Refresh login")
        self.check_button.clicked.connect(lambda: self.start("check"))
        self.login_button.clicked.connect(lambda: self.start("login"))
        actions.addWidget(self.check_button)
        actions.addWidget(self.login_button)
        layout.addLayout(actions)
        QTimer.singleShot(0, lambda: self.start("check"))

    def change_tab(self, index):
        self.go.setText(self.tabs.tabText(index))
        self.go.setEnabled(self.worker is None and (self.convert_ready if index == 2 else self.ready))
        self.health["studio"][0].setVisible(index == 1 and self.studio_completed)

    def picker(self, field, folder=False, file_filter="Videos (*.mp4 *.mkv)"):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(field)
        button = QPushButton("Browse…")

        def choose():
            value = (
                QFileDialog.getExistingDirectory(self, "Destination", field.text())
                if folder
                else QFileDialog.getOpenFileName(
                    self, "Video", field.text(), file_filter
                )[0]
            )
            if value:
                field.setText(value)

        button.clicked.connect(choose)
        row.addWidget(button)
        return widget

    def download_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a YouTube video URL")
        self.destination = QLineEdit(str(self.config.path("downloads")))
        form.addRow("YouTube URL", self.url)
        form.addRow("Save to", self.picker(self.destination, folder=True))
        format_buttons = QWidget()
        format_layout = QHBoxLayout(format_buttons)
        format_layout.setContentsMargins(0, 0, 0, 0)
        self.download_formats = QButtonGroup(self)
        self.download_formats.setExclusive(True)
        self.mp4_button = QPushButton("MP4")
        self.mp3_button = QPushButton("MP3")
        self.mp4_button.setToolTip("Best available video and audio, merged without re-encoding")
        self.mp3_button.setToolTip("Best available audio, converted to MP3")
        for button, title, subtitle in (
            (self.mp4_button, "MP4", "Video+Audio"),
            (self.mp3_button, "MP3", "Audio"),
        ):
            button.setText("")
            button.setAccessibleName(f"{title}: {subtitle}")
            button.setCheckable(True)
            button.setStyleSheet("QPushButton:checked { background-color: palette(highlight); color: palette(highlighted-text); font-weight: bold; }")
            button.setMinimumHeight(64)
            content = QLabel(f"<b>{title}</b><br><small>{subtitle}</small>")
            content.setAlignment(Qt.AlignCenter)
            content.setAttribute(Qt.WA_TransparentForMouseEvents)
            content.setStyleSheet("color: palette(button-text); background: transparent; font-weight: normal;")
            button.toggled.connect(lambda checked, label=content: label.setStyleSheet(
                "background: transparent; font-weight: normal; color: palette("
                + ("highlighted-text" if checked else "button-text") + ");"
            ))
            button_layout = QVBoxLayout(button)
            button_layout.setContentsMargins(8, 8, 8, 8)
            button_layout.addWidget(content)
            self.download_formats.addButton(button)
            format_layout.addWidget(button)
        self.mp4_button.setChecked(True)
        form.addRow("Format", format_buttons)
        advanced = QGroupBox("Optional quality settings")
        advanced.setCheckable(True)
        advanced.setChecked(False)
        inner = QWidget()
        options = QFormLayout(inner)
        self.quality = QComboBox()
        self.quality.addItems(["Best available", "Exactly 1080p", "Exactly 4K"])
        options.addRow("Quality", self.quality)
        box = QVBoxLayout(advanced)
        box.addWidget(inner)
        inner.hide()
        advanced.toggled.connect(inner.setVisible)
        form.addRow(advanced)

        self.mp4_button.toggled.connect(advanced.setVisible)
        return page

    def upload_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.video = QLineEdit()
        self.title = QLineEdit()
        self.title.setPlaceholderText("Use filename if empty")
        self.title.setMaxLength(100)
        self.description = QPlainTextEdit()
        self.description.setMaximumHeight(85)
        self.visibility = QComboBox()
        self.visibility.addItems(["public", "unlisted", "private"])
        self.visibility.setCurrentText(self.config.values["upload"]["visibility"])
        self.audience = QCheckBox("Made for kids")
        self.audience.setChecked(self.config.values["upload"]["made_for_kids"])
        form.addRow("Video", self.picker(self.video))
        form.addRow("Title", self.title)
        form.addRow("Description", self.description)
        form.addRow("Visibility", self.visibility)
        form.addRow(self.audience)
        self.visibility_note = QLabel()

        def visibility_note():
            self.visibility_note.setText(
                "This upload will be " + self.visibility.currentText() + "."
            )

        self.visibility.currentTextChanged.connect(visibility_note)
        visibility_note()
        form.addRow(self.visibility_note)
        return page

    def convert_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.convert_file = QLineEdit()
        self.convert_file.setPlaceholderText("Choose a local MP4 video")
        form.addRow("MP4 video", self.picker(self.convert_file, file_filter="MP4 videos (*.mp4)"))
        note = QLabel("Saves an MP3 beside the original MP4 with the same filename. The MP4 stays untouched.")
        note.setWordWrap(True)
        form.addRow(note)
        return page

    def transfer(self):
        if self.tabs.currentIndex() == 0:
            if not self.url.text().strip():
                self.message.setText("Paste a YouTube URL first.")
                return
            values = {
                "url": self.url.text().strip(),
                "download_path": self.destination.text().strip() or None,
                "container": "mp4" if self.mp4_button.isChecked() else "mp3",
                "exact_1080_only": self.mp4_button.isChecked() and self.quality.currentIndex() == 1,
                "exact_4k_only": self.mp4_button.isChecked() and self.quality.currentIndex() == 2,
            }
            self.start("download", values)
        elif self.tabs.currentIndex() == 1:
            if not self.video.text().strip():
                self.message.setText("Choose a video file first.")
                return
            self.start(
                "upload",
                {
                    "video_path": self.video.text().strip(),
                    "title": self.title.text(),
                    "description": self.description.toPlainText(),
                    "visibility": self.visibility.currentText(),
                    "made_for_kids": self.audience.isChecked(),
                },
            )
        else:
            if not self.convert_file.text().strip():
                self.message.setText("Choose an MP4 file first.")
                return
            self.start("convert", {"source": self.convert_file.text().strip()})

    def start(self, action, values=None):
        if self.worker is not None:
            return
        self.config.cancel_event.clear()
        self.last_action = action
        self.studio_completed = False
        for key in ("studio", "youtube"):
            self.health[key][0].hide()
        self.tabs.setEnabled(False)
        for widget in (self.go, self.check_button, self.login_button):
            widget.setEnabled(False)
        self.open_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, 0)
        self.message.setText(
            "Checking setup…" if action == "check"
            else "Refreshing login…" if action == "login"
            else f"Starting {action}…"
        )
        self.worker = Worker(self.config, action, values or {})
        self.worker.event.connect(self.on_event)
        self.worker.outcome.connect(self.on_result)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def on_event(self, event):
        if event["kind"] == "health":
            label, name = self.health[event["name"]]
            state = event["state"]
            dark = self.palette().window().color().lightness() < 128
            colors = (
                {"green": "#79dda4", "orange": "#f4cb73", "red": "#ff918b"}
                if dark
                else {"green": "#237747", "orange": "#976300", "red": "#b3261e"}
            )
            symbol, color = {"green": "✓", "orange": "○", "red": "✕"}[state], colors[
                state
            ]
            text = f"{symbol} {name}"
            if event["name"] not in self.installed_keys or state == "red":
                text += ": " + event["message"]
            label.setText(text)
            label.setStyleSheet(f"color: {color};")
            label.setToolTip(event["message"] + "\nChecked: " + event["checked_at"])
            label.show()
        else:
            if event.get("total"):
                self.progress.setRange(0, 100)
                self.progress.setValue(
                    min(100, int(event.get("downloaded", 0) * 100 / event["total"]))
                )
            self.message.setText(event.get("message", "Working…"))

    def on_result(self, result):
        self.last_result = result
        if self.last_action in {"check", "login"}:
            if "checks" in result:
                checks = {row["name"]: row["state"] for row in result["checks"]}
                self.ready = all(checks.get(name) == "green" for name in self.installed_keys)
                self.convert_ready = all(checks.get(name) == "green" for name in ("ffmpeg", "ffprobe"))
            if result.get("error"):
                message = result["error"]
            elif result.get("ok"):
                message = "Ready."
            elif self.ready:
                message = "Tools ready. YouTube login will open when needed."
            elif self.convert_ready:
                message = "Convert is ready. Other tools need attention; see the status below."
            else:
                message = "Setup needs attention. See the status below; run setup for missing components."
            self.message.setText(message)
        else:
            outcome = result.get("status", "failed")
            message = result.get("error") or outcome.capitalize()
            if outcome == "unresolved":
                message = (
                    "Outcome uncertain. Check this video in Studio before retrying. "
                    + message
                )
            if result.get("job_id"):
                message += "\nJob: " + result["job_id"]
            if result.get("youtube_url"):
                message += "\n" + result["youtube_url"]
            if self.last_action == "convert" and result.get("local_path"):
                message += "\nSaved: " + result["local_path"]
            if result.get("warnings"):
                message += "\n" + "\n".join(result["warnings"])
            self.message.setText(message)
            reused = result.get("stage") in {"cached_output", "duplicate"}
            if self.last_action in {"download", "upload"} and not result.get("ok") and outcome != "cancelled":
                self.on_event(
                    {
                        "kind": "health",
                        "name": "youtube",
                        "state": "orange" if outcome == "unresolved" else "red",
                        "message": message.split("\n")[0],
                        "checked_at": now(),
                    }
                )
            if self.last_action == "upload" and result.get("ok") and not reused:
                self.studio_completed = True
                self.on_event(
                    {
                        "kind": "health",
                        "name": "studio",
                        "state": "green",
                        "message": "Configured channel verified; upload completed",
                        "checked_at": now(),
                    }
                )
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if result.get("ok") else 0)

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.tabs.setEnabled(True)
        for widget in (self.check_button, self.login_button):
            widget.setEnabled(True)
        self.go.setEnabled(self.convert_ready if self.tabs.currentIndex() == 2 else self.ready)
        self.cancel_button.setEnabled(False)
        self.open_button.setEnabled(
            bool(
                self.last_result.get("youtube_url")
                or self.last_result.get("local_path")
            )
        )
        if self.close_pending:
            self.close()

    def cancel(self):
        self.config.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.message.setText("Cancelling safely…" +
                             (" an attached upload will not be submitted again."
                              if self.last_action == "upload" else ""))

    def open_result(self):
        if self.last_result.get("youtube_url") and self.last_action == "upload":
            QDesktopServices.openUrl(QUrl(self.last_result["youtube_url"]))
        elif self.last_result.get("local_path"):
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(Path(self.last_result["local_path"]).parent))
            )

    def closeEvent(self, event):
        if self.worker is not None:
            self.close_pending = True
            self.cancel()
            event.ignore()
        else:
            event.accept()


def main():
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DUTY.Desktop")
    app = QApplication(sys.argv)
    app.setApplicationName("DUTY")
    app.setWindowIcon(QIcon(str(ROOT / "img/logo.png")))
    try:
        window = Window(Config())
    except Exception as exc:
        QMessageBox.critical(None, "DUTY", str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
