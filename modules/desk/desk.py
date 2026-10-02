"""Desk for Tanka: the user's day, kept by their assistant.

- **Cards.** A **check** card holds the pending items of one topic (a project or any subject the
  user names); a **reminder** is one thing at one time. The assistant adds and ticks them with
  desk_card when the user asks, or when it finds something left pending with its evidence; it
  never deletes. The user ticks, snoozes and archives them on the page (`tanka ui`).
- **The daemon's poll** (`cli.py events`, no model) fires the reminders that come due, with one
  desktop notification each, and once a day says the brief in the page's chat.

A workspace's cards live in HOME/cards/<workspace>.json, outside every workspace, so the assistant
reaches them only through its tools. Its settings are `.claude/desk.json`, which it cannot write.

Standard library only.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
import re
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_common as tc  # noqa: E402
import tanka_kit as kit  # noqa: E402
from tanka_kit import (ToolError, clip, day_time, hhmm, now, read_json, run, safe_id, scrub,  # noqa: E402,F401
                       today_start, write_json)

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_DESK_HOME", kit.SHARED / "desk"))
CARD_KINDS = ("check", "reminder")
CARD_CHARS, TOPIC_CHARS = 200, 40
MAX_AHEAD_DAYS = 60
GENERAL = "general"
ASSISTANT = "assistant"     # who added a card through desk_card; "codepanion" in cards from before the desk
DEFAULT_BRIEF = "09:00"     # the daily brief in the chat; "" turns it off
DEFAULT_STALE_DAYS = 3      # an open item this old is called out in the brief
DEFAULT_FOUND_PER_HOUR = 3  # cards the assistant adds on its own (with evidence) per hour; what the user asks for is not rationed
BRIEF_LATE_HOURS = 12       # a machine that wakes later than this after the brief time skips the day


def by_assistant(x: dict) -> bool:
    return x.get("by") in (ASSISTANT, "codepanion")


# ---------------------------------------------------------------- settings

def config_file(ws: Path) -> Path:
    return ws / ".claude" / "desk.json"


def installed(ws: Path) -> bool:
    return config_file(ws).is_file() or (ws / ".claude" / "skills" / "desk").is_dir()


def load_config(ws: Path) -> dict:
    raw = read_json(config_file(ws), {})
    return {"brief": str(raw.get("brief", DEFAULT_BRIEF) or ""), "stale_days": raw.get("stale_days", DEFAULT_STALE_DAYS),
            "found_per_hour": int(raw.get("found_per_hour", DEFAULT_FOUND_PER_HOUR))}


def projects_of(scope: str) -> list[str]:
    """The projects the codepanion watches for this workspace, when it is installed: they are topics."""
    try:
        sys.path.insert(0, str(REPO / "modules" / "codepanion"))
        import codepanion
    except ImportError:
        return []
    return codepanion.projects_of(scope)


def real(path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


# ---------------------------------------------------------------- cards: checks and reminders

# Both are written by the user (the page) and by the assistant (desk_card), never deleted by the
# assistant: it can add and tick, the user archives.

def cards_file(scope: str) -> Path:
    return HOME / "cards" / f"{safe_id(scope)}.json"


@contextlib.contextmanager
def card_store(scope: str, write: bool = False):
    """The scope's cards under an exclusive lock: the page, the tools and the daemon all write here."""
    f = cards_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(f, {"cards": []})
        yield data["cards"]
        if write:
            write_json(f, data)


def card_scopes() -> list[str]:
    """Every workspace with the desk."""
    return [s for s in kit.scopes() if installed(kit.ws_dir(s))]


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def topic_of(scope: str, topic: str | None) -> tuple[str, str]:
    """(label, project path or ''): a watched project's folder name or path becomes that project."""
    raw = re.sub(r"\s+", " ", str(topic or "")).strip()
    if not raw:
        return GENERAL, ""
    for p in projects_of(scope):
        if raw.lower() in (Path(p).name.lower(), p.lower(), real(raw).lower()):
            return Path(p).name, p
    if len(raw) > TOPIC_CHARS:
        raise ToolError(f"A topic is a short name (at most {TOPIC_CHARS} characters), like a project or a subject.")
    return raw, ""


def parse_at(at: str, t: float) -> float:
    """'HH:MM' (today, or tomorrow if already past) or 'YYYY-MM-DD HH:MM', local time."""
    at = str(at or "").strip()
    base = datetime.fromtimestamp(t)
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", at):
            h, m = map(int, at.split(":"))
            when = base.replace(hour=h, minute=m, second=0, microsecond=0)
            if when.timestamp() < t - 60:
                when += timedelta(days=1)
        else:
            when = datetime.strptime(at, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ToolError(f"'{at}' is not a time. Use HH:MM for today (e.g. 17:30) or YYYY-MM-DD HH:MM.") from None
    if when.timestamp() < t - 60:
        raise ToolError(f"{when:%Y-%m-%d %H:%M} is already past (now {base:%Y-%m-%d %H:%M}). Ask the user when.")
    if when.timestamp() > t + MAX_AHEAD_DAYS * 86400:
        raise ToolError(f"A reminder is at most {MAX_AHEAD_DAYS} days ahead. Ask the user for a nearer date.")
    return when.timestamp()


def found_last_hour(cards: list[dict], t: float) -> int:
    """Cards the assistant added on its own (they carry evidence) in the last hour."""
    things = [c for c in cards if c["kind"] == "reminder"] + [i for c in cards if c["kind"] == "check" for i in c["items"]]
    return sum(by_assistant(x) and x.get("evidence") and t - x.get("t", 0) < 3600 for x in things)


def add_card(scope: str, kind: str, topic: str | None, text: str, at: str | None = None,
             by: str = "user", evidence: str = "", project: str = "") -> dict:
    """Add a check item (to its topic's card, made if missing) or a reminder. Adding the same open
    item or reminder twice returns the existing one."""
    if kind not in CARD_KINDS:
        raise ToolError(f"kind is {' or '.join(CARD_KINDS)}.")
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ToolError("Say what the card is about: text cannot be empty.")
    if len(text) > CARD_CHARS:
        raise ToolError(f"Keep it under {CARD_CHARS} characters: one pending thing per card or item.")
    label, found_project = topic_of(scope, topic)
    project = project or found_project
    if kind == "check" and at:
        raise ToolError("A check item has no time; for something at a time use kind reminder.")
    if kind == "reminder" and not at:
        raise ToolError("A reminder needs at: HH:MM for today or YYYY-MM-DD HH:MM. If the user did not say when, ask.")
    t = now()
    when = parse_at(at, t) if kind == "reminder" else None
    meta = {"t": round(t, 3), "by": by}
    if evidence:
        meta["evidence"] = clip(scrub(evidence), 300)
    with card_store(scope, write=True) as cards:
        if by == ASSISTANT and evidence:  # found on its own; what the user asks for is not rationed
            budget = load_config(kit.ws_dir(scope))["found_per_hour"]
            if found_last_hour(cards, t) >= budget:
                raise ToolError("The budget for this hour is spent. Do not add it now; say nothing.")
        live = [c for c in cards if not c.get("archived") and c["topic"].lower() == label.lower()]
        if kind == "reminder":
            same = [c for c in live if c["kind"] == "reminder" and not c.get("done_at") and norm_text(c["text"]) == norm_text(text)]
            if same:
                return dict(same[0], existed=True)
            card = {"id": "r-" + secrets.token_hex(3), "kind": "reminder", "topic": label, "project": project,
                    "text": scrub(text), "at": when, "fired_at": None, "done_at": None, **meta}
            cards.append(card)
            return card
        card = next((c for c in live if c["kind"] == "check"), None)
        if card is None:
            card = {"id": "c-" + secrets.token_hex(3), "kind": "check", "topic": label, "project": project, "items": [], **meta}
            cards.append(card)
        same = [i for i in card["items"] if not i.get("done_at") and norm_text(i["text"]) == norm_text(text)]
        if same:
            return dict(same[0], card=card["id"], topic=label, existed=True)
        item = {"id": "i-" + secrets.token_hex(3), "text": scrub(text), "done_at": None, **meta}
        card["items"].append(item)
        return dict(item, card=card["id"], topic=label)


def find_open(cards: list[dict], kind: str, topic: str | None, text: str) -> tuple[dict, dict | None]:
    """The open item or reminder named by its id or by its exact text (and topic, if given)."""
    key = str(text or "").strip()
    found = []
    for c in cards:
        if c.get("archived") or c["kind"] != kind or (topic and c["topic"].lower() != topic.lower()):
            continue
        if kind == "reminder":
            if not c.get("done_at") and (c["id"] == key or norm_text(c["text"]) == norm_text(key)):
                found.append((c, None))
        else:
            found += [(c, i) for i in c["items"] if not i.get("done_at") and (i["id"] == key or norm_text(i["text"]) == norm_text(key))]
    if len(found) != 1:
        what = "No open" if not found else f"{len(found)} open"
        raise ToolError(f"{what} {kind} matches '{clip(key, 60)}'. Use desk_pending and pass the id it shows.")
    return found[0]


def close_card(scope: str, kind: str, topic: str | None, text: str, by: str = "user") -> dict:
    """Tick an open check item, or mark a reminder done."""
    label = topic_of(scope, topic)[0] if topic else None
    with card_store(scope, write=True) as cards:
        card, item = find_open(cards, kind, label, text)
        target = item or card
        target["done_at"], target["done_by"] = round(now(), 3), by
        return dict(target, topic=card["topic"], card=card["id"])


def set_item(scope: str, item_id: str, done: bool) -> dict:
    """The page's checkbox: tick or untick an item by id."""
    with card_store(scope, write=True) as cards:
        for c in cards:
            for i in c.get("items", []):
                if i["id"] == item_id:
                    i["done_at"], i["done_by"] = (round(now(), 3), "user") if done else (None, None)
                    return i
    raise ToolError(f"No item {item_id}.")


def update_card(scope: str, card_id: str, action: str, minutes: int = 0) -> dict:
    """The page's buttons on a card: done (a reminder), snooze (a reminder), archive (any card)."""
    if action not in ("done", "snooze", "archive"):
        raise ToolError("action is done, snooze or archive.")
    with card_store(scope, write=True) as cards:
        for c in cards:
            if c["id"] != card_id:
                continue
            if action == "archive":
                c["archived"] = round(now(), 3)
            elif c["kind"] != "reminder":
                raise ToolError("Only a reminder can be done or snoozed; tick a check card's items instead.")
            elif action == "done":
                c["done_at"], c["done_by"] = round(now(), 3), "user"
            else:
                if not 1 <= minutes <= 24 * 60:
                    raise ToolError("Snooze between 1 minute and 24 hours.")
                c["at"], c["fired_at"] = now() + minutes * 60, None
            return c
    raise ToolError(f"No card {card_id}.")


def pending(scope: str) -> dict:
    """What the page and desk_pending show: open reminders by time, and each topic's open
    items plus the ones ticked today."""
    t = now()
    day = today_start(t)
    with card_store(scope) as cards:
        live = [c for c in cards if not c.get("archived")]
    reminders = sorted((c for c in live if c["kind"] == "reminder" and (not c.get("done_at") or c["done_at"] >= day)),
                       key=lambda c: (bool(c.get("done_at")), c["at"]))
    checks = []
    for c in live:
        if c["kind"] != "check":
            continue
        items = [i for i in c["items"] if not i.get("done_at") or i["done_at"] >= day]
        checks.append(dict(c, items=items, open=sum(not i.get("done_at") for i in items)))
    checks.sort(key=lambda c: (c["open"] == 0, c["topic"].lower()))
    return {"now": t, "reminders": [dict(r, due=r["at"] <= t and not r.get("done_at")) for r in reminders], "checks": checks}


def due_count() -> int:
    """Reminders whose time has come and that the user has not marked done, in every workspace."""
    t, n = now(), 0
    for f in (HOME / "cards").glob("*.json") if (HOME / "cards").is_dir() else []:
        n += sum(c["kind"] == "reminder" and not c.get("archived") and not c.get("done_at") and c["at"] <= t
                 for c in read_json(f, {"cards": []})["cards"])
    return n


def state_file() -> Path:
    return HOME / "state.json"


def fire_reminders() -> int:
    """Run by the daemon's poll: a desktop notification once per due reminder. No model."""
    fired, t = 0, now()
    for scope in card_scopes():
        if not cards_file(scope).is_file():
            continue
        with card_store(scope, write=True) as cards:
            for c in cards:
                if c["kind"] == "reminder" and not c.get("archived") and not c.get("done_at") and not c.get("fired_at") and c["at"] <= t:
                    c["fired_at"] = round(t, 3)
                    fired += 1
                    kit.chat_event(scope, {"t": c["fired_at"], "who": "reminder", "id": c["id"]})
                    if not os.environ.get("TANKA_CODEPANION_BACKTEST"):
                        tc.desktop_notify(f"⏰ {c['topic']} · {scope}", c["text"])
    return fired


def brief_time(hhmm_: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d\d):(\d\d)", hhmm_.strip())
    return (int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None


def brief_of(cards: list[dict], t: float, stale_days: int) -> dict:
    """The day's brief: the open reminders due by tonight (overdue ones too), the open check items
    per topic, and the items open stale_days or more. Ids only: the chat shows each one as it is now."""
    tonight = today_start(t) + 86400
    live = [c for c in cards if not c.get("archived")]
    reminders = sorted((c for c in live if c["kind"] == "reminder" and not c.get("done_at") and c["at"] < tonight),
                       key=lambda c: c["at"])
    items = [(c, i) for c in live if c["kind"] == "check" for i in c.get("items", []) if not i.get("done_at")]
    topics: dict[str, int] = {}
    for c, _ in items:
        topics[c["topic"]] = topics.get(c["topic"], 0) + 1
    stale = [i["id"] for _, i in sorted(items, key=lambda x: x[1].get("t", t)) if t - i.get("t", t) >= stale_days * 86400]
    return {"reminders": [c["id"] for c in reminders], "topics": topics, "stale": stale}


def daily_brief() -> int:
    """Run by the daemon's poll: once a day, at the workspace's brief time, the desk says in the
    chat what the day holds and what has stalled. No model; nothing is said on a day with nothing open."""
    t, sent = now(), 0
    st = read_json(state_file(), {})
    said = st.setdefault("brief", {})
    today = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    for scope in card_scopes():
        if said.get(scope) == today or not cards_file(scope).is_file():
            continue
        try:
            cfg = load_config(kit.ws_dir(scope))
        except (ToolError, ValueError, TypeError):
            continue
        hm_ = brief_time(cfg["brief"]) if cfg["brief"] else None
        days = cfg["stale_days"] if isinstance(cfg["stale_days"], int) and cfg["stale_days"] >= 1 else DEFAULT_STALE_DAYS
        if hm_ is None:
            continue
        at = datetime.fromtimestamp(t).replace(hour=hm_[0], minute=hm_[1], second=0, microsecond=0).timestamp()
        if t < at:
            continue
        said[scope] = today
        if t - at > BRIEF_LATE_HOURS * 3600:
            continue
        with card_store(scope) as cards:
            b = brief_of(cards, t, days)
        if not b["reminders"] and not b["topics"]:
            continue
        kit.chat_event(scope, {"t": round(t, 3), "who": "brief", **b})
        sent += 1
        if not os.environ.get("TANKA_CODEPANION_BACKTEST"):
            n_items = sum(b["topics"].values())
            tc.desktop_notify(f"Your day · {scope}", f"{len(b['reminders'])} reminder(s), {n_items} to do"
                              + (f", {len(b['stale'])} stalled" if b["stale"] else ""))
    write_json(state_file(), st)
    return sent


def rel(at: float, t: float) -> str:
    mins = -(-(at - t) // 60)  # ceiling: a reminder 20 s away is 'in 1 min', not 'in 0'
    mins = int(mins)
    if at <= t:
        return f"due since {hhmm(at)}" if -mins < 24 * 60 else f"due since {day_time(at)}"
    if mins < 60:
        return f"in {mins} min"
    return f"in {mins // 60} h {mins % 60:02d} min" if mins < 24 * 60 else f"in {mins // 1440} day(s)"


def list_pending(scope: str, topic: str | None) -> None:
    p, t = pending(scope), now()
    label = topic_of(scope, topic)[0] if topic else None
    rem = [r for r in p["reminders"] if not label or r["topic"].lower() == label.lower()]
    checks = [c for c in p["checks"] if not label or c["topic"].lower() == label.lower()]
    items = [(c, i) for c in checks for i in c["items"]]
    n_open = sum(not i.get("done_at") for _, i in items)
    print(f"Now {datetime.fromtimestamp(t):%Y-%m-%d %H:%M (%a)}. {sum(not r.get('done_at') for r in rem)} open reminder(s), "
          f"{n_open} open item(s) in {len(checks)} topic(s)" + (f" for '{label}'" if label else "") + ":")
    for r in rem:
        state = f"done {hhmm(r['done_at'])}" if r.get("done_at") else f"{day_time(r['at'])} ({rel(r['at'], t)})"
        print(f"{r['id']} reminder [{r['topic']}] {state}: {r['text']}" + ("  (added by you)" if by_assistant(r) else ""))
    for c, i in items:
        state = f"done {hhmm(i['done_at'])}" if i.get("done_at") else "open"
        print(f"{i['id']} check [{c['topic']}] {state}: {i['text']}" + ("  (added by you)" if by_assistant(i) else ""))
    if not rem and not items:
        print("Nothing pending." + (" Topics with cards: " + ", ".join(sorted({c['topic'] for c in p['checks']})) if label and p["checks"] else ""))


def card_tool(scope: str, kind: str, text: str, topic: str | None, at: str | None, done: bool, evidence: str) -> None:
    if done:
        x = close_card(scope, kind, topic, text, by=ASSISTANT)
        what = "Ticked" if kind == "check" else "Marked done"
        print(f"{what} {x['id']} [{x['topic']}]: {x['text']}. Tell the user in one line; do not call it again.")
        return
    x = add_card(scope, kind, topic, text, at, by=ASSISTANT, evidence=evidence)
    if x.get("existed"):
        print(f"Already there: {x['id']} [{x['topic']}] {x['text']}. Nothing added; do not call it again.")
    elif kind == "reminder":
        print(f"Reminder {x['id']} [{x['topic']}] for {day_time(x['at'])} ({rel(x['at'], now())}): {x['text']}. "
              "The user gets a desktop notification then, and sees it in the page. Do not call it again.")
    else:
        print(f"Added {x['id']} to the [{x['topic']}] check card: {x['text']}. It shows in the page. Do not call it again.")


# ---------------------------------------------------------------- moving in from the codepanion

def migrate() -> list[str]:
    """Cards, the chat and the brief used to live in the codepanion module. Move them once."""
    done = []
    old = Path(os.environ.get("TANKA_CODEPANION_HOME", kit.SHARED / "codepanion"))
    if kit.adopt(old / "cards", HOME / "cards"):
        done.append(f"cards → {HOME / 'cards'}")
    if kit.adopt(old / "chat", kit.PAGE_HOME / "chat"):
        done.append(f"chat → {kit.PAGE_HOME / 'chat'}")
    st = read_json(old / "state.json", {})
    if st.get("brief") and not state_file().is_file():
        write_json(state_file(), {"brief": st["brief"]})
        done.append("brief state")
    for scope in kit.scopes():
        ws = kit.ws_dir(scope)
        cp = ws / ".claude" / "codepanion.json"
        if cp.is_file() and not config_file(ws).is_file():
            raw = read_json(cp, {})
            write_json(config_file(ws), {k: raw[k] for k in ("brief", "stale_days") if k in raw})
            done.append(f"{scope}: brief settings → {config_file(ws)}")
    return done
