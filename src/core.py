"""Shared paths, configuration, setup and operation lifecycle for DUTY."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import hashlib
import importlib.metadata
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import tomllib
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL_NAMES = {"runtime", "permanent", "work", "downloads", "history", "logs", "cache", "dist", "provider"}
DEFAULTS = {
    "upload": {"visibility": "public", "made_for_kids": False, "headless": False},
    "timeouts": {"login": 600, "element": 45, "upload": 7200},
    "auth": {"max_age_hours": 168},
}


class Cancelled(Exception):
    """Cooperative cancellation; upload code journals attachment before propagating."""


class BusyError(RuntimeError):
    pass


class Config:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.non_interactive = False
        self.cancel_event = threading.Event()
        self.on_event = None
        self.values = {key: dict(value) for key, value in DEFAULTS.items()}
        path = self.root / "settings.toml"
        if path.is_file():
            with path.open("rb") as stream:
                for section, values in tomllib.load(stream).items():
                    if section not in self.values or not isinstance(values, dict):
                        raise ValueError(f"Unknown settings section: {section}")
                    if set(values) - set(self.values[section]):
                        raise ValueError(f"Unknown settings in {section}")
                    self.values[section].update(values)
        if self.values["upload"]["visibility"] not in {"public", "private", "unlisted"}:
            raise ValueError("upload.visibility must be public, private or unlisted")
        for key in ("made_for_kids", "headless"):
            if not isinstance(self.values["upload"][key], bool):
                raise ValueError(f"upload.{key} must be a boolean")
        for section in ("timeouts", "auth"):
            for key, value in self.values[section].items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                    raise ValueError(f"{section}.{key} must be a positive number")

    def path(self, *parts):
        relative = Path(*parts)
        base = self.root / "local" if relative.parts and relative.parts[0] in LOCAL_NAMES else self.root
        return base / relative

    def tool(self, name):
        filename = {"python": "python.exe", "firefox": "firefox.exe", "geckodriver": "geckodriver.exe",
                    "deno": "deno.exe", "ffmpeg": "ffmpeg.exe", "ffprobe": "ffprobe.exe"}[name]
        return self.path("runtime", "ffmpeg" if name == "ffprobe" else name, filename)

    def prepare(self):
        for name in ("permanent/auth/generations", "work", "downloads", "history", "logs", "cache"):
            self.path(name).mkdir(parents=True, exist_ok=True)
        os.environ["TEMP"] = os.environ["TMP"] = str(self.path("work"))
        tempfile.tempdir = str(self.path("work"))
        os.environ["DENO_DIR"] = str(self.path("cache", "deno"))
        os.environ["XDG_CACHE_HOME"] = str(self.path("cache"))
        os.environ["DENO_NO_UPDATE_CHECK"] = os.environ["DENO_NO_PROMPT"] = "1"
        os.environ["PYTHONPYCACHEPREFIX"] = str(self.path("cache", "pycache"))
        sys.pycache_prefix = str(self.path("cache", "pycache"))

    def timeout(self, name):
        return float(self.values["timeouts"][name])

    def check_cancel(self):
        if self.cancel_event.is_set():
            raise Cancelled("Operation cancelled")

    def emit(self, kind, **values):
        if self.on_event:
            self.on_event({"kind": kind, **values})


@contextmanager
def installation_lock(config):
    """A stdlib lock works even before third-party requirements are installed."""
    path = config.root / "local" / "duty.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise BusyError("Another DUTY operation is running") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def migrate_layout(config):
    """Resume whole-directory moves, preserving all existing state and journals."""
    root = config.root
    pairs = [(root / name, root / "local" / name) for name in LOCAL_NAMES - {"provider"}]
    pairs += [(root / "vendor/bgutil/server/node_modules", config.path("provider/server/node_modules"))]
    marker = root / "local/migration.json"
    if marker.is_file() and not any(source.exists() for source, _ in pairs):
        return
    # Preflight every destination before moving anything in this invocation.
    for source, target in pairs:
        if source.exists() and target.exists():
            raise RuntimeError(f"Migration conflict: both {source} and {target} exist; neither was overwritten")
    for source, target in pairs:
        if source.exists():
            source.resolve().relative_to(root)
            target.resolve().relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
    # Runtime provider code is a local copy so native dependencies stay under local/.
    source = root / "vendor/bgutil/server"
    server = config.path("provider/server")
    if source.exists() and (server / "node_modules").exists() and not (server / "src").exists():
        shutil.copytree(source, server, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"))
    python_pth = config.path("runtime/python/python311._pth")
    if python_pth.exists():
        with python_pth.open(encoding="utf-8", newline="") as stream:
            content = stream.read()
        updated = content.replace("..\\..\\", "..\\..\\..\\") if "..\\..\\..\\" not in content else content
        if content != updated:
            with python_pth.open("w", encoding="utf-8", newline="") as stream:
                stream.write(updated)
    # Only recorded media paths are relocated. Browser databases are left intact.
    for path in config.path("history").glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        changed = False
        def relocate(value):
            nonlocal changed
            if isinstance(value, dict):
                for key, item in value.items():
                    if key == "local_path" and isinstance(item, str):
                        for name in ("downloads", "work"):
                            old = root / name
                            try:
                                relative = Path(item).relative_to(old)
                            except ValueError:
                                continue
                            value[key] = str(config.path(name) / relative)
                            changed = True
                            break
                    else:
                        relocate(item)
            elif isinstance(value, list):
                for item in value:
                    relocate(item)
        relocate(row)
        if changed:
            atomic_json(path, row)
    atomic_json(marker, {"layout": 2})


_active = ContextVar("duty_operation", default=None)
_environment_lock = threading.RLock()
_keys = ("TEMP", "TMP", "DENO_DIR", "XDG_CACHE_HOME", "DENO_NO_UPDATE_CHECK", "DENO_NO_PROMPT", "PYTHONPYCACHEPREFIX")


@contextmanager
def operation_context(config):
    if _active.get() is not None:
        if _active.get() != config.root:
            raise RuntimeError("Cannot nest operations from different installations")
        yield config
        return
    if not _environment_lock.acquire(blocking=False):
        raise BusyError("Another DUTY operation is running")
    previous = {key: os.environ.get(key) for key in _keys}
    previous_tempdir, previous_pycache = tempfile.tempdir, sys.pycache_prefix
    token = None
    try:
        with installation_lock(config):
            migrate_layout(config)
            config.prepare()
            token = _active.set(config.root)
            yield config
    finally:
        if token is not None:
            _active.reset(token)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        tempfile.tempdir, sys.pycache_prefix = previous_tempdir, previous_pycache
        _environment_lock.release()


def transfer(function):
    signature = inspect.signature(function)
    @wraps(function)
    def run(*args, **kwargs):
        arguments = signature.bind_partial(*args, **kwargs)
        config = arguments.arguments.get("config") or Config()
        arguments.arguments["config"] = config
        with operation_context(config):
            config.check_cancel()
            return function(*arguments.args, **arguments.kwargs)
    return run


def dependency_versions(config):
    missing = []
    for line in config.path("requirements.txt").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        name, version = line.strip().split("==", 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "missing"
        if actual != version:
            missing.append(f"{name}: {actual}; expected {version}")
    return missing


def status(config, *, online=False, refresh=False):
    """Check components, then saved authentication; Studio is checked only by uploads."""
    checks = []
    def report(name, state, message):
        row = {"name": name, "state": state, "message": message, "checked_at": now()}
        checks.append(row)
        config.emit("health", **row)
    for name in ("python", "firefox", "geckodriver", "deno", "ffmpeg", "ffprobe"):
        path = config.tool(name)
        report(name, "green" if path.is_file() else "red", "Installed" if path.is_file() else "Missing; run setup")
    missing = dependency_versions(config)
    report("dependencies", "red" if missing else "green", "; ".join(missing) if missing else "Pinned Python packages installed")
    try:
        from .download import provider_options
        options = provider_options(config)
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, **options}):
            from yt_dlp_plugins.extractor.getpot_bgutil_script import BgUtilScriptDenoPTP
        subprocess.run([str(config.tool("deno")), "run", "--no-lock", "--cached-only", "--node-modules-dir=manual",
                        "--allow-env", "--allow-read", "--allow-ffi",
                        str(config.path("provider/server/src/generate_once.ts")), "--version"],
                       check=True, capture_output=True, text=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        report("provider", "green", "yt-dlp, Deno and provider ready locally; no bot guarantee")
    except Exception as exc:
        report("provider", "red", str(exc))
    channel = bound_channel(config)
    report("account", "green" if channel else "orange", channel or "Sign-in required")
    if missing:
        report("cookies", "orange", "Not checked; run setup")
        return {"ok": False, "channel_id": channel, "checks": checks}
    from .session import Auth
    auth = Auth(config)
    try:
        saved = auth.current()
        stale = auth.stale(saved)
        report("cookies", "orange" if stale or refresh else "green",
               "Refresh needed" if stale or refresh else "Session saved; cookies within local expiry/age limits")
        components_ready = all(row["state"] != "red" for row in checks)
        if (online or refresh) and components_ready and (stale or refresh):
            config.check_cancel()
            saved = auth.login(refresh=True) if refresh else auth.ensure()
            channel = saved[1]["channel_id"]
            report("account", "green", channel)
            report("cookies", "green", "Session saved; cookies within local expiry/age limits")
    except Cancelled:
        raise
    except ChannelMismatch as exc:
        report("account", "red", str(exc))
        report("cookies", "orange", "Saved session does not match the configured channel")
    except Exception as exc:
        report("cookies", "red", str(exc))
    # Later entries supersede earlier checks for the same component.
    latest = {row["name"]: row for row in checks}
    return {"ok": all(row["state"] != "red" for row in latest.values()), "channel_id": channel, "checks": list(latest.values())}


def _run_setup(config, *command, **kwargs):
    config.check_cancel()
    config.emit("message", message="Installing required components…")
    subprocess.run(list(map(str, command)), check=True, **kwargs)


def setup(config):
    """Install missing local tools only; existing runtime components are reused."""
    if os.name != "nt" or sys.version_info[:2] < (3, 11) or sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("Initial setup requires Windows x64 Python 3.11+ with pip")
    sources = json.loads(config.path("vendor/runtime-sources.json").read_text())
    cache = config.path("cache/setup")
    cache.mkdir(parents=True, exist_ok=True)
    def archive(name):
        config.check_cancel()
        source = sources[name]
        path = cache / (name + (".exe" if name in {"firefox", "7zr"} else ".zip"))
        if not path.is_file():
            config.emit("message", message=f"Downloading {name}")
            partial = path.with_suffix(".partial")
            with urllib.request.urlopen(source["url"], timeout=120) as response, partial.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    config.check_cancel()
                    stream.write(chunk)
            partial.replace(path)
        if file_hash(path) != source["sha256"]:
            raise RuntimeError(f"Checksum mismatch for {name}; remove {path} and run setup again")
        return path
    for name in ("python", "firefox", "geckodriver", "deno", "ffmpeg"):
        if config.tool(name).is_file() and (name != "ffmpeg" or config.tool("ffprobe").is_file()):
            continue
        target = config.path("runtime", name)
        if target.exists():
            raise RuntimeError(f"Incomplete component at {target}; move it aside before setup")
        stage = Path(tempfile.mkdtemp(prefix=name + "_", dir=cache))
        payload = stage / "payload"
        payload.mkdir()
        if name == "firefox":
            _run_setup(config, archive("7zr"), "x", archive("firefox"), "-o" + str(stage / "extracted"), "-y", stdout=subprocess.DEVNULL)
            shutil.copytree(stage / "extracted/core", payload, dirs_exist_ok=True)
            policy = payload / "distribution/policies.json"
            policy.parent.mkdir(exist_ok=True)
            atomic_json(policy, {"policies": {"DisableAppUpdate": True, "DisableTelemetry": True, "DontCheckDefaultBrowser": True}})
        elif name == "ffmpeg":
            with zipfile.ZipFile(archive(name)) as z:
                for item in z.infolist():
                    if item.is_dir():
                        continue
                    relative = Path(*Path(item.filename).parts[1:])
                    destination = payload / (relative.name if relative.parts[0] == "bin" else relative)
                    destination.resolve().relative_to(payload.resolve())
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(item) as source, destination.open("wb") as out:
                        shutil.copyfileobj(source, out)
        else:
            with zipfile.ZipFile(archive(name)) as z:
                for item in z.infolist():
                    (payload / item.filename).resolve().relative_to(payload.resolve())
                z.extractall(payload)
        if name in {"geckodriver", "deno"}:
            shutil.copy2(archive(name + "_license"), payload / "LICENSE.txt")
        if not (payload / config.tool(name).name).is_file():
            raise RuntimeError(f"Downloaded {name} archive did not contain its executable")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload.rename(target)
    pth = config.path("runtime/python/python311._pth")
    with pth.open("w", encoding="utf-8", newline="") as stream:
        stream.write("python311.zip\n.\nLib/site-packages\n..\\..\\..\\\nimport site\n")
    python = config.tool("python")
    try:
        identity = subprocess.run([str(python), "-B", "-c",
            "import json,sys,sysconfig; print(json.dumps([sys.implementation.name,"
            "list(sys.version_info[:3]),sysconfig.get_platform()]))"],
            check=True, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if json.loads(identity.stdout) != ["cpython", [3, 11, 9], "win-amd64"]:
            raise ValueError("Expected CPython 3.11.9 Windows x64")
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError("Local Python must run as CPython 3.11.9 Windows x64. "
                           "Move the incompatible local/runtime/python folder aside and rerun setup.") from exc
    packages = config.path("runtime/python/Lib/site-packages")
    installed = {d.metadata["Name"].lower().replace("_", "-"): d.version for d in importlib.metadata.distributions(path=[str(packages)])}
    requirements = [line.strip() for line in config.path("requirements.txt").read_text().splitlines() if line.strip() and not line.startswith("#")]
    missing = [line for line in requirements if installed.get(line.split("==")[0].lower().replace("_", "-")) != line.split("==")[1]]
    if missing:
        # External pip runs in a separate process through the actual portable
        # interpreter, so wheel selection and dependency markers both use 3.11.9.
        if Path(sys.executable).resolve() in {python.resolve(), python.with_name("pythonw.exe").resolve()}:
            raise RuntimeError("Run dependency setup using external Windows x64 Python 3.11+ with pip; portable Python has no pip.")
        try:
            pip_version = importlib.metadata.version("pip")
        except importlib.metadata.PackageNotFoundError as exc:
            raise RuntimeError("The setup launcher needs pip. Use Windows x64 Python 3.11+ with pip.") from exc
        version = re.match(r"^(\d+)\.(\d+)", pip_version)
        if not version or tuple(map(int, version.groups())) < (22, 3):
            raise RuntimeError("Setup needs pip 22.3 or newer for --python. Update pip in the external Python and retry.")
        pip_command = [sys.executable, "-m", "pip", "--isolated", "--python", str(python)]
        try:
            subprocess.run([*pip_command, "--version"], check=True, capture_output=True,
                           text=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            detail = (getattr(exc, "stderr", None) or str(exc)).strip()
            raise RuntimeError("External pip could not run through portable Python 3.11.9. "
                               "Use a pip release supporting --python and Python 3.11. " + detail) from exc
        try:
            # Resolve all pins together, not just missing packages, to keep
            # transitive dependencies consistent with the complete requirements.
            _run_setup(config, *pip_command, "install", "--only-binary=:all:", "--upgrade", "--target", packages,
                       "--cache-dir", config.path("cache/pip"), "-r", config.path("requirements.txt"))
        except subprocess.CalledProcessError as exc:
            raise RuntimeError("Dependency installation failed. Review pip's error above for unavailable Python 3.11 "
                               "Windows x64 wheels, conflicting pins or network errors, then retry setup. "
                               "DUTY does not compile packages or relax its pins.") from exc
    server = config.path("provider/server")
    if not (server / "node_modules").is_dir():
        stage = Path(tempfile.mkdtemp(prefix="provider_", dir=cache))
        staged_server = stage / "server"
        shutil.copytree(config.path("vendor/bgutil/server"), staged_server,
                        ignore=shutil.ignore_patterns("node_modules", ".git", "__pycache__"))
        with zipfile.ZipFile(archive("node_build")) as z:
            z.extractall(stage / "node")
        node = next((stage / "node").glob("*/node.exe"))
        env = os.environ.copy()
        env["PATH"] = str(node.parent) + os.pathsep + env.get("PATH", "")
        env["npm_config_cache"] = str(config.path("cache/npm"))
        _run_setup(config, node, node.parent / "node_modules/npm/bin/npm-cli.js", "ci", "--omit=dev", "--no-audit", "--no-fund",
                   cwd=staged_server, env=env)
        if server.exists():
            backup = server.with_name("server_previous_" + uuid.uuid4().hex)
            server.rename(backup)
        server.parent.mkdir(parents=True, exist_ok=True)
        staged_server.rename(server)
    return {"ok": True, "python": str(config.tool("python")), "message": "Setup complete. Double-click DUTY.exe to open the app."}


# Transfer records and errors retained from the original implementation.


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class AuthenticationError(RuntimeError):
    pass


class ChannelMismatch(AuthenticationError):
    pass


class LoginRequired(AuthenticationError):
    pass


def bound_channel(config):
    path = config.path("permanent", "channel.json")
    return json.loads(path.read_text(encoding="utf-8"))["channel_id"] if path.is_file() else None


def bind_channel(config, channel_id):
    import re
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{22}", channel_id):
        raise ValueError("Expected a YouTube channel ID beginning with UC")
    expected = bound_channel(config)
    if expected and channel_id != expected:
        raise ChannelMismatch(f"Selected {channel_id}; this installation is bound to {expected}")
    atomic_json(config.path("permanent", "channel.json"), {"channel_id": channel_id})


class UploadLimit(RuntimeError):
    pass


class UnresolvedUpload(RuntimeError):
    pass


@dataclass
class Result:
    operation: str
    status: str
    local_path: str | None = None
    source: str | None = None
    video_id: str | None = None
    youtube_url: str | None = None
    error: str | None = None
    job_id: str | None = None
    stop_batch: bool = False
    error_code: str | None = None
    stage: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


class History:
    def __init__(self, config):
        self.folder = config.path("history")
        self.folder.mkdir(parents=True, exist_ok=True)

    def records(self):
        records = []
        for path in sorted(self.folder.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                self.validate(record)
                if path.stem != record["job_id"]:
                    raise ValueError("History filename does not match job_id")
                records.append(record)
            except (ValueError, OSError) as exc:
                # Never ignore damaged upload journals: duplicate protection depends on them.
                raise RuntimeError(f"Unreadable history record {path.name}: {exc}") from exc
        return records

    def write(self, record):
        self.validate(record)
        record["updated_at"] = now()
        atomic_json(self.folder / (record["job_id"] + ".json"), record)

    def duplicate(self, fingerprint: str, channel_id: str):
        for row in self.records():
            if (row.get("operation") == "upload" and row.get("fingerprint") == fingerprint
                    and row.get("channel_id") == channel_id
                    and row.get("status") in {"attaching", "submitted", "unresolved", "completed"}):
                return row
        return None

    @staticmethod
    def validate(record):
        if not isinstance(record, dict):
            raise ValueError("History record must be an object")
        job_id = record.get("job_id")
        if not isinstance(job_id, str) or not re.fullmatch(r"(?:upload|download|batch)_[A-Za-z0-9_-]+", job_id):
            raise ValueError("Invalid history job_id")
        if record.get("operation") not in {"upload", "download", "batch"}:
            raise ValueError("Invalid history operation")
        if record.get("status") not in {"started", "prepared", "attaching", "submitted", "completed", "failed", "unresolved", "pending", "login_required", "cancelled"}:
            raise ValueError("Invalid history status")
        for name in ("local_path", "source", "video_id", "channel_id", "fingerprint", "error"):
            if record.get(name) is not None and not isinstance(record[name], str):
                raise ValueError(f"Invalid history {name}")



def error_code(exc):
    if isinstance(exc, Cancelled):
        return "cancelled"
    if isinstance(exc, BusyError):
        return "busy"
    if isinstance(exc, ChannelMismatch):
        return "channel_mismatch"
    if isinstance(exc, LoginRequired):
        return "login_required"
    if isinstance(exc, AuthenticationError):
        return "authentication"
    if isinstance(exc, UploadLimit):
        return "upload_limit"
    message = str(exc).lower()
    if any(term in message for term in ("provider", "bgutil", "po token", "po-token", "javascript challenge")):
        return "provider"
    if any(term in message for term in ("requested format", "no video formats", "requested exact", "requested h.264")):
        return "format_unavailable"
    if any(term in message for term in ("video unavailable", "video is unavailable", "video has been removed", "private video", "not available in your country")):
        return "video_unavailable"
    if any(term in message for term in ("http error", "connection", "network", "timed out", "timeout", "urlopen")):
        return "network"
    if isinstance(exc, OSError):
        return "filesystem"
    if isinstance(exc, ValueError):
        return "invalid_input"
    return "transfer_failed"
