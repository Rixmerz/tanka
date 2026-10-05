// cauce on the page: the Code tab, and finished or stuck tasks in the chat (docs/page.md).
// A worker here is not a Claude Code subagent: it is a one-shot `claude -p` that cauce launches for one
// attempt of one task, in a model and effort cell, with its own turn and dollar limits and only the tools
// and MCP servers that task needs. The tab shows the ones out now, and each task's way through its ladder.
// Above it, the projects: every repository this workspace may use, and those cauce knows that it could.
(() => {
  const { el, api, act, hm, when, plural, icon, remember } = Tanka;
  const T = {
    tab: "Code",
    addProject: "Add project", closeAdd: "Close",
    known: "Repositories cauce has worked in that this workspace does not use yet:", noKnown: "cauce has not worked anywhere else yet.",
    allow: "Use here", gone: "directory gone", lastSeen: t => `last used ${t}`, sessionsN: n => plural(n, "session", "sessions"),
    views: { tasks: "Tasks", sessions: "Sessions", problems: "Problems" },
    needs: "Needs you", agents: "Agents working", between: "Between attempts", inSessions: "In your Claude Code sessions",
    queued: "Pending", done: "Done",
    nothing: "Nothing here.", noRepos: s => `This workspace may use no repository yet. Add one with Add project, or in a terminal: tanka cauce allow ${s} <directory>`,
    notReachable: "cauce did not answer:", noAgents: "No worker is out now. Pending tasks run when you press Run queue.",
    beside: "runs beside others", waits: "waits its turn",
    attempt: n => `attempt ${n || 1}`, cost: c => `$${(c || 0).toFixed(2)}`,
    running: (since, turns, budget) => [since ? `running ${since}` : "", turns ? `up to ${turns} turns` : "", budget != null ? `$${Number(budget).toFixed(2)} left` : ""].filter(Boolean).join(" · "),
    dead: "its cauce process is gone; the next sweep marks it interrupted",
    capsNone: "no MCP servers", planning: "choosing the next cell",
    paused: r => `Paused: ${r || "a task did not pass"}`, unpause: "Reopen", run: "Run queue", confirmRun: "Confirm: run and spend",
    working: "running its queue", cancel: "Cancel", confirmCancel: "Confirm cancel",
    details: "Details", hide: "Hide", loading: "Loading…", branch: "Branch to review:", merge: "Merge with:", toReview: "to review",
    passedAt: c => `passed at ${c}`, startedAt: c => `started at ${c}`, why: "Why it started there:",
    stepLine: a => `${a.cell} · ${a.passed ? "passed" : (a.failure || "failed")}` + (a.turns ? ` · ${a.turns} turns` : "") + (a.cost_usd ? ` · $${a.cost_usd.toFixed(2)}` : ""),
    move: m => ({ retry: "retried", more_effort: "more effort", next_model: "next model", more_turns: "more turns", replan: "replan" }[m] || m),
    stepTitle: s => `${s.cell}: ${s.passed ? "passed" : (s.failure || "failed")}${s.move ? " → " + s.move : ""}${s.move_reason ? " (" + s.move_reason + ")" : ""}`,
    sessionsIntro: "Claude Code sessions cauce saw in these projects. Resume one in your terminal with its command; the page cannot attach to a session.",
    noSessions: "No session in this project yet. Only enrolled projects (a .cauce/ folder) show their sessions.",
    lastPrompt: p => `last: ${p}`, session: id => `session ${String(id).slice(0, 8)}`,
    sessionMeta: x => `${plural(x.prompts || 0, "prompt", "prompts")} · started ${when(epoch(x.started_at))} · $${(x.cost_usd || 0).toFixed(2)}`,
    noPrompt: "(no prompt recorded)", runningNow: "a task is running", copy: "Copy", copied: "Copied",
    searchPh: "Search problems and fixes", everywhere: "All of cauce", here: "These projects",
    noProblems: q => q ? `cauce remembers nothing matching “${q}”.` : "cauce has not recorded a problem yet.",
    state: { open: "open", solved: "solved", recurring: "came back" },
    seen: (a, b) => [a ? `first seen ${when(epoch(a))}` : "", b && b !== a ? `last seen ${when(epoch(b))}` : ""].filter(Boolean).join(" · "),
    symptom: "Symptom: ", whyFailed: "Why it failed: ", whyWorked: "Why: ", disproved: d => `worked until ${when(epoch(d))}, then the problem came back`,
    deadEnds: n => plural(n, "dead end", "dead ends"), task: n => `task #${n}`, commit: c => `commit ${String(c).slice(0, 10)}`,
    chatPassed: t => `#${t.id} passed in ${t.repo} at ${t.cell}`, chatStopped: t => `#${t.id} stopped in ${t.repo}: ${t.status}`,
    hello: "Ask me what is going on in your code, what was tried on a problem, or tell me what to fix or build; I queue it for cauce, and you run the queue in the Code tab.",
    suggest: [["What needs me in my code?", true], ["Queue in cauce: ", false]],
  };
  const MARK = { worked: "✓", failed: "✗", partial: "◐", pending: "…", disproved: "↺" };
  const stored = k => { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } };
  const st = { open: null, detail: null, confirm: "", project: stored("cauce.project"), view: stored("cauce.view") || "tasks",
    adding: false, known: null, sessions: null, problems: null, q: "", everywhere: true,
    copied: "", msg: "", editing: false, searching: false, lastSig: "", loadedAt: 0, scope: "" };
  const epoch = iso => (Date.parse(iso) || 0) / 1000;
  const post = (sc, name, body) => act(api(`/api/m/cauce/${name}`, Object.assign({ scope: sc.scope }, body)));
  const get = (sc, name, q) => api(`/api/m/cauce/${name}?scope=${encodeURIComponent(sc.scope)}` + Object.entries(q || {}).map(([k, v]) => `&${k}=${encodeURIComponent(v)}`).join(""));
  const since = iso => {
    const ms = Date.now() - Date.parse(iso);
    if (!(ms >= 0)) return "";
    const m = Math.round(ms / 60000);
    return m < 1 ? "under a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  // While a field of the tab has the focus, polls do not rebuild the tab: what is being typed stays.
  const field = (tag, props, ...kids) => el(tag, Object.assign({}, props, { on: Object.assign({
    focus: () => { st.editing = true; }, blur: () => { st.editing = false; } }, props.on || {}) }), ...kids);

  function guarded(key, label, armed, fn) {
    const on = st.confirm === key;
    return el("button", { type: "button", class: on ? "btn primary" : "btn",
      on: { click: () => { if (on) { st.confirm = ""; fn(); } else { st.confirm = key; Tanka.render(); } } } }, on ? armed : label);
  }

  async function toggle(sc, id) {
    st.open = st.open === id ? null : id; st.detail = null; Tanka.render();
    if (!st.open) return;
    try { st.detail = await get(sc, "task", { id }); } catch (e) { st.detail = { error: e.message }; }
    Tanka.render();
  }

  const loading = new Set();
  async function load(sc, what) {
    if (loading.has(what)) return;
    loading.add(what);
    const asked = st.q;
    try {
      if (what === "known") st.known = (await get(sc, "projects")).projects;
      if (what === "sessions") st.sessions = (await get(sc, "sessions", st.project ? { repo: st.project } : {})).sessions;
      if (what === "problems") st.problems = (await get(sc, "problems", { q: st.q, all: st.everywhere ? "1" : "" })).problems;
    } catch (e) { st[what] = { error: e.message }; }
    loading.delete(what);
    st.loadedAt = Date.now();
    if (what === "problems" && st.q !== asked) return load(sc, what);  // typed on while it loaded
    Tanka.render();
  }

  function setProject(sc, name) {
    st.project = name; st.sessions = null; remember("cauce.project", name); Tanka.render();
    if (st.view === "sessions") load(sc, "sessions");
  }
  function setView(sc, v) {
    st.view = v; remember("cauce.view", v); Tanka.render();
    if (v !== "tasks") load(sc, v);
  }

  // ------------------------------------------------------------ the projects
  function projectBar(sc, mod) {
    const needs = {};
    for (const t of (mod.board && mod.board.needs_you) || []) needs[t.repo_name] = (needs[t.repo_name] || 0) + 1;
    const chip = (name, label) => el("button", { type: "button", class: "chip" + (st.project === name ? " on" : ""), "aria-pressed": String(st.project === name),
      on: { click: () => setProject(sc, name) } }, label, name && needs[name] ? el("span", { class: "badge" }, needs[name]) : null);
    return el("div", { class: "projbar" }, (mod.repos || []).map(n => chip(n, n)),
      el("button", { type: "button", class: "ghost", "aria-expanded": String(st.adding),
        on: { click: () => { st.adding = !st.adding; Tanka.render(); if (st.adding) load(sc, "known"); } } }, st.adding ? T.closeAdd : "+ " + T.addProject));
  }

  function knownList(sc) {
    if (!st.adding) return null;
    if (!st.known) return el("p", { class: "meta" }, T.loading);
    if (st.known.error) return el("p", { class: "meta" }, st.known.error);
    const others = st.known.filter(p => !p.allowed);
    return el("div", { class: "card" }, el("div", { class: "meta" }, others.length ? T.known : T.noKnown),
      others.length ? el("ul", { class: "known" }, others.map(p => el("li", {},
        el("span", { class: "dir" }, p.dir || p.repo),
        el("span", { class: "meta" }, [p.exists ? T.lastSeen(when(epoch(p.last_seen))) : T.gone, T.sessionsN(p.sessions || 0)].join(" · ")),
        p.exists ? el("button", { type: "button", class: "btn", on: { click: async () => {
          await act(api("/api/m/cauce/allow", { scope: sc.scope, dir: p.dir })); st.known = null; load(sc, "known"); } } }, T.allow) : null))) : null);
  }

  function viewBar(sc) {
    return el("div", { class: "modes views", role: "radiogroup" }, Object.entries(T.views).map(([v, label]) =>
      el("button", { type: "button", role: "radio", "aria-checked": String(st.view === v), on: { click: () => setView(sc, v) } }, label)));
  }

  // ------------------------------------------------------------ tasks
  function flowStrip(t) {
    const f = t.flow;
    if (!f) return null;
    const now = t.worker ? t.worker.cell : null, byCell = {};
    for (const s of f.steps || []) (byCell[s.cell] = byCell[s.cell] || []).push(s);
    const cells = [...(f.ladder || [])];
    for (const c of [...Object.keys(byCell), now]) if (c && !cells.includes(c)) cells.push(c);
    const out = [el("span", { class: "kind" }, f.kind || "")];
    cells.forEach((c, i) => {
      const tries = byCell[c] || [], passed = tries.some(s => s.passed);
      const cls = c === now ? "now" : passed ? "ok" : tries.length ? "fail" : c === f.start ? "start" : "";
      const ico = c === now ? icon("activity") : passed ? icon("check") : tries.length ? icon("x") : null;
      if (i) out.push(el("span", { class: "to", "aria-hidden": "true" }, "→"));
      out.push(el("span", { class: "step " + cls, title: tries.map(T.stepTitle).join("\n") || (c === f.start ? T.startedAt(c) : c) },
        ico, c, tries.length > 1 ? ` ×${tries.length}` : ""));
    });
    return el("div", { class: "flow" }, out);
  }

  function detailView(t) {
    if (st.open !== t.id) return null;
    const d = st.detail;
    if (!d) return el("p", { class: "meta" }, T.loading);
    if (d.error) return el("p", { class: "meta" }, d.error);
    const reasons = (d.plan && d.plan.reasons) || [];
    return el("div", { class: "evidence" },
      reasons.length ? el("div", { class: "meta" }, T.why + " " + reasons.join("; ")) : null,
      el("ol", { class: "steps" }, (d.attempts || []).map(a => el("li", {},
        T.stepLine(a), a.move ? el("span", { class: "why" }, ` → ${T.move(a.move)}${a.move_reason ? ": " + a.move_reason : ""}`) : null,
        a.summary ? el("div", { class: "meta" }, a.summary) : null))),
      d.branch ? el("div", {}, T.branch + " " + d.branch, el("div", { class: "meta" }, T.merge + " git merge " + d.branch)) : null);
  }

  const description = t => t.body && t.body.trim() !== (t.title || "").trim()
    ? el("p", { class: "desc" + (st.open === t.id ? " full" : "") }, t.body) : null;
  const detailsBtn = (sc, t) => el("button", { type: "button", class: "ghost", on: { click: () => toggle(sc, t.id) } }, st.open === t.id ? T.hide : T.details);

  function card(sc, t, extra, buttons, inLane) {
    return el("article", { class: "card" },
      el("div", { class: "row" }, inLane ? null : el("span", { class: "chip on" }, t.repo_name || "?"),
        el("span", { class: "meta" }, "#" + t.id), extra ? el("span", { class: "meta" }, extra) : null, el("span", { class: "spacer" }),
        buttons || null, detailsBtn(sc, t)),
      el("p", { class: "text" }, t.title || ""), description(t),
      t.asks ? el("div", { class: "meta" }, t.asks) : null,
      t.status === "queued" && t.parallel != null
        ? el("div", { class: "meta", title: t.parallel_reason || "" }, el("span", { class: "chip" + (t.parallel ? " on" : "") },
            t.parallel ? T.beside : T.waits), " ", t.parallel_reason || "") : null,
      flowStrip(t), detailView(t));
  }

  function agent(sc, t, cancel) {
    const w = t.worker;
    return el("article", { class: "card agent" },
      el("div", { class: "row" }, el("span", { class: "dot" + (w.alive ? "" : " off"), title: w.alive ? "" : T.dead }),
        el("span", { class: "cell" }, w.cell), el("span", { class: "meta" }, T.attempt(w.seq)),
        el("span", { class: "chip on" }, t.repo_name || "?"), el("span", { class: "meta" }, "#" + t.id),
        el("span", { class: "spacer" }), cancel, detailsBtn(sc, t)),
      el("p", { class: "text" }, t.title || ""), description(t),
      el("div", { class: "meta" }, T.running(since(w.started_at), w.max_turns, w.budget_usd), " · ", T.cost(t.cost_usd) + " so far"),
      el("div", { class: "caps" }, (w.capabilities || []).length ? w.capabilities.map(c => el("span", { class: "chip" }, c)) : el("span", { class: "meta" }, T.capsNone)),
      w.alive ? null : el("div", { class: "meta" }, T.dead),
      flowStrip(t), detailView(t));
  }

  function section(title, nodes, count, empty) {
    return [el("h2", {}, title, el("span", { class: "meta" }, " " + (count == null ? nodes.length : count))),
      nodes.length ? nodes : el("p", { class: "empty" }, empty || T.nothing)];
  }

  function tasksView(sc, mod) {
    const b = mod.board, working = new Set(mod.working || []);
    const keep = t => !st.project || t.repo_name === st.project;
    const cancel = t => guarded("cancel:" + t.id, T.cancel, T.confirmCancel, () => post(sc, "cancel", { id: t.id }));
    const running = (b.running || []).filter(keep);
    const agents = running.filter(t => t.worker), between = running.filter(t => t.flow && !t.worker), sessions = (b.answering || []).filter(keep);
    const lanes = (mod.repos || []).filter(n => !st.project || n === st.project).map(name => {
      const lane = (b.queued || []).find(l => l.repo_name === name) || { tasks: [], paused: false };
      if (!lane.tasks.length && !lane.paused && !working.has(name)) return null;
      return el("div", { class: "card" },
        el("div", { class: "row" }, el("strong", {}, name), el("span", { class: "meta" }, plural(lane.tasks.length, "task", "tasks")),
          working.has(name) ? el("span", { class: "chip on" }, T.working) : null, el("span", { class: "spacer" }),
          lane.paused ? el("button", { type: "button", class: "btn", on: { click: () => post(sc, "unpause", { repo: name }) } }, T.unpause) : null,
          lane.tasks.length && !lane.paused && !working.has(name) ? guarded("work:" + name, T.run, T.confirmRun, () => post(sc, "work", { repo: name })) : null),
        lane.paused ? el("div", { class: "meta" }, T.paused(lane.reason)) : null,
        lane.tasks.map(t => card(sc, t, "", cancel(t), true)));
    }).filter(Boolean);
    const pending = lanes.length ? (b.queued || []).filter(l => !st.project || l.repo_name === st.project).reduce((n, l) => n + l.tasks.length, 0) : 0;
    return [
      ...section(T.needs, (b.needs_you || []).filter(keep).map(t => card(sc, t, t.status === "done" ? T.toReview : t.status))),
      ...section(T.agents, agents.map(t => agent(sc, t, cancel(t))), null, T.noAgents),
      ...(between.length ? section(T.between, between.map(t => card(sc, t, T.planning, cancel(t)))) : []),
      ...section(T.queued, lanes, pending),
      ...section(T.done, (b.done || []).filter(keep).map(t => card(sc, t, `${T.passedAt(t.final_cell || "")} · ${T.cost(t.cost_usd)}`))),
      ...(sessions.length ? section(T.inSessions, sessions.map(t => card(sc, t, t.kind || ""))) : []),
    ];
  }

  // ------------------------------------------------------------ sessions
  function copyBtn(key, text) {
    return el("button", { type: "button", class: "btn", on: { click: () => {
      const done = () => { st.copied = key; Tanka.render(); setTimeout(() => { if (st.copied === key) { st.copied = ""; Tanka.render(); } }, 2000); };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => {}); } } }, st.copied === key ? T.copied : T.copy);
  }

  function sessionsView(sc) {
    if (!st.sessions) { load(sc, "sessions"); return [el("p", { class: "meta" }, T.loading)]; }
    if (st.sessions.error) return [el("div", { class: "note-banner" }, T.notReachable + " " + st.sessions.error)];
    return [el("p", { class: "meta" }, T.sessionsIntro),
      ...(st.sessions.length ? st.sessions.map(x => el("article", { class: "card" },
        el("div", { class: "row" }, el("span", { class: "chip on" }, x.repo_name || "?"), el("span", { class: "meta" }, T.session(x.id)),
          x.running ? el("span", { class: "chip on" }, T.runningNow) : null, el("span", { class: "spacer" }),
          el("span", { class: "meta" }, when(epoch(x.last_seen_at)))),
        el("p", { class: "text" }, x.name || x.last_prompt || T.noPrompt),
        x.name && x.last_prompt ? el("div", { class: "meta" }, T.lastPrompt(x.last_prompt)) : null,
        el("div", { class: "meta" }, T.sessionMeta(x)),
        el("div", { class: "cmdline" }, el("code", {}, x.resume), copyBtn("s:" + x.id, x.resume))))
        : [el("p", { class: "empty" }, T.noSessions)])];
  }

  // ------------------------------------------------------------ problems
  let searchTimer = 0;
  function problemsView(sc) {
    const controls = el("div", { class: "psearch" },
      // Not a frozen field: its results must show as they come, so the tab rebuilds and the box gets its focus back.
      el("input", { type: "search", id: "cauce-q", placeholder: T.searchPh, value: st.q, "aria-label": T.searchPh, on: {
        focus: () => { st.searching = true; },
        blur: e => { if (document.contains(e.target)) st.searching = false; },
        input: e => { st.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(() => load(sc, "problems"), 300); } } }),
      el("div", { class: "modes", role: "radiogroup" }, [[true, T.everywhere], [false, T.here]].map(([v, label]) =>
        el("button", { type: "button", role: "radio", "aria-checked": String(st.everywhere === v),
          on: { click: () => { st.everywhere = v; load(sc, "problems"); } } }, label))));
    if (!st.problems) { load(sc, "problems"); return [controls, el("p", { class: "meta" }, T.loading)]; }
    if (st.problems.error) return [controls, el("div", { class: "note-banner" }, T.notReachable + " " + st.problems.error)];
    const list = st.problems.filter(p => st.everywhere || !st.project || p.repo_name === st.project);
    if (!list.length) return [controls, el("p", { class: "empty" }, T.noProblems(st.q.trim()))];
    return [controls, ...list.map(p => {
      const fixes = p.fixes || [];
      const dead = fixes.filter(f => f.outcome === "failed" || f.invalidated_on).length;
      return el("article", { class: "card" },
        el("div", { class: "row" }, el("span", { class: "chip " + (p.state || "open") }, T.state[p.state] || p.state),
          el("span", { class: "chip" + (p.repo_name ? " on" : "") }, p.repo_name || p.repo || "?"),
          dead ? el("span", { class: "meta" }, T.deadEnds(dead)) : null, el("span", { class: "spacer" }),
          el("span", { class: "meta" }, "#" + p.id)),
        el("p", { class: "text" }, p.title),
        T.seen(p.first_seen, p.last_seen) ? el("div", { class: "meta" }, T.seen(p.first_seen, p.last_seen)) : null,
        p.symptom ? el("div", { class: "meta" }, T.symptom + p.symptom) : null,
        fixes.length ? el("ol", { class: "fixes" }, fixes.map(f => {
          const outcome = f.invalidated_on && f.outcome === "worked" ? "disproved" : f.outcome;
          const refs = [f.task_id ? T.task(f.task_id) : "", f.commit_sha ? T.commit(f.commit_sha) : ""].filter(Boolean).join(" · ");
          return el("li", { "data-outcome": outcome },
            el("span", { class: "mk", "aria-label": outcome }, MARK[outcome] || "•"), el("span", {}, f.description),
            outcome === "disproved" ? el("span", { class: "why" }, T.disproved(f.invalidated_on) + (f.why ? ": " + f.why : ""))
              : f.why ? el("span", { class: "why" }, (outcome === "worked" ? T.whyWorked : T.whyFailed) + f.why) : null,
            refs ? el("span", { class: "refs" }, refs) : null);
        })) : null);
    })];
  }

  // ------------------------------------------------------------ the tab
  function render(sc, mod) {
    if (st.scope !== sc.scope) { Object.assign(st, { scope: sc.scope, known: null, sessions: null, problems: null, open: null }); }
    // One project at a time, always: every repository's cards at once is a wall nobody reads.
    if (!(mod.repos || []).includes(st.project)) st.project = (mod.repos || [])[0] || "";
    const top = [projectBar(sc, mod), knownList(sc), viewBar(sc)];
    if (mod.error) return [...top, el("div", { class: "note-banner" }, T.notReachable + " " + mod.error)];
    if (st.view === "problems") {
      if (st.searching) setTimeout(() => {
        const box = document.getElementById("cauce-q");
        if (box && document.activeElement !== box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
      });
      return [...top, ...problemsView(sc)];
    }
    if (!mod.board) return [...top, el("p", { class: "empty" }, T.noRepos(sc.scope))];
    if (st.view === "sessions") return [...top, ...sessionsView(sc)];
    return [...top, ...tasksView(sc, mod)];
  }

  function taskEvent(sc, m) {
    const ok = m.status === "done";
    return el("div", { class: "event" + (ok ? " quiet" : "") },
      el("div", { class: "head" }, icon(ok ? "check" : "zap"), el("span", { class: "label" }, ok ? T.chatPassed(m) : T.chatStopped(m)),
        el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      el("p", { class: "text" }, m.title),
      m.asks ? el("div", { class: "state" }, m.asks) : null,
      el("div", { class: "acts" }, el("button", { type: "button", class: "btn", on: { click: () => {
        st.view = "tasks"; if (m.repo_name) st.project = m.repo_name; Tanka.go("cauce"); toggle(sc, m.id); } } }, T.details)));
  }

  const needs = mod => (mod && mod.board && mod.board.counts && mod.board.counts.needs_you) || 0;
  Tanka.module("cauce", {
    // The minute is in the signature so a worker's "running for" moves on; a focused field freezes it.
    tabs: [{ id: "cauce", label: T.tab, render, badge: (sc, mod) => needs(mod),
             sig: (sc, mod) => st.editing ? st.lastSig : (st.lastSig = JSON.stringify([mod, st.open, st.detail, st.confirm, st.project,
               st.view, st.adding, st.known, st.sessions, st.problems, st.everywhere, st.copied, st.msg, Math.floor(Date.now() / 60000)])) }],
    // Sessions and problems are loaded by the tab itself; a poll refreshes them every half minute.
    refresh: async (sc) => {
      if (Tanka.ui.tab !== "cauce" || st.editing || Date.now() - st.loadedAt < 30000) return;
      if (st.view === "sessions" || st.view === "problems") await load(sc, st.view);
    },
    items: { task: taskEvent },
    attention: (sc, mod) => needs(mod),
    using: name => /cauce_queue/.test(name) ? "queueing it for cauce…" : /cauce_memory/.test(name) ? "checking what cauce remembers…" : /cauce_/.test(name) ? "checking your code tasks…" : null,
    suggest: T.suggest,
    hello: () => T.hello,
  });
})();
