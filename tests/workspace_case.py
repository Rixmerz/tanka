"""What the codepanion, desk, boards and page tests share: every home in a temporary directory, a
workspace named duck that watches one project and has the desk, a fake clock, and the hook calls
Claude Code makes."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for sub in ("modules/boards", "modules/desk", "modules/codepanion", "plugin/scripts"):
    sys.path.insert(0, str(REPO / sub))
import boards as b  # noqa: E402
import codepanion as c  # noqa: E402
import desk as d  # noqa: E402
import tanka_kit as kit  # noqa: E402


def load_cli(module: str):
    """A module's cli.py under its own name: every module has one."""
    spec = importlib.util.spec_from_file_location(f"{module}_cli", REPO / "modules" / module / "cli.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXAMPLES = REPO / "builder" / "skills" / "new-codepanion" / "examples"
FAKE_KEY = "sk-" + "ant-" + "x" * 24  # built at runtime, so no scanner sees a key in this file


def out_of(fn, *args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


class WorkspaceCase(unittest.TestCase):
    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home, self.wsdir = self.tmp / "home", self.tmp / "workspaces"
        self.project = self.tmp / "code" / "app"
        (self.project / "src").mkdir(parents=True)
        self.other = self.tmp / "code" / "other"
        self.other.mkdir(parents=True)
        self.ws = self.wsdir / "duck"
        (self.ws / ".tanka").mkdir(parents=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}")
        (self.ws / ".claude").mkdir()
        (self.ws / ".claude" / "desk.json").write_text("{}")
        self.patch(c, "HOME", self.home)
        self.patch(c, "CLAUDE_HOME", self.tmp / "claude")
        self.patch(d, "HOME", self.tmp / "desk")
        self.patch(b, "HOME", self.tmp / "boards")
        self.patch(kit, "WORKSPACES", self.wsdir)
        self.patch(kit, "PAGE_HOME", self.tmp / "page")
        # A subprocess a test starts (a module's post-install, the tap) must see the same homes, never the user's.
        env = {"TANKA_WORKSPACES": self.wsdir, "TANKA_CODEPANION_HOME": self.home, "TANKA_DESK_HOME": self.tmp / "desk",
               "TANKA_PAGE_HOME": self.tmp / "page", "TANKA_BOARDS_HOME": self.tmp / "boards",
               "TANKA_AUTOMATION_HOME": self.tmp / "automation", "CLAUDE_CONFIG_DIR": self.tmp / "claude"}
        for k, v in env.items():
            old = os.environ.get(k)
            os.environ[k] = str(v)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
        c.watch_set(str(self.project), "duck")
        self.t = [time.time()]
        self.patch(c, "now", lambda: self.t[0])
        self.patch(d, "now", lambda: self.t[0])

    def configure(self, lenses=("stuck",), **extra):
        c.lenses_dir(self.ws).mkdir(parents=True, exist_ok=True)
        for name in lenses:
            if (EXAMPLES / f"{name}.md").is_file():
                shutil.copy(EXAMPLES / f"{name}.md", c.lenses_dir(self.ws) / f"{name}.md")
        c.write_json(c.config_file(self.ws), {"lenses": list(lenses), "proactivity": 1, **extra})

    def hook(self, name, cwd=None, session="s1", **fields):
        c.tap({"hook_event_name": name, "cwd": str(cwd or self.project), "session_id": session, **fields})
        self.t[0] += 1

    def feed(self, session="s1"):
        return c.read_feed(c.feed_dir() / f"{session}.jsonl")

    def fail(self, error="Traceback\nKeyError: 'user_id'", session="s1"):
        self.hook("PostToolUseFailure", session=session, tool_name="Bash", tool_input={"command": "pytest -q"}, error=error)


CodepanionCase = WorkspaceCase
