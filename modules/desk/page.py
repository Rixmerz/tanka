"""The desk on the page (docs/page.md): Your day beside the chat, and the reminders that fired and
the daily brief in it, each shown as things stand now."""
from __future__ import annotations

import json
import time
from pathlib import Path

import desk as d
import tanka_kit as kit

QUOTE_CHARS = 300


def installed(ws: Path) -> bool:
    return d.installed(ws)


def cards(scope: str) -> list[dict]:
    with d.card_store(scope) as cs:
        return list(cs)


def state(scope: str, ws: Path) -> dict:
    return {"pending": d.pending(scope)}


def reminder_state(card: dict | None, fired: float, t: float) -> dict:
    if card is None:
        return {"state": "gone"}
    out = {"text": card.get("text", ""), "topic": card.get("topic"), "at": card.get("at")}
    if card.get("archived"):
        return dict(out, state="gone")
    if card.get("done_at"):
        return dict(out, state="done", done_at=card["done_at"])
    if not card.get("fired_at") or card["fired_at"] > fired + 1:
        return dict(out, state="snoozed")
    return dict(out, state="due" if card.get("at", t) <= t else "snoozed")


def brief_state(m: dict, by_id: dict, items: dict) -> dict:
    """A daily brief as things stand now: each reminder and stalled item with its text and whether it is done."""
    def item(x: dict, cd: dict) -> dict:
        return {"id": x["id"], "text": x.get("text", ""), "topic": cd.get("topic"), "at": x.get("at"),
                "done": bool(x.get("done_at")), "gone": bool(cd.get("archived"))}
    rem = [item(by_id[i], by_id[i]) if i in by_id else {"id": i, "gone": True} for i in m.get("reminders", [])]
    stale = [item(*items[i]) if i in items else {"id": i, "gone": True} for i in m.get("stale", [])]
    return dict(m, reminders=rem, stale=stale)


def stream(scope: str, ws: Path) -> list[dict]:
    """The reminders that fired and the briefs, from the chat file, each as it stands now."""
    t, everything = time.time(), cards(scope)
    by_id = {cd["id"]: cd for cd in everything}
    items = {i["id"]: (i, cd) for cd in everything for i in cd.get("items", [])}
    out, logged = [], set()
    for m in kit.read_jsonl(kit.chat_file(scope)):
        if m.get("who") == "reminder":
            logged.add(m.get("id"))
            out.append(dict(m, **reminder_state(by_id.get(m.get("id")), m["t"], t)))
        elif m.get("who") == "brief":
            out.append(brief_state(m, by_id, items))
    # Reminders that fired before the chat kept a record of it.
    out += [dict({"t": cd["fired_at"], "who": "reminder", "id": cd["id"]}, **reminder_state(cd, cd["fired_at"], t))
            for cd in everything if cd.get("kind") == "reminder" and cd.get("fired_at") and cd["id"] not in logged]
    return out


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the cards it added and the ones it ticked."""
    everything = cards(scope)
    out = []
    for t0, t1 in spans:
        chips = []
        for cd in everything:
            entries = [(cd, cd["topic"])] if cd["kind"] == "reminder" else [(i, cd["topic"]) for i in cd.get("items", [])]
            for x, topic in entries:
                base = {"id": x["id"], "text": x.get("text", ""), "topic": topic, "gone": bool(cd.get("archived"))}
                if t0 < x.get("t", 0) <= t1:
                    chips.append(dict(base, type=cd["kind"], at=x.get("at")))
                if x.get("done_at") and t0 < x["done_at"] <= t1 and x.get("done_by") != "user":
                    chips.append(dict(base, type="done"))
        out.append(chips)
    return out


def quote(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS - 1] + "…"


def context(scope: str, ws: Path, since: float, until: float) -> list[tuple[float, str]]:
    """The reminders and briefs shown in the chat between the user's last two messages, with ids."""
    out = []
    for m in stream(scope, ws):
        if not since < m["t"] < until:
            continue
        if m["who"] == "reminder":
            out.append((m["t"], f"reminder {m.get('id')} \"{quote(m.get('text', ''))}\" ({m.get('state')})"))
        else:
            rem = "; ".join(f"{r['id']} \"{quote(r.get('text', ''))}\"" for r in m.get("reminders", []))
            stale = "; ".join(f"{i['id']} \"{quote(i.get('text', ''))}\"" for i in m.get("stale", []))
            out.append((m["t"], f"the daily brief: reminders today: {rem or 'none'}; open items per topic: "
                                f"{json.dumps(m.get('topics', {}), ensure_ascii=False)}; stalled: {stale or 'none'}"))
    return out


def hint(scope: str, ws: Path) -> str:
    return ("You keep their cards, they do not. When they ask to be reminded of something, call desk_card with kind "
            "reminder and the time; when they mention something to do with no time, kind check, with the project or "
            "subject as topic; when they say they finished something, tick it with desk_card done true. To see what is "
            "pending, desk_pending. If a reminder has no time, ask for it. Never offer to archive or delete a card: the "
            "user does that on the page. To tick an item the context names, pass its id as text to desk_card with done true.")


def health() -> list[dict]:
    n = d.due_count()
    return [{"label": "Reminders due", "ok": None if not n else False, "text": str(n)}]


ACTIONS = {
    "item": lambda scope, ws, b: d.set_item(scope, str(b.get("id", "")), bool(b.get("done"))),
    "card": lambda scope, ws, b: d.update_card(scope, str(b.get("id", "")), str(b.get("action", "")), int(b.get("minutes") or 0)),
}
