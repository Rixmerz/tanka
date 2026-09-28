#!/usr/bin/env python3
"""Tanka tools: manifests, validation, execution and the `tanka tools` CLI.

A tool is a JSON manifest at <workspace>/.claude/skills/<skill>/tools/<name>.json
that wraps one command. The skill directory decides the tool's prefix, the
manifest decides its schema and effect class, and the command does the work.
Every rule a tool must follow is checked here, so the Tanka MCP server, the
hooks and the authoring docs all quote the same numbers. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# Limits. docs/tool-rules.md quotes these; change them here, not there.
# Tool selection on Haiku drops below 90% between 10 and 15 tools
# (docs/research/02), so the total is a hard cap, not a suggestion.
# --------------------------------------------------------------------------- #
MAX_TOOLS_TOTAL = 15
MAX_TOOLS_PER_SKILL = 6
MAX_PARAMS = 6
MAX_REQUIRED = 4
MIN_SENTENCES = 3
DESC_MIN_CHARS = 150
DESC_MAX_CHARS = 900
PARAM_DESC_MIN_CHARS = 10
PARAM_DESC_MAX_CHARS = 250
MAX_EXAMPLES = 3
MAX_COMPILED_DESC = 1800  # Claude Code truncates tool descriptions at 2 KB
MAX_NAME_LEN = 48
STRING_MAX_LEN = 2000
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 600
MAX_OUTPUT_CHARS = 6000  # well under MAX_MCP_OUTPUT_TOKENS, so results never spill to a file
MAX_SKILL_LINES = 100  # docs/skill-rules.md; a warning, not a failure

EFFECTS = ("read", "draft", "modify", "send")  # destructive is never a tool
PARAM_TYPES = ("string", "integer", "number", "boolean")
PARAM_KEYS = {"type", "description", "required", "enum", "default", "pattern", "minimum", "maximum"}
MANIFEST_KEYS = {"name", "effect", "description", "params", "examples", "run", "timeout_sec"}

SKILL_RE = re.compile(r"^[a-z][a-z0-9-]*$")
PARAM_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
PLACEHOLDER_RE = re.compile(r"\{([a-z][a-z0-9_]*)\}")
SENTENCE_END_RE = re.compile(r"[.!?](\s|$)")

SERVER_NAME = "tanka"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"


def skills_dir(ws: Path) -> Path:
    return ws / ".claude" / "skills"


def skill_prefix(skill: str) -> str:
    return skill.replace("-", "_") + "_"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _check_value(pname: str, spec: dict, value) -> str | None:
    t = spec.get("type")
    if t == "boolean":
        if not isinstance(value, bool):
            return f"'{pname}' must be true or false"
    elif t == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            return f"'{pname}' must be a whole number"
    elif t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{pname}' must be a number"
    elif t == "string":
        if not isinstance(value, str):
            return f"'{pname}' must be text"
        if not value.strip():
            return f"'{pname}' is empty"
        if len(value) > STRING_MAX_LEN:
            return f"'{pname}' is longer than {STRING_MAX_LEN} characters"
        if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
            return f"'{pname}' does not have the expected format ({spec['pattern']})"
    if "enum" in spec and value not in spec["enum"]:
        return f"'{pname}' must be one of: {', '.join(map(str, spec['enum']))}"
    if t in ("integer", "number"):
        if "minimum" in spec and value < spec["minimum"]:
            return f"'{pname}' must be at least {spec['minimum']}"
        if "maximum" in spec and value > spec["maximum"]:
            return f"'{pname}' must be at most {spec['maximum']}"
    return None


def check_args(manifest: dict, args: dict) -> tuple[dict, list[str]]:
    """Apply defaults and validate call arguments. Returns (args, errors)."""
    params = manifest.get("params", {})
    errs: list[str] = []
    if not isinstance(args, dict):
        return {}, ["arguments must be an object"]
    unknown = [k for k in args if k not in params]
    if unknown:
        errs.append(f"unknown field(s): {', '.join(unknown)}; this tool accepts only: {', '.join(params) or 'no fields'}")
    out: dict = {}
    for pname, spec in params.items():
        if pname in args and args[pname] is not None:
            e = _check_value(pname, spec, args[pname])
            if e:
                errs.append(e)
            out[pname] = args[pname]
        elif "default" in spec:
            out[pname] = spec["default"]
        elif spec.get("required"):
            errs.append(f"missing required field '{pname}' ({spec.get('description', '')})")
    return out, errs


def validate_manifest(m, skill: str, file_stem: str) -> list[str]:
    """Every rule a single manifest must follow. Empty list means valid."""
    if not isinstance(m, dict):
        return ["the manifest must be a JSON object"]
    errs: list[str] = []
    extra = set(m) - MANIFEST_KEYS
    if extra:
        errs.append(f"unknown keys: {', '.join(sorted(extra))} (allowed: {', '.join(sorted(MANIFEST_KEYS))})")

    name = m.get("name")
    prefix = skill_prefix(skill)
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name or ""):
        errs.append("name must be lowercase snake_case")
    else:
        if not name.startswith(prefix) or len(name) - len(prefix) < 3:
            errs.append(f"name must start with the skill prefix '{prefix}' followed by at least 3 characters")
        if len(name) > MAX_NAME_LEN:
            errs.append(f"name is longer than {MAX_NAME_LEN} characters")
        if name != file_stem:
            errs.append(f"file must be named {name}.json")

    if m.get("effect") not in EFFECTS:
        errs.append(f"effect must be one of {', '.join(EFFECTS)} (destructive actions are never tools)")

    desc = m.get("description")
    if not isinstance(desc, str):
        errs.append("description is required")
    else:
        if not DESC_MIN_CHARS <= len(desc) <= DESC_MAX_CHARS:
            errs.append(f"description must be {DESC_MIN_CHARS}-{DESC_MAX_CHARS} characters (it has {len(desc)})")
        if len(SENTENCE_END_RE.findall(desc)) < MIN_SENTENCES:
            errs.append(f"description needs at least {MIN_SENTENCES} sentences: what it does, when to use it, when not to, what it returns")

    params = m.get("params", {})
    if not isinstance(params, dict):
        errs.append("params must be an object of name -> spec")
        params = {}
    if len(params) > MAX_PARAMS:
        errs.append(f"at most {MAX_PARAMS} params (it has {len(params)}); split the tool or fix values in the command")
    required = [p for p, s in params.items() if isinstance(s, dict) and s.get("required")]
    if len(required) > MAX_REQUIRED:
        errs.append(f"at most {MAX_REQUIRED} required params (it has {len(required)})")
    for pname, spec in params.items():
        where = f"param '{pname}'"
        if not PARAM_RE.fullmatch(pname):
            errs.append(f"{where}: name must be snake_case, at most 24 characters")
        if not isinstance(spec, dict):
            errs.append(f"{where}: spec must be an object")
            continue
        bad = set(spec) - PARAM_KEYS
        if bad:
            errs.append(f"{where}: unknown keys {', '.join(sorted(bad))} (no nested objects or arrays)")
        t = spec.get("type")
        if t not in PARAM_TYPES:
            errs.append(f"{where}: type must be one of {', '.join(PARAM_TYPES)}")
        pd = spec.get("description")
        if not isinstance(pd, str) or not PARAM_DESC_MIN_CHARS <= len(pd) <= PARAM_DESC_MAX_CHARS:
            errs.append(f"{where}: description must be {PARAM_DESC_MIN_CHARS}-{PARAM_DESC_MAX_CHARS} characters")
        if "enum" in spec:
            en = spec["enum"]
            if not isinstance(en, list) or not 2 <= len(en) <= 12 or len(set(map(str, en))) != len(en):
                errs.append(f"{where}: enum must list 2-12 distinct values")
        if "pattern" in spec:
            if t != "string":
                errs.append(f"{where}: pattern only applies to strings")
            else:
                try:
                    re.compile(spec["pattern"])
                except re.error as exc:
                    errs.append(f"{where}: invalid pattern ({exc})")
        if ("minimum" in spec or "maximum" in spec) and t not in ("integer", "number"):
            errs.append(f"{where}: minimum/maximum only apply to numbers")
        if spec.get("required") and "default" in spec:
            errs.append(f"{where}: a required param cannot have a default")
        if "default" in spec and t in PARAM_TYPES:
            e = _check_value(pname, spec, spec["default"])
            if e:
                errs.append(f"{where}: default is invalid: {e}")

    examples = m.get("examples")
    if not isinstance(examples, list) or not 1 <= len(examples) <= MAX_EXAMPLES:
        errs.append(f"examples must list 1-{MAX_EXAMPLES} complete argument objects")
    elif not errs:
        for i, ex in enumerate(examples, 1):
            _, ee = check_args(m, ex)
            errs.extend(f"example {i}: {e}" for e in ee)

    run = m.get("run")
    if not isinstance(run, list) or not run:
        errs.append("run must be a non-empty argv list (no shell)")
    else:
        first = run[0]
        if not isinstance(first, str) or PLACEHOLDER_RE.search(first):
            errs.append("run[0] must be a fixed program name, not a placeholder")
        for item in run:
            group = item if isinstance(item, list) else [item]
            if not group or not all(isinstance(x, str) for x in group):
                errs.append("run items must be strings, or lists of strings for an optional group")
                continue
            if isinstance(item, list) and not any(PLACEHOLDER_RE.search(x) for x in group):
                errs.append("an optional group in run must contain a placeholder")
            for x in group:
                for ph in PLACEHOLDER_RE.findall(x):
                    if ph not in params:
                        errs.append(f"run uses {{{ph}}} but there is no param with that name")
                    elif isinstance(item, str) and not params[ph].get("required") and "default" not in params[ph]:
                        errs.append(f"run uses optional {{{ph}}} outside a group; wrap it as [\"--flag\", \"{{{ph}}}\"] so it is dropped when absent")

    to = m.get("timeout_sec", DEFAULT_TIMEOUT)
    if isinstance(to, bool) or not isinstance(to, int) or not 1 <= to <= MAX_TIMEOUT:
        errs.append(f"timeout_sec must be a whole number between 1 and {MAX_TIMEOUT}")

    if not errs and len(compiled_description(m)) > MAX_COMPILED_DESC:
        errs.append(f"description plus examples exceed {MAX_COMPILED_DESC} characters; shorten them")
    return errs


def compiled_description(m: dict) -> str:
    # MCP has no input_examples field, so the examples ride in the description.
    lines = [m["description"].strip()]
    for ex in m.get("examples", []):
        lines.append("Example: " + json.dumps(ex, ensure_ascii=False))
    return "\n".join(lines)


def input_schema(m: dict) -> dict:
    props = {}
    for pname, spec in m.get("params", {}).items():
        p = {"type": spec["type"], "description": spec["description"]}
        for k in ("enum", "default", "pattern", "minimum", "maximum"):
            if k in spec:
                p[k] = spec[k]
        if spec["type"] == "string":
            p["maxLength"] = STRING_MAX_LEN
        props[pname] = p
    req = [p for p, s in m.get("params", {}).items() if s.get("required")]
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if req:
        schema["required"] = req
    return schema


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def scan(ws: Path) -> tuple[dict, list[str]]:
    """Return ({tool_name: manifest}, problems). Only valid tools are returned.

    A manifest carries two private keys once loaded: _skill and _dir.
    """
    tools: dict = {}
    problems: list[str] = []
    root = skills_dir(ws)
    if not root.is_dir():
        return tools, problems
    for sdir in sorted(p for p in root.iterdir() if p.is_dir()):
        tdir = sdir / "tools"
        if not tdir.is_dir():
            continue
        skill = sdir.name
        if not SKILL_RE.fullmatch(skill):
            problems.append(f"skill '{skill}': directory name must be lowercase letters, digits and hyphens; its tools were skipped")
            continue
        skill_md = sdir / "SKILL.md"
        skill_text = skill_md.read_text(encoding="utf-8") if skill_md.is_file() else ""
        if not skill_text:
            problems.append(f"skill '{skill}': has tools/ but no SKILL.md; its tools were skipped")
            continue
        found = []
        for f in sorted(tdir.glob("*.json")):
            try:
                m = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                problems.append(f"{skill}/tools/{f.name}: not valid JSON ({exc})")
                continue
            errs = validate_manifest(m, skill, f.stem)
            if not errs and m["name"] not in skill_text:
                errs.append(f"SKILL.md of '{skill}' never mentions {m['name']}; say when to use it there")
            if errs:
                problems.extend(f"{skill}/tools/{f.name}: {e}" for e in errs)
                continue
            m = dict(m, _skill=skill, _dir=str(tdir))
            found.append(m)
        if len(found) > MAX_TOOLS_PER_SKILL:
            problems.append(f"skill '{skill}': {len(found)} tools, the limit is {MAX_TOOLS_PER_SKILL}; none of them were loaded")
            continue
        for m in found:
            tools[m["name"]] = m
    if len(tools) > MAX_TOOLS_TOTAL:
        problems.append(f"{len(tools)} tools in total, the limit is {MAX_TOOLS_TOTAL}; only the first {MAX_TOOLS_TOTAL} alphabetically were loaded")
        tools = dict(sorted(tools.items())[:MAX_TOOLS_TOTAL])
    return tools, problems


def skill_warnings(ws: Path) -> list[str]:
    """Skill rules that do not block loading but make Haiku worse."""
    out: list[str] = []
    for skill in skill_names(ws):
        text = (skills_dir(ws) / skill / "SKILL.md").read_text(encoding="utf-8")
        fm = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        meta = dict(re.findall(r"^(name|description):\s*(.+)$", fm.group(1), re.M)) if fm else {}
        if meta.get("name", "").strip() != skill:
            out.append(f"skill '{skill}': frontmatter name must be '{skill}'")
        if not meta.get("description", "").strip():
            out.append(f"skill '{skill}': frontmatter needs a description with the trigger words")
        lines = text.count("\n") + 1
        if lines > MAX_SKILL_LINES:
            out.append(f"skill '{skill}': {lines} lines, keep it under {MAX_SKILL_LINES}")
        if re.search(r"^```(bash|sh|shell|zsh|fish|console)\b", text, re.M):
            out.append(f"skill '{skill}': has shell code blocks; the assistant cannot run commands")
    return out


def skill_names(ws: Path) -> list[str]:
    root = skills_dir(ws)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "SKILL.md").is_file())


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
def build_argv(m: dict, args: dict) -> tuple[list[str], list[str]]:
    argv: list[str] = []
    errs: list[str] = []
    for item in m["run"]:
        group = item if isinstance(item, list) else [item]
        if isinstance(item, list) and any(args.get(ph) is None for x in group for ph in PLACEHOLDER_RE.findall(x)):
            continue  # optional group whose value was not given
        for x in group:
            whole = PLACEHOLDER_RE.fullmatch(x)
            val = PLACEHOLDER_RE.sub(lambda mo: _fmt(args.get(mo.group(1))), x)
            if whole and val.startswith("-"):
                # A value that looks like a flag would be read as one by the program.
                errs.append(f"'{whole.group(1)}' cannot start with '-'")
            argv.append(val)
    return argv, errs


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


def run_tool(ws: Path, m: dict, raw_args: dict) -> tuple[str, bool]:
    """Run one tool call. Returns (text, is_error)."""
    args, errs = check_args(m, raw_args or {})
    if not errs:
        argv, errs = build_argv(m, args)
    if errs:
        return ("Invalid arguments: " + "; ".join(errs) + ". Fix them and call again, or ask the user for the missing detail.", True)
    env = dict(os.environ, TANKA_WORKSPACE=str(ws), TANKA_TOOL=m["name"], TANKA_SKILL=m["_skill"])
    timeout = int(m.get("timeout_sec", DEFAULT_TIMEOUT))
    try:
        p = subprocess.run(argv, input=json.dumps(args, ensure_ascii=False), capture_output=True, text=True,
                           cwd=m["_dir"], env=env, timeout=timeout)
    except FileNotFoundError:
        return (f"The program '{argv[0]}' is not installed on this machine. Tell the user; do not retry.", True)
    except subprocess.TimeoutExpired:
        return (f"{m['name']} did not finish within {timeout} s. Do not retry it now; tell the user it timed out.", True)
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        tail = ((p.stderr or "").strip() or out)[-1500:]
        return (f"{m['name']} failed (exit {p.returncode}): {tail}", True)
    if not out:
        out = "Done. The command finished without output."
    if len(out) > MAX_OUTPUT_CHARS:
        extra = len(out) - MAX_OUTPUT_CHARS
        out = out[:MAX_OUTPUT_CHARS] + f"\n[truncated: {extra} more characters. Narrow the request with the tool's filters.]"
    return out, False


# --------------------------------------------------------------------------- #
# CLI: tanka tools check|list|test, and the MCP config the launcher passes
# --------------------------------------------------------------------------- #
def _usage() -> None:
    print("usage: tanka_tools.py check <ws> | list <ws> | test <ws> <tool> '<json args>' | mcp-config <ws> <plugin_dir>", file=sys.stderr)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        _usage()
        return 2
    cmd, ws = argv[0], Path(argv[1]).resolve()
    if cmd == "check":
        tools, problems = scan(ws)
        for name, m in sorted(tools.items()):
            print(f"ok   {name} ({m['effect']}, {len(m.get('params', {}))} params)")
        for p in problems:
            print(f"FAIL {p}")
        warns = skill_warnings(ws)
        for w in warns:
            print(f"warn {w}")
        print(f"{len(tools)}/{MAX_TOOLS_TOTAL} tools loaded, {len(problems)} problem(s), {len(warns)} warning(s)")
        return 1 if problems else 0
    if cmd == "list":
        tools, _ = scan(ws)
        for name, m in sorted(tools.items()):
            print(f"{name}  [{m['effect']}]  {m['description'].split('. ')[0]}.")
        return 0
    if cmd == "test" and len(argv) >= 3:
        tools, problems = scan(ws)
        m = tools.get(argv[2])
        if not m:
            print(f"no valid tool named {argv[2]}", file=sys.stderr)
            for p in problems:
                print(f"  {p}", file=sys.stderr)
            return 2
        try:
            args = json.loads(argv[3]) if len(argv) > 3 else {}
        except json.JSONDecodeError as exc:
            print(f"arguments are not valid JSON: {exc}", file=sys.stderr)
            return 2
        text, is_error = run_tool(ws, m, args)
        print(("ERROR " if is_error else "") + text)
        return 1 if is_error else 0
    if cmd == "mcp-config" and len(argv) >= 3:
        # One --mcp-config value per line: Tanka's own server, then the
        # workspace's mcp.json only when the policy lets foreign servers load.
        import tanka_common as tc
        server = Path(argv[2]) / "scripts" / "tanka_mcp.py"
        print(json.dumps({"mcpServers": {SERVER_NAME: {"command": sys.executable, "args": [str(server), str(ws)]}}}))
        mcp_file = ws / ".tanka" / "mcp.json"
        if tc.load_policy(ws).get("external_mcp", "deny") != "deny" and mcp_file.is_file():
            print(str(mcp_file))
        return 0
    _usage()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
