#!/usr/bin/env python3
"""Tanka launcher: starts Claude Code (Haiku by default) in a clean workspace with ONLY the Tanka plugin,
ONLY Tanka's own MCP server (the tools under .claude/skills/*/tools/), and no user settings.

The same launcher runs on Linux, macOS and Windows: `bin/tanka` (bash) and `bin/tanka.cmd` (Windows) only
find a Python and hand over to this file. Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

TANKA_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TANKA_REPO / "plugin" / "scripts"))
import tanka_os as tos  # noqa: E402

ENV = os.environ
PLUGIN_DIR = ENV.get("TANKA_PLUGIN_DIR") or str(TANKA_REPO / "plugin")
TEMPLATE_DIR = TANKA_REPO / "workspace-template"
DEFAULT_WS = ENV.get("TANKA_HOME") or str(Path.home() / "tanka-workspace")
# Named workspaces live here, as directories or as symlinks to a workspace kept elsewhere.
WORKSPACES_DIR = ENV.get("TANKA_WORKSPACES") or str(Path.home() / ".tanka" / "workspaces")
# Names that are commands cannot be workspace names.
RESERVED = set("init start resume run doctor test tools dev help modules install uninstall routine trigger "
               "automation ui profile".split())
# Interactive sessions skip Claude Code prompts; the Tanka hooks and --disallowedTools still apply.
PERMISSION_MODE = ENV.get("TANKA_PERMISSION_MODE") or "bypassPermissions"
# A workspace has a profile (haiku or sonnet, chosen at `tanka init`) that sets the model and how much harness
# it gets. TANKA_MODEL overrides only the model, for one launch; the profile's harness stays.
MODEL_OVERRIDE = ENV.get("TANKA_MODEL", "")
# Authoring designs tools a small model must use correctly: think it through.
DEV_EFFORT = ENV.get("TANKA_DEV_EFFORT") or "high"
CLAUDE_BIN = ENV.get("CLAUDE_BIN") or "claude"
PY = sys.executable


class Exit(Exception):
    def __init__(self, code: int):
        self.code = code


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise Exit(code)


def usage() -> None:
    print(f"""tanka - a guardrailed assistant harness on top of Claude Haiku or Sonnet

Usage:
  tanka <name>                     Reopen the named workspace: pick a previous session (a new one if it has none)
  tanka start <name> [-- args...]  New session in the workspace; creates it first if it does not exist
  tanka init [name|dir] [--model haiku|sonnet|opus] [--strict]
                                   Create a clean workspace without starting it (asks which model when it can)
  tanka profile [name|dir] [strict|standard]
                                   Show or change a workspace's model and harness (haiku: strict, sonnet: lighter)
  tanka resume [name|dir]          Same as tanka <name> (never plain claude -r)
  tanka run "task" [dir] [--objective name] [--max-turns N] [--budget USD (0: no cap)]
                                   Delegated task, no human in the loop (fail-closed: sends are denied)
  tanka doctor [dir]               Check configuration, plugin and state
  tanka tools check|list [dir]     Validate or list the workspace's tools
  tanka tools test <tool> '<json>' [dir]
                                   Run one tool exactly as the assistant would, without Claude
  tanka dev [name|dir]             Authoring session (your default model) to create skills and tools
  tanka test                       Run the harness test suite
  tanka ui [name] [--detach] | ui stop
                                   The local page: chat with each workspace, its day, boards, reports
  tanka modules [check <module>]   List the reusable modules, or validate one
  tanka install <module> <name> [--scope S]
                                   Add a module's skill to a workspace
  tanka uninstall <module> <name>  Remove a module's skill (its tools); its settings and data stay
  tanka <module> <command>         A module's own commands (e.g. tanka whatsapp link)
  tanka routine add <name|dir> <routine> --every 5m "task"
                                   Run the assistant on a clock, unattended
  tanka trigger add <name|dir> <trigger> --on whatsapp:<role> "task"
                                   Run it when an event arrives (see tanka modules)
  tanka routine|trigger list|remove|run ...
  tanka automation service install|remove|status | daemon [--detach] | ensure | status | stop | alert <ws> [--whatsapp N] | log <ws>

Every [dir] also takes a workspace name. A name is a directory (or a symlink to
one) under {WORKSPACES_DIR}; without one, the default is {DEFAULT_WS}.""")
    list_workspaces()
    print("""
Environment: TANKA_HOME (default workspace), TANKA_WORKSPACES (named workspaces), TANKA_MODEL (overrides the workspace's model for one launch), TANKA_EFFORT (its effort), TANKA_INIT_MODEL (haiku|sonnet|opus, skips the question), TANKA_PLUGIN_DIR, TANKA_PERMISSION_MODE (bypassPermissions), TANKA_DEV_EFFORT (high), TANKA_AUTOCOMPACT_PCT (60 haiku, 75 sonnet), TANKA_PAGE_DAY_USD (5), TANKA_COMPACT_TOKENS (90000), CLAUDE_BIN""")


def is_name(s: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9_-]*", s or ""))


def list_workspaces(out=sys.stdout) -> None:
    found = []
    d = Path(WORKSPACES_DIR)
    if d.is_dir():
        found = sorted(p.name for p in d.iterdir() if (p / ".tanka" / "policy.json").is_file())
    tail = (" " + " ".join(found)) if found else " (none yet; create one with: tanka start <name>)"
    print(f"Workspaces:{tail}", file=out)


def resolve_ws(d: str = "") -> str:
    """A bare word is a workspace name under WORKSPACES_DIR; anything with a slash is a path. Names resolve
    through symlinks to the real directory, because Claude Code keys a project's session history by its real
    path and `resume` must find it."""
    d = d or DEFAULT_WS
    if d == "--":
        d = DEFAULT_WS
    if is_name(d):
        p = Path(WORKSPACES_DIR) / d
        return str(p.resolve()) if p.is_dir() else str(p)
    return d


def is_module(s: str) -> bool:
    return is_name(s) and (TANKA_REPO / "modules" / s / "module.json").is_file()


def check_name(s: str) -> None:
    if is_name(s) and (s in RESERVED or is_module(s)):
        die(f"x '{s}' is a tanka command or module, not a workspace name. Pick another name.")


def has_sessions(ws: str) -> bool:
    """True when Claude Code has an interactive session to offer for this workspace. Headless runs (`tanka run`,
    entrypoint "sdk-cli") are saved too, but the resume picker hides them, so counting them opens an empty picker."""
    key = re.sub(r"[^a-zA-Z0-9]", "-", ws)
    folder = Path(ENV.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects" / key
    for f in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                if any('"entrypoint":"cli"' in line for line in fh):
                    return True
        except OSError:
            continue
    return False


def require_ws(ws: str) -> None:
    if not (Path(ws) / ".tanka" / "policy.json").is_file():
        die(f"x '{ws}' is not a Tanka workspace (no .tanka/policy.json). Run: tanka init {ws}")


def full_model(m: str) -> str:
    """Full model ids: an alias would let Claude Code pick whichever version it prefers."""
    return {"haiku": "claude-haiku-5-5", "sonnet": "claude-sonnet-5-5", "opus": "claude-opus-5-5"}.get(m, m)


def run_py(*args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run([PY, *args], **kw)


def ws_launch(ws: str, what: str = "") -> str:
    """One line from the harness: "<profile> <model> <autocompact%> <tool-search> <effort|->". Anything
    unreadable is the strict profile."""
    p = run_py(str(Path(PLUGIN_DIR) / "scripts" / "tanka_tools.py"), "profile", ws,
               capture_output=True, text=True, encoding="utf-8")
    line = p.stdout if p.returncode == 0 and p.stdout.strip() else "haiku claude-haiku-5-5 60 false -"
    profile, model, pct, search, effort = (line.split() + ["", "", "", "", ""])[:5]
    if what == "model":
        return full_model(MODEL_OVERRIDE or model)
    if what == "effort":
        e = ENV.get("TANKA_EFFORT") or effort or "-"
        return "-" if e == "default" else e
    if what == "pct":
        return pct
    if what == "search":
        return search
    return profile


def apply_profile(ws: str) -> None:
    """Compaction and tool search depend on the profile: a stronger model keeps its rules longer and copes
    with more tools."""
    ENV["ENABLE_TOOL_SEARCH"] = ws_launch(ws, "search")
    ENV["CLAUDE_CODE_AUTOCOMPACT_PCT_OVERRIDE"] = ENV.get("TANKA_AUTOCOMPACT_PCT") or ws_launch(ws, "pct")


def ask(prompt: str) -> str:
    """A question on stderr, so it shows even when stdout is captured (like bash `read -p`)."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def choose_model(pick: str = "") -> str:
    """The model of a new workspace: haiku, sonnet or opus, from --model, TANKA_INIT_MODEL, or a question when
    there is a terminal; haiku otherwise. Since Haiku 5.5 every model gets the standard guardrails unless
    --strict asks for the strict ones (15 tools, the rules repeated every turn), which remain for small,
    cheap, low-effort assistants."""
    pick = pick or ENV.get("TANKA_INIT_MODEL", "")
    if not pick and interactive():
        print("Which model will this workspace use? (launched by its full id, e.g. claude-haiku-5-5)\n"
              "  haiku   Haiku 5.5: cheap and fast; good with tools at high effort\n"
              "  sonnet  Sonnet 5.5: more judgment for long, open-ended work\n"
              "  opus    Opus 5.5: the strongest, the most expensive\n"
              "  Safety does not change: nothing is deleted, sends and changes ask first.", file=sys.stderr)
        pick = ask("Model [haiku/sonnet/opus] (haiku): ")
    pick = (pick or "haiku").lower()
    if pick not in ("haiku", "sonnet", "opus"):
        die(f"x Unknown model '{pick}': use haiku, sonnet or opus.")
    return pick


def choose_profile(pick: str) -> str:
    """The guardrails of an existing workspace: haiku (strict) or sonnet (standard)."""
    p = (pick or "").lower()
    if p in ("haiku", "strict"):
        return "haiku"
    if p in ("sonnet", "standard"):
        return "sonnet"
    die(f"x Unknown guardrails '{pick}': use strict (haiku) or standard (sonnet).")
    return ""


def write_profile(ws: str, profile: str, model: str = "") -> None:
    """workspace.json keeps every other key (the model and effort chosen on the page)."""
    path = Path(ws) / ".tanka" / "workspace.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    data["profile"] = profile
    if model:
        data["model"] = model
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def guardrails(profile: str) -> str:
    return "strict" if profile == "haiku" else "standard"


def cmd_profile(args: list[str]) -> int:
    arg = args[0] if args else ""
    ws = resolve_ws(arg)
    require_ws(ws)
    if len(args) < 2 or not args[1]:
        print(f"{ws_launch(ws)} (model: {ws_launch(ws, 'model')}, effort: {ws_launch(ws, 'effort')})")
        return 0
    pick = choose_profile(args[1])
    write_profile(ws, pick)
    print(f"+ {arg or ws} now has the {guardrails(pick)} guardrails. It takes effect the next time you open it.")
    return 0


def cmd_init(args: list[str]) -> int:
    name, rest = (args[0] if args else ""), args[1:]
    model_flag, strict = "", False
    i = 0
    while i < len(rest):
        if rest[i] == "--model":
            model_flag = rest[i + 1] if i + 1 < len(rest) else ""
            i += 2
        elif rest[i] == "--strict":
            strict = True
            i += 1
        else:
            i += 1
    check_name(name)
    ws = resolve_ws(name)
    if (Path(ws) / ".tanka" / "policy.json").exists():
        die(f"x A workspace already exists at {ws} (refusing to overwrite).")
    model = choose_model(model_flag)
    profile = "haiku" if strict else "sonnet"
    shutil.copytree(TEMPLATE_DIR, ws, dirs_exist_ok=True)
    for sub in (".tanka/state", ".tanka/drafts", ".claude/skills"):
        (Path(ws) / sub).mkdir(parents=True, exist_ok=True)
    write_profile(ws, profile, model)
    ref = name if is_name(name) else ws
    print(f"+ Workspace created at {ws} ({full_model(model)}, {guardrails(profile)} guardrails)")
    print(f"  1. Build its skills and tools: tanka dev {ref}   (rules: docs/skill-rules.md, docs/tool-rules.md)")
    print(f"  2. Check them: tanka tools check {ref}")
    print(f"  3. Open it: tanka {ref}   The first session asks which language to work in.")
    return 0


# Claude Code's own tools an assistant is offered. Every other built-in tool costs context on every message
# (about 20K tokens together) and is one the assistant never uses: Agent stays for the tanka:* subagents.
BUILTIN_TOOLS = "Read,Write,Edit,Glob,Grep,Skill,ToolSearch,Agent"


def lines_of(*args: str) -> list[str]:
    p = run_py(*args, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        sys.stderr.write(p.stderr)
        raise Exit(p.returncode)
    out = p.stdout[:-1] if p.stdout.endswith("\n") else p.stdout
    return out.split("\n") if out else []


def common_args(ws: str) -> list[str]:
    """Isolation: no user settings, no foreign MCP servers, no other plugins, no browser integration."""
    tools = str(Path(PLUGIN_DIR) / "scripts" / "tanka_tools.py")
    args = ["--setting-sources", "project,local", "--strict-mcp-config", "--mcp-config"]
    args += lines_of(tools, "mcp-config", ws, PLUGIN_DIR)
    args += lines_of(tools, "read-dirs", ws)
    args += ["--plugin-dir", PLUGIN_DIR, "--model", ws_launch(ws, "model"), "--no-chrome",
             "--tools", ENV.get("TANKA_BUILTIN_TOOLS") or BUILTIN_TOOLS,
             "--disallowedTools", "Bash", "PowerShell", "NotebookEdit", "WebSearch", "WebFetch"]
    effort = ws_launch(ws, "effort")
    if effort != "-":
        args += ["--effort", effort]
    return args


def claude_exec(argv: list[str]) -> int:
    """Hand the terminal to Claude Code: exec on POSIX; on Windows, whose exec is a spawn that returns at once,
    run it and pass its exit code on."""
    exe = tos.which(CLAUDE_BIN)
    if tos.IS_WINDOWS:
        try:
            return subprocess.call([exe, *argv])
        except FileNotFoundError:
            die(f"x '{CLAUDE_BIN}' not found in PATH")
    try:
        os.execvp(exe, [CLAUDE_BIN, *argv])
    except FileNotFoundError:
        die(f"x '{CLAUDE_BIN}' not found in PATH")
    return 1


def py_exec(argv: list[str]) -> int:
    """Run a Tanka Python script in place of this process (a child on Windows, see claude_exec)."""
    if tos.IS_WINDOWS:
        return subprocess.call([PY, *argv])
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(PY, [PY, *argv])
    return 1


def ensure_daemon() -> None:
    """Reminders, the daily brief and every routine and trigger run from the automation daemon, so an
    interactive launch starts it when there is work for it and none runs. TANKA_NO_DAEMON=1 skips this."""
    if ENV.get("TANKA_NO_DAEMON") or ENV.get("TANKA_UNATTENDED"):
        return
    try:
        run_py(str(Path(PLUGIN_DIR) / "scripts" / "tanka_automation.py"), "ensure", stdout=sys.stderr)
    except OSError:
        pass


def cmd_start(args: list[str]) -> int:
    arg = args[0] if args else ""
    if arg == "--":
        arg = ""
    check_name(arg)
    ws = resolve_ws(arg)
    rest = args[1:] if arg else list(args)  # a leading `--` is the separator, not a name
    if rest[:1] == ["--"]:
        rest = rest[1:]
    if not (Path(ws) / ".tanka" / "policy.json").is_file():
        # A typo here would silently create a new, empty assistant, so ask first.
        if not sys.stdin.isatty():
            die(f"x No workspace at {ws}. Create it with: tanka init {arg or ws}")
        list_workspaces(sys.stderr)
        ok = ask(f"No workspace '{arg or ws}' yet. Create it at {ws}? [y/N] ")
        if ok[:1] not in ("y", "Y", "s", "S"):
            die("x Not created.")
        cmd_init([arg or ws])
    os.chdir(ws)
    ensure_daemon()
    apply_profile(ws)
    return claude_exec([*common_args(ws), "--permission-mode", PERMISSION_MODE, *rest])


def cmd_resume(args: list[str]) -> int:
    arg = args[0] if args else ""
    ws = resolve_ws(arg)
    if not (Path(ws) / ".tanka" / "policy.json").is_file():
        print(f"x No workspace '{arg or ws}'.", file=sys.stderr)
        list_workspaces(sys.stderr)
        die(f"  To create it: tanka start {arg or '<name>'}")
    if has_sessions(ws):
        return cmd_start([ws, "--", "--resume"])
    print(f"+ No previous sessions in {arg or ws}; starting a new one.", file=sys.stderr)
    return cmd_start([ws])


def cmd_run(args: list[str]) -> int:
    task, rest = (args[0] if args else ""), args[1:]
    if not task:
        print("x No task given.", file=sys.stderr)
        usage()
        raise Exit(1)
    ws, objective = DEFAULT_WS, ""
    max_turns = ENV.get("TANKA_MAX_TURNS") or "20"
    budget = ENV.get("TANKA_BUDGET_USD") or "1.00"
    while rest:
        a = rest[0]
        if a in ("--objective", "--max-turns", "--budget"):
            val = rest[1] if len(rest) > 1 else ""
            if a == "--objective":
                objective = val
            elif a == "--max-turns":
                max_turns = val
            else:
                budget = val
            rest = rest[2:]
        elif a == "--":
            rest = rest[1:]
            break
        else:
            ws = resolve_ws(a)
            rest = rest[1:]
    require_ws(ws)
    os.chdir(ws)
    if objective:
        src = Path(ws) / ".tanka" / "objectives" / f"{objective}.json"
        if not src.is_file():
            die(f"x No such file: {src}")
        (Path(ws) / ".tanka" / "state").mkdir(parents=True, exist_ok=True)
        obj = json.loads(src.read_text(encoding="utf-8"))
        obj["status"] = "active"
        obj["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        obj["may_send"] = False  # delegated mode: never send without a human
        (Path(ws) / ".tanka" / "state" / "objective.json").write_text(
            json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"+ Objective '{obj.get('title')}' activated (may_send forced to false in delegated mode)")
        sys.stdout.flush()
    apply_profile(ws)
    common = common_args(ws)
    # Tools read this to tell a run with nobody watching from one the user attends: a send
    # tool refuses there unless the user opted that recipient in (e.g. auto_reply on a role).
    ENV["TANKA_UNATTENDED"] = "1"
    # Fail-closed: anything that would ask for confirmation is denied automatically.
    # --budget 0 means no cap: the flag is left out.
    budget_args = [] if budget in ("0", "0.00") else ["--max-budget-usd", budget]
    try:
        return subprocess.call([tos.which(CLAUDE_BIN), *common, "-p", task,
                                "--permission-mode", "default", "--permission-prompts", "none",
                                "--max-turns", max_turns, *budget_args, "--output-format", "text", *rest])
    except FileNotFoundError:
        die(f"x '{CLAUDE_BIN}' not found in PATH", 127)
    return 127


def valid_json(path: Path) -> bool:
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def cmd_doctor(args: list[str]) -> int:
    ws = resolve_ws(args[0] if args else "")
    print(f"Repo:     {TANKA_REPO}")
    print(f"Plugin:   {PLUGIN_DIR}")
    print(f"Workspace: {ws}")
    claude = shutil.which(CLAUDE_BIN)
    if not claude:
        print(f"x '{CLAUDE_BIN}' not found in PATH")
        return 1
    v = subprocess.run([claude, "--version"], capture_output=True, text=True)
    print(f"Claude:   {(v.stdout.splitlines() or [''])[0]}")
    print(f"Python:   Python {sys.version.split()[0]} ({PY})")
    if tos.IS_WINDOWS:
        bash = ENV.get("CLAUDE_CODE_GIT_BASH_PATH") or shutil.which("bash")
        print(f"Git Bash: {bash or 'x not found: Claude Code on Windows runs hooks with Git Bash (git-scm.com)'}")
    print("--- plugin validate")
    sys.stdout.flush()
    subprocess.run([claude, "plugin", "validate", PLUGIN_DIR])
    if (Path(ws) / ".tanka" / "policy.json").is_file():
        print("--- workspace")
        for f in ("policy.json", "persona.json", "mcp.json"):
            print(f"+ .tanka/{f}" if valid_json(Path(ws) / ".tanka" / f) else f"x .tanka/{f} is invalid")
        if not valid_json(Path(ws) / ".claude" / "settings.json"):
            print("x .claude/settings.json is invalid")
            return 1
        print("+ .claude/settings.json")
        sys.stdout.flush()
        return run_py(str(Path(PLUGIN_DIR) / "scripts" / "tanka_status.py"), ws,
                      env=dict(ENV, CLAUDE_PROJECT_DIR=ws)).returncode
    print("(no workspace at {}; run tanka init)".format(ws))
    return 0


def cmd_tools(args: list[str]) -> int:
    sub, rest = (args[0] if args else "check"), args[1:]
    tools = str(Path(PLUGIN_DIR) / "scripts" / "tanka_tools.py")
    if sub in ("check", "list"):
        ws = resolve_ws(rest[0] if rest else "")
        require_ws(ws)
        return run_py(tools, sub, ws).returncode
    if sub == "test":
        tool = rest[0] if rest else ""
        targs = rest[1] if len(rest) > 1 else ""
        ws = resolve_ws(rest[2] if len(rest) > 2 else "")
        require_ws(ws)
        if not tool:
            die("x Usage: tanka tools test <tool> '<json args>' [dir]")
        return run_py(tools, "test", ws, tool, targs or "{}").returncode
    print(f"x Unknown: tanka tools {sub}", file=sys.stderr)
    usage()
    return 1


def cmd_dev(args: list[str]) -> int:
    ws = resolve_ws(args[0] if args else "")
    rest = args[1:]
    if rest[:1] == ["--"]:
        rest = rest[1:]
    require_ws(ws)
    ws = os.path.abspath(ws)
    # Authoring runs from the repo on the user's normal model and settings, with the workspace added as a
    # directory. It never runs inside the workspace, so the assistant's deny rules and CLAUDE.md do not apply
    # to the author, and the assistant itself can never write a tool.
    os.chdir(TANKA_REPO)
    ensure_daemon()
    ENV["TANKA_WORKSPACE"] = ws
    # builder/settings.json declares the Rastro plugin, which authoring uses to map sites (skill map-site).
    return claude_exec([
        "--plugin-dir", str(TANKA_REPO / "builder"), "--settings", str(TANKA_REPO / "builder" / "settings.json"),
        "--add-dir", ws, "--model", full_model(ENV.get("TANKA_DEV_MODEL") or "opus"), "--effort", DEV_EFFORT,
        "--append-system-prompt",
        f"Tanka authoring mode. The Tanka workspace is {ws}. Skills go in {ws}/.claude/skills/<skill>/, tools in "
        f"{ws}/.claude/skills/<skill>/tools/. Follow docs/skill-rules.md, docs/tool-rules.md, for a tool run by a "
        "stronger model docs/subagent-rules.md, for a reusable module under modules/ docs/module-rules.md, and to "
        "reach a website the map-site skill with Rastro, in this repository, and verify every tool with bin/tanka "
        "tools check and bin/tanka tools test before calling it done.", *rest])


def cmd_test(args: list[str]) -> int:
    os.chdir(TANKA_REPO)
    return run_py("-m", "unittest", "discover", "-s", "tests", "-v").returncode


def basename(p: str) -> str:
    return os.path.basename(p.rstrip("/\\")) or p


def dispatch(argv: list[str]) -> int:
    first, rest = (argv[0] if argv else ""), argv[1:]
    scripts = Path(PLUGIN_DIR) / "scripts"
    simple = {"init": cmd_init, "profile": cmd_profile, "start": cmd_start, "resume": cmd_resume, "run": cmd_run,
              "doctor": cmd_doctor, "test": cmd_test, "tools": cmd_tools, "dev": cmd_dev}
    if first in simple:
        return simple[first](rest)
    if first == "modules":
        return py_exec([str(scripts / "tanka_modules.py"), *(rest or ["list"])])
    if first == "ui":
        return py_exec([str(scripts / "tanka_page.py"), *rest])
    if first in ("routine", "trigger"):
        auto = str(scripts / "tanka_automation.py")
        # The second word, when there is one past the verb, names a workspace: resolve it like everywhere else.
        if rest[:1] and rest[0] in ("add", "remove", "run") and len(rest) > 1 and rest[1]:
            ws = resolve_ws(rest[1])
            require_ws(ws)
            return py_exec([auto, first, rest[0], ws, *rest[2:]])
        if rest[:1] == ["list"] and len(rest) > 1 and rest[1]:
            ws = resolve_ws(rest[1])
            require_ws(ws)
            return py_exec([auto, first, "list", ws])
        return py_exec([auto, first, *(rest or ["list"])])
    if first == "automation":
        auto = str(scripts / "tanka_automation.py")
        if rest[:1] and rest[0] in ("alert", "log") and len(rest) > 1 and rest[1]:
            ws = resolve_ws(rest[1])
            require_ws(ws)
            return py_exec([auto, rest[0], ws, *rest[2:]])
        return py_exec([auto, *rest])
    if first == "install":
        if len(rest) < 2 or not rest[0] or not rest[1]:
            die("x Usage: tanka install <module> <workspace> [--scope NAME]")
        # Resolve the workspace here, so names work exactly as in every other command.
        ws = resolve_ws(rest[1])
        require_ws(ws)
        scope = rest[3] if len(rest) > 3 and rest[2] == "--scope" and rest[3] else basename(rest[1])
        return py_exec([str(scripts / "tanka_modules.py"), "install", rest[0], ws, scope])
    if first == "uninstall":
        if len(rest) < 2 or not rest[0] or not rest[1]:
            die("x Usage: tanka uninstall <module> <workspace>")
        ws = resolve_ws(rest[1])
        require_ws(ws)
        return py_exec([str(scripts / "tanka_modules.py"), "uninstall", rest[0], ws, basename(rest[1])])
    if first in ("-h", "--help", "help", ""):
        usage()
        return 0
    if first.startswith("-"):
        print(f"x Unknown option: {first}", file=sys.stderr)
        usage()
        return 1
    if is_module(first) and (TANKA_REPO / "modules" / first / "cli.py").is_file():
        return py_exec([str(TANKA_REPO / "modules" / first / "cli.py"), *rest])
    return cmd_resume(argv)


def main(argv: list[str]) -> int:
    # Every hook, tool and page Claude Code starts from here inherits these: the Python the hooks run with
    # (Windows has no python3) and UTF-8 everywhere.
    tos.export_runtime_env()
    ENV["ENABLE_TOOL_SEARCH"] = "false"
    # The workspace's own settings refuse every prompt without this, so a plain `claude` or `claude -r` there
    # (no Tanka tools, no hooks, the user's plugins) stops at the first message instead of running as an
    # unguarded assistant.
    ENV["TANKA_LAUNCHED"] = "1"
    # Haiku follows its rules less well as the context grows (docs/research/02), so compact earlier than the
    # default. TANKA_AUTOCOMPACT_PCT=100 keeps the default.
    ENV["CLAUDE_CODE_AUTOCOMPACT_PCT_OVERRIDE"] = ENV.get("TANKA_AUTOCOMPACT_PCT") or "60"
    try:
        return dispatch(argv)
    except Exit as e:
        return e.code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
