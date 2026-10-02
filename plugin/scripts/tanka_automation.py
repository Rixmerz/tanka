#!/usr/bin/env python3
"""Tanka automation: routines (every N minutes) and triggers (on an event) that run the assistant unattended.

Each workspace keeps its own in `.claude/automations.json`, a file the user edits
through `tanka routine` / `tanka trigger` and the assistant cannot write (its
Write allowlist stops at `.tanka/` and `notes/`):

    {"routines": {"<name>": {"every": "5m", "task": "..."}},
     "triggers": {"<name>": {"on": "whatsapp:client", "scope": "<ws>", "task": "..."}},
     "alert":    {"notify": true, "whatsapp": "+15551234567"}}

One daemon (`tanka automation daemon`, usually a launchd agent on macOS or a systemd user service on
Linux, `tanka automation service install`; a lock keeps it
to one) polls the event sources that modules declare in module.json ("events"): the
ones some trigger listens to, and the ones marked "always" (the companion's reminders
fire without any trigger). It keeps the
routines' clock, and starts `tanka run` in the workspace. A run is unattended
(TANKA_UNATTENDED=1, sends refused by the harness unless the objective allows
them, and every send tool checks the recipient was opted in with auto_reply).
It ends with a `REPORT:` line; anything but `REPORT: -` reaches the user as a
desktop notification, a WhatsApp to their own number if configured, and the
workspace's `.tanka/automation.log`; a report worth telling is also kept in
`.tanka/reports.jsonl`, where the companion's chat shows it.

Standard library only.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_common as tc  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
TANKA = REPO / "bin" / "tanka"
MODULES = REPO / "modules"
STATE_HOME = Path(os.environ.get("TANKA_AUTOMATION_HOME", Path.home() / ".tanka" / "shared" / "automation"))
WORKSPACES = Path(os.environ.get("TANKA_WORKSPACES", Path.home() / ".tanka" / "workspaces"))
MAX_RUNS_PER_HOUR = int(os.environ.get("TANKA_AUTOMATION_MAX_RUNS_PER_HOUR", "20"))
DEFAULT_BUDGET = os.environ.get("TANKA_AUTOMATION_BUDGET_USD", "0.50")
DEBOUNCE_SECONDS = 10  # messages come in bursts: wait for the burst to end, then run once
NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,40}")
EVERY_RE = re.compile(r"(\d+)\s*(m|min|h|d)")
REPORT_RE = re.compile(r"^\s*[*_`]*REPORT[*_`]*\s*:\s*(.+?)\s*$", re.M)
UNIT = {"m": 60, "min": 60, "h": 3600, "d": 86400}


# ---------------------------------------------------------------- config

def config_file(ws: Path) -> Path:
    return ws / ".claude" / "automations.json"


def load(ws: Path) -> dict:
    f = config_file(ws)
    data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    return {"routines": data.get("routines", {}), "triggers": data.get("triggers", {}), "alert": data.get("alert", {"notify": True})}


def save(ws: Path, data: dict) -> None:
    config_file(ws).parent.mkdir(parents=True, exist_ok=True)
    config_file(ws).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def seconds(every: str) -> int:
    m = EVERY_RE.fullmatch(every.strip())
    if not m or int(m.group(1)) == 0:
        raise ValueError(f"'{every}' is not an interval: use 5m, 30min, 2h or 1d")
    total = int(m.group(1)) * UNIT[m.group(2)]
    if total < 60:
        raise ValueError("the shortest interval is 1m")
    return total


def module_events() -> dict:
    """{module name: poll seconds} for every module that declares events."""
    out = {}
    for mj in sorted(MODULES.glob("*/module.json")):
        ev = json.loads(mj.read_text(encoding="utf-8")).get("events")
        if ev:
            out[mj.parent.name] = int(ev.get("poll_seconds", 60))
    return out


def always_polled() -> set[str]:
    """Modules whose events are polled even when no trigger listens: their poll does work of its own."""
    return {mj.parent.name for mj in MODULES.glob("*/module.json")
            if (json.loads(mj.read_text(encoding="utf-8")).get("events") or {}).get("always")}


def check_on(on: str) -> tuple[str, str]:
    source, _, value = on.partition(":")
    sources = module_events()
    if source not in sources or not value:
        known = ", ".join(f"{s}:<…>" for s in sources) or "none"
        raise ValueError(f"'{on}' is not an event: use one of {known}")
    return source, value


def workspaces() -> list[Path]:
    seen, out = set(), []
    candidates = sorted(WORKSPACES.iterdir()) if WORKSPACES.is_dir() else []
    if os.environ.get("TANKA_HOME"):
        candidates.append(Path(os.environ["TANKA_HOME"]))
    for c in candidates:
        real = c.resolve()
        if (real / ".tanka" / "policy.json").is_file() and real not in seen:
            seen.add(real)
            out.append(real)
    return out


def name_of(ws: Path) -> str:
    for c in WORKSPACES.iterdir() if WORKSPACES.is_dir() else []:
        if c.resolve() == ws:
            return c.name
    return ws.name


# ---------------------------------------------------------------- one run

def may_send(ws: Path, scope: str) -> bool:
    """True when the user opted some recipient of this workspace in to replies without them."""
    for module, registry, key in (("whatsapp", "contacts.json", "roles"), ("gmail", "accounts.json", "accounts")):
        home = Path(os.environ.get(f"TANKA_{module.upper()}_HOME", Path.home() / ".tanka" / "shared" / module))
        try:
            entries = json.loads((home / registry).read_text(encoding="utf-8")).get(key, {})
        except (OSError, json.JSONDecodeError):
            continue
        if any(v.get("workspace") == scope and v.get("auto_reply") for v in entries.values()):
            return True
    return False


def prompt(task: str, woke: str) -> str:
    return (f"{task}\n\n"
            f"What woke you: {woke}\n\n"
            "You are running on your own: the user is not here to answer questions. Do what the task says with your "
            "skills. Anything that needs the user (a reply they must approve, a decision, a detail you lack) goes in a "
            "file in .tanka/drafts/, and you say so. A send tool that refuses because the user is not here is "
            "expected: save the proposal instead and do not retry.\n"
            "End with one line `REPORT: <one sentence for the user>`, or `REPORT: -` when there is nothing they need to know.")


def objective(ws: Path, name: str, scope: str) -> str:
    oid = f"auto-{name}"
    classes = ["read", "draft"] + (["send"] if may_send(ws, scope) else [])
    obj = {"id": oid, "title": f"Automation: {name}", "status": "active",
           "goal": "Do the automation's task without the user, and leave for them what needs them",
           "done_when": ["The task is done, or what needs the user is saved in .tanka/drafts/", "The run ends with a REPORT line"],
           "allowed_tool_classes": classes, "allowed_tools": [], "may_send": False, "recipient_allowlist": [],
           "max_tool_calls": 20}
    d = ws / ".tanka" / "objectives"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{oid}.json").write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    return oid


def log(ws: Path, line: str) -> None:
    with (ws / ".tanka" / "automation.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")


def keep_report(ws: Path, kind: str, name: str, spec: dict, code: int, report: str) -> None:
    """One JSON line per report worth telling, for whatever shows them (the companion's chat)."""
    entry = {"t": round(time.time(), 3), "kind": kind, "name": name, "on": spec.get("on"), "exit": code, "report": report[:500]}
    with (ws / ".tanka" / "reports.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def alert(ws: Path, cfg: dict, text: str) -> None:
    title = f"Tanka · {name_of(ws)}"
    if cfg.get("notify", True):
        tc.desktop_notify(title, text)
    if cfg.get("whatsapp"):
        subprocess.run([sys.executable, str(MODULES / "whatsapp" / "cli.py"), "alert", cfg["whatsapp"], f"{title}: {text}"],
                       check=False, timeout=120, capture_output=True)


def run_once(ws: Path, kind: str, name: str, spec: dict, woke: str, runner=None) -> str:
    """Run the assistant for one routine or trigger; returns its report ('-' when nothing to tell)."""
    scope = spec.get("scope") or name_of(ws)
    oid = objective(ws, name, scope)
    cmd = [str(TANKA), "run", prompt(spec["task"], woke), str(ws), "--objective", oid,
           "--max-turns", str(spec.get("max_turns", 15)), "--budget", str(spec.get("budget", DEFAULT_BUDGET))]
    runner = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL))
    # `tanka run` activates its objective in the workspace's state, where the user's own sessions read it.
    # Put back whatever was there, so an automation never leaves the user's next session restricted.
    active = ws / ".tanka" / "state" / "objective.json"
    before = active.read_text(encoding="utf-8") if active.is_file() else None
    try:
        p = runner(cmd)
    finally:
        if before is None:
            active.unlink(missing_ok=True)
        else:
            active.write_text(before, encoding="utf-8")
    found = REPORT_RE.findall(p.stdout or "")
    report = found[-1].strip() if found else ("the run failed: " + (p.stderr or p.stdout or "").strip()[-200:] if p.returncode else "-")
    log(ws, f"{kind} {name} | {woke} | exit {p.returncode} | {report}")
    if report not in ("-", "—", ""):
        keep_report(ws, kind, name, spec, p.returncode, report)
        alert(ws, load(ws)["alert"], f"{name}: {report}")
    return report


# ---------------------------------------------------------------- the daemon

class Daemon:
    def __init__(self, runner=None, events=None, clock=time.time):
        self.state_file = STATE_HOME / "state.json"
        self.state = json.loads(self.state_file.read_text(encoding="utf-8")) if self.state_file.is_file() else {}
        self.runner, self.clock = runner, clock
        self.events = events or self.fetch_events
        self.next_poll: dict[str, float] = {}
        self.pending: dict[tuple, dict] = {}   # (ws, trigger) -> {"since", "last", "what": [...]}
        self.runs: list[float] = []

    def save_state(self) -> None:
        STATE_HOME.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state), encoding="utf-8")

    @staticmethod
    def fetch_events(module: str) -> list[dict]:
        p = subprocess.run([sys.executable, str(MODULES / module / "cli.py"), "events"],
                           capture_output=True, text=True, timeout=180)
        if p.returncode != 0:
            raise RuntimeError((p.stderr or p.stdout).strip()[-300:])
        return [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith("{")]

    def budget_ok(self) -> bool:
        now = self.clock()
        self.runs = [t for t in self.runs if now - t < 3600]
        return len(self.runs) < MAX_RUNS_PER_HOUR

    def start(self, ws: Path, kind: str, name: str, spec: dict, woke: str) -> None:
        if not self.budget_ok():
            log(ws, f"{kind} {name} | skipped: over {MAX_RUNS_PER_HOUR} runs this hour (TANKA_AUTOMATION_MAX_RUNS_PER_HOUR)")
            return
        self.runs.append(self.clock())
        run_once(ws, kind, name, spec, woke, self.runner)

    def tick(self) -> None:
        now = self.clock()
        spaces = [(ws, load(ws)) for ws in workspaces()]
        # Routines: due when their interval has passed since the last run.
        for ws, cfg in spaces:
            for name, spec in cfg["routines"].items():
                key = f"{ws}|routine|{name}"
                if now - self.state.get(key, 0) >= seconds(spec["every"]):
                    self.state[key] = now
                    self.save_state()
                    self.start(ws, "routine", name, spec, f"the routine \"{name}\" (every {spec['every']})")
        # Triggers: poll each source that some trigger listens to, at the module's own pace.
        wanted = {check_on(t["on"])[0] for _, cfg in spaces for t in cfg["triggers"].values()} | always_polled()
        for module, pace in module_events().items():
            if module not in wanted or now < self.next_poll.get(module, 0):
                continue
            self.next_poll[module] = now + pace
            try:
                events = self.events(module)
            except Exception as e:  # a source that is down must not stop the others
                self.next_poll[module] = now + max(pace, 60)
                print(f"{datetime.now():%H:%M:%S} {module} events failed: {e}", file=sys.stderr)
                continue
            for ev in events:
                self.route(ev, spaces, now)
        # Fire triggers whose burst is over (quiet for DEBOUNCE_SECONDS, or waiting a minute already).
        for key, p in list(self.pending.items()):
            if now - p["last"] >= DEBOUNCE_SECONDS or now - p["since"] >= 60:
                ws, name, spec = p["ws"], p["name"], p["spec"]
                del self.pending[key]
                self.start(ws, "trigger", name, spec, "; ".join(dict.fromkeys(p["what"])))

    def route(self, ev: dict, spaces, now: float) -> None:
        for ws, cfg in spaces:
            for name, spec in cfg["triggers"].items():
                source, value = check_on(spec["on"])
                scope = spec.get("scope") or name_of(ws)
                if ev.get("source") != source or ev.get("scope") != scope:
                    continue
                # A module's event may carry its own `match` (what `--on source:<value>` compares) and `what` (the
                # sentence the run is told); WhatsApp and Gmail predate that and are described here.
                match = ev.get("match", ev.get("role") if source == "whatsapp" else ev.get("account"))
                if value not in ("*", match):
                    continue
                what = ev.get("what") or (
                    f"a new WhatsApp message from {ev['contact']} ({ev.get('name') or 'no name'}, role {ev['role']})"
                    if source == "whatsapp" else f"a new email from {ev.get('from') or 'someone'} in {ev['account']}")
                p = self.pending.setdefault((str(ws), name), {"ws": ws, "name": name, "spec": spec, "since": now, "what": []})
                p["last"] = now
                p["what"].append(what)

    def loop(self) -> int:
        lock = hold_lock()
        if lock is None:
            print(f"= The automation daemon is already running (pid {daemon_pid()}).")
            return 0
        print(f"Tanka automation running (pid {os.getpid()}): {len(workspaces())} workspace(s). Ctrl+C to stop.", flush=True)
        while True:
            try:
                self.tick()
            except ValueError as e:
                print(f"x {e}", file=sys.stderr)
            time.sleep(2)


# ---------------------------------------------------------------- one daemon at a time

LOCK_FILE = STATE_HOME / "daemon.lock"


def hold_lock():
    """The open lock file while this process is the daemon, or None when another one is."""
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    return f


def daemon_pid() -> int | None:
    """The running daemon's pid, or None. Two daemons would start every run twice."""
    if not LOCK_FILE.is_file():
        return None
    with open(LOCK_FILE, "a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            pid = f.read().strip()
            return int(pid) if pid.isdigit() else -1
        fcntl.flock(f, fcntl.LOCK_UN)
    return None


def start_detached() -> int:
    """Start the daemon in the background, logging to daemon.log; returns its pid (the running one if any)."""
    pid = daemon_pid()
    if pid:
        return pid
    STATE_HOME.mkdir(parents=True, exist_ok=True)
    log = open(STATE_HOME / "daemon.log", "a", encoding="utf-8")
    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "daemon"], stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        time.sleep(0.1)
        pid = daemon_pid()
        if pid:
            return pid
    raise ValueError(f"the automation daemon did not start; see {STATE_HOME / 'daemon.log'}")


# ---------------------------------------------------------------- service (launchd on macOS, systemd --user on Linux)

UNIT_PATH = Path.home() / ".config" / "systemd" / "user" / "tanka-automation.service"
LAUNCHD_LABEL = "com.tanka.automation"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def service_env() -> dict[str, str]:
    """What the daemon needs from this shell: Tanka's own settings, PATH and where Claude Code is."""
    return {k: v for k, v in sorted(os.environ.items())
            if k.startswith(("TANKA_", "RASTRO_")) or k in ("PATH", "CLAUDE_BIN", "CLAUDE_CONFIG_DIR")}


def stop_detached() -> None:
    """A daemon started by hand holds the lock, and the service's own would exit at once: stop it first."""
    pid = daemon_pid()
    if pid and pid > 0:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not daemon_pid():
                return
            time.sleep(0.1)


def quiet_run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, capture_output=True, text=True)


def service_installed() -> bool:
    return (PLIST_PATH if sys.platform == "darwin" else UNIT_PATH).is_file()


def service_start(run=quiet_run) -> bool:
    """Start the installed service now (it may have been stopped); True when it took."""
    if sys.platform == "darwin":
        target = f"gui/{os.getuid()}"
        if run(["launchctl", "bootstrap", target, str(PLIST_PATH)]).returncode == 0:
            return True
        return run(["launchctl", "kickstart", f"{target}/{LAUNCHD_LABEL}"]).returncode == 0  # already loaded
    return run(["systemctl", "--user", "start", "tanka-automation.service"]).returncode == 0


def service_stop(run=quiet_run) -> None:
    """Stop the service until the next login (launchd would restart a daemon killed by hand)."""
    if sys.platform == "darwin":
        run(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"])
    else:
        run(["systemctl", "--user", "stop", "tanka-automation.service"])


def service(action: str, run=None) -> int:
    """Keep the daemon running across logins and restart it if it crashes."""
    run = run or (lambda cmd: subprocess.run(cmd, check=False, stderr=subprocess.DEVNULL if "bootout" in cmd else None))
    if action not in ("install", "remove", "status"):
        print("Usage: tanka automation service install|remove|status", file=sys.stderr)
        return 1
    if sys.platform == "darwin":
        return launchd_service(action, run)
    if not shutil.which("systemctl"):
        print("x Neither launchd nor systemctl is available: run `tanka automation daemon` under your own supervisor.",
              file=sys.stderr)
        return 1
    return systemd_service(action, run)


def launchd_service(action: str, run) -> int:
    domain = f"gui/{os.getuid()}"
    if action == "install":
        import plistlib
        STATE_HOME.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLIST_PATH.write_bytes(plistlib.dumps({
            "Label": LAUNCHD_LABEL, "ProgramArguments": [str(TANKA), "automation", "daemon"],
            "EnvironmentVariables": service_env(), "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
            "StandardOutPath": str(STATE_HOME / "daemon.log"), "StandardErrorPath": str(STATE_HOME / "daemon.log")}))
        stop_detached()
        run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])  # a previous install, if any
        code = run(["launchctl", "bootstrap", domain, str(PLIST_PATH)]).returncode
        if code == 0:
            print(f"+ Installed {PLIST_PATH}: the daemon starts at login and restarts if it crashes.")
        return code
    if action == "remove":
        run(["launchctl", "bootout", f"{domain}/{LAUNCHD_LABEL}"])
        PLIST_PATH.unlink(missing_ok=True)
        print("- Service removed.")
        return 0
    if not PLIST_PATH.is_file():
        print("= No service installed: tanka automation service install")
        return 0
    return run(["launchctl", "print", f"{domain}/{LAUNCHD_LABEL}"]).returncode


def systemd_service(action: str, run) -> int:
    if action == "install":
        env = "".join(f"Environment={k}={v}\n" for k, v in service_env().items())
        UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        UNIT_PATH.write_text(f"""[Unit]
Description=Tanka automation (routines, triggers, the companion's reminders and brief)

[Service]
ExecStart={TANKA} automation daemon
Restart=on-failure
RestartSec=10
{env}
[Install]
WantedBy=default.target
""", encoding="utf-8")
        stop_detached()
        run(["systemctl", "--user", "daemon-reload"])
        return run(["systemctl", "--user", "enable", "--now", "tanka-automation.service"]).returncode
    if action == "remove":
        run(["systemctl", "--user", "disable", "--now", "tanka-automation.service"])
        UNIT_PATH.unlink(missing_ok=True)
        run(["systemctl", "--user", "daemon-reload"])
        print("- Service removed.")
        return 0
    return run(["systemctl", "--user", "status", "--no-pager", "tanka-automation.service"]).returncode


# ---------------------------------------------------------------- CLI

def listing(ws: Path) -> None:
    cfg = load(ws)
    print(f"{name_of(ws)} ({ws}):")
    for name, s in cfg["routines"].items():
        print(f"  routine {name:<16} every {s['every']:<6} {s['task'][:70]}")
    for name, s in cfg["triggers"].items():
        print(f"  trigger {name:<16} on {s['on']:<18} {s['task'][:70]}")
    if not cfg["routines"] and not cfg["triggers"]:
        print("  (none)")
    a = cfg["alert"]
    print(f"  alerts: {'desktop' if a.get('notify', True) else 'no desktop'}" + (f", WhatsApp {a['whatsapp']}" if a.get("whatsapp") else ""))


def ensure() -> int | None:
    """Run by an interactive `tanka start`/`dev`: start the daemon when none runs and there is work for
    it (a routine, a trigger, or a companion whose reminders and brief need its poll). Returns the pid it
    started, or None. Silent when one already runs."""
    if daemon_pid():
        return None
    jobs, companions = 0, 0
    for ws in workspaces():
        try:
            cfg = load(ws)
        except ValueError:
            continue
        jobs += len(cfg["routines"]) + len(cfg["triggers"])
        companions += (ws / ".claude" / "companion.json").is_file()
    if not jobs and not companions:
        return None
    if service_installed() and service_start():
        for _ in range(50):
            if (pid := daemon_pid()) and pid > 0:
                break
            time.sleep(0.1)
        print(f"+ Started the automation service again (pid {pid}): it was stopped. Stop it with: tanka automation stop")
        return pid
    pid = start_detached()
    print(f"+ Started the automation daemon (pid {pid}): reminders, the companion's brief and your {jobs} "
          "routine(s) and trigger(s) run from it. Stop it with: tanka automation stop")
    return pid


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 0
    what, rest = argv[0], argv[1:]
    try:
        if what == "ensure":
            ensure()
            return 0
        if what == "daemon":
            if "--detach" in rest:
                print(f"+ The automation daemon runs in the background (pid {start_detached()}); "
                      f"log in {STATE_HOME / 'daemon.log'}. Stop it with: tanka automation stop")
                return 0
            return Daemon().loop()
        if what in ("status", "stop"):
            pid = daemon_pid()
            if not pid:
                print("= The automation daemon is not running. Start it with: tanka automation daemon [--detach]")
                return 0
            if what == "status":
                print(f"The automation daemon is running (pid {pid}).")
                return 0
            if pid < 0:  # locked, but the pid is still being written: never signal -1 (every process)
                print("x The daemon is starting; run this again in a second.", file=sys.stderr)
                return 1
            if service_installed():
                service_stop()
                print(f"- Stopped the automation service (pid {pid}) until your next login, or until `tanka start` "
                      "starts it again. To remove it: tanka automation service remove")
                return 0
            os.kill(pid, signal.SIGTERM)
            print(f"- Stopped the automation daemon (pid {pid}). A run it had started finishes on its own.")
            return 0
        if what == "service":
            return service(rest[0] if rest else "status")
        if what in ("routine", "trigger") and rest and rest[0] == "list":
            for ws in ([Path(rest[1])] if len(rest) > 1 else workspaces()):
                listing(ws)
            return 0
        if what in ("routine", "trigger") and len(rest) >= 3 and rest[0] in ("add", "remove", "run"):
            op, ws, name = rest[0], Path(rest[1]), rest[2]
            if not NAME_RE.fullmatch(name):
                raise ValueError(f"'{name}' is not a name: lowercase letters, digits, - and _")
            cfg = load(ws)
            table = cfg["routines" if what == "routine" else "triggers"]
            if op == "remove":
                if table.pop(name, None) is None:
                    raise ValueError(f"there is no {what} named {name}")
                save(ws, cfg)
                print(f"- {what} {name} removed.")
                return 0
            if op == "run":
                if name not in table:
                    raise ValueError(f"there is no {what} named {name}")
                print(run_once(ws, what, name, table[name], f"the user ran the {what} \"{name}\" by hand"))
                return 0
            opts, task = rest[3:], None
            spec = {}
            i = 0
            while i < len(opts):
                if opts[i] in ("--every", "--on", "--scope", "--budget") and i + 1 < len(opts):
                    spec[opts[i][2:]] = opts[i + 1]
                    i += 2
                else:
                    task = opts[i]
                    i += 1
            if not task:
                raise ValueError("say what the assistant should do, in quotes")
            if what == "routine":
                seconds(spec.get("every", ""))
            else:
                check_on(spec.get("on", ""))
            spec["task"] = task
            table[name] = spec
            save(ws, cfg)
            print(f"+ {what} {name} saved in {config_file(ws)}.")
            print("  It runs while the automation daemon is up: tanka automation service install (or: tanka automation daemon).")
            return 0
        if what == "alert" and rest:
            ws, cfg = Path(rest[0]), load(Path(rest[0]))
            opts = rest[1:]
            if "--whatsapp" in opts:
                cfg["alert"]["whatsapp"] = opts[opts.index("--whatsapp") + 1]
            if "--no-whatsapp" in opts:
                cfg["alert"].pop("whatsapp", None)
            if "--no-notify" in opts:
                cfg["alert"]["notify"] = False
            if "--notify" in opts:
                cfg["alert"]["notify"] = True
            save(ws, cfg)
            listing(ws)
            return 0
        if what == "log" and rest:
            f = Path(rest[0]) / ".tanka" / "automation.log"
            print("\n".join(f.read_text(encoding="utf-8").splitlines()[-30:]) if f.is_file() else "(no runs yet)")
            return 0
    except (ValueError, IndexError) as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print("Usage: see `tanka help` (routine, trigger, automation)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
