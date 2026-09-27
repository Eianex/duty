"""DUTY-managed Firefox sessions and saved authentication generations."""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import time
import ctypes
import threading
import http.cookiejar
import json
import tempfile
import uuid
import warnings
from pathlib import Path
from functools import partial
from types import SimpleNamespace
from datetime import datetime, timezone
from contextlib import closing, contextmanager
from ctypes import wintypes
from urllib.parse import urlparse

from selenium import webdriver
from selenium.webdriver.common import service as selenium_service
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium_firefox.firefox import Firefox

from .core import AuthenticationError, ChannelMismatch, LoginRequired, atomic_json, now, bind_channel, bound_channel


class LoginWindow:
    """Launch ordinary Firefox for interactive Google login, without WebDriver.

    Observe only this dedicated profile's new Studio history and cookie names.
    After normal browser shutdown the saved session is verified through Studio.
    """
    def __init__(self, config, profile):
        self.config, self.profile = config, profile
        self.started = time.time()
        profile.mkdir(parents=True, exist_ok=True)
        # Start login with ordinary browser preferences, not Selenium's user.js.
        (profile / "user.js").write_text(
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.aboutwelcome.enabled", false);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
            'user_pref("browser.tabs.warnOnClose", false);\n'
            'user_pref("browser.warnOnQuitShortcut", false);\n'
            'user_pref("signon.rememberSignons", false);\n', encoding="utf-8")
        self.process = subprocess.Popen([str(config.tool("firefox")), "-wait-for-browser", "-no-remote", "-profile",
                                         str(profile), "https://studio.youtube.com/"],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def channel(self):
        if self.process.poll() is not None:
            raise AuthenticationError("The login window was closed before a Studio session was detected. Run login again.")
        database = self.profile / "places.sqlite"
        cookies = self.profile / "cookies.sqlite"
        if not database.is_file() or not cookies.is_file():
            return None
        try:
            with closing(sqlite3.connect(cookies.as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                row = db.execute("SELECT 1 FROM moz_cookies WHERE host IN ('.youtube.com','youtube.com') "
                                 "AND name IN ('SAPISID','__Secure-3PAPISID','__Secure-1PAPISID') "
                                 "AND expiry > ? LIMIT 1", (int(time.time()),)).fetchone()
            if not row:
                return None
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=.2)) as db:
                rows = db.execute("SELECT url FROM moz_places WHERE url LIKE 'https://studio.youtube.com/channel/%' "
                                  "AND last_visit_date >= ? ORDER BY last_visit_date DESC LIMIT 5",
                                  (int(self.started * 1_000_000),)).fetchall()
            for (url,) in rows:
                match = re.match(r"https://studio\.youtube\.com/channel/(UC[\w-]{22})(?:/|$)", url)
                if match:
                    return match.group(1)
        except sqlite3.Error:
            return None  # Browser initialization or a short SQLite writer lock.
        return None

    def close(self):
        if self.process.poll() is not None:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        owned = self._process_tree()
        def close_owned(hwnd, _):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in owned:
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: flush profile normally.
            return True
        user32.EnumWindows(callback_type(close_owned), 0)
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            raise AuthenticationError("Close DUTY's login window so its session can be saved") from exc

    def _process_tree(self):
        # Firefox's Windows launcher owns the browser child; both belong to this login.
        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.CreateToolhelp32Snapshot(2, 0)
        if handle == wintypes.HANDLE(-1).value:
            raise OSError("Cannot inspect DUTY browser process tree")
        parents = {}
        try:
            entry = Entry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(handle, ctypes.byref(entry))
            while more:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                more = kernel.Process32NextW(handle, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(handle)
        owned = {self.process.pid}
        while True:
            children = {pid for pid, parent in parents.items() if parent in owned}
            if children <= owned:
                return owned
            owned.update(children)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class ManagedFirefox(Firefox):
    # The upstream destructor assumes Selenium owns a throwaway profile.
    # DUTY explicitly closes its session and retains the on-disk working profile.
    def __del__(self):
        pass


class BrowserSession:
    def __init__(self, config, profile, *, headless=False):
        self.config, self.profile = config, profile
        self.closed = False
        self.shutdown_warning = None
        self._close_deadline = None
        self._close_timer = None
        self._browser_handle = None
        self._shutdown_lock = threading.Lock()
        profile.mkdir(parents=True, exist_ok=True)
        for name in ("firefox", "geckodriver"):
            if not config.tool(name).is_file():
                raise FileNotFoundError(f"Missing bundled {name}; run python src/main.py setup")

        def driver_factory(**kwargs):
            # selenium-firefox prepares preferences in a cloned profile. Keep those
            # preferences, but launch using -profile so Selenium won't delete our data.
            prepared = kwargs.pop("firefox_profile")
            try:
                shutil.copy2(str(prepared.path) + "/user.js", profile / "user.js")
            finally:
                # The wrapper's cloned profile is not used by GeckoDriver.
                clone = type(profile)(prepared.tempfolder or prepared.path).resolve()
                if clone.parent == config.path("work").resolve() and clone != profile.resolve().parent:
                    shutil.rmtree(clone)
            options = Options()
            options.binary_location = str(config.tool("firefox"))
            options.add_argument("-no-remote")
            options.add_argument("-profile")
            options.add_argument(str(profile))
            if headless:
                options.add_argument("-headless")
            # Selenium 3 has no service creation-flags option. Adapt only its
            # subprocess reference during startup; ordinary Firefox stays visible.
            original_subprocess = selenium_service.subprocess
            service_processes = SimpleNamespace(**vars(original_subprocess))
            service_processes.Popen = partial(subprocess.Popen,
                                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            selenium_service.subprocess = service_processes
            try:
                driver = webdriver.Firefox(
                    executable_path=str(config.tool("geckodriver")), options=options,
                    service_log_path=str(config.path("logs", "geckodriver.log")),
                    service_args=["--profile-root", str(config.path("work"))],
                )
            finally:
                selenium_service.subprocess = original_subprocess
            try:
                driver.set_page_load_timeout(config.timeout("element"))
                driver.command_executor.set_timeout(config.timeout("element"))
                actual = driver.capabilities.get("moz:profile")
                if not actual or profile.resolve() != type(profile)(actual).resolve():
                    raise RuntimeError("Firefox did not use DUTY's managed profile")
            except BaseException:
                driver.quit()
                raise
            return driver

        self.browser = ManagedFirefox(
            profile_path=str(profile), geckodriver_path=str(config.tool("geckodriver")),
            firefox_binary_path=str(config.tool("firefox")),
            cookies_folder_path=str(profile.parent / "wrapper"),
            pickle_cookies=False, full_screen=False, headless=headless,
            webdriver_class=driver_factory,
        )
        self.driver = self.browser.driver

    def close_after_save(self, seconds=5):
        """Start a hard, session-scoped deadline before submitting Studio's Save."""
        self._close_deadline = time.monotonic() + seconds
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        pid = self.driver.capabilities.get("moz:processID")
        self._browser_handle = kernel.OpenProcess(0x00100001, False, pid) if pid else None
        if not self._browser_handle:
            raise RuntimeError("Cannot establish the bounded shutdown for DUTY's Firefox process")
        self._kernel = kernel
        # Reserve half a second for Windows to finish process teardown.
        self._close_timer = threading.Timer(max(0, seconds - .5), self._stop_upload_browser)
        self._close_timer.daemon = True
        self._close_timer.start()

    def _stop_upload_browser(self):
        # The process handle identifies only this session even if Windows reuses a PID.
        with self._shutdown_lock:
            if self._browser_handle and self._kernel.WaitForSingleObject(self._browser_handle, 0) == 258:
                self._kernel.TerminateProcess(self._browser_handle, 1)
                self.shutdown_warning = "Firefox required bounded shutdown; previous saved authentication retained"

    def _close_upload(self):
        finished = threading.Event()
        def quit_driver():
            try:
                self.driver.quit()
            except Exception:
                pass
            finally:
                finished.set()
        thread = threading.Thread(target=quit_driver, daemon=True)
        thread.start()
        finished.wait(max(0, self._close_deadline - time.monotonic() - .2))
        self._stop_upload_browser()
        service = self.driver.service.process
        if service and service.poll() is None:
            service.kill()
        if self._close_timer:
            self._close_timer.cancel()
        with self._shutdown_lock:
            if self._browser_handle:
                self._kernel.CloseHandle(self._browser_handle)
                self._browser_handle = None
        self.closed = True
        self.driver.command_executor.set_timeout(self.config.timeout("element"))
        # Session capture happens only after closure; never wait here on profile copying.

    def close(self):
        if not self.closed and self._close_deadline is not None:
            self._close_upload()
            return
        if not self.closed:
            self.driver.quit()
            self.closed = True
            # Firefox must release its profile before SQLite cookie export/copy.
            lock = self.profile / "parent.lock"
            deadline = time.monotonic() + 15
            while lock.exists():
                try:
                    lock.unlink()
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Firefox has not released its working profile")
                    time.sleep(.2)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def until(predicate, timeout: float, message: str, cancel=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel:
            cancel()
        try:
            value = predicate()
            if value:
                return value
        except (NoSuchElementException, StaleElementReferenceException):
            pass
        time.sleep(.3)
    raise TimeoutError(message)


def studio_channel(driver) -> str | None:
    url = urlparse(driver.current_url)
    if url.hostname != "studio.youtube.com":
        return None
    match = re.match(r"/channel/(UC[\w-]{22})(?:/|$)", url.path)
    if match and driver.find_elements(By.CSS_SELECTOR, "ytcp-app"):
        return match.group(1)
    return None


def verify_channel(session, expected: str, timeout: float):
    session.driver.get("https://studio.youtube.com/")

    def check():
        channel = studio_channel(session.driver)
        if channel and channel != expected:
            raise ChannelMismatch(f"Selected channel {channel}; DUTY expects {expected}. Run login --refresh and select the configured channel.")
        if urlparse(session.driver.current_url).hostname == "accounts.google.com":
            raise AuthenticationError("YouTube Studio requires login")
        return channel

    # A slow/unavailable Studio page is not evidence of an expired login.
    return until(check, timeout, "Could not verify the configured Studio channel", session.config.check_cancel)


# Saved authentication generations and disposable working copies.

IGNORE = shutil.ignore_patterns("parent.lock", "lock", ".parentlock", "cache2", "startupCache", "*.pkl")
AUTH_COOKIES = {"SAPISID", "__Secure-3PAPISID", "__Secure-1PAPISID", "SID"}


class Auth:
    def __init__(self, config):
        self.config = config
        self.folder = config.path("permanent", "auth")
        self.warnings = []

    def current(self):
        pointer = self.folder / "current.json"
        if not pointer.is_file():
            return None
        generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
        if not isinstance(generation, str) or not generation.isalnum():
            raise AuthenticationError("Invalid saved authentication generation")
        path = self.folder / "generations" / generation
        metadata = json.loads((path / "session.json").read_text(encoding="utf-8"))
        expected = bound_channel(self.config)
        if expected and metadata.get("channel_id") != expected:
            raise ChannelMismatch("Saved authentication does not match this installation's configured channel")
        return path, metadata

    def stale(self, current=None):
        current = current or self.current()
        if not current:
            return True
        path, metadata = current
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(metadata["captured_at"])).total_seconds()
        if age > self.config.values["auth"]["max_age_hours"] * 3600:
            return True
        try:
            jar = http.cookiejar.MozillaCookieJar(str(path / "cookies.txt"))
            jar.load(ignore_discard=True, ignore_expires=True)
            return not any(c.name in AUTH_COOKIES and not c.is_expired() for c in jar)
        except (OSError, http.cookiejar.LoadError):
            return True

    def working_profile(self):
        current = self.current()
        base = Path(tempfile.mkdtemp(prefix="browser_", dir=self.config.path("work")))
        profile = base / "profile"
        try:
            if current:
                shutil.copytree(current[0] / "profile", profile, ignore=IGNORE)
            else:
                profile.mkdir()
        except BaseException:
            self.cleanup(profile)
            raise
        return profile

    def capture(self, profile: Path, channel_id: str):
        from yt_dlp.cookies import extract_cookies_from_browser
        expected = bound_channel(self.config)
        if expected and expected != channel_id:
            raise ChannelMismatch(f"Selected {channel_id}; this installation is bound to {expected}")
        current = self.current()
        if current and current[1]["channel_id"] != channel_id:
            raise ChannelMismatch("Authentication refresh selected a different channel")
        jar = extract_cookies_from_browser("firefox", str(profile))
        cookies = [c for c in jar if c.domain.lstrip(".") in {"youtube.com", "google.com", "accounts.google.com"}]
        if not any(c.name in AUTH_COOKIES and not c.is_expired() for c in cookies):
            raise AuthenticationError("Firefox did not provide usable signed-in cookies; saved authentication was retained")
        generation = uuid.uuid4().hex
        path = self.folder / "generations" / generation
        path.mkdir(parents=True)
        try:
            shutil.copytree(profile, path / "profile", ignore=IGNORE)
            saved = http.cookiejar.MozillaCookieJar(str(path / "cookies.txt"))
            for cookie in cookies:
                saved.set_cookie(cookie)
            saved.save(ignore_discard=True, ignore_expires=True)
            atomic_json(path / "session.json", {"channel_id": channel_id, "captured_at": now()})
            bind_channel(self.config, channel_id)
            atomic_json(self.folder / "current.json", {"generation": generation})
        except BaseException:
            # Only discard this unpublished generation, never the active one.
            pointer = self.folder / "current.json"
            active = json.loads(pointer.read_text()).get("generation") if pointer.exists() else None
            if active != generation:
                shutil.rmtree(path)
            raise
        return path

    def login(self, *, refresh=False):
        current = self.current()
        if not refresh and current and not self.stale(current):
            return current
        if getattr(self.config, "non_interactive", False):
            raise LoginRequired("DUTY authentication is required. Run python src/main.py login and sign in to the standalone browser, then retry this command.")
        expected = current[1]["channel_id"] if current else bound_channel(self.config)
        profile = self.working_profile()
        self.config.emit("message", message="Complete Google login and channel selection in DUTY's Firefox window.")
        print("Sign in to YouTube in DUTY's Firefox window. Complete 2FA and select your channel. DUTY saves the session automatically.", flush=True)
        try:
            with LoginWindow(self.config, profile) as window:
                try:
                    channel = until(window.channel, self.config.timeout("login"), "Login timed out; saved authentication was retained", self.config.check_cancel)
                except TimeoutError as exc:
                    raise AuthenticationError(str(exc)) from exc
            if expected and channel != expected:
                raise ChannelMismatch(f"Selected {channel}, expected {expected}. Select the configured channel and retry login --refresh.")
            self.capture(profile, channel)
        finally:
            self.cleanup(profile)
        print(f"Saved DUTY session for channel {channel}.", flush=True)
        return self.current()

    def ensure(self):
        return self.login()

    def clean_working(self, profile):
        base = profile.resolve().parent
        if base.exists() and base.parent == self.config.path("work").resolve() and base.name.startswith("browser_"):
            shutil.rmtree(base)

    def cleanup(self, profile):
        try:
            self.clean_working(profile)
        except OSError as exc:
            message = f"Could not clean working browser profile: {exc}"
            self.warnings.append(message)
            warnings.warn(message, RuntimeWarning)

    @contextmanager
    def cookie_copy(self):
        current = self.current()
        if not current:
            raise AuthenticationError("No saved authentication. Run login.")
        temporary = tempfile.NamedTemporaryFile(prefix="youtube_cookies_", suffix=".txt", delete=False,
                                                dir=self.config.path("work"))
        path = Path(temporary.name)
        temporary.close()
        try:
            shutil.copyfile(current[0] / "cookies.txt", path)
            yield path
        finally:
            path.unlink(missing_ok=True)


def authentication_rejected(message: str) -> bool:
    lower = message.lower()
    # Bot/403 errors alone are not proof that a user's login has expired.
    return any(fragment in lower for fragment in (
        "cookies are no longer valid", "cookies have expired", "login required",
        "sign in to confirm your age", "this video is private. sign in",
    ))
