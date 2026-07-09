from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import wave
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from ramblefix.config import (
    DEFAULT_WHISPER_CPP_SERVER_URL,
    DEFAULT_WHISPER_CPP_SMALL_MODEL,
    DEFAULT_WHISPER_SERVER_BINARY,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8178
DEFAULT_LOG_DIR = Path("logs")


@dataclass(frozen=True)
class SidecarState:
    status: str
    ready: bool
    port_open: bool
    owned: bool
    pid: int | None
    url: str
    host: str
    port: int
    binary: str
    model: str
    pid_path: str
    log_path: str
    state_path: str
    launch_path: str
    error: str = ""
    warmed: bool = False


def status(
    *,
    host: str | None = None,
    port: int | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    paths = _paths(log_dir)
    binary = _server_binary()
    model = _server_model()
    pid = _read_pid(paths["pid"])
    owned_running = _owned_process(pid, paths=paths, binary=binary, model=model, host=host_value, port=port_value)
    port_open = _port_open(host_value, port_value)
    ready = _probe_inference(host_value, port_value, paths=paths, timeout_seconds=1.5)

    if ready:
        status_value = "ready"
    elif port_open:
        status_value = "port_conflict_or_unready"
    elif pid is not None and owned_running:
        status_value = "starting"
    elif pid is not None and _pid_running(pid):
        status_value = "unowned_pid"
    elif pid is not None:
        status_value = "stale"
    else:
        status_value = "stopped"

    result = SidecarState(
        status=status_value,
        ready=ready,
        port_open=port_open,
        owned=owned_running,
        pid=pid if owned_running else None,
        url=_server_url(host_value, port_value),
        host=host_value,
        port=port_value,
        binary=str(binary),
        model=str(model),
        pid_path=str(paths["pid"]),
        log_path=str(paths["log"]),
        state_path=str(paths["state"]),
        launch_path=str(paths["launch"]),
    )
    _write_state(paths["state"], result)
    return result


def ensure_ready(
    *,
    warm: bool = True,
    timeout_seconds: float = 15.0,
    host: str | None = None,
    port: int | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    current = status(host=host_value, port=port_value, log_dir=log_dir)
    if current.ready:
        return warmup(host=host_value, port=port_value, log_dir=log_dir, timeout_seconds=min(timeout_seconds, 8.0)) if warm else current
    if current.port_open:
        return _error_state(
            "port_conflict_or_unready",
            f"{host_value}:{port_value} is open but did not behave like whisper.cpp /inference",
            host_value,
            port_value,
            _paths(log_dir),
            _server_binary(),
            _server_model(),
        )

    current = start(warm=warm, timeout_seconds=timeout_seconds, host=host_value, port=port_value, log_dir=log_dir)
    return current


def start(
    *,
    warm: bool = True,
    timeout_seconds: float = 15.0,
    host: str | None = None,
    port: int | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    paths = _paths(log_dir)
    with _sidecar_lock(paths):
        current = status(host=host_value, port=port_value, log_dir=log_dir)
        if current.ready:
            return warmup(host=host_value, port=port_value, log_dir=log_dir, timeout_seconds=min(timeout_seconds, 8.0)) if warm else current
        if current.status == "starting" and current.owned and current.pid is not None:
            waited = _wait_for_existing_start(
                current.pid,
                paths=paths,
                host=host_value,
                port=port_value,
                warm=warm,
                timeout_seconds=timeout_seconds,
                log_dir=log_dir,
            )
            if waited.status not in {"stopped", "stale"}:
                return waited
        if current.port_open:
            return _error_state(
                "port_conflict_or_unready",
                f"{host_value}:{port_value} is already open but not a validated whisper.cpp server",
                host_value,
                port_value,
                paths,
                _server_binary(),
                _server_model(),
            )

        binary = _server_binary()
        model = _server_model()
        if not binary.exists():
            return _error_state("missing_binary", f"missing whisper-server binary: {binary}", host_value, port_value, paths, binary, model)
        if not model.exists():
            return _error_state("missing_model", f"missing whisper.cpp model: {model}", host_value, port_value, paths, binary, model)

        _rotate_log(paths["log"])
        log_file = paths["log"].open("a", encoding="utf-8")
        command = [
            str(binary),
            "-m",
            str(model),
            "-l",
            "auto",
            "-tr",
            "-nt",
            "--host",
            host_value,
            "--port",
            str(port_value),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_file.close()

        launch_record = {
            "pid": process.pid,
            "pgid": _process_group(process.pid),
            "binary": str(binary),
            "model": str(model),
            "host": host_value,
            "port": port_value,
            "command": command,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        paths["pid"].write_text(str(process.pid), encoding="utf-8")
        paths["launch"].write_text(json.dumps(launch_record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return _error_state(
                    "exited",
                    f"whisper-server exited with code {process.returncode}; see {paths['log']}",
                    host_value,
                    port_value,
                    paths,
                    binary,
                    model,
                    pid=process.pid,
                )
            if _probe_inference(host_value, port_value, paths=paths, timeout_seconds=1.5):
                current = status(host=host_value, port=port_value, log_dir=log_dir)
                return warmup(host=host_value, port=port_value, log_dir=log_dir, timeout_seconds=min(timeout_seconds, 8.0)) if warm else current
            time.sleep(0.3)

        return _error_state(
            "timeout",
            f"whisper-server did not become ready within {timeout_seconds:.1f}s",
            host_value,
            port_value,
            paths,
            binary,
            model,
            pid=process.pid,
        )


def stop(
    *,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    host: str | None = None,
    port: int | None = None,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    paths = _paths(log_dir)
    binary = _server_binary()
    model = _server_model()
    pid = _read_pid(paths["pid"])
    if pid is None:
        return status(host=host_value, port=port_value, log_dir=log_dir)
    if not _pid_running(pid):
        _clear_owned_files(paths)
        return status(host=host_value, port=port_value, log_dir=log_dir)
    if not _owned_process(pid, paths=paths, binary=binary, model=model, host=host_value, port=port_value):
        return _error_state(
            "unowned_pid_refused",
            f"refusing to stop pid {pid}; launch metadata or process command does not match RambleFix sidecar",
            host_value,
            port_value,
            paths,
            binary,
            model,
            pid=pid,
            owned=False,
        )

    pgid = _launch_record(paths).get("pgid")
    try:
        if isinstance(pgid, int) and pgid > 0:
            os.killpg(pgid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _clear_owned_files(paths)
        return status(host=host_value, port=port_value, log_dir=log_dir)
    except PermissionError as exc:
        return _error_state("permission", f"cannot stop pid {pid}: {exc}", host_value, port_value, paths, binary, model, pid=pid)

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            _clear_owned_files(paths)
            return status(host=host_value, port=port_value, log_dir=log_dir)
        time.sleep(0.2)

    return _error_state("still_running", f"pid {pid} did not stop after SIGTERM", host_value, port_value, paths, binary, model, pid=pid)


def restart(
    *,
    warm: bool = True,
    timeout_seconds: float = 15.0,
    host: str | None = None,
    port: int | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    stopped = stop(host=host_value, port=port_value, log_dir=log_dir)
    if stopped.status in {"permission", "still_running", "unowned_pid_refused"}:
        return stopped
    if stopped.port_open and not stopped.ready:
        return stopped
    if stopped.ready and not stopped.owned:
        return stopped
    return start(warm=warm, timeout_seconds=timeout_seconds, host=host_value, port=port_value, log_dir=log_dir)


def warmup(
    *,
    host: str | None = None,
    port: int | None = None,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    timeout_seconds: float = 8.0,
) -> SidecarState:
    host_value = _server_host(host)
    port_value = _server_port(port)
    paths = _paths(log_dir)
    current = status(host=host_value, port=port_value, log_dir=log_dir)
    if not current.ready:
        return current
    if not _probe_inference(host_value, port_value, paths=paths, timeout_seconds=timeout_seconds):
        return _error_state(
            "warmup_failed",
            "warmup failed: /inference did not return a valid JSON response",
            host_value,
            port_value,
            paths,
            _server_binary(),
            _server_model(),
            pid=current.pid,
            owned=current.owned,
        )
    warmed = SidecarState(**{**asdict(current), "warmed": True})
    _write_state(paths["state"], warmed)
    return warmed


def state_to_json(state: SidecarState) -> str:
    return json.dumps(asdict(state), indent=2, ensure_ascii=False)


def as_dict(state: SidecarState) -> dict[str, Any]:
    return asdict(state)


def _server_host(host: str | None) -> str:
    value = host or os.environ.get("RAMBLEFIX_WHISPER_HOST", DEFAULT_HOST)
    if value not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError(f"RambleFix sidecar host must be loopback, got {value!r}")
    return value


def _server_port(port: int | None) -> int:
    if port is not None:
        return int(port)
    raw = os.environ.get("RAMBLEFIX_WHISPER_PORT", str(DEFAULT_PORT))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"invalid RAMBLEFIX_WHISPER_PORT={raw!r}") from exc
    if value <= 0 or value > 65535:
        raise RuntimeError(f"invalid RAMBLEFIX_WHISPER_PORT={raw!r}")
    return value


def _server_binary() -> Path:
    return Path(os.environ.get("RAMBLEFIX_WHISPER_SERVER_BINARY", DEFAULT_WHISPER_SERVER_BINARY)).expanduser()


def _server_model() -> Path:
    return Path(os.environ.get("RAMBLEFIX_WHISPER_MODEL", DEFAULT_WHISPER_CPP_SMALL_MODEL)).expanduser()


def _server_url(host: str, port: int) -> str:
    if host == DEFAULT_HOST and port == DEFAULT_PORT:
        return DEFAULT_WHISPER_CPP_SERVER_URL
    return f"http://{host}:{port}/inference"


def _paths(log_dir: str | Path) -> dict[str, Path]:
    root = Path(log_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return {
        "pid": root / "whisper_cpp_server.pid",
        "log": root / "whisper_cpp_server.log",
        "state": root / "whisper_cpp_server_state.json",
        "launch": root / "whisper_cpp_server_launch.json",
        "lock": root / "whisper_cpp_server.lock",
        "warmup": root / "whisper_cpp_warmup.wav",
    }


@contextmanager
def _sidecar_lock(paths: dict[str, Path]) -> Any:
    paths["lock"].parent.mkdir(parents=True, exist_ok=True)
    with paths["lock"].open("a+", encoding="utf-8") as lock_file:
        try:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _read_pid(path: Path) -> int | None:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _pid_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _owned_process(
    pid: int | None,
    *,
    paths: dict[str, Path],
    binary: Path,
    model: Path,
    host: str,
    port: int,
) -> bool:
    if not _pid_running(pid):
        return False
    launch = _launch_record(paths)
    if launch.get("pid") != pid:
        return False
    if str(Path(str(launch.get("binary", ""))).expanduser()) != str(binary):
        return False
    if str(Path(str(launch.get("model", ""))).expanduser()) != str(model):
        return False
    if launch.get("host") != host or int(launch.get("port") or -1) != port:
        return False
    pgid = launch.get("pgid")
    if isinstance(pgid, int) and pgid > 0 and _process_group(pid) != pgid:
        return False
    command = _process_command(pid)
    if not command:
        return False
    return binary.name in command and str(model) in command and str(port) in command


def _launch_record(paths: dict[str, Path]) -> dict[str, Any]:
    try:
        payload = json.loads(paths["launch"].read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _process_group(pid: int | None) -> int | None:
    if pid is None:
        return None
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def _process_command(pid: int | None) -> str:
    if pid is None:
        return ""
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            text=True,
            capture_output=True,
            timeout=2.0,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _port_open(host: str, port: int) -> bool:
    try:
        import socket

        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _wait_for_existing_start(
    pid: int,
    *,
    paths: dict[str, Path],
    host: str,
    port: int,
    warm: bool,
    timeout_seconds: float,
    log_dir: str | Path,
) -> SidecarState:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            _clear_owned_files(paths)
            return status(host=host, port=port, log_dir=log_dir)
        if _probe_inference(host, port, paths=paths, timeout_seconds=1.5):
            current = status(host=host, port=port, log_dir=log_dir)
            return warmup(host=host, port=port, log_dir=log_dir, timeout_seconds=min(timeout_seconds, 8.0)) if warm else current
        time.sleep(0.3)
    return _error_state(
        "starting_timeout",
        f"existing sidecar pid {pid} did not become ready within {timeout_seconds:.1f}s",
        host,
        port,
        paths,
        _server_binary(),
        _server_model(),
        pid=pid,
    )


def _probe_inference(host: str, port: int, *, paths: dict[str, Path], timeout_seconds: float) -> bool:
    _write_warmup_wav(paths["warmup"])
    try:
        with paths["warmup"].open("rb") as audio_file:
            response = requests.post(
                _server_url(host, port),
                files={"file": (paths["warmup"].name, audio_file, "audio/wav")},
                data={"response_format": "json", "temperature": "0.0", "translate": "true"},
                timeout=(0.5, timeout_seconds),
            )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return False
    return isinstance(payload, dict) and "text" in payload


def _write_warmup_wav(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    samplerate = 16_000
    samples = int(0.25 * samplerate)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(samplerate)
        wav.writeframes(b"\x00\x00" * samples)


def _write_state(path: Path, state: SidecarState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(state), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _clear_owned_files(paths: dict[str, Path]) -> None:
    paths["pid"].unlink(missing_ok=True)
    paths["launch"].unlink(missing_ok=True)


def _rotate_log(path: Path, *, max_bytes: int = 2_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size <= max_bytes:
        return
    rotated = path.with_suffix(path.suffix + ".1")
    rotated.unlink(missing_ok=True)
    path.rename(rotated)


def _error_state(
    status_value: str,
    error: str,
    host: str,
    port: int,
    paths: dict[str, Path],
    binary: Path,
    model: Path,
    *,
    pid: int | None = None,
    owned: bool | None = None,
) -> SidecarState:
    if owned is None:
        owned = _owned_process(pid, paths=paths, binary=binary, model=model, host=host, port=port)
    state = SidecarState(
        status=status_value,
        ready=False,
        port_open=_port_open(host, port),
        owned=owned,
        pid=pid if owned else None,
        url=_server_url(host, port),
        host=host,
        port=port,
        binary=str(binary),
        model=str(model),
        pid_path=str(paths["pid"]),
        log_path=str(paths["log"]),
        state_path=str(paths["state"]),
        launch_path=str(paths["launch"]),
        error=error,
    )
    _write_state(paths["state"], state)
    return state
