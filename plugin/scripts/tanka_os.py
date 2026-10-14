"""What differs between Linux, macOS and Windows, in one place: file locks, process groups, whether a pid is
alive, the Python that runs the hooks and tools, and how to find a program on PATH.

Everything else in Tanka calls these instead of fcntl, os.killpg or os.kill(pid, 0), which do not exist on
Windows or, worse, do something else there: os.kill(pid, 0) on Windows terminates the process.
Standard library only.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"

if IS_WINDOWS:
    import msvcrt
else:
    import fcntl


def python_path() -> str:
    """This interpreter, with forward slashes on Windows: hook commands run in Git Bash there, where
    backslashes are escapes, and a forward-slash path works for both bash and CreateProcess."""
    exe = sys.executable or ("python" if IS_WINDOWS else "python3")
    return exe.replace("\\", "/") if IS_WINDOWS else exe


def export_runtime_env(env: dict | None = None) -> dict:
    """The variables every process Tanka starts should inherit: the Python the plugin's hooks run with
    (hooks.json reads TANKA_PYTHON, because Windows has no python3) and UTF-8 for every file and pipe
    (Windows defaults to the ANSI code page)."""
    env = os.environ if env is None else env
    env.setdefault("TANKA_PYTHON", python_path())
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def console_python() -> str:
    """This interpreter, but python.exe rather than pythonw.exe on Windows: a windowless parent would give every
    console program it starts a window of its own."""
    exe = Path(sys.executable)
    if IS_WINDOWS and exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return sys.executable


def which(name: str) -> str:
    """The name to start a program by. POSIX finds it on PATH by itself; Windows needs its full path for claude.cmd
    and other PATHEXT scripts, which CreateProcess will not find by name. A program not on PATH stays a name, and
    the caller's FileNotFoundError says so."""
    return (shutil.which(name) or name) if IS_WINDOWS else name


def tanka_argv(launcher) -> list[str]:
    """The command that runs the Tanka launcher at `launcher` (bin/tanka): the file itself on POSIX; on Windows,
    which cannot run a bash script, tanka.py next to it under this Python."""
    p = Path(launcher)
    if p.suffix == ".py":
        return [sys.executable, str(p)]
    if IS_WINDOWS and p.with_name("tanka.py").is_file() and p.stem == "tanka":
        return [sys.executable, str(p.with_name("tanka.py"))]
    return [str(p)]


def python_argv(argv: list[str]) -> list[str]:
    """A tool's command with `python3`/`python` replaced by this interpreter where that name does not run one."""
    if argv and argv[0] in ("python3", "python") and (IS_WINDOWS or not shutil.which(argv[0])):
        return [sys.executable, *argv[1:]]
    return argv


# --------------------------------------------------------------------------- #
# File locks (fcntl.flock on POSIX, msvcrt.locking on Windows)
# --------------------------------------------------------------------------- #
# Windows locks are mandatory byte ranges: a lock on byte 0 would stop other processes reading what the file
# holds (the daemon's pid, for one). The lock sits on one byte far past any content instead.
_LOCK_OFFSET = 0x7FFFFFF0


def _locking(fh, mode) -> None:
    fd = fh.fileno()
    pos = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, pos, os.SEEK_SET)


def lock(fh, blocking: bool = True) -> None:
    """Lock an open file exclusively. Without blocking, raises BlockingIOError when another process holds it."""
    if not IS_WINDOWS:
        fcntl.flock(fh, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        return
    while True:
        try:
            _locking(fh, msvcrt.LK_NBLCK)
            return
        except OSError:
            if not blocking:
                raise BlockingIOError("the file is locked by another process") from None
            time.sleep(0.05)


def unlock(fh) -> None:
    if not IS_WINDOWS:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return
    try:
        _locking(fh, msvcrt.LK_UNLCK)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Processes
# --------------------------------------------------------------------------- #
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_CONSOLE = 0x00000010


def group_kwargs() -> dict:
    """Popen arguments that put the child in its own process group (a session on POSIX), so a stop reaches
    everything it started and a Ctrl+C in the terminal does not. On Windows the child also gets a hidden console
    of its own, so it outlives the terminal (a daemon) and the programs it runs open no windows."""
    if not IS_WINDOWS:
        return {"start_new_session": True}
    return {"creationflags": CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW}


def pid_alive(pid: int) -> bool:
    """True while the process exists. Never signals it: on Windows os.kill(pid, 0) would terminate it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5  # access denied: it exists, it is someone else's
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def kill_group(pid: int, force: bool = False) -> None:
    """Stop a process and everything in its group: SIGTERM (or SIGKILL with force) to the POSIX process group,
    taskkill /T on Windows, which has no graceful signal for a process without a console of ours."""
    if IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(int(pid)), "/T"] + (["/F"] if force else [])
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
            if p.returncode != 0 and not force:  # a windowless process cannot be asked to close: force it
                subprocess.run(cmd + ["/F"], capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        os.killpg(int(pid), signal.SIGKILL if force else signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def terminate(pid: int) -> None:
    """Ask one process to stop (SIGTERM; on Windows, end it and its children)."""
    if IS_WINDOWS:
        kill_group(pid, force=True)
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def interrupt(pid: int) -> None:
    """Ctrl-C for a process we started detached (SIGINT; on Windows, which cannot deliver it, terminate)."""
    if IS_WINDOWS:
        terminate(pid)
        return
    os.kill(int(pid), signal.SIGINT)
