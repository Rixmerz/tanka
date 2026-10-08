"""The routines tab on the page: its state, badge and attention, the user's approve/reject/remove actions and
their errors, scope isolation, what makes the tab rebuild, page.js's escaping rules, and the tab being found
and served by the page. The library is a fake with the contract's shapes, so the page is tested on its own."""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_page import PageCase  # noqa: E402
from workspace_case import REPO, kit  # noqa: E402
import tanka_chat as chat  # noqa: E402
import tanka_page as page  # noqa: E402

MOD = REPO / "modules" / "routines"
LIMITS = {"max_automations": 10, "min_interval_seconds": 3600, "max_budget_usd": 1.0, "max_pending": 5, "max_task_chars": 4000}


class FakeRoutines(types.ModuleType):
    """The contract of modules/routines/routines.py, in memory, one store per scope."""

    def __init__(self):
        super().__init__("routines")
        self.ToolError = kit.ToolError
        self.data: dict[str, dict] = {}
        self.daemon = False

    def scope(self, scope):
        return self.data.setdefault(scope, {"routines": [], "triggers": [], "proposals": []})

    def propose(self, scope, pid, name, t=1000.0, task="Summarise the open issues of PROJ every morning"):
        self.scope(scope)["proposals"].insert(0, {"id": pid, "name": name, "every": "1d", "task": task, "budget": 0.25,
                                                  "why": "You asked for it each morning", "t": t, "status": "pending"})

    def state(self, scope, ws):
        s = self.scope(scope)
        return {"routines": [dict(x) for x in s["routines"]], "triggers": [dict(x) for x in s["triggers"]],
                "proposals": [dict(x) for x in s["proposals"]][:20], "limits": dict(LIMITS), "daemon": self.daemon}

    def _pending(self, scope, pid):
        p = next((x for x in self.scope(scope)["proposals"] if x["id"] == pid), None)
        if p is None or p["status"] != "pending":
            raise kit.ToolError(f"No pending proposal {pid}.")
        return p

    def approve(self, scope, ws, pid):
        p = self._pending(scope, pid)
        p["status"] = "approved"
        self.scope(scope)["routines"].append({"name": p["name"], "every": p["every"], "task": p["task"],
                                              "budget": p["budget"], "source": "assistant", "last": None})
        return f"Approved {p['name']}."

    def reject(self, scope, ws, pid):
        self._pending(scope, pid)["status"] = "rejected"
        return f"Rejected {pid}."

    def remove(self, scope, ws, name):
        rs = self.scope(scope)["routines"]
        if not any(x["name"] == name for x in rs):
            raise kit.ToolError(f"No routine named {name}.")
        rs[:] = [x for x in rs if x["name"] != name]
        return f"Removed {name}."


def load_page(fake):
    """modules/routines/page.py, importing the fake as `routines`."""
    old = sys.modules.get("routines")
    sys.modules["routines"] = fake
    try:
        spec = importlib.util.spec_from_file_location("page_routines_test", MOD / "page.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if old is None:
            sys.modules.pop("routines", None)
        else:
            sys.modules["routines"] = old
    return mod


def sig(mod_state):
    """What page.js's sig hashes from the server: the module's state (the rest is the page's own memory)."""
    return json.dumps(mod_state, sort_keys=True)


class RoutinesPageCase(PageCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeRoutines()
        self.page = load_page(self.fake)
        (self.ws / ".claude" / "skills" / "routines").mkdir(parents=True)
        hooks = chat.hooks()
        old = hooks.get("routines")
        hooks["routines"] = self.page
        self.addCleanup(lambda: hooks.__setitem__("routines", old) if old is not None else hooks.pop("routines", None))


class TestState(RoutinesPageCase):
    def test_shape_and_pending_count(self):
        self.fake.propose("duck", "p1", "issues-digest", t=1000)
        self.fake.propose("duck", "p2", "inbox", t=2000)
        self.fake.reject("duck", self.ws, "p1")
        st = self.page.state("duck", self.ws)
        self.assertEqual(set(st), {"routines", "triggers", "proposals", "limits", "daemon"})
        self.assertEqual([p["id"] for p in st["proposals"]], ["p2", "p1"])  # newest first
        self.assertEqual(self.page.waiting(st), 1)  # the badge and the attention count

    def test_scope_isolation(self):
        (self.wsdir / "goose" / ".tanka").mkdir(parents=True)
        self.fake.propose("goose", "g1", "other")
        self.fake.propose("duck", "d1", "mine")
        self.assertEqual([p["id"] for p in self.page.state("duck", self.ws)["proposals"]], ["d1"])
        self.assertEqual([p["id"] for p in self.scope()["modules"]["routines"]["proposals"]], ["d1"])

    def test_sig_changes_only_with_the_data(self):
        self.fake.propose("duck", "p1", "issues-digest")
        a, b = sig(self.page.state("duck", self.ws)), sig(self.page.state("duck", self.ws))
        self.assertEqual(a, b)
        self.page.ACTIONS["approve"]("duck", self.ws, {"id": "p1"})
        self.assertNotEqual(sig(self.page.state("duck", self.ws)), a)

    def test_installed_follows_the_skill(self):
        self.assertTrue(self.page.installed(self.ws))
        (self.ws / ".claude" / "skills" / "routines").rmdir()
        self.assertFalse(self.page.installed(self.ws))
        self.assertNotIn("routines", self.scope()["modules"])

    def test_effects_chip_for_a_proposal_made_in_the_answer(self):
        self.fake.propose("duck", "p1", "issues-digest", t=1500)
        got = self.page.effects("duck", self.ws, [(1000, 2000), (2000, 3000)])
        self.assertEqual(got[0][0]["id"], "p1")
        self.assertEqual(got[1], [])

    def test_hint_names_only_its_own_tools(self):
        h = self.page.hint("duck", self.ws)
        tools = set(re.findall(r"\b[a-z]+_[a-z_]+\b", h))
        self.assertEqual(tools, set(self.page.TOOLS))
        self.assertIn("routines_propose", chat.hint("duck"))


class TestActions(RoutinesPageCase):
    def post(self, name, body):
        status, data, _ = self.call("POST", f"/api/m/routines/{name}", dict({"scope": "duck"}, **body))
        return status, json.loads(data)

    def test_approve(self):
        self.fake.propose("duck", "p1", "issues-digest")
        status, got = self.post("approve", {"id": "p1"})
        self.assertEqual(status, 200)
        self.assertEqual((got["ok"], got["daemon"]), (True, False))
        self.assertIn("issues-digest", got["message"])
        self.assertEqual(self.scope()["modules"]["routines"]["routines"][0]["name"], "issues-digest")

    def test_reject(self):
        self.fake.propose("duck", "p1", "issues-digest")
        self.assertEqual(self.post("reject", {"id": "p1"})[0], 200)
        self.assertEqual(self.fake.state("duck", self.ws)["proposals"][0]["status"], "rejected")
        self.assertEqual(self.fake.state("duck", self.ws)["routines"], [])

    def test_remove(self):
        self.fake.propose("duck", "p1", "issues-digest")
        self.fake.approve("duck", self.ws, "p1")
        self.assertEqual(self.post("remove", {"name": "issues-digest"})[0], 200)
        self.assertEqual(self.fake.state("duck", self.ws)["routines"], [])

    def test_errors_are_400_with_the_message(self):
        for name, body, msg in (("approve", {"id": "nope"}, "No pending"), ("reject", {"id": "nope"}, "No pending"),
                                ("remove", {"name": "nope"}, "No routine")):
            status, got = self.post(name, body)
            self.assertEqual(status, 400, name)
            self.assertIn(msg, got["error"])

    def test_an_action_never_reaches_another_scope(self):
        (self.wsdir / "goose" / ".tanka").mkdir(parents=True)
        self.fake.propose("goose", "g1", "other")
        self.assertEqual(self.post("approve", {"id": "g1"})[0], 400)
        self.assertEqual(self.fake.state("goose", self.ws)["proposals"][0]["status"], "pending")

    def test_only_user_actions(self):
        self.assertEqual(set(self.page.ACTIONS), {"approve", "reject", "remove"})
        self.assertEqual(self.call("POST", "/api/m/routines/propose", {"scope": "duck", "name": "x"})[0], 404)


class TestPageJs(RoutinesPageCase):
    def test_no_html_sinks_or_browser_dialogs(self):
        js = (MOD / "page.js").read_text(encoding="utf-8")
        for bad in ("innerHTML", "outerHTML", "eval", "document.write", "alert(", "confirm(", "insertAdjacentHTML"):
            self.assertNotIn(bad, js, bad)

    def test_the_tab_is_served_with_the_page(self):
        html = page.page_html()
        self.assertIn("// ---- module: routines", html)
        self.assertIn('Tanka.module("routines"', html)
        js = (MOD / "page.js").read_text(encoding="utf-8")
        for key in ('id: "routines"', "badge:", "sig:", "attention:", "Nothing is waiting for you."):
            self.assertIn(key, js)

    def test_state_reaches_the_page(self):
        self.fake.propose("duck", "p1", "issues-digest")
        mod = self.scope()["modules"]["routines"]
        self.assertEqual(mod["proposals"][0]["task"], "Summarise the open issues of PROJ every morning")
        self.assertEqual(self.page.waiting(mod), 1)


class TestAgainstTheRealLibrary(PageCase):
    """When modules/routines/routines.py exists, the page's calls match it."""

    def test_state_and_errors(self):
        if not (MOD / "routines.py").is_file():
            self.skipTest("routines.py is not there yet")
        sys.path.insert(0, str(MOD))
        self.addCleanup(sys.path.remove, str(MOD))
        spec = importlib.util.spec_from_file_location("routines_real_for_page", MOD / "routines.py")
        real = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(real)
        pg = load_page(real)
        st = pg.state("duck", self.ws)
        for key in ("routines", "triggers", "proposals", "limits", "daemon"):
            self.assertIn(key, st)
        for name, body in (("approve", {"id": "nope"}), ("reject", {"id": "nope"}), ("remove", {"name": "nope"})):
            with self.assertRaises(Exception) as cm:
                pg.ACTIONS[name]("duck", self.ws, body)
            self.assertEqual(type(cm.exception).__name__, "ToolError", name)
