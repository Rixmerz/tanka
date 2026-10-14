"""What tanka_os hides about the platform, and the pieces that let Tanka start on Windows."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_os as tos  # noqa: E402

HOLD = """
import sys, time
sys.path.insert(0, sys.argv[1])
import tanka_os
fh = open(sys.argv[2], "a+")
tanka_os.lock(fh)
fh.seek(0); fh.truncate(); fh.write("1234"); fh.flush()
print("locked", flush=True)
time.sleep(30)
"""


class Locks(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.file = self.tmp / "x.lock"

    def test_a_held_lock_refuses_a_second_holder_and_keeps_its_content_readable(self):
        p = subprocess.Popen([sys.executable, "-c", HOLD, str(REPO / "plugin" / "scripts"), str(self.file)],
                             stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(p.stdout.readline().strip(), "locked")
            with open(self.file, "a+") as fh:
                with self.assertRaises(BlockingIOError):
                    tos.lock(fh, blocking=False)
                fh.seek(0)
                self.assertEqual(fh.read(), "1234")  # what the daemon's pid file holds stays readable
        finally:
            p.kill()
            p.wait()
            p.stdout.close()
        with open(self.file, "a+") as fh:
            tos.lock(fh, blocking=False)
            tos.unlock(fh)

    def test_lock_and_unlock_leave_the_file_position_alone(self):
        with open(self.file, "a+") as fh:
            fh.write("abc")
            fh.flush()
            fh.seek(1)
            tos.lock(fh)
            self.assertEqual(fh.read(), "bc")
            tos.unlock(fh)


class Processes(unittest.TestCase):
    def test_pid_alive(self):
        self.assertTrue(tos.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(tos.pid_alive(p.pid))
        for bad in (None, "", "x", 0, -1):
            self.assertFalse(tos.pid_alive(bad))

    def test_kill_group_stops_the_child_and_what_it_started(self):
        code = "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); " \
               "print('up', flush=True); time.sleep(60)"
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **tos.group_kwargs())
        self.assertEqual(p.stdout.readline().strip(), "up")
        tos.kill_group(p.pid)
        p.wait(timeout=10)
        p.stdout.close()
        self.assertFalse(tos.pid_alive(p.pid))
        tos.kill_group(p.pid, force=True)  # already gone: no error

    def test_terminate(self):
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        tos.terminate(p.pid)
        p.wait(timeout=10)
        tos.terminate(p.pid)  # already gone: no error


class Commands(unittest.TestCase):
    def test_python_argv(self):
        self.assertEqual(tos.python_argv(["node", "x.js"]), ["node", "x.js"])
        self.assertEqual(tos.python_argv([]), [])
        with mock.patch.object(tos, "IS_WINDOWS", True):
            self.assertEqual(tos.python_argv(["python3", "t.py", "a"]), [sys.executable, "t.py", "a"])
            self.assertEqual(tos.python_argv(["python", "t.py"]), [sys.executable, "t.py"])
        with mock.patch.object(tos, "IS_WINDOWS", False), mock.patch.object(tos.shutil, "which", return_value=None):
            self.assertEqual(tos.python_argv(["python3", "t.py"])[0], sys.executable)  # no python3 on PATH

    def test_tanka_argv(self):
        launcher = REPO / "bin" / "tanka"
        with mock.patch.object(tos, "IS_WINDOWS", False):
            self.assertEqual(tos.tanka_argv(launcher), [str(launcher)])
        with mock.patch.object(tos, "IS_WINDOWS", True):
            self.assertEqual(tos.tanka_argv(launcher), [sys.executable, str(REPO / "bin" / "tanka.py")])
            self.assertEqual(tos.tanka_argv("/x/fake-tanka"), ["/x/fake-tanka"])  # a test's stand-in stays itself

    def test_which_resolves_only_on_windows(self):
        with mock.patch.object(tos, "IS_WINDOWS", False):
            self.assertEqual(tos.which("python3"), "python3")
        with mock.patch.object(tos, "IS_WINDOWS", True), mock.patch.object(tos.shutil, "which", return_value=r"C:\n\claude.cmd"):
            self.assertEqual(tos.which("claude"), r"C:\n\claude.cmd")

    def test_runtime_env(self):
        env = tos.export_runtime_env({})
        self.assertEqual(env["PYTHONUTF8"], "1")
        self.assertTrue(env["TANKA_PYTHON"])
        self.assertNotIn("\\", tos.export_runtime_env({})["TANKA_PYTHON"] if tos.IS_WINDOWS else "")
        self.assertEqual(tos.export_runtime_env({"TANKA_PYTHON": "/my/py"})["TANKA_PYTHON"], "/my/py")


class Launchers(unittest.TestCase):
    def test_hooks_run_with_the_launchers_python(self):
        for f in (REPO / "plugin" / "hooks" / "hooks.json", REPO / "link" / "hooks" / "hooks.json"):
            for groups in json.loads(f.read_text(encoding="utf-8"))["hooks"].values():
                for h in (h for g in groups for h in g["hooks"]):
                    self.assertTrue(h["command"].startswith('"${TANKA_PYTHON:-python3}" '), (f, h["command"]))

    def test_the_hook_command_runs_with_and_without_tanka_python(self):
        cmd = '"${TANKA_PYTHON:-python3}" -c "import sys; print(sys.version_info[0])"'
        for env in ({**os.environ, "TANKA_PYTHON": sys.executable}, {k: v for k, v in os.environ.items() if k != "TANKA_PYTHON"}):
            p = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)
            self.assertEqual(p.stdout.strip(), "3", p.stderr)

    def test_the_bash_shim_and_the_python_launcher_agree(self):
        a = subprocess.run([str(REPO / "bin" / "tanka"), "help"], capture_output=True, text=True)
        b = subprocess.run([sys.executable, str(REPO / "bin" / "tanka.py"), "help"], capture_output=True, text=True)
        self.assertEqual(a.returncode, 0, a.stderr)
        self.assertEqual(a.stdout, b.stdout)
        self.assertIn("tanka start <name>", a.stdout)

    def test_the_bash_shim_needs_a_working_python(self):
        bare = Path(tempfile.mkdtemp())  # a PATH with bash and nothing else
        (bare / "bash").symlink_to(shutil.which("bash"))
        p = subprocess.run([str(bare / "bash"), str(REPO / "bin" / "tanka"), "help"], capture_output=True, text=True,
                           env={"PATH": str(bare), "TANKA_PYTHON": "/nonexistent/python"})
        self.assertEqual(p.returncode, 1)
        self.assertIn("needs Python 3.9", p.stderr)

    def test_the_windows_launcher_runs_tanka_py(self):
        text = (REPO / "bin" / "tanka.cmd").read_bytes().decode("ascii")
        self.assertIn('"%~dp0tanka.py" %*', text)
        self.assertIn("py -3", text)
        attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.cmd text eol=crlf", attrs)  # cmd.exe misreads labels and blocks in LF files

    def test_a_new_workspace_from_the_python_launcher(self):
        tmp = Path(tempfile.mkdtemp())
        env = {**os.environ, "HOME": str(tmp), "TANKA_WORKSPACES": str(tmp / "ws"), "TANKA_INIT_MODEL": "sonnet",
               "TANKA_NO_DAEMON": "1"}
        run = lambda *a: subprocess.run([sys.executable, str(REPO / "bin" / "tanka.py"), *a], capture_output=True,
                                        text=True, env=env, stdin=subprocess.DEVNULL)
        p = run("init", "shop")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((tmp / "ws" / "shop" / ".tanka" / "policy.json").is_file())
        self.assertEqual(json.loads((tmp / "ws" / "shop" / ".tanka" / "workspace.json").read_text())["model"], "sonnet")
        self.assertIn("refusing to overwrite", run("init", "shop").stderr)
        self.assertIn("not a workspace name", run("init", "tools").stderr)
        self.assertEqual(run("profile", "shop", "strict").returncode, 0)
        self.assertEqual(run("profile", "shop").stdout.strip(), "haiku (model: claude-sonnet-5-5, effort: -)")
        # A start without a terminal never creates a workspace by surprise.
        p = run("start", "shpo")
        self.assertEqual(p.returncode, 1)
        self.assertIn("No workspace at", p.stderr)


if __name__ == "__main__":
    unittest.main()
