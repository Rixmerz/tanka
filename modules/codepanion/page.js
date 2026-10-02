// The codepanion on the page: notes, sessions and lenses, and its voice in the chat (docs/page.md).
(() => {
  const { el, api, act, mark, hm, when, clock, plural } = Tanka;
  const T = {
    notes: "Notes", sessions: "Sessions", lenses: "Lenses",
    noted: l => `Noticed on my own · ${l}`, evidence: "Show evidence", useful: "Useful", notUseful: "Not useful",
    notesCount: (n, u) => `${plural(n, "note", "notes")} · ${u} new`, markSeen: "Mark all seen",
    notesEmpty: "It has not said anything yet. It speaks only when a signal wakes one of its lenses, or the guardian sees something.",
    session: "session", silent: p => `Its lenses stay silent: ${p}`, more: n => ` (and ${n} more, see Lenses)`, evidenceLbl: "evidence: ",
    noSessions: p => `No session in the last 24 h in ${p}.`,
    noWatch: s => `This workspace watches no project yet: tanka codepanion watch add <path> ${s}`,
    noPrompt: "(no prompt yet)", hide: "Hide", timeline: "Timeline", loading: "Loading…",
    sessionStats: s => `${s.prompts} prompt(s) · ${s.tools} tool call(s) · ${s.failures} failed`,
    showingLast: (n, o) => `Showing the last ${n} events; ${o} earlier ones are left out.`,
    firings: n => `${n} signal(s) crossed (⚡). Whether one woke a lens depends on the lenses, the budget and quiet hours.`,
    noFirings: "No signal crossed in this session.",
    sessionStart: (src, br) => `session start (${src || "new"})${br ? " on " + br : ""}`, turnEnd: "— turn end", sessionEnd: r => "session end " + (r || ""),
    checkFails: "check fails, so its lenses do not run:",
    proactivity: v => "proactivity " + v, notifyOn: "notify on", notifyOff: "notify off", quiet: v => "quiet " + (v || "none"),
    budget: b => `budget ${b.runs_per_hour} runs, ${b.notes_per_hour} notes / h`, thresholds: "thresholds: ",
    changeHint: s => `To change any of this, run /new-codepanion in tanka dev ${s}: it checks and backtests the change.`,
    noLenses: s => `No lenses yet: only the built-in checks run. Build lenses with /new-codepanion in tanka dev ${s}.`,
    active: "active", inactive: "inactive", speaks: v => "speaks " + (v || "?"), wakesOn: v => "wakes on " + v,
    problems: n => plural(n, "problem", "problems"), checkOk: "check ok",
    lensStats: l => `${plural(l.notes, "note", "notes")} · 👍 ${l.good} · 👎 ${l.bad}`, rubric: "Rubric and examples",
    tune: l => `Two 👎 in a row for ${l}. Should it speak less?`, tuneStricter: "Wake less often", tunePause: "Pause it",
    tuneDone: { stricter: "Made stricter", pause: "Paused", resume: "Resumed" },
    using: name => /codepanion_/.test(name) ? "reading your coding sessions…" : null,
  };
  const cp = { open: null, detail: null, seenSent: "" };
  const rateBtn = (sc, n, v) => el("button", { type: "button", class: "ghost", "aria-pressed": String(n.verdict === v),
    on: { click: () => act(api("/api/m/codepanion/rate", { scope: sc.scope, id: n.id, verdict: v })) } }, v === "good" ? "👍 " + T.useful : "👎 " + T.notUseful);

  // A 👎 streak on one lens: the user picks how it changes, or ignores it. The page never changes a lens on its own.
  function tuneEvent(sc, m) {
    const btn = (action, label) => el("button", { type: "button", class: "btn",
      on: { click: () => act(api("/api/m/codepanion/lens", { scope: sc.scope, lens: m.lens, action })) } }, label);
    return el("div", { class: "event" },
      el("div", { class: "head" }, mark(), el("span", { class: "label" }, m.lens), el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      el("p", { class: "text" }, m.state === "open" ? T.tune(m.lens) : (m.done || T.tuneDone[m.state] || m.state)),
      m.state === "open" ? el("div", { class: "acts" }, m.tunable && m.lens !== "guardian" ? btn("stricter", T.tuneStricter) : null,
        btn("pause", T.tunePause)) : null);
  }

  function noteEvent(sc, m) {
    return el("div", { class: "event" },
      el("div", { class: "head" }, mark(), el("span", { class: "label" }, T.noted(m.lens || "?")),
        el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      el("p", { class: "text" }, m.text),
      m.evidence ? el("details", { class: "ev" }, el("summary", {}, T.evidence), el("pre", {}, m.evidence)) : null,
      el("div", { class: "acts" }, rateBtn(sc, m, "good"), rateBtn(sc, m, "bad")));
  }

  function notesView(sc, mod) {
    const notes = mod.notes || [], unseen = notes.filter(n => !n.seen).length, out = [];
    if ((mod.problems || []).length) out.push(el("div", { class: "note-banner" },
      T.silent(mod.problems[0]) + (mod.problems.length > 1 ? T.more(mod.problems.length - 1) : "")));
    out.push(el("div", { class: "row" }, el("span", { class: "meta" }, T.notesCount(notes.length, unseen)), el("span", { class: "spacer" }),
      unseen ? el("button", { class: "btn", on: { click: () => act(api("/api/m/codepanion/seen", { scope: sc.scope })) } }, T.markSeen) : null));
    if (!notes.length) return out.concat(el("p", { class: "empty" }, T.notesEmpty));
    return out.concat(notes.map(n => el("article", { class: "card" + (n.seen ? "" : " unseen") },
      el("div", { class: "row" }, el("span", { class: "chip on" }, n.lens), el("span", { class: "meta" }, when(n.t)),
        n.session ? el("span", { class: "meta" }, T.session + " " + String(n.session).slice(0, 8)) : null,
        el("span", { class: "spacer" }), rateBtn(sc, n, "good"), rateBtn(sc, n, "bad")),
      el("p", { class: "text" }, n.text),
      el("div", { class: "evidence" }, T.evidenceLbl + n.evidence))));
  }

  async function detail(sc) {
    if (!cp.open) return;
    try { cp.detail = await api(`/api/m/codepanion/session?scope=${encodeURIComponent(sc.scope)}&id=${encodeURIComponent(cp.open)}`); }
    catch (e) { cp.detail = null; }
  }
  async function toggle(sc, id) { cp.open = cp.open === id ? null : id; cp.detail = null; Tanka.render(); await detail(sc); Tanka.render(); }

  function sessionsView(sc, mod) {
    const list = mod.sessions || [];
    if (!list.length) return [el("p", { class: "empty" }, (mod.projects || []).length ? T.noSessions(mod.projects.join(", ")) : T.noWatch(sc.scope))];
    return list.map(s => {
      const open = cp.open === s.id;
      const card = el("article", { class: "card" },
        el("div", { class: "row" },
          el("span", { class: "chip " + (s.status === "active" ? "on" : "") }, s.status),
          el("strong", {}, s.title || T.noPrompt), el("span", { class: "spacer" }),
          el("button", { class: "btn", "aria-expanded": String(open), on: { click: () => toggle(sc, s.id) } }, open ? T.hide : T.timeline)),
        el("div", { class: "meta" }, `${s.project}${s.branch ? " · " + s.branch : ""} · ${when(s.start)} → ${clock(s.last)} · ${T.sessionStats(s)} · ${s.id.slice(0, 8)}`));
      if (open) card.append(cp.detail && cp.detail.summary.id === s.id ? timeline(cp.detail) : el("p", { class: "meta" }, T.loading));
      return card;
    });
  }

  function timeline(d) {
    const fires = {};
    for (const f of d.firings) (fires[f.i - d.offset] = fires[f.i - d.offset] || []).push(f);
    const items = [];
    d.events.forEach((e, i) => {
      let text, cls = "";
      if (e.e === "prompt") { text = "› " + (e.text || ""); cls = "prompt"; }
      else if (e.e === "tool") { text = `${e.tool} ${e.target || ""}${e.lines ? ` (+${e.lines})` : ""}${e.ok ? "" : "  ✗ " + (e.error || "")}`; cls = e.ok ? "" : "fail"; }
      else if (e.e === "session_start") text = T.sessionStart(e.source, e.branch);
      else if (e.e === "turn_end") text = T.turnEnd;
      else if (e.e === "session_end") text = T.sessionEnd(e.reason);
      else text = e.e;
      items.push(el("li", { class: cls }, clock(e.t) + "  " + text));
      for (const f of fires[i] || []) items.push(el("li", { class: "fire" }, clock(f.t) + "  ⚡ " + f.signal + ": " + f.evidence));
    });
    return el("div", {},
      d.offset ? el("p", { class: "meta" }, T.showingLast(d.events.length, d.offset)) : null,
      el("p", { class: "meta" }, d.firings.length ? T.firings(d.firings.length) : T.noFirings),
      el("ul", { class: "timeline" }, items));
  }

  function lensesView(sc, mod) {
    const out = [];
    if ((mod.problems || []).length) out.push(el("div", { class: "card" }, el("strong", { class: "problem" }, T.checkFails),
      el("ul", {}, mod.problems.map(p => el("li", { class: "problem" }, p)))));
    for (const w of mod.warnings || []) out.push(el("p", { class: "warning" }, "! " + w));
    const cfg = mod.config || {};
    out.push(el("div", { class: "card" },
      el("div", { class: "row" },
        el("span", { class: "chip" }, T.proactivity(cfg.proactivity)), el("span", { class: "chip" }, cfg.notify ? T.notifyOn : T.notifyOff),
        el("span", { class: "chip" }, T.quiet(cfg.quiet_hours)), el("span", { class: "chip" }, T.budget(cfg.budget || {}))),
      el("div", { class: "meta" }, T.thresholds + Object.entries(cfg.thresholds || {}).map(([k, v]) => k + " " + v).join(" · ")),
      el("div", { class: "meta" }, T.changeHint(sc.scope))));
    if (!(mod.lenses || []).length) out.push(el("p", { class: "empty" }, T.noLenses(sc.scope)));
    for (const l of mod.lenses || []) out.push(el("article", { class: "card" },
      el("div", { class: "row" },
        el("strong", {}, l.name), el("span", { class: "chip " + (l.active ? "on" : "") }, l.active ? T.active : T.inactive),
        el("span", { class: "chip" }, T.speaks(l.meta.speaks)), el("span", { class: "chip" }, T.wakesOn([].concat(l.meta.wakes_on || []).join(", "))),
        el("span", { class: "chip " + (l.problems.length ? "no" : "ok") }, l.problems.length ? T.problems(l.problems.length) : T.checkOk),
        el("span", { class: "spacer" }), el("span", { class: "meta" }, T.lensStats(l))),
      l.problems.length ? el("ul", {}, l.problems.map(p => el("li", { class: "problem" }, p))) : null,
      el("details", {}, el("summary", {}, T.rubric), Object.entries(l.sections).map(([k, v]) => [el("div", { class: "meta" }, k), el("pre", { class: "section" }, v)]))));
    return out;
  }

  const unseen = mod => (mod && mod.notes || []).filter(n => !n.seen).length;
  Tanka.module("codepanion", {
    tabs: [
      { id: "notes", label: T.notes, render: notesView, badge: (sc, mod) => unseen(mod) },
      { id: "sessions", label: T.sessions, render: sessionsView },
      { id: "lenses", label: T.lenses, render: lensesView },
    ],
    items: { note: noteEvent, tune: tuneEvent },
    using: T.using,
    refresh: (sc, mod) => detail(sc),
    // Notes shown in the chat count as seen.
    onChat: (sc, mod) => {
      const ids = (sc.chat || []).filter(m => m.module === "codepanion" && m.who === "note" && !m.seen).map(m => m.id).join();
      if (ids && ids !== cp.seenSent) { cp.seenSent = ids; api("/api/m/codepanion/seen", { scope: sc.scope }).catch(() => {}); }
    },
  });
})();
