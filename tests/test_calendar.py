"""Calendar module: scope, the unattended guard, agenda parsing, free time and create validation, without a browser."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "calendar"))
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import gcal  # noqa: E402
import tanka_tools as tt  # noqa: E402

KEY_OCT_20 = (2026 - 1970) << 9 | 10 << 5 | 20  # 2026-10-20, a Tuesday


def ev(day: str, start: str | None, end: str | None, title: str = "Busy", kind: str = "timed") -> dict:
    s = gcal.parse_clock(start) if start else None
    e = gcal.parse_clock(end) if end else None
    return {"id": f"{day}{start}{title}", "date": day, "title": title, "kind": kind, "start": s, "end": e, "place": ""}


class CalendarCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.registry = {"accounts": {
            "Me@Example.com": {"workspace": "personal"},
            "office@example.com": {"workspace": "work"},
            "second@example.com": {"workspace": "work", "unattended_write": True}}}
        (self.tmp / "gmail").mkdir()
        (self.tmp / "gmail" / "accounts.json").write_text(json.dumps(self.registry))
        for name, value in {"HOME": self.tmp / "calendar", "REGISTRY": self.tmp / "calendar" / "accounts.json",
                            "GMAIL_HOME": self.tmp / "gmail", "GMAIL_REGISTRY": self.tmp / "gmail" / "accounts.json",
                            "LOCK": self.tmp / "gmail" / ".lock"}.items():
            old = getattr(gcal, name)
            setattr(gcal, name, value)
            self.addCleanup(setattr, gcal, name, old)
        self.env = mock.patch.dict(os.environ, {}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        for k in ("TANKA_UNATTENDED", "TANKA_CHAT"):
            os.environ.pop(k, None)
        old = gcal.session
        gcal.session = lambda: (_ for _ in ()).throw(AssertionError("the browser must not be used"))
        self.addCleanup(setattr, gcal, "session", old)


class TestScope(CalendarCase):
    def test_falls_back_to_the_gmail_list(self):
        self.assertEqual(gcal.pick_account("personal", None), "me@example.com")

    def test_its_own_list_wins(self):
        gcal.HOME.mkdir()
        gcal.REGISTRY.write_text(json.dumps({"accounts": {"cal@example.com": {"workspace": "personal"}}}))
        self.assertEqual(gcal.pick_account("personal", None), "cal@example.com")

    def test_several_accounts_need_a_choice(self):
        with self.assertRaisesRegex(gcal.ToolError, "Ask the user which one"):
            gcal.pick_account("work", None)
        self.assertEqual(gcal.pick_account("work", "second"), "second@example.com")

    def test_other_workspace_account_is_refused(self):
        with self.assertRaisesRegex(gcal.ToolError, "not a calendar this assistant may use"):
            gcal.pick_account("personal", "office@example.com")

    def test_no_list_at_all(self):
        gcal.GMAIL_REGISTRY.unlink()
        with self.assertRaisesRegex(gcal.ToolError, "does not exist"):
            gcal.pick_account("personal", None)

    def test_refusals_happen_before_the_browser(self):
        with self.assertRaisesRegex(gcal.ToolError, "not a calendar"):
            gcal.agenda("personal", "office@example.com", None, 1)
        with self.assertRaisesRegex(gcal.ToolError, "No Google account is assigned"):
            gcal.free("nobody", "me@example.com", 3, 30)
        with self.assertRaisesRegex(gcal.ToolError, "no event number 2"):
            gcal.event("personal", 2)
        with self.assertRaisesRegex(gcal.ToolError, "Ask the user which one"):
            gcal.create("work", "Team sync", "2026-10-20 15:00", "30m", "", "", False)

    def test_only_calendar_google_com_is_opened(self):
        with mock.patch.object(gcal, "rastro") as r, self.assertRaisesRegex(gcal.ToolError, "only open calendar.google.com"):
            gcal.goto("https://mail.google.com/mail/u/0/")
        r.assert_not_called()
        url = gcal.create_url("me@example.com", "Team sync", date(2026, 10, 20), 900, 930, "", "")
        self.assertTrue(url.startswith("https://calendar.google.com/calendar/u/0/r/eventedit?"))
        self.assertIn("authuser=me%40example.com", url)


class TestUnattended(CalendarCase):
    def test_unattended_without_chat_is_refused(self):
        os.environ["TANKA_UNATTENDED"] = "1"
        with self.assertRaisesRegex(gcal.ToolError, "Nobody is watching"):
            gcal.check_unattended("me@example.com")
        with self.assertRaisesRegex(gcal.ToolError, "Nobody is watching"):
            gcal.create("personal", "Team sync", "2026-10-20 15:00", "30m", "", "", False)

    def test_chat_or_unattended_write_allows_it(self):
        os.environ["TANKA_UNATTENDED"] = "1"
        gcal.check_unattended("second@example.com")
        os.environ["TANKA_CHAT"] = "1"
        gcal.check_unattended("me@example.com")
        os.environ.pop("TANKA_UNATTENDED")
        os.environ.pop("TANKA_CHAT")
        gcal.check_unattended("me@example.com")


class TestParsing(unittest.TestCase):
    def test_clock(self):
        cases = {"1pm": 780, "2:45pm": 885, "12pm": 720, "12am": 0, "10am": 600, "13:00": 780,
                 "1:00 p. m.": 780, "9:30 a.m.": 570, "25:00": None, "13pm": None, "soon": None}
        for text, want in cases.items():
            self.assertEqual(gcal.parse_clock(text), want, text)

    def test_datekey(self):
        self.assertEqual(gcal.decode_datekey(KEY_OCT_20), date(2026, 10, 20))

    def test_spanish_labels(self):
        e = gcal.parse_event("a1", KEY_OCT_20, "De 1pm a 2:45pm, Team sync, Clara Client, Aceptado, Ubicación: Room 2, Floor 3, "
                             "20 de octubre de 2026", "Team sync")
        self.assertEqual((e["kind"], e["start"], e["end"], e["title"], e["place"], e["date"]),
                         ("timed", 780, 885, "Team sync", "Room 2, Floor 3", "2026-10-20"))
        e = gcal.parse_event("a2", KEY_OCT_20, "De 4pm a 5pm, Review, Clara Client, Aceptado, Sin ubicación, 20 de octubre de 2026", "Review")
        self.assertEqual(e["place"], "")
        e = gcal.parse_event("a3", KEY_OCT_20, "Todo el día, Holiday, Calendario: Holidays, 20 de octubre de 2026", "Holiday")
        self.assertEqual((e["kind"], e["start"]), ("allday", None))
        e = gcal.parse_event("a4", KEY_OCT_20, "Del 17 de septiembre de 2026 a las 1pm hasta el 10 de diciembre de 2026 a las 11pm, "
                             "Course, Clara Client, Aceptado, Ubicación: Online, jueves", "Course")
        self.assertEqual(e["kind"], "multi")
        e = gcal.parse_event("a5", KEY_OCT_20, "De 13:00 a 14:30, Lunch, Sin ubicación, 20 de octubre de 2026", "")
        self.assertEqual((e["start"], e["end"], e["title"]), (780, 870, "(no title)"))

    def test_english_labels(self):
        e = gcal.parse_event("b1", KEY_OCT_20, "1pm to 2:45pm, Team sync, Clara Client, Accepted, Location: Room 2, October 20, 2026",
                             "Team sync\n")
        self.assertEqual((e["kind"], e["start"], e["end"], e["place"]), ("timed", 780, 885, "Room 2"))
        e = gcal.parse_event("b2", KEY_OCT_20, "All day, Holiday, Calendar: Holidays, October 20, 2026", "Holiday")
        self.assertEqual(e["kind"], "allday")
        e = gcal.parse_event("b3", KEY_OCT_20, "11pm to 1am, Night shift, No location, October 20, 2026", "Night shift")
        self.assertEqual((e["start"], e["end"], e["place"]), (1380, 1440, ""))

    def test_agenda_grouping_and_numbering(self):
        events = [ev("2026-10-21", "09:00", "10:00", "B"), ev("2026-10-20", "15:00", "16:00", "A2"),
                  ev("2026-10-20", None, None, "Holiday", "allday"), ev("2026-10-20", "09:00", "09:30", "A1"),
                  ev("2026-10-27", "09:00", "10:00", "Too late"), ev("2026-10-19", "09:00", "10:00", "Too early")]
        events.append(dict(events[1]))  # the agenda can show the same entry twice
        got = gcal.in_range(events, date(2026, 10, 20), 2)
        self.assertEqual([e["title"] for e in got], ["Holiday", "A1", "A2", "B"])
        text = gcal.agenda_text("me@example.com", date(2026, 10, 20), 2, got, date(2026, 10, 20))
        lines = text.splitlines()
        self.assertIn("4 event(s)", lines[0])
        self.assertEqual(lines[1], "1 | 2026-10-20 | all day | Holiday")
        self.assertEqual(lines[2], "2 | 2026-10-20 | 09:00-09:30 | A1")
        self.assertIn("calendar_event", lines[-1])

    def test_output_cap(self):
        many = [dict(ev("2026-10-20", "09:00", "10:00", "x" * 150), id=str(i)) for i in range(200)]
        text = gcal.agenda_text("me@example.com", date(2026, 10, 20), 1, many, date(2026, 10, 20))
        self.assertLessEqual(len(text), 5800)
        self.assertIn("more line(s) cut", text)
        detail = gcal.detail_text(1, {"title": "T", "description": "y" * 20000, "guests": ["G"] * 10, "guest_count": 40})
        self.assertLessEqual(len(detail), 5800)
        self.assertIn("and 30 more", detail)
        self.assertIn("description cut", detail)

    def test_detail_without_guest_names(self):
        text = gcal.detail_text(2, {"title": "All hands", "when": "Tuesday", "recurrence": "Every week", "guest_count": 50, "guests": []})
        self.assertIn("Guests: 50", text)
        self.assertIn("Repeats: Every week", text)


class TestFreeSlots(unittest.TestCase):
    HOURS = (9 * 60, 18 * 60)
    WEEK = {1, 2, 3, 4, 5}

    def slots(self, events, start=date(2026, 10, 20), days=1, minutes=30, now=None):
        return [(d, gcal.hhmm(a), gcal.hhmm(b)) for d, a, b in
                gcal.free_slots(events, start, days, minutes, self.HOURS, self.WEEK, now)]

    def test_empty_day_is_all_free(self):
        self.assertEqual(self.slots([]), [("2026-10-20", "09:00", "18:00")])

    def test_overlapping_events_merge(self):
        events = [ev("2026-10-20", "10:00", "11:30"), ev("2026-10-20", "11:00", "12:00"), ev("2026-10-20", "10:30", "10:45")]
        self.assertEqual(self.slots(events), [("2026-10-20", "09:00", "10:00"), ("2026-10-20", "12:00", "18:00")])

    def test_all_day_and_multi_day_do_not_block(self):
        events = [ev("2026-10-20", None, None, "Holiday", "allday"), ev("2026-10-20", None, None, "Course", "multi")]
        self.assertEqual(self.slots(events), [("2026-10-20", "09:00", "18:00")])

    def test_working_hours_edges(self):
        events = [ev("2026-10-20", "08:00", "09:15"), ev("2026-10-20", "17:45", "19:00"), ev("2026-10-20", "12:00", "12:20"),
                  ev("2026-10-20", "12:40", "13:00")]
        # the 20 minutes between 12:20 and 12:40 are too short for 30
        self.assertEqual(self.slots(events), [("2026-10-20", "09:15", "12:00"), ("2026-10-20", "13:00", "17:45")])
        self.assertEqual(self.slots([ev("2026-10-20", "06:00", "20:00")]), [])

    def test_weekends_are_skipped(self):
        got = self.slots([], start=date(2026, 10, 23), days=4)  # Friday to Monday
        self.assertEqual([d for d, _, _ in got], ["2026-10-23", "2026-10-26"])

    def test_today_starts_from_now(self):
        got = self.slots([], now=datetime(2026, 10, 20, 14, 2))
        self.assertEqual(got, [("2026-10-20", "14:05", "18:00")])
        self.assertEqual(self.slots([], now=datetime(2026, 10, 20, 17, 50)), [])

    def test_settings(self):
        self.assertEqual(gcal.parse_work_hours("08:30-17:00"), (510, 1020))
        with self.assertRaises(gcal.ToolError):
            gcal.parse_work_hours("18:00-09:00")
        self.assertEqual(gcal.parse_work_days("1,2,3"), {1, 2, 3})
        with self.assertRaises(gcal.ToolError):
            gcal.parse_work_days("8")


class TestCreate(CalendarCase):
    def test_validation(self):
        self.assertEqual(gcal.parse_new_event(" Team  sync ", "2026-10-20", "15:00", "45m"), ("Team sync", date(2026, 10, 20), 900, 945))
        self.assertEqual(gcal.parse_new_event("Team sync", "2026-10-20", "9:00", "10:30")[2:], (540, 630))
        bad = [("", "2026-10-20", "15:00", "16:00", "title"), ("T", "2026-13-01", "15:00", "16:00", "not a date"),
               ("T", "2026-10-20", "3pm", "16:00", "start time"), ("T", "2026-10-20", "15:00", "soon", "duration"),
               ("T", "2026-10-20", "15:00", "14:00", "before"), ("T", "2026-10-20", "15:00", "15:00", "before"),
               ("T", "2026-10-20", "23:00", "120m", "midnight"), ("T", "2026-10-20", "15:00", "25:00", "end time")]
        for title, day, start, end, msg in bad:
            with self.assertRaisesRegex(gcal.ToolError, msg):
                gcal.parse_new_event(title, day, start, end)

    def test_duplicates_and_overlaps(self):
        day = [ev("2026-10-20", "15:00", "16:00", "Team sync"), ev("2026-10-20", "16:30", "17:00", "Review"),
               ev("2026-10-20", None, None, "Holiday", "allday")]
        same, over = gcal.conflicts(day, "team SYNC", date(2026, 10, 20), 900, 930)
        self.assertEqual(len(same), 1)
        same, over = gcal.conflicts(day, "Call", date(2026, 10, 20), 960, 1000)
        self.assertEqual(([e["title"] for e in same], [e["title"] for e in over]), ([], ["Review"]))
        self.assertEqual(gcal.conflicts(day, "Call", date(2026, 10, 20), 960, 990), ([], []))  # touching is not overlapping

    def test_no_guest_ever(self):
        params = json.loads((REPO / "modules/calendar/skill/tools/calendar_create.json").read_text())["params"]
        self.assertFalse({"guests", "guest", "attendees", "invite", "to"} & set(params))
        url = gcal.create_url("me@example.com", "Team sync", date(2026, 10, 20), 900, 930, "Room 2", "Notes")
        self.assertNotIn("add=", url)
        self.assertIn("dates=20261020T150000%2F20261020T153000", url)

    def fake_browser(self, before: list[dict], after: list[dict], save: dict):
        """A Rastro stand-in: the day before and after saving, and the form's answer."""
        calls = {"goto": [], "agenda": 0}

        def agenda(address, start):
            calls["agenda"] += 1
            return before if calls["agenda"] == 1 else after
        patches = [mock.patch.object(gcal, "session", contextlib.nullcontext),
                   mock.patch.object(gcal, "fetch_agenda", side_effect=agenda),
                   mock.patch.object(gcal, "goto", side_effect=calls["goto"].append),
                   mock.patch.object(gcal, "js", return_value=save)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return calls

    def run_create(self, *args) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            gcal.create("personal", *args)
        return out.getvalue()

    def test_create_saves_and_confirms(self):
        made = ev("2026-10-20", "15:00", "15:45", "Team sync")
        calls = self.fake_browser([], [made], {"ok": True})
        out = self.run_create("Team sync", "2026-10-20 15:00", "45m", "Room 2", "", False)
        self.assertIn("Created \"Team sync\" on 2026-10-20 15:00-15:45", out)
        self.assertIn("Do not repeat", out)
        self.assertEqual(len(calls["goto"]), 1)
        self.assertIn("/r/eventedit?", calls["goto"][0])

    def test_duplicate_is_not_created(self):
        calls = self.fake_browser([ev("2026-10-20", "15:00", "16:00", "Team sync")], [], {"ok": True})
        out = self.run_create("Team sync", "2026-10-20 15:00", "16:00", "", "", False)
        self.assertIn("already in the calendar", out)
        self.assertEqual(calls["goto"], [])

    def test_overlap_needs_permission(self):
        busy = [ev("2026-10-20", "15:30", "16:30", "Review")]
        calls = self.fake_browser(busy, busy, {"ok": True})
        with self.assertRaisesRegex(gcal.ToolError, "overlaps: 15:30-16:30 Review"):
            self.run_create("Call", "2026-10-20 15:00", "16:00", "", "", False)
        self.assertEqual(calls["goto"], [])

    def test_overlap_allowed(self):
        busy = [ev("2026-10-20", "15:30", "16:30", "Review")]
        self.fake_browser(busy, busy + [ev("2026-10-20", "15:00", "16:00", "Call")], {"ok": True})
        self.assertIn("Created", self.run_create("Call", "2026-10-20 15:00", "16:00", "", "", True))

    def test_unconfirmed_save_says_do_not_retry(self):
        self.fake_browser([], [], {"ok": True})
        with self.assertRaisesRegex(gcal.ToolError, "may or may not exist"):
            self.run_create("Team sync", "2026-10-20 15:00", "45m", "", "", False)

    def test_a_dialog_after_saving_does_not_hide_an_event_that_was_created(self):
        made = ev("2026-10-20", "15:00", "15:45", "Team sync")
        self.fake_browser([], [made], {"question": "Event saved"})
        self.assertIn("Created \"Team sync\"", self.run_create("Team sync", "2026-10-20 15:00", "45m", "", "", False))

    def test_a_dialog_with_no_event_in_the_agenda_says_what_google_asked(self):
        self.fake_browser([], [], {"question": "Send invitations?"})
        with self.assertRaisesRegex(gcal.ToolError, r"Send invitations\?.*Nothing was created"):
            self.run_create("Team sync", "2026-10-20 15:00", "45m", "", "", False)

    def test_form_with_guests_is_not_saved(self):
        self.fake_browser([], [], {"error": "the form has guests"})
        with self.assertRaisesRegex(gcal.ToolError, "Nothing was created"):
            self.run_create("Team sync", "2026-10-20 15:00", "45m", "", "", False)

    def test_start_needs_date_and_time(self):
        with self.assertRaisesRegex(gcal.ToolError, "date and a time"):
            gcal.create("personal", "Team sync", "15:00", "45m", "", "", False)


class TestManifests(unittest.TestCase):
    def test_valid_and_at_most_five(self):
        tools = sorted((REPO / "modules/calendar/skill/tools").glob("*.json"))
        self.assertEqual(len(tools), 5)
        for f in tools:
            m = json.loads(f.read_text())
            self.assertEqual(tt.validate_manifest(m, "calendar", f.stem), [], f.name)
            self.assertIn('SCOPE = "__SCOPE__"', f.with_suffix(".py").read_text())


class TestInstall(unittest.TestCase):
    def test_install_and_check(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_CALENDAR_HOME=str(tmp / "cal"),
                   TANKA_GMAIL_HOME=str(tmp / "gmail"))
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "plans", "--strict")  # modules must fit the strict budget
        p = tanka("install", "calendar", "plans")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('SCOPE = "plans"', (tmp / "workspaces" / "plans" / ".claude/skills/calendar/tools/calendar_create.py").read_text())
        self.assertIn("5/15 tools loaded, 0 problem(s), 0 warning(s)", tanka("tools", "check", "plans").stdout)


class TestDelete(CalendarCase):
    def setUp(self):
        super().setUp()
        self.patch("REQUESTS", self.tmp / "calendar" / "requests")
        gcal.HOME.mkdir(parents=True, exist_ok=True)
        self.event = ev("2026-10-20", "15:00", "15:45", "Team sync")
        gcal.listing_file("personal").write_text(json.dumps({"address": "me@example.com", "events": {"1": self.event}}))

    def patch(self, name, value):
        old = getattr(gcal, name)
        setattr(gcal, name, value)
        self.addCleanup(setattr, gcal, name, old)

    def browser(self, *answers, after=None):
        replies = list(answers)
        calls = {"js": 0}
        def js(expr, timeout=60):
            calls["js"] += 1
            return replies.pop(0)
        for p in (mock.patch.object(gcal, "session", contextlib.nullcontext), mock.patch.object(gcal, "open_as"),
                  mock.patch.object(gcal, "js", side_effect=js),
                  mock.patch.object(gcal, "fetch_agenda", return_value=[self.event] if after is None else after)):
            p.start()
            self.addCleanup(p.stop)
        return calls

    def ask(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            gcal.delete_tool("personal", 1)
        return out.getvalue(), gcal.read_requests("personal")[0]

    def test_asking_touches_no_browser_and_deletes_nothing(self):
        out, r = self.ask()
        self.assertIn("NOTHING was deleted", out)
        self.assertEqual((r["status"], r["event"]["title"]), ("pending", "Team sync"))
        with self.assertRaisesRegex(gcal.ToolError, "already waiting"):
            gcal.delete_tool("personal", 1)

    def test_the_click_deletes_and_proves_it_is_gone(self):
        _, r = self.ask()
        calls = self.browser({"title": "Team sync", "recurrence": "", "guest_count": 0}, {"ok": True}, after=[])
        self.assertIn("Deleted \"Team sync\"", gcal.confirm_delete("personal", r["id"]))
        self.assertEqual(calls["js"], 2)
        self.assertEqual(gcal.read_requests("personal")[0]["status"], "deleted")

    def test_events_with_guests_or_that_repeat_are_never_deleted(self):
        for seen, why in (({"title": "Team sync", "recurrence": "Weekly", "guest_count": 0}, "repeats"),
                          ({"title": "Team sync", "recurrence": "", "guest_count": 3}, "guests")):
            gcal.write_requests("personal", [])
            _, r = self.ask()
            calls = self.browser(seen)
            with self.assertRaisesRegex(gcal.ToolError, why):
                gcal.confirm_delete("personal", r["id"])
            self.assertEqual(calls["js"], 1)  # the delete button was never pressed

    def test_a_question_from_google_or_a_surviving_event_is_not_reported_as_deleted(self):
        _, r = self.ask()
        self.browser({"title": "Team sync", "recurrence": "", "guest_count": 0}, {"question": "Send updates?"})
        with self.assertRaisesRegex(gcal.ToolError, "Send updates"):
            gcal.confirm_delete("personal", r["id"])
        self.assertEqual(gcal.read_requests("personal")[0]["status"], "pending")

    def test_keeping_it_opens_no_browser(self):
        _, r = self.ask()
        self.assertIn("nothing was deleted", gcal.dismiss_delete("personal", r["id"]))
        with self.assertRaisesRegex(gcal.ToolError, "No pending request"):
            gcal.dismiss_delete("personal", r["id"])


if __name__ == "__main__":
    unittest.main()
