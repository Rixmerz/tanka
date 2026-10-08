// Routines on the page: what runs on its own, and the assistant's proposals waiting for the user (docs/page.md).
// The assistant only proposes; Approve, Reject and Remove here are the user's own clicks.
(() => {
  const { el, api, when, icon } = Tanka;
  const T = {
    tab: "Routines",
    waiting: "Waiting for you", active: "Active", decided: "Recently decided",
    nothingWaiting: "Nothing is waiting for you.",
    noRoutines: "No routines yet. Ask the assistant for something done on a schedule, and it proposes one here for you to approve.",
    noTriggers: "No triggers.",
    nothingDecided: "Nothing decided yet.",
    every: e => `every ${e}`, upTo: b => `up to ${Number(b).toFixed(2)} USD per run`,
    why: "Why", task: "What it will do, exactly",
    approve: "Approve", reject: "Reject", remove: "Remove",
    confirmRemove: n => `Remove ${n}? It stops running.`, yesRemove: "Yes, remove it", cancel: "Cancel",
    routines: "Routines", triggers: "Triggers",
    triggersNote: "Triggers run when an event happens; they are managed with the CLI (tanka automation).",
    on: o => `on ${o}`,
    from: { you: "you", assistant: "assistant" },
    never: "Has not run yet.", ok: "ok", failed: "failed",
    daemonUp: "The automation daemon is running.",
    daemonDown: "The automation daemon is not running: it runs while the automation daemon is up.",
    status: { approved: "approved", rejected: "rejected", withdrawn: "withdrawn", pending: "pending" },
    chip: "Review routine proposal",
    using: name => /routines_propose/.test(name) ? "proposing a routine…" : /routines_(list|history)/.test(name) ? "checking the routines…" : /routines_withdraw/.test(name) ? "withdrawing a proposal…" : null,
  };
  // What the page remembers between polls: the row asking to confirm a removal, the last message and error.
  const st = { confirm: null, note: null, error: null };
  const DECIDED = 8;

  const proposals = mod => (mod && mod.proposals) || [];
  const pending = mod => proposals(mod).filter(p => p.status === "pending");
  const firstLine = s => String(s || "").split("\n").find(l => l.trim()) || "";

  async function post(sc, name, body) {
    st.error = null; st.note = null;
    try {
      const res = await api(`/api/m/routines/${name}`, Object.assign({ scope: sc.scope }, body));
      st.note = { text: res.message || "", daemon: name === "approve" ? !!res.daemon : null };
    } catch (e) {
      st.error = e.message;
    }
    st.confirm = null;
    await Tanka.refresh();
  }

  // The CSP refuses style attributes; a style set from the script is allowed.
  const scroller = n => { n.style.maxHeight = "18em"; n.style.overflow = "auto"; return n; };
  const btn = (label, cls, fn) => el("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), on: { click: fn } }, label);
  const sched = x => [el("span", { class: "tag" }, T.every(x.every)), el("span", { class: "tag" }, T.upTo(x.budget))];

  function proposalCard(sc, p) {
    return el("div", { class: "card unseen", "data-card": p.id },
      el("div", { class: "row" }, el("strong", {}, p.name), ...sched(p), el("span", { class: "spacer" }), el("span", { class: "meta" }, when(p.t))),
      p.why ? el("p", { class: "meta" }, T.why + ": " + p.why) : null,
      el("div", { class: "meta" }, T.task),
      // The whole task, as text: the user approves exactly this. Long text scrolls inside the box.
      scroller(el("pre", { class: "section" }, p.task || "")),
      el("div", { class: "row" },
        btn([icon("check"), T.approve], "primary", () => post(sc, "approve", { id: p.id })),
        btn(T.reject, "", () => post(sc, "reject", { id: p.id }))));
  }

  function lastRun(x) {
    if (!x.last) return el("div", { class: "meta" }, T.never);
    return el("div", { class: "meta" }, when(x.last.t) + " · ",
      el("span", { class: "chip " + (x.last.ok ? "ok" : "no") }, x.last.ok ? T.ok : T.failed),
      firstLine(x.last.text) ? " " + firstLine(x.last.text) : "");
  }

  function routineCard(sc, x) {
    const armed = st.confirm === x.name;
    return el("div", { class: "card" },
      el("div", { class: "row" }, el("strong", {}, x.name), ...sched(x),
        el("span", { class: "chip" + (x.source === "assistant" ? " on" : "") }, T.from[x.source] || String(x.source || "")),
        el("span", { class: "spacer" }),
        armed ? null : btn(T.remove, "", () => { st.confirm = x.name; Tanka.render(); })),
      el("p", { class: "text" }, x.task || ""),
      lastRun(x),
      armed ? el("div", { class: "row warning" }, T.confirmRemove(x.name),
        btn(T.yesRemove, "primary", () => post(sc, "remove", { name: x.name })),
        btn(T.cancel, "", () => { st.confirm = null; Tanka.render(); })) : null);
  }

  const triggerRow = x => el("div", { class: "card" },
    el("div", { class: "row" }, el("strong", {}, x.name), el("span", { class: "tag" }, T.on(x.on)), el("span", { class: "tag" }, T.upTo(x.budget))),
    x.task ? el("p", { class: "text" }, x.task) : null);

  const decidedRow = p => el("div", { class: "row meta" },
    el("span", { class: "chip" + (p.status === "approved" ? " ok" : p.status === "rejected" ? " no" : "") }, T.status[p.status] || p.status),
    el("strong", {}, p.name), T.every(p.every), "·", when(p.t));

  function render(sc, mod) {
    const m = mod || {}, wait = pending(m), done = proposals(m).filter(p => p.status !== "pending").slice(0, DECIDED);
    const routines = m.routines || [], triggers = m.triggers || [];
    const out = [];
    if (st.error) out.push(el("div", { class: "note-banner problem", role: "alert" }, st.error));
    if (st.note) out.push(el("div", { class: "note-banner", role: "status" }, st.note.text,
      st.note.daemon === null ? null : el("div", { class: st.note.daemon ? "meta" : "warning" }, st.note.daemon ? T.daemonUp : T.daemonDown)));
    out.push(el("div", { class: "sec" }, T.waiting));
    out.push(...(wait.length ? wait.map(p => proposalCard(sc, p)) : [el("div", { class: "empty" }, T.nothingWaiting)]));
    if (wait.length && !m.daemon) out.push(el("p", { class: "warning" }, T.daemonDown));
    out.push(el("div", { class: "sec" }, T.active), el("div", { class: "meta" }, T.routines));
    out.push(...(routines.length ? routines.map(x => routineCard(sc, x)) : [el("div", { class: "empty" }, T.noRoutines)]));
    out.push(el("div", { class: "meta" }, T.triggers), el("p", { class: "meta" }, T.triggersNote));
    out.push(...(triggers.length ? triggers.map(triggerRow) : [el("div", { class: "empty" }, T.noTriggers)]));
    out.push(el("div", { class: "sec" }, T.decided));
    out.push(...(done.length ? done.map(decidedRow) : [el("div", { class: "empty" }, T.nothingDecided)]));
    return el("div", { class: "page" }, out);
  }

  const chip = (sc, f) => f.type === "proposal"
    ? el("button", { type: "button", class: "fx", title: f.text, on: { click: () => Tanka.go("routines") } },
        icon("clock"), el("span", { class: "k" }, T.chip), el("span", { class: "x" }, f.text))
    : null;

  Tanka.module("routines", {
    tabs: [{ id: "routines", label: T.tab, render, badge: (sc, mod) => pending(mod).length,
             sig: (sc, mod) => JSON.stringify([mod, st.confirm, st.note, st.error]) }],
    chips: chip,
    attention: (sc, mod) => pending(mod).length,
    using: T.using,
  });
})();
