// cauce on the page: the Code tab, and finished or stuck tasks in the chat (docs/page.md).
// A worker here is not a Claude Code subagent: it is a one-shot `claude -p` that cauce launches for one
// attempt of one task, in a model and effort cell, with its own turn and dollar limits and only the tools
// and MCP servers that task needs. The tab shows the ones out now, and each task's way through its ladder.
(() => {
  const { el, api, act, hm, plural, icon } = Tanka;
  const T = {
    tab: "Code",
    needs: "Needs you", agents: "Agents working", between: "Between attempts", sessions: "In your Claude Code sessions",
    queued: "Pending", done: "Done",
    nothing: "Nothing here.", noRepos: s => `This workspace may use no repository yet. In a terminal: tanka cauce allow ${s} <directory>`,
    notReachable: "cauce did not answer:", noAgents: "No worker is out now. Pending tasks run when you press Run queue.",
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
    chatPassed: t => `#${t.id} passed in ${t.repo} at ${t.cell}`, chatStopped: t => `#${t.id} stopped in ${t.repo}: ${t.status}`,
    hello: "Ask me what is going on in your code, or tell me what to fix or build; I queue it for cauce, and you run the queue in the Code tab.",
    suggest: [["What needs me in my code?", true], ["Queue in cauce: ", false]],
  };
  const st = { open: null, detail: null, confirm: "" };
  const post = (sc, name, body) => act(api(`/api/m/cauce/${name}`, Object.assign({ scope: sc.scope }, body)));
  const since = iso => {
    const ms = Date.now() - Date.parse(iso);
    if (!(ms >= 0)) return "";
    const m = Math.round(ms / 60000);
    return m < 1 ? "under a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };

  // Two clicks for what cannot be taken back: the first arms it, the second does it.
  function guarded(key, label, armed, fn) {
    const on = st.confirm === key;
    return el("button", { type: "button", class: on ? "btn primary" : "btn",
      on: { click: () => { if (on) { st.confirm = ""; fn(); } else { st.confirm = key; Tanka.render(); } } } }, on ? armed : label);
  }

  async function toggle(sc, id) {
    st.open = st.open === id ? null : id; st.detail = null; Tanka.render();
    if (!st.open) return;
    try { st.detail = await api(`/api/m/cauce/task?scope=${encodeURIComponent(sc.scope)}&id=${id}`); }
    catch (e) { st.detail = { error: e.message }; }
    Tanka.render();
  }

  // The ladder the router planned, each cell marked by what happened in it: where it started, where it
  // failed and moved on, where it passed, and the cell a worker is running in now.
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

  function head(sc, t, extra, buttons, inLane) {
    return el("div", { class: "row" }, inLane ? null : el("span", { class: "chip on" }, t.repo_name || "?"),
      el("span", { class: "meta" }, "#" + t.id), extra ? el("span", { class: "meta" }, extra) : null, el("span", { class: "spacer" }),
      buttons || null,
      el("button", { type: "button", class: "ghost", on: { click: () => toggle(sc, t.id) } }, st.open === t.id ? T.hide : T.details));
  }

  function card(sc, t, extra, buttons, inLane) {
    return el("article", { class: "card" }, head(sc, t, extra, buttons, inLane),
      el("p", { class: "text" }, t.title || ""),
      t.asks ? el("div", { class: "meta" }, t.asks) : null,
      flowStrip(t), detailView(t));
  }

  // One worker out now: the cell it runs in, how long, its limits and the servers it was given.
  function agent(sc, t, cancel) {
    const w = t.worker;
    return el("article", { class: "card agent" },
      el("div", { class: "row" }, el("span", { class: "dot" + (w.alive ? "" : " off"), title: w.alive ? "" : T.dead }),
        el("span", { class: "cell" }, w.cell), el("span", { class: "meta" }, T.attempt(w.seq)),
        el("span", { class: "chip on" }, t.repo_name || "?"), el("span", { class: "meta" }, "#" + t.id),
        el("span", { class: "spacer" }), cancel,
        el("button", { type: "button", class: "ghost", on: { click: () => toggle(sc, t.id) } }, st.open === t.id ? T.hide : T.details)),
      el("p", { class: "text" }, t.title || ""),
      el("div", { class: "meta" }, T.running(since(w.started_at), w.max_turns, w.budget_usd), " · ", T.cost(t.cost_usd) + " so far"),
      el("div", { class: "caps" }, (w.capabilities || []).length ? w.capabilities.map(c => el("span", { class: "chip" }, c)) : el("span", { class: "meta" }, T.capsNone)),
      w.alive ? null : el("div", { class: "meta" }, T.dead),
      flowStrip(t), detailView(t));
  }

  function section(title, nodes, count, empty) {
    return [el("h2", {}, title, el("span", { class: "meta" }, " " + (count == null ? nodes.length : count))),
      nodes.length ? nodes : el("p", { class: "empty" }, empty || T.nothing)];
  }

  function render(sc, mod) {
    if (mod.error) return [el("div", { class: "note-banner" }, T.notReachable + " " + mod.error)];
    if (!mod.board) return [el("p", { class: "empty" }, T.noRepos(sc.scope))];
    const b = mod.board, working = new Set(mod.working || []);
    const cancel = t => guarded("cancel:" + t.id, T.cancel, T.confirmCancel, () => post(sc, "cancel", { id: t.id }));
    const running = b.running || [];
    const agents = running.filter(t => t.worker), between = running.filter(t => t.flow && !t.worker), sessions = running.filter(t => !t.flow);
    const lanes = (mod.repos || []).map(name => {
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
    return [
      ...section(T.needs, (b.needs_you || []).map(t => card(sc, t, t.status === "done" ? T.toReview : t.status))),
      ...section(T.agents, agents.map(t => agent(sc, t, cancel(t))), null, T.noAgents),
      ...(between.length ? section(T.between, between.map(t => card(sc, t, T.planning, cancel(t)))) : []),
      ...section(T.queued, lanes, (b.counts || {}).queued || 0),
      ...section(T.done, (b.done || []).map(t => card(sc, t, `${T.passedAt(t.final_cell || "")} · ${T.cost(t.cost_usd)}`))),
      ...(sessions.length ? section(T.sessions, sessions.map(t => card(sc, t, t.kind || ""))) : []),
    ];
  }

  function taskEvent(sc, m) {
    const ok = m.status === "done";
    return el("div", { class: "event" + (ok ? " quiet" : "") },
      el("div", { class: "head" }, icon(ok ? "check" : "zap"), el("span", { class: "label" }, ok ? T.chatPassed(m) : T.chatStopped(m)),
        el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      el("p", { class: "text" }, m.title),
      m.asks ? el("div", { class: "state" }, m.asks) : null,
      el("div", { class: "acts" }, el("button", { type: "button", class: "btn", on: { click: () => { Tanka.go("cauce"); toggle(sc, m.id); } } }, T.details)));
  }

  const needs = mod => (mod && mod.board && mod.board.counts && mod.board.counts.needs_you) || 0;
  Tanka.module("cauce", {
    // The minute is in the signature so a worker's "running for" moves on between polls.
    tabs: [{ id: "cauce", label: T.tab, render, badge: (sc, mod) => needs(mod),
             sig: (sc, mod) => JSON.stringify([mod, st.open, st.detail, st.confirm, Math.floor(Date.now() / 60000)]) }],
    items: { task: taskEvent },
    attention: (sc, mod) => needs(mod),
    using: name => /cauce_queue/.test(name) ? "queueing it for cauce…" : /cauce_/.test(name) ? "checking your code tasks…" : null,
    suggest: T.suggest,
    hello: () => T.hello,
  });
})();
