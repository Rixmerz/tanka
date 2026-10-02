"""Boards module: view rules, recording rows through the generated tool, reading them, the page's part."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_case import REPO, WorkspaceCase, b, load_cli, out_of  # noqa: E402
import tanka_modules as tm  # noqa: E402
import tanka_tools as tt  # noqa: E402

cli = load_cli("boards")
GRADES = json.loads((REPO / "modules" / "boards" / "examples" / "grades.json").read_text())


class BoardCase(WorkspaceCase):
    def setUp(self):
        super().setUp()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(tm.install("boards", self.ws, "duck"), 0)
        self.view("grades", GRADES)

    def view(self, name, spec):
        b.views_dir(self.ws).mkdir(parents=True, exist_ok=True)
        (b.views_dir(self.ws) / f"{name}.json").write_text(json.dumps(spec))
        return b.build(self.ws, "duck")


class TestViews(BoardCase):
    def test_the_example_passes(self):
        self.assertEqual(b.check(self.ws), ([], []))

    def test_bad_views_say_what_is_wrong(self):
        bad = json.loads(json.dumps(GRADES))
        bad["fields"]["extra"] = {"label": "Extra", "type": "text"}  # seven fields: a tool takes six
        bad["key"] = ["course", "nobody"]
        bad["actions"][0]["says"] = "Look at {studnet} again, please."
        bad["fields"]["status"]["choices"] = ["only"]
        problems = b.view_problems("grades", bad)
        for part in ("1-6 entries", "'nobody', which is not a field", "{studnet}", "2-12 distinct"):
            self.assertTrue(any(part in p for p in problems), (part, problems))
        self.assertIn("2-24 lowercase", b.view_problems("A", GRADES)[0])

    def test_tones_colour_some_choices_and_nothing_else(self):
        def problems(field, tones):
            spec = json.loads(json.dumps(GRADES))
            spec["fields"][field]["tones"] = tones
            return [p for p in b.view_problems("grades", spec) if "tones" in p]
        self.assertEqual(problems("status", {"reviewed": "info", "published": "good"}), [])
        self.assertTrue(problems("status", {"reviewed": "purple"}))     # not one of the page's tones
        self.assertTrue(problems("status", {"lost": "bad"}))            # not one of the choices
        self.assertTrue(problems("status", ["info"]))                   # not a mapping
        self.assertTrue(problems("student", {"reviewed": "info"}))      # only a choice field has tones

    def test_build_refuses_views_that_fail_check(self):
        (b.views_dir(self.ws) / "broken.json").write_text("{not json")
        with self.assertRaisesRegex(b.ToolError, "do not pass check"):
            b.build(self.ws, "duck")


class TestTools(BoardCase):
    def test_build_makes_a_typed_tool_the_harness_accepts(self):
        tools, problems = tt.scan(self.ws)
        self.assertEqual(problems, [])
        m = tools["boards_record_grades"]
        self.assertEqual(m["effect"], "draft")
        self.assertEqual(m["params"]["status"]["enum"], ["pending", "reviewed", "re-review", "published"])
        self.assertEqual((m["params"]["grade"]["minimum"], m["params"]["grade"]["maximum"]), (1, 7))
        self.assertEqual(sorted(p for p, s in m["params"].items() if s.get("required")), ["course", "evaluation", "student"])
        self.assertIn("boards_record_grades", (self.ws / ".claude/skills/boards/SKILL.md").read_text())
        self.assertIn("boards_rows", tools)

    def test_the_harness_refuses_a_bad_value_before_the_tool_runs(self):
        tools, _ = tt.scan(self.ws)
        _, errs = tt.check_args(tools["boards_record_grades"], {"course": "P1", "student": "Ana", "evaluation": "EVA1", "grade": 9})
        self.assertTrue(errs)
        _, errs = tt.check_args(tools["boards_record_grades"], {"course": "P1", "student": "Ana", "evaluation": "EVA1", "status": "done"})
        self.assertTrue(errs)

    def test_the_tool_records_and_updates_by_key(self):
        run = lambda args: subprocess.run(  # noqa: E731 - the generated script, exactly as the harness runs it
            [sys.executable, str(self.ws / ".claude/skills/boards/tools/boards_record_grades.py")], input=json.dumps(args),
            capture_output=True, text=True, env=dict(__import__("os").environ, TANKA_PLUGIN_DIR=str(REPO / "plugin")))
        p = run({"course": "Programming I", "student": "Ana Pérez", "evaluation": "EVA2", "grade": 5.5, "status": "pending"})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("new row with grade, status", p.stdout)
        p = run({"course": "programming i", "student": "ana  pérez", "evaluation": "EVA2", "grade": 6.2, "feedback": "Bien."})
        self.assertIn("updated grade, feedback", p.stdout)
        rows = b.live_rows("duck", "grades")
        self.assertEqual(len(rows), 1)  # the same key, written differently, is the same row
        self.assertEqual((rows[0]["fields"]["grade"], rows[0]["fields"]["status"]), (6.2, "pending"))
        p = run({"course": "Programming I", "student": "Ana Pérez"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("evaluation", p.stderr)

    def test_a_key_written_without_accents_is_the_same_row(self):
        b.record("duck", "grades", {"course": "Programación I", "student": "Ana Pérez", "evaluation": "EVA1", "grade": 5})
        row, changed = b.record("duck", "grades", {"course": "programacion i", "student": "ANA  PEREZ", "evaluation": "eva1", "grade": 6})
        self.assertEqual(changed, ["grade"])
        (only,) = b.live_rows("duck", "grades")
        self.assertEqual((only["fields"]["student"], only["fields"]["grade"]), ("Ana Pérez", 6))
        self.assertIn("1 row(s) matching", out_of(b.rows_tool, "duck", "grades", "perez"))

    def test_rows_tool_lists_views_then_rows(self):
        b.record("duck", "grades", {"course": "P1", "student": "Ana", "evaluation": "EVA1", "grade": 6})
        b.record("duck", "grades", {"course": "P2", "student": "Luis", "evaluation": "EVA1", "status": "reviewed"})
        listing = out_of(b.rows_tool, "duck", None, None)
        self.assertIn("grades: Grades.", listing)
        self.assertIn("status (one of pending, reviewed, re-review, published)", listing)
        self.assertIn("Record with boards_record_grades", listing)
        rows = out_of(b.rows_tool, "duck", "grades", "p2")
        self.assertIn("1 row(s) matching", rows)
        self.assertIn("P2 · Luis · EVA1 | status=reviewed", rows)

    def test_a_removed_view_takes_its_tool_along(self):
        (b.views_dir(self.ws) / "grades.json").unlink()
        self.assertIn("removed boards_record_grades (its view is gone)", b.build(self.ws, "duck"))
        self.assertFalse((self.ws / ".claude/skills/boards/tools/boards_record_grades.py").exists())
        self.assertIn("No boards yet.", (self.ws / ".claude/skills/boards/SKILL.md").read_text())

    def test_a_long_example_is_cut_and_the_tool_still_loads(self):
        spec = json.loads(json.dumps(GRADES))
        spec["example"]["feedback"] = "Muy bien. " * 200  # 2000 characters
        self.view("grades", spec)
        tools, problems = tt.scan(self.ws)
        self.assertEqual(problems, [])
        self.assertLessEqual(len(tools["boards_record_grades"]["examples"][0]["feedback"]), b.EXAMPLE_CHARS)

    def test_build_writes_nothing_the_harness_would_refuse(self):
        real = b.tool_manifest
        self.patch(b, "tool_manifest", lambda name, spec: dict(real(name, spec), description="Too short."))
        with self.assertRaisesRegex(b.ToolError, "harness would refuse"):
            b.build(self.ws, "duck")
        tools, _ = tt.scan(self.ws)
        self.assertIn("boards_record_grades", tools)  # the earlier, valid tool is still there

    def test_build_counts_against_the_tool_limit(self):
        self.patch(tt, "MAX_TOOLS_TOTAL", 2)
        spec = dict(GRADES, title="Clients")
        (b.views_dir(self.ws) / "clients.json").write_text(json.dumps(spec))
        with self.assertRaisesRegex(b.ToolError, "over the 2-tool limit"):
            b.build(self.ws, "duck")


class TestPagePart(BoardCase):
    def setUp(self):
        super().setUp()
        import tanka_chat as chat
        import tanka_page as page
        self.chat, self.page = chat, page

    def test_state_effects_and_the_users_own_changes(self):
        t0 = time.time() - 1
        row, _ = b.record("duck", "grades", {"course": "P1", "student": "Ana", "evaluation": "EVA1", "status": "reviewed"})
        st = self.page.scope_state("duck")["modules"]["boards"]["views"][0]
        self.assertEqual((st["name"], st["count"], st["group_by"]), ("grades", 1, "course"))
        self.assertNotIn("course", st["columns"])
        hooks = dict(self.chat.installed("duck"))
        (chips,) = hooks["boards"].effects("duck", self.ws, [(t0, time.time() + 1)])
        self.assertEqual((chips[0]["label"], chips[0]["type"]), ("P1 · Ana · EVA1", "created"))
        b.set_field("duck", "grades", row["id"], "status", "published")
        self.assertEqual(b.live_rows("duck", "grades")[0]["fields"]["status"], "published")
        with self.assertRaisesRegex(b.ToolError, "not a field the page can change"):
            b.set_field("duck", "grades", row["id"], "grade", "7")
        b.archive("duck", "grades", row["id"])
        self.assertEqual(b.live_rows("duck", "grades"), [])

    def test_the_hint_names_the_board_tools(self):
        self.assertIn("boards_rows", self.chat.hint("duck"))


class TestPageOnly(WorkspaceCase):
    """A view without the boards skill: the page shows it and the workspace's own tools fill it."""

    def setUp(self):
        super().setUp()
        import tanka_chat as chat
        self.chat = chat
        b.views_dir(self.ws).mkdir(parents=True)
        (b.views_dir(self.ws) / "grades.json").write_text(json.dumps(GRADES))

    def test_a_view_alone_puts_the_board_on_the_page(self):
        self.assertFalse(b.installed(self.ws))
        self.assertIn("boards", dict(self.chat.installed("duck")))
        (b.views_dir(self.ws) / "grades.json").unlink()
        self.assertNotIn("boards", dict(self.chat.installed("duck")))

    def test_the_hint_says_nothing_without_the_skill(self):
        hooks = dict(self.chat.installed("duck"))
        self.assertEqual(hooks["boards"].hint("duck", self.ws), "")
        self.assertNotIn("boards_rows", self.chat.hint("duck"))

    def test_another_tool_records_without_the_skill(self):
        row, changed = b.record("duck", "grades", {"course": "P1", "student": "Ana", "evaluation": "EVA1", "grade": 6})
        self.assertEqual(changed, ["course", "student", "evaluation", "grade"])
        self.assertEqual(b.live_rows("duck", "grades")[0]["id"], row["id"])
        import tanka_page as page
        (view,) = page.scope_state("duck")["modules"]["boards"]["views"]  # what the page serves
        self.assertEqual((view["name"], view["count"], view["problems"]), ("grades", 1, []))

    def test_try_record_writes_or_says_why_and_never_raises(self):
        ok = {"course": "P1", "student": "Ana", "evaluation": "EVA1"}
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertTrue(b.try_record("duck", "grades", dict(ok, grade=6, feedback=None)))  # None is left out
            self.assertFalse(b.try_record("duck", "grades", dict(ok, grade=9)))               # off the scale
            self.assertFalse(b.try_record("duck", "grades", dict(ok, status="done")))          # not a choice
            self.assertFalse(b.try_record("duck", "grades", {"course": "P1", "grade": 5}))     # no key
            self.assertFalse(b.try_record("duck", "nope", ok))                                  # no such view
            (b.views_dir(self.ws) / "grades.json").write_text("{not json")
            self.assertFalse(b.try_record("duck", "grades", dict(ok, grade=5)))                # broken view
        self.assertEqual(err.getvalue().count("boards: "), 5)
        self.assertIn("grade goes from 1 to 7", err.getvalue())
        (row,) = b.live_rows("duck", "grades")
        self.assertEqual(row["fields"], dict(ok, grade=6))

    def test_try_record_survives_an_unexpected_error(self):
        def boom(*a, **k):
            raise OSError("disk full")
        self.patch(b, "record", boom)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertFalse(b.try_record("duck", "grades", {"course": "P1", "student": "Ana", "evaluation": "EVA1"}))
        self.assertIn("disk full", err.getvalue())

    def test_check_works_and_build_says_it_needs_the_skill(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["check", "duck"]), 0)
            self.assertEqual(cli.main(["build", "duck"]), 1)
        self.assertIn("no boards skill", out.getvalue())
        self.assertIn("tanka install boards duck", err.getvalue())

    def test_a_symlinked_workspace_keeps_its_rows_under_its_name(self):
        (self.wsdir / "alias").symlink_to(self.ws)
        b.record("alias", "grades", {"course": "P1", "student": "Ana", "evaluation": "EVA1", "grade": 6})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["show", "alias", "grades"]), 0)
            self.assertEqual(cli.main(["check", "alias"]), 0)
            self.assertEqual(cli.main(["show", "alias"]), 0)
        self.assertIn("P1 · Ana · EVA1 | grade=6", out.getvalue())
        self.assertIn("1 row(s). Key: course + student + evaluation", out.getvalue())
        self.assertIn("Filled by the workspace's own tools.", out.getvalue())  # no boards_record_grades to name
        self.assertIn("alias: ok", out.getvalue())


class TestInstall(WorkspaceCase):
    def test_module_passes_its_rules(self):
        self.assertEqual(tm.check("boards"), [])

    def test_the_cli_copies_the_example_and_builds(self):
        with contextlib.redirect_stdout(io.StringIO()):
            tm.install("boards", self.ws, "duck")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["example", str(self.ws)]), 0)
            self.assertEqual(cli.main(["build", str(self.ws)]), 0)
            self.assertEqual(cli.main(["check", str(self.ws)]), 0)
        self.assertIn("made boards_record_grades", out.getvalue())
        shutil.rmtree(b.views_dir(self.ws))
