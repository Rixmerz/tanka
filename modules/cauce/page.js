// cauce on the page: the Code tab, and finished or stuck tasks in the chat (docs/page.md).
(() => {
  const { el, api, act, hm, when, plural, icon } = Tanka;
  const T = {
    tab: "Code",
    needs: "Needs you", running: "Running", queued: "Queued", done: "Finished",
    nothing: "Nothing here.", noRepos: s => `This workspace may use no repository yet. In a terminal: tanka cauce allow ${s} <directory>`,
    notReachable: "cauce did not answer:",
    attempt: n => `attempt ${n || 1}`, cost: c => `$${(c || 0).toFixed(2)}`,
    paused: r => `Paused: ${r || "a task did not pass"}`, unpause: "Reopen", run: "Run queue", confirmRun: "Confirm: run and spend",
    working: "running its queue", cancel: "Cancel", confirmCancel: "Confirm cancel",
    details: "Details", toReview: "to review", hide: "Hide", loading: "Loading…", branch: "Branch to review:", merge: "Merge with:",
    passedAt: c => `passed at ${c}`, attemptLine: a => `${a.seq}. ${a.cell} · ${a.passed ? "passed" : (a.failure || "failed")}` + (a.move ? ` → ${a.move}` : ""),
    chatPassed: (t) => `#${t.id} passed in ${t.repo} at ${t.cell}`, chatStopped: (t) => `#${t.id} stopped in ${t.repo}: ${t.status}`,
    hello: "Ask me what is going on in your code, or tell me what to fix or build; I queue it for cauce, and you run the queue in the Code tab.",
    suggest: [["What needs me in my code?", true], ["Queue in cauce: ", false]],
  };
  const st = { open: null, detail: null, confirm: "" };
  const post = (sc, name, body) => act(api(`/api/m/cauce/${name}`, Object.assign({ scope: sc.scope }, body)));

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

  function detailView(t) {
    if (st.open !== t.id) return null;
    const d = st.detail;
    if (!d) return el("p", { class: "meta" }, T.loading);
    if (d.error) return el("p", { class: "meta" }, d.error);
    return el("div", { class: "evidence" },
      (d.attempts || []).map(a => el("div", {}, el("div", {}, T.attemptLine(a)),
        a.summary ? el("div", { class: "meta" }, a.summary) : null,
        a.move_reason ? el("div", { class: "meta" }, a.move_reason) : null)),
      d.branch ? el("div", {}, T.branch + " " + d.branch, el("div", { class: "meta" }, T.merge + " git merge " + d.branch)) : null);
  }

  function card(sc, t, extra, buttons, inLane) {
    return el("article", { class: "card" },
      el("div", { class: "row" }, inLane ? null : el("span", { class: "chip on" }, t.repo_name || "?"), el("span", { class: "meta" }, "#" + t.id),
        extra ? el("span", { class: "meta" }, extra) : null, el("span", { class: "spacer" }),
        buttons || null,
        el("button", { type: "button", class: "ghost", on: { click: () => toggle(sc, t.id) } }, st.open === t.id ? T.hide : T.details)),
      el("p", { class: "text" }, t.title || ""),
      t.asks ? el("div", { class: "meta" }, t.asks) : null,
      detailView(t));
  }

  function section(title, nodes, count) {
    return [el("h2", {}, title, el("span", { class: "meta" }, " " + (count == null ? nodes.length : count))), nodes.length ? nodes : el("p", { class: "empty" }, T.nothing)];
  }

  function render(sc, mod) {
    if (mod.error) return [el("div", { class: "note-banner" }, T.notReachable + " " + mod.error)];
    if (!mod.board) return [el("p", { class: "empty" }, T.noRepos(sc.scope))];
    const b = mod.board, working = new Set(mod.working || []);
    const cancel = t => guarded("cancel:" + t.id, T.cancel, T.confirmCancel, () => post(sc, "cancel", { id: t.id }));
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
      ...section(T.running, (b.running || []).map(t => card(sc, t, `${t.current_cell || ""} · ${T.attempt(t.attempt)} · ${T.cost(t.cost_usd)}`, cancel(t)))),
      ...section(T.queued, lanes, (b.counts || {}).queued || 0),
      ...section(T.done, (b.done || []).map(t => card(sc, t, `${T.passedAt(t.final_cell || "")} · ${T.cost(t.cost_usd)}`))),
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
    tabs: [{ id: "cauce", label: T.tab, render, badge: (sc, mod) => needs(mod),
             sig: (sc, mod) => JSON.stringify([mod, st.open, st.detail, st.confirm]) }],
    items: { task: taskEvent },
    attention: (sc, mod) => needs(mod),
    using: name => /cauce_queue/.test(name) ? "queueing it for cauce…" : /cauce_/.test(name) ? "checking your code tasks…" : null,
    suggest: T.suggest,
    hello: () => T.hello,
  });
})();
