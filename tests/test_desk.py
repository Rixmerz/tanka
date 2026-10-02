"""Desk module: check items and reminders, the daily brief, the tools, install, and moving in from
an older codepanion."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_case import WorkspaceCase, d, kit, load_cli, out_of  # noqa: E402
import tanka_modules as tm  # noqa: E402

cli = load_cli("desk")


class TestCards(WorkspaceCase):
    def test_check_items_group_by_topic_and_never_duplicate(self):
        a = d.add_card("duck", "check", "app", "Agregar tests al login")
        b = d.add_card("duck", "check", "APP", "agregar  tests al login")
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["existed"])
        self.assertEqual(a["topic"], "app")  # the watched project's folder name
        d.add_card("duck", "check", "facturas", "Pagar la luz")
        checks = d.pending("duck")["checks"]
        self.assertEqual(sorted(x["topic"] for x in checks), ["app", "facturas"])
        self.assertEqual(next(x for x in checks if x["topic"] == "app")["project"], str(self.project))

    def test_reminder_times(self):
        self.t[0] = datetime(2026, 10, 1, 12, 0).timestamp()
        self.assertEqual(datetime.fromtimestamp(d.parse_at("17:30", self.t[0])), datetime(2026, 10, 1, 17, 30))
        self.assertEqual(datetime.fromtimestamp(d.parse_at("09:00", self.t[0])), datetime(2026, 10, 2, 9, 0))
        with self.assertRaisesRegex(d.ToolError, "already past"):
            d.parse_at("2026-09-30 10:00", self.t[0])
        with self.assertRaisesRegex(d.ToolError, "at most"):
            d.parse_at("2027-09-30 10:00", self.t[0])
        with self.assertRaisesRegex(d.ToolError, "needs at"):
            d.add_card("duck", "reminder", None, "Llamar a soporte")
        with self.assertRaisesRegex(d.ToolError, "no time"):
            d.add_card("duck", "check", None, "Llamar a soporte", "17:30")

    def test_closing_by_id_or_text(self):
        i = d.add_card("duck", "check", "app", "Agregar tests al login")
        d.add_card("duck", "check", "web", "Agregar tests al login")
        with self.assertRaisesRegex(d.ToolError, "2 open"):
            d.close_card("duck", "check", None, "Agregar tests al login")
        self.assertEqual(d.close_card("duck", "check", None, i["id"])["id"], i["id"])
        r = d.add_card("duck", "reminder", None, "Llamar a soporte", "23:59")
        self.assertIsNotNone(d.close_card("duck", "reminder", None, "llamar a soporte")["done_at"])
        with self.assertRaisesRegex(d.ToolError, "No open"):
            d.close_card("duck", "reminder", None, r["id"])

    def test_only_what_it_finds_on_its_own_is_rationed(self):
        d.write_json(d.config_file(self.ws), {"found_per_hour": 1})
        d.add_card("duck", "check", "app", "Tests del login", by=d.ASSISTANT, evidence="16:40 prompt: lo hago mañana")
        with self.assertRaisesRegex(d.ToolError, "budget"):
            d.add_card("duck", "check", "app", "Docs del login", by=d.ASSISTANT, evidence="16:41 prompt: después")
        d.add_card("duck", "reminder", None, "Pedido del usuario", "23:59", by=d.ASSISTANT)  # asked in chat: no evidence

    def test_due_reminders_notify_once_and_count(self):
        sent = []
        self.patch(d.tc, "desktop_notify", lambda title, text: sent.append((title, text)) or True)
        r = d.add_card("duck", "reminder", "app", "Revisar el PR", datetime.fromtimestamp(self.t[0] + 120).strftime("%H:%M"))
        self.assertEqual(d.fire_reminders(), 0)
        self.t[0] = r["at"] + 1
        self.assertEqual(d.fire_reminders(), 1)
        self.assertEqual(d.fire_reminders(), 0)
        self.assertEqual(sent, [("⏰ app · duck", "Revisar el PR")])
        self.assertEqual(d.due_count(), 1)
        d.update_card("duck", r["id"], "snooze", 10)
        self.assertEqual(d.due_count(), 0)
        self.t[0] += 11 * 60
        self.assertEqual(d.fire_reminders(), 1)
        d.update_card("duck", r["id"], "done")
        self.assertEqual(d.due_count(), 0)
        fired = [m for m in kit.read_jsonl(kit.chat_file("duck")) if m["who"] == "reminder"]
        self.assertEqual(len(fired), 2)  # each firing stays in the page's chat

    def test_a_workspace_without_the_desk_fires_nothing(self):
        (self.ws / ".claude" / "desk.json").unlink()
        r = d.add_card("duck", "reminder", None, "Revisar el PR", "23:59")
        self.t[0] = r["at"] + 1
        self.assertEqual(d.fire_reminders(), 0)

    def test_cards_belong_to_one_workspace(self):
        (self.wsdir / "other" / ".tanka").mkdir(parents=True)
        d.add_card("duck", "check", "app", "Solo de duck")
        self.assertEqual(d.pending("other")["checks"], [])
        self.assertIn("Solo de duck", out_of(d.list_pending, "duck", None))
        self.assertIn("Nothing pending", out_of(d.list_pending, "other", None))

    def test_the_tool_adds_and_ticks(self):
        self.assertIn("Added", out_of(d.card_tool, "duck", "check", "Pagar la luz", "casa", None, False, ""))
        self.assertIn("Already there", out_of(d.card_tool, "duck", "check", "Pagar la luz", "casa", None, False, ""))
        self.assertIn("Ticked", out_of(d.card_tool, "duck", "check", "pagar la luz", None, None, True, ""))
        self.assertIn("done", out_of(d.list_pending, "duck", "casa"))
        self.assertIn("(added by you)", out_of(d.list_pending, "duck", "casa"))


class TestBrief(WorkspaceCase):
    """The daily brief: once a day at its time, with what the day holds and what has stalled. No model."""

    def setUp(self):
        super().setUp()
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        self.morning = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        self.t[0] = self.morning - 5 * 86400

    def briefs(self):
        return [m for m in kit.read_jsonl(kit.chat_file("duck")) if m["who"] == "brief"]

    def test_the_brief_comes_once_at_its_time_with_the_stalled_items(self):
        old = d.add_card("duck", "check", "app", "Revisar el PR")
        self.t[0] = self.morning
        fresh = d.add_card("duck", "check", "app", "Subir la versión")
        rem = d.add_card("duck", "reminder", "", "Llamar a Ana", "17:00")
        self.assertEqual(d.daily_brief(), 0)  # 08:00, before the default 09:00
        self.t[0] = self.morning + 3600
        self.assertEqual(d.daily_brief(), 1)
        self.assertEqual(d.daily_brief(), 0)  # once a day
        b = self.briefs()[0]
        self.assertEqual((b["reminders"], b["topics"], b["stale"]), ([rem["id"]], {"app": 2}, [old["id"]]))
        self.assertNotIn(fresh["id"], b["stale"])

    def test_no_brief_with_nothing_open_when_off_or_too_late(self):
        self.t[0] = self.morning + 3600
        d.add_card("duck", "check", "app", "algo")
        d.write_json(d.config_file(self.ws), {"brief": ""})
        self.assertEqual(d.daily_brief(), 0)
        d.write_json(d.config_file(self.ws), {"brief": "09:00"})
        self.t[0] = self.morning + 14 * 3600  # 22:00: more than 12 h late, the day is skipped
        self.assertEqual(d.daily_brief(), 0)
        self.assertEqual(self.briefs(), [])

    def test_the_cli_sets_the_time(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["brief", str(self.ws), "07:30", "--stale", "5"]), 0)
        cfg = d.load_config(self.ws)
        self.assertEqual((cfg["brief"], cfg["stale_days"]), ("07:30", 5))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["brief", str(self.ws), "25:00"]), 1)


class TestMoveIn(WorkspaceCase):
    def test_cards_chat_and_brief_settings_move_once(self):
        old = self.home  # the codepanion's home, where they used to live
        (old / "cards").mkdir(parents=True)
        (old / "cards" / "duck.json").write_text(json.dumps({"cards": [{"id": "r-1", "kind": "reminder", "topic": "general",
                                                                        "text": "x", "at": 1, "by": "codepanion"}]}))
        (old / "chat").mkdir()
        (old / "chat" / "duck.jsonl").write_text(json.dumps({"t": 1, "who": "you", "text": "hola"}) + "\n")
        (old / "state.json").write_text(json.dumps({"brief": {"duck": "2026-10-01"}, "emitted": {}}))
        (self.ws / ".claude" / "desk.json").unlink()
        d.write_json(self.ws / ".claude" / "codepanion.json", {"lenses": [], "brief": "08:15", "stale_days": 4})
        done = d.migrate()
        self.assertEqual(len(done), 4)
        self.assertEqual(d.pending("duck")["reminders"][0]["id"], "r-1")
        self.assertEqual(kit.read_jsonl(kit.chat_file("duck"))[0]["text"], "hola")
        self.assertEqual(d.load_config(self.ws)["brief"], "08:15")
        self.assertEqual(d.read_json(d.state_file(), {})["brief"], {"duck": "2026-10-01"})
        self.assertEqual(d.migrate(), [])  # once


class TestUpgrade(WorkspaceCase):
    def test_the_poll_moves_data_but_never_turns_the_desk_back_on(self):
        (self.ws / ".claude" / "desk.json").unlink()
        d.write_json(self.ws / ".claude" / "codepanion.json", {"lenses": []})
        d.migrate(settings=False)
        self.assertFalse((self.ws / ".claude" / "desk.json").exists())

    def test_migrate_moves_the_card_tools_out_of_an_old_codepanion(self):
        tools = self.ws / ".claude" / "skills" / "codepanion" / "tools"
        tools.mkdir(parents=True)
        for name in ("codepanion_card", "codepanion_pending", "codepanion_note"):
            (tools / f"{name}.json").write_text("{}")
            (tools / f"{name}.py").write_text("")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["migrate"]), 0)
        self.assertEqual(sorted(f.name for f in tools.iterdir()), ["codepanion_note.json", "codepanion_note.py"])
        self.assertTrue((self.ws / ".claude" / "skills" / "desk" / "tools" / "desk_card.py").is_file())
        self.assertNotIn("codepanion_card", (tools.parent / "SKILL.md").read_text())
        self.assertIn("card tools now come from the desk", out.getvalue())


class TestInstall(WorkspaceCase):
    def test_module_passes_its_rules_and_installs(self):
        self.assertEqual(tm.check("desk"), [])
        (self.ws / ".claude" / "desk.json").unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tm.install("desk", self.ws, "duck"), 0)
        tool = (self.ws / ".claude" / "skills" / "desk" / "tools" / "desk_card.py").read_text()
        self.assertIn('SCOPE = "duck"', tool)
        self.assertEqual(d.load_config(self.ws)["brief"], d.DEFAULT_BRIEF)

    def test_the_codepanion_brings_the_desk(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(tm.install("codepanion", self.ws, "duck"), 0)
        self.assertIn("codepanion needs desk: installing it first", out.getvalue())
        skills = sorted(p.name for p in (self.ws / ".claude" / "skills").iterdir())
        self.assertEqual(skills, ["codepanion", "desk"])


if __name__ == "__main__":
    import unittest
    unittest.main()
