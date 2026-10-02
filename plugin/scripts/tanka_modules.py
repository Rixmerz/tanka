#!/usr/bin/env python3
"""Tanka modules: reusable skills that ship with the repository and install into any workspace.

A module is a directory under `modules/<name>/` (rules: docs/module-rules.md):

    module.json   {"name", "description", "requires": [programs on PATH]}
    README.md     what it does, setup, configuration table
    skill/        SKILL.md + tools/: copied into a workspace by `install`
    cli.py        optional: `tanka <name> <command>`, and `post-install <workspace> <scope>`

`install` copies `skill/` to `<workspace>/.claude/skills/<name>/`, replacing
`__SCOPE__` in every text file with the scope (the workspace's name unless
given), and refuses when the workspace would go over the tool budget.

Standard library only.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_tools as tt  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
MODULES = REPO / "modules"
NAME_RE = re.compile(r"[a-z][a-z0-9-]*")
SCOPE_RE = re.compile(r"[A-Za-z0-9_-]+")
PLACEHOLDER = "__SCOPE__"
TEXT_SUFFIXES = {".md", ".json", ".py", ".sh", ".txt"}


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
    if len(before) + adds > tt.MAX_TOOLS_TOTAL:
        also = f" (with {', '.join(x['name'] for x in plan[:-1])}, which it needs)" if len(plan) > 1 else ""
        print(f"x {ws} has {len(before)} tools and {name}{also} adds {adds}: over the {tt.MAX_TOOLS_TOTAL}-tool limit. "
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
    print(f"+ {name} installed in {dest} (scope \"{scope}\", {adds} tools; the workspace now has {len(before) + adds}).")
    if missing:
        print(f"! Not on this machine yet: {', '.join(missing)}. The tools fail until it is installed.")
    cli = m["_dir"] / "cli.py"
    if cli.is_file():
        sys.stdout.flush()  # our lines first: the module's post-install writes to the same terminal
        subprocess.run([sys.executable, str(cli), "post-install", str(ws), scope], check=False)
    print(f"  See {m['_dir'] / 'README.md'} for setup; check with: tanka tools check {ws}")
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
    print("Usage: tanka modules [list|check <module>] | tanka install <module> <workspace> [--scope NAME]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
