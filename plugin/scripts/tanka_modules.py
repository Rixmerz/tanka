#!/usr/bin/env python3
"""Tanka modules: reusable skills that ship with the repository and install into any workspace.

A module is a directory under `modules/<name>/` (rules: docs/module-rules.md):

    module.json   {"name", "description", "requires": [programs on PATH]}
    README.md     what it does, setup, configuration table
    skill/        SKILL.md + tools/: copied into a workspace by `install`
    cli.py        optional: `tanka <name> <command>`, `post-install <workspace> <scope>`,
                  and `post-remove <workspace> <scope>`

`install` copies `skill/` to `<workspace>/.claude/skills/<name>/`, replacing
`__SCOPE__` in every text file with the scope (the workspace's name unless
given), marks the copy with `.module.json`, and refuses when the workspace
would go over the tool budget. `uninstall` removes that skill and nothing else:
the module's settings in the workspace and its data in ~/.tanka/shared stay.

The page's Workspace tab (docs/page.md) reads `skills()` and `report()` and
turns a workspace's own skills on and off with `set_enabled()`.

Standard library only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_tools as tt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MODULES = REPO / "modules"
NAME_RE = re.compile(r"[a-z][a-z0-9-]*")
SCOPE_RE = re.compile(r"[A-Za-z0-9_-]+")
PLACEHOLDER = "__SCOPE__"
TEXT_SUFFIXES = {".md", ".json", ".py", ".sh", ".txt"}
MARKER = ".module.json"  # in an installed skill's directory: which module put it there, and when
CLI_COMMAND_RE = re.compile(r"\bcmd\s*(?:==\s*[\"']([a-z][a-z-]*)[\"']|in\s*\(([^)]*)\))")
NOT_SETUP = {"post-install", "post-remove", "events", "help"}
CLI_TIMEOUT = 120  # a module's hook, when its output is captured (the page waits for it)


def load(name: str) -> dict:
    """The module's manifest, with `_dir`; raises ValueError saying what is wrong."""
    mdir = MODULES / name
    if not NAME_RE.fullmatch(name) or not (mdir / "module.json").is_file():
        known = ", ".join(m["name"] for m in available()) or "none"
        raise ValueError(f"no module named '{name}' (available: {known})")
    m = json.loads((mdir / "module.json").read_text(encoding="utf-8"))
    return dict(m, _dir=mdir)


def available() -> list[dict]:
    out = []
    for d in sorted(p for p in MODULES.iterdir() if (p / "module.json").is_file()) if MODULES.is_dir() else []:
        try:
            out.append(dict(json.loads((d / "module.json").read_text(encoding="utf-8")), _dir=d))
        except json.JSONDecodeError:
            continue
    return out


def missing_programs(m: dict) -> list[str]:
    return [p for p in m.get("requires", []) if not shutil.which(p)]


def copy_skill(m: dict, ws: Path, scope: str) -> Path:
    dest = tt.skills_dir(ws) / m["name"]
    shutil.copytree(m["_dir"] / "skill", dest)
    for f in dest.rglob("*"):
        if f.is_file() and f.suffix in TEXT_SUFFIXES:
            text = f.read_text(encoding="utf-8")
            if PLACEHOLDER in text:
                f.write_text(text.replace(PLACEHOLDER, scope), encoding="utf-8")
    return dest


def check(name: str) -> list[str]:
    """Problems with a module; empty when it would install cleanly into an empty workspace."""
    try:
        m = load(name)
    except (ValueError, json.JSONDecodeError) as e:
        return [str(e)]
    problems = []
    for key in ("name", "description"):
        if not m.get(key):
            problems.append(f"module.json needs a '{key}'")
    if m.get("name") != name:
        problems.append(f"module.json name must be '{name}' (the directory name)")
    for need in m.get("needs", []):
        if need == name or not (MODULES / str(need) / "module.json").is_file():
            problems.append(f"needs names '{need}', which is not another module in {MODULES}")
    for required in ("README.md", "skill/SKILL.md"):
        if not (m["_dir"] / required).is_file():
            problems.append(f"missing {required}")
    if problems:
        return problems
    tools_dir = m["_dir"] / "skill" / "tools"
    scripts = list(tools_dir.glob("*.py")) if tools_dir.is_dir() else []
    if scripts and not any(PLACEHOLDER in s.read_text(encoding="utf-8") for s in scripts):
        problems.append(f"no tool script mentions {PLACEHOLDER}: a module's tools must be scoped to the workspace that installs them")
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        tt.skills_dir(ws).mkdir(parents=True)
        copy_skill(m, ws, "check")
        tools, tool_problems = tt.scan(ws)
        problems += tool_problems + tt.skill_warnings(ws)
        if not tools:
            problems.append("the skill has no valid tools")
    return problems


def tool_count(m: dict) -> int:
    return len(list((m["_dir"] / "skill" / "tools").glob("*.json")))


def off_dir(ws: Path) -> Path:
    """Where a workspace's own skills wait while turned off: Claude Code and the Tanka tools read only skills/."""
    return ws / ".claude" / "skills.off"


def installed_names(ws: Path) -> set[str]:
    return {m["name"] for m in available() if (tt.skills_dir(ws) / m["name"]).exists()}


def module_of(sdir: Path) -> str | None:
    """The module a skill directory came from, or None for a skill of the workspace's own.

    `install` leaves a marker. A copy made before the marker existed counts as the module's when it has
    the module's name and every tool file the module ships (it may have more: boards adds one per view)."""
    try:
        marker = json.loads((sdir / MARKER).read_text(encoding="utf-8"))
        if isinstance(marker, dict) and NAME_RE.fullmatch(str(marker.get("module", ""))):
            return str(marker["module"])
    except (OSError, ValueError):
        pass
    shipped = MODULES / sdir.name / "skill" / "tools"
    if not NAME_RE.fullmatch(sdir.name) or not (MODULES / sdir.name / "module.json").is_file() or not shipped.is_dir():
        return None
    files = [f.name for f in shipped.iterdir() if f.is_file()]
    return sdir.name if files and all((sdir / "tools" / f).is_file() for f in files) else None


def skill_tools(sdir: Path) -> list[dict]:
    out = []
    for f in sorted((sdir / "tools").glob("*.json")):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(m, dict):
            out.append({"name": str(m.get("name") or f.stem), "effect": str(m.get("effect") or "")})
    return out


def skills(ws: Path) -> list[dict]:
    """Every skill in the workspace, on (skills/) and off (skills.off/), with where it came from and its tools."""
    out = []
    for root, enabled in ((tt.skills_dir(ws), True), (off_dir(ws), False)):
        if not root.is_dir():
            continue
        for sdir in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
            module = module_of(sdir)
            out.append({"name": sdir.name, "enabled": enabled, "origin": "module" if module else "own",
                        "module": module, "tools": skill_tools(sdir)})
    return out


def setup_command(m: dict) -> str | None:
    """`tanka <module>` when its cli.py has commands of its own (link, login, setup), else None."""
    cli = m["_dir"] / "cli.py"
    if not cli.is_file():
        return None
    names = set()
    for one, many in CLI_COMMAND_RE.findall(cli.read_text(encoding="utf-8")):
        names |= {one} if one else set(re.findall(r"[\"']([a-z][a-z-]*)[\"']", many))
    return f"tanka {m['name']}" if names - NOT_SETUP else None


def report(ws: Path) -> dict:
    """What the page's Workspace tab shows: the tool budget, the skills, and every module."""
    here = installed_names(ws)
    mods = available()
    tools, _ = tt.scan(ws)
    return {
        "tools_used": len(tools), "tools_max": tt.tools_max(ws), "skills": skills(ws),
        "modules": [{"name": m["name"], "description": m.get("description", ""), "tools": tool_count(m),
                     "installed": m["name"] in here, "needs": list(m.get("needs", [])),
                     "needed_by": sorted(o["name"] for o in mods if o["name"] in here and m["name"] in o.get("needs", [])),
                     "missing_programs": missing_programs(m), "setup": setup_command(m)} for m in mods],
    }


def set_enabled(ws: Path, skill: str, enabled: bool) -> str:
    """Turn a workspace's own skill on or off by moving it between skills/ and skills.off/; raises ValueError."""
    if not tt.SKILL_RE.fullmatch(skill or ""):
        raise ValueError(f"'{skill}' is not a skill name")
    on, off = tt.skills_dir(ws) / skill, off_dir(ws) / skill
    src, dest = (off, on) if enabled else (on, off)
    if not src.is_dir():
        raise ValueError(f"no skill '{skill}' is {'off' if enabled else 'on'} in this workspace")
    if module_of(src):
        raise ValueError(f"'{skill}' comes from a module: install or remove the module instead")
    if dest.exists():
        raise ValueError(f"{dest} already exists; move one of them by hand")
    if enabled:
        used, adds = len(tt.scan(ws)[0]), len(list((src / "tools").glob("*.json")))
        if used + adds > tt.tools_max(ws):
            raise ValueError(f"the workspace has {used} tools and '{skill}' adds {adds}: over the {tt.tools_max(ws)}-tool "
                             "limit. Turn another skill off first.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    return f"{skill} is {'on' if enabled else 'off'}."


def to_install(name: str, ws: Path, seen: tuple = ()) -> list[dict]:
    """The modules `name` needs that the workspace lacks, dependencies first, then `name` itself."""
    if name in seen:
        raise ValueError(f"the modules {' → '.join(seen + (name,))} need each other")
    m = load(name)
    out = []
    for need in m.get("needs", []):
        if not (tt.skills_dir(ws) / need).exists():
            out += [x for x in to_install(need, ws, seen + (name,)) if x["name"] not in {o["name"] for o in out}]
    return out + [m]


def install(name: str, ws: Path, scope: str) -> int:
    try:
        plan = to_install(name, ws)
    except ValueError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    if not SCOPE_RE.fullmatch(scope):
        print(f"x The scope must be letters, digits, - or _ (got {scope!r}).", file=sys.stderr)
        return 1
    m = plan[-1]
    dest = tt.skills_dir(ws) / m["name"]
    if dest.exists():
        print(f"x {dest} already exists; remove it first to reinstall.", file=sys.stderr)
        return 1
    before, _ = tt.scan(ws)
    adds = sum(tool_count(x) for x in plan)
    if len(before) + adds > tt.tools_max(ws):
        also = f" (with {', '.join(x['name'] for x in plan[:-1])}, which it needs)" if len(plan) > 1 else ""
        print(f"x {ws} has {len(before)} tools and {name}{also} adds {adds}: over the {tt.tools_max(ws)}-tool limit. "
              "Merge or drop tools first (docs/tool-rules.md).", file=sys.stderr)
        return 1
    for dep in plan[:-1]:
        print(f"  {name} needs {dep['name']}: installing it first.")
        if install(dep["name"], ws, scope) != 0:
            return 1
    before, _ = tt.scan(ws)
    adds = tool_count(m)
    missing = missing_programs(m)
    copy_skill(m, ws, scope)
    (dest / MARKER).write_text(json.dumps({"module": m["name"], "installed": int(time.time())}) + "\n", encoding="utf-8")
    print(f"+ {name} installed in {dest} (scope \"{scope}\", {adds} tools; the workspace now has {len(before) + adds}).")
    if missing:
        print(f"! Not on this machine yet: {', '.join(missing)}. The tools fail until it is installed.")
    cli = m["_dir"] / "cli.py"
    if cli.is_file():
        run_cli(cli, "post-install", ws, scope)
    print(f"  See {m['_dir'] / 'README.md'} for setup; check with: tanka tools check {ws}")
    return 0


def run_cli(cli: Path, command: str, ws: Path, scope: str) -> int:
    """A module's cli.py hook. In a terminal it writes there; when this process's output is captured (the
    page installs a module), the hook's output is captured too and written through it."""
    sys.stdout.flush()  # our lines first: the hook writes to the same output
    argv = [sys.executable, str(cli), command, str(ws), scope]
    if sys.stdout is sys.__stdout__ and sys.stderr is sys.__stderr__:
        return subprocess.run(argv, check=False).returncode
    try:
        p = subprocess.run(argv, check=False, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=CLI_TIMEOUT)
    except subprocess.TimeoutExpired:
        print(f"! {cli.parent.name} {command} took more than {CLI_TIMEOUT} s and was stopped; "
              f"run it in a terminal: python3 {cli} {command} {ws} {scope}", file=sys.stderr)
        return 1
    sys.stdout.write(p.stdout)
    sys.stderr.write(p.stderr)
    return p.returncode


def uninstall(name: str, ws: Path, scope: str | None = None) -> int:
    """Remove a module's skill (its tools) from a workspace, and nothing else: its settings in the workspace
    (.claude/<name>.json, lenses, views) and its data in ~/.tanka/shared stay."""
    try:
        m = load(name)
    except ValueError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    dest = tt.skills_dir(ws) / name
    if not dest.is_dir():
        print(f"x {name} is not installed in {ws}.", file=sys.stderr)
        return 1
    if module_of(dest) != name:
        print(f"x {dest} is a skill of the workspace's own, not the {name} module's: it is not removed.", file=sys.stderr)
        return 1
    users = sorted(o["name"] for o in available()
                   if o["name"] != name and name in o.get("needs", []) and (tt.skills_dir(ws) / o["name"]).is_dir())
    if users:
        print(f"x {', '.join(users)} {'needs' if len(users) == 1 else 'need'} {name}: remove {', '.join(users)} first.",
              file=sys.stderr)
        return 1
    shutil.rmtree(dest)
    print(f"- {name} removed from {ws} (its tools; its settings and data stay).")
    cli = m["_dir"] / "cli.py"
    if cli.is_file() and "post-remove" in cli.read_text(encoding="utf-8"):
        run_cli(cli, "post-remove", ws, scope or ws.name)  # an exit for an unknown command is ignored
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "list"
    if cmd == "list":
        mods = available()
        print(f"{len(mods)} module(s) in {MODULES}:")
        for m in mods:
            tools = len(list((m["_dir"] / "skill" / "tools").glob("*.json")))
            missing = missing_programs(m)
            note = f"  (needs: {', '.join(missing)})" if missing else ""
            print(f"  {m['name']:<12} {tools} tools  {m.get('description', '')}{note}")
        print("Install one with: tanka install <module> <workspace> [--scope NAME]")
        return 0
    if cmd == "check" and len(argv) == 2:
        problems = check(argv[1])
        for p in problems:
            print(f"x {p}")
        print(f"{argv[1]}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
        return 0 if not problems else 1
    if cmd == "install" and len(argv) >= 4:
        return install(argv[1], Path(argv[2]), argv[3])
    if cmd == "uninstall" and len(argv) >= 3:
        return uninstall(argv[1], Path(argv[2]), argv[3] if len(argv) >= 4 else None)
    print("Usage: tanka modules [list|check <module>] | tanka install <module> <workspace> [--scope NAME]"
          " | tanka uninstall <module> <workspace>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
