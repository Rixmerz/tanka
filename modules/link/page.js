// Link on the page: the Claude Code sessions open with tanka-link, and a box to send each one a prompt
// (docs/page.md). What is sent here is the user's own message.
(() => {
  const { el, api, when, icon } = Tanka;
  const T = {
    tab: "Sessions",
    none: "No Claude Code session is open with tanka-link. Install the plugin (see the link module's README) and open a session.",
    listening: "waiting", busy: "working",
    placeholder: "Prompt for this session…", send: "Send", sent: "Sent: it gets it now if it is waiting, or when its turn ends.",
    queued: n => `${n} waiting`, recent: "Last messages", from: { user: "you" },
    using: name => /link_send/.test(name) ? "sending to a Claude Code session…" : /link_sessions/.test(name) ? "looking at your sessions…" : null,
  };
  const st = { text: {}, busy: null, note: null, error: null };

  async function send(sc, s) {
    const text = (st.text[s.id] || "").trim();
    if (!text) return;
    st.busy = s.id; st.error = null; st.note = null; Tanka.render();
    try { await api("/api/m/link/send", { scope: sc.scope, id: s.id, text }); st.text[s.id] = ""; st.note = s.id; }
    catch (e) { st.error = e.message; }
    st.busy = null;
    await Tanka.refresh();
  }

  function card(sc, s) {
    const box = el("textarea", { rows: "2", placeholder: T.placeholder, on: { input: e => { st.text[s.id] = e.target.value; } } }, st.text[s.id] || "");
    return el("div", { class: "card" },
      el("div", { class: "row" }, icon("wrench"), el("strong", {}, s.project || "?"), s.branch ? el("span", { class: "tag" }, s.branch) : null,
        el("span", { class: "chip" + (s.state === "listening" ? " ok" : "") }, s.state === "listening" ? T.listening : T.busy),
        s.queued ? el("span", { class: "chip" }, T.queued(s.queued)) : null, el("span", { class: "spacer" }), el("span", { class: "meta" }, when(s.seen))),
      el("div", { class: "meta" }, s.cwd || ""),
      (s.recent || []).length ? el("div", { class: "meta" }, T.recent + ": " + s.recent.map(m => `${T.from[m.from] || m.from}: ${String(m.text).slice(0, 80)}`).join(" · ")) : null,
      box,
      el("div", { class: "row" }, el("button", { type: "button", class: "btn primary", disabled: st.busy === s.id, on: { click: () => send(sc, s) } }, T.send),
        st.note === s.id ? el("span", { class: "meta" }, T.sent) : null));
  }

  function render(sc, mod) {
    const list = (mod && mod.sessions) || [];
    return el("div", { class: "page" },
      st.error ? el("div", { class: "note-banner problem", role: "alert" }, st.error) : null,
      list.length ? list.map(s => card(sc, s)) : el("div", { class: "empty" }, T.none));
  }

  Tanka.module("link", {
    tabs: [{ id: "sessions", label: T.tab, render, badge: (sc, mod) => ((mod && mod.sessions) || []).length,
             sig: (sc, mod) => JSON.stringify([mod, st.busy, st.note, st.error]) }],
    using: T.using,
  });
})();
