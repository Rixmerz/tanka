"""WhatsApp on the page (docs/page.md): a People tab with this workspace's roles and contacts, and the
record the assistant keeps of each person. The user changes roles, contacts and a role's flags by hand
here; nothing on this tab opens WhatsApp, so it never reads or sends a message."""
from __future__ import annotations

import functools
import re
from collections import Counter
from pathlib import Path

import tanka_kit as kit
import wa

NAME_CHARS = 60
FLAGS = ("read", "reply", "auto_reply")
NOTE_DATE = re.compile(r"^\s*(?:[-*]\s*)?(\d{4}-\d{2}-\d{2})\b", re.M)
AUTO_REPLY_WARNING = "the assistant will answer these people without asking you"


def page_errors(fn):
    """A refusal from wa reaches the page as a 400 with its sentence, like any other refusal."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except wa.ToolError as e:
            raise kit.ToolError(str(e)) from None
    return wrapper


def own_roles(roles: dict, scope: str) -> dict:
    return {name: r for name, r in roles.items() if isinstance(r, dict) and r.get("workspace") == scope}


def installed(ws: Path) -> bool:
    """The skill is installed, or a role names this workspace (a hand-made WhatsApp skill gets the tab too)."""
    if (ws / ".claude" / "skills" / "whatsapp").is_dir():
        return True
    try:
        # A role names a workspace by its Tanka name, which may be a symlink to a folder with another name.
        here = Path(ws).resolve()
        return any(kit.ws_dir(str(r.get("workspace") or "")) == here
                   for r in wa.registry()["roles"].values() if isinstance(r, dict) and r.get("workspace"))
    except Exception:  # noqa: BLE001 - a missing or broken contacts.json only means "not set up"
        return False


def scoped(scope: str) -> tuple[dict, dict]:
    """wa.scope, after a read that words a missing or broken file for the page rather than for the model."""
    wa.registry_raw()
    return wa.scope(scope)


def state(scope: str, ws: Path) -> dict:
    """This workspace's roles with their flags and how many contacts hold each; the people come with GET people."""
    try:
        roles, people = scoped(scope)
    except Exception as e:  # noqa: BLE001 - must not take /api/state down
        return {"roles": [], "registry": str(wa.REGISTRY), "problem": str(e)}
    counts = Counter(c.get("role") for c in people.values())
    return {"registry": str(wa.REGISTRY), "problem": None,
            "roles": [{"name": name, **{f: bool(r.get(f)) for f in FLAGS}, "instructions": bool(r.get("instructions")),
                       "count": counts.get(name, 0)} for name, r in roles.items()]}


@page_errors
def people(scope: str, ws: Path, q: dict) -> dict:
    """This workspace's contacts with the header of their record, and the header keys, most common first."""
    _, mine = scoped(scope)
    rows, keys = [], Counter()
    for num, c in sorted(mine.items(), key=lambda kv: str(kv[1].get("name", "")).lower()):
        fields, notes = wa.person_record(num, ws)
        dates = NOTE_DATE.findall(notes)
        keys.update(k for k in fields if k != "name")
        rows.append({"number": "+" + num, "name": str(c.get("name") or ""), "role": c["role"], "fields": fields,
                     "notes": len(dates), "last_note": max(dates) if dates else None})
    return {"people": rows, "fields": [k for k, _ in sorted(keys.items(), key=lambda kv: (-kv[1], kv[0]))]}


@page_errors
def person(scope: str, ws: Path, q: dict) -> dict:
    num = wa.digits(q.get("number"))
    _, mine = scoped(scope)
    if num not in mine:
        raise kit.ToolError(f"+{num} is not one of this workspace's contacts.")
    fields, notes = wa.person_record(num, ws)
    return {"number": "+" + num, "name": str(mine[num].get("name") or ""), "role": mine[num]["role"],
            "fields": fields, "notes": notes, "path": f"{wa.PEOPLE_DIR}/{num}.md"}


# ---------------------------------------------------------------- what the user changes by hand

def clean_name(name) -> str:
    name = " ".join(str(name or "").split())
    if not 1 <= len(name) <= NAME_CHARS:
        raise kit.ToolError(f"A name has 1 to {NAME_CHARS} characters.")
    return name


def role_of(data: dict, scope: str, role) -> str:
    role = str(role or "")
    if role not in own_roles(data["roles"], scope):
        raise kit.ToolError(f"\"{role}\" is not a role of this workspace.")
    return role


def own_contact(data: dict, scope: str, number) -> list[str]:
    """The stored keys of a contact of this workspace, or a refusal that says nothing about anyone else."""
    num = wa.digits(number)
    keys = [k for k in wa.contact_keys(data["contacts"], num) if isinstance(data["contacts"][k], dict)]
    if not keys or data["contacts"][keys[-1]].get("role") not in own_roles(data["roles"], scope):
        raise kit.ToolError(f"+{num} is not one of this workspace's contacts.")
    return keys


@page_errors
def set_role(scope: str, ws: Path, body: dict) -> dict:
    def change(data):
        role = role_of(data, scope, body.get("role"))
        for k in own_contact(data, scope, body.get("number")):
            data["contacts"][k]["role"] = role
        return {"ok": True}
    return wa.registry_update(change)


@page_errors
def add(scope: str, ws: Path, body: dict) -> dict:
    num = wa.digits(body.get("number"))
    if not 8 <= len(num) <= 15:
        raise kit.ToolError("A number has 8 to 15 digits, with the country code.")
    name = clean_name(body.get("name"))

    def change(data):
        role = role_of(data, scope, body.get("role"))
        keys = wa.contact_keys(data["contacts"], num)
        if not keys:
            data["contacts"]["+" + num] = {"name": name, "role": role}
            return {"ok": True, "number": "+" + num}
        c = data["contacts"][keys[-1]]
        held = c.get("role") if isinstance(c, dict) else None
        if held in own_roles(data["roles"], scope):
            raise kit.ToolError(f"+{num} is already a contact of this workspace ({c.get('name') or 'no name'}, {held}).")
        if held in data["roles"]:
            raise kit.ToolError(f"+{num} belongs to another workspace; it was not changed.")
        # In the file with no role, or a role that no longer exists: it is free, so it takes this one.
        for k in keys:
            if isinstance(data["contacts"][k], dict):
                data["contacts"][k].update(name=name, role=role)
            else:
                data["contacts"][k] = {"name": name, "role": role}
        return {"ok": True, "number": "+" + num}
    return wa.registry_update(change)


@page_errors
def rename(scope: str, ws: Path, body: dict) -> dict:
    name = clean_name(body.get("name"))

    def change(data):
        for k in own_contact(data, scope, body.get("number")):
            data["contacts"][k]["name"] = name
        return {"ok": True}
    return wa.registry_update(change)


@page_errors
def remove(scope: str, ws: Path, body: dict) -> dict:
    def change(data):
        for k in own_contact(data, scope, body.get("number")):
            del data["contacts"][k]
        return {"ok": True}
    return wa.registry_update(change)


@page_errors
def role_flags(scope: str, ws: Path, body: dict) -> dict:
    """read, reply and auto_reply of one role; only the flags present in the body change."""
    for f in FLAGS:
        if f in body and not isinstance(body[f], bool):
            raise kit.ToolError(f"{f} must be true or false.")

    def change(data):
        name = role_of(data, scope, body.get("role"))
        role = data["roles"][name]
        # Truthy as wa.py reads them; only the flags sent are written, so a hand-typed "read": 1 is kept.
        new = {f: body[f] for f in FLAGS if f in body}
        if not new.get("reply", bool(role.get("reply"))):
            new["auto_reply"] = False
        if new.get("auto_reply") and not role.get("auto_reply") and body.get("confirm") is not True:
            raise kit.ToolError(f"Turning auto-reply on for {name} needs confirming: {AUTO_REPLY_WARNING}.")
        role.update(new)
        return {"ok": True, "role": name, **{f: bool(role.get(f)) for f in FLAGS}}
    return wa.registry_update(change)


def hint(scope: str, ws: Path) -> str:
    return ""  # the whatsapp skill describes its own tools


def health() -> list[dict]:
    """How many contacts and roles contacts.json holds. Reads the file only; the browser is never touched."""
    label = "WhatsApp contacts"
    if not wa.REGISTRY.is_file():
        return [{"label": label, "ok": None, "text": "no contacts.json"}]
    try:
        reg = wa.registry()
    except Exception as e:  # noqa: BLE001
        return [{"label": label, "ok": False, "text": str(e)}]
    return [{"label": label, "ok": None,
             "text": f"{len(reg['contacts'])} contact(s), {len(reg['roles'])} role(s) in {wa.REGISTRY}"}]


ACTIONS = {"set_role": set_role, "add": add, "rename": rename, "remove": remove, "role_flags": role_flags}
GETS = {"people": people, "person": person}
