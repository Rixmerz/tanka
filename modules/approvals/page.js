// Approvals on the page: a workspace skill asks, and the chat shows the exact text with Approve and Dismiss
// (docs/page.md). Only these two clicks act; the assistant's tool only leaves the request.
(() => {
  const { el, api, icon } = Tanka;
  const T = {
    ask: "Approve this?", approve: "Approve", dismiss: "Dismiss", running: "Running…",
    approved: "Approved", dismissed: "Dismissed", failed: "Failed",
    once: "Runs once, exactly as written.",
  };
  const st = { busy: null, error: null };

  async function act(sc, name, id) {
    st.busy = id; st.error = null; Tanka.render();
    try { await api(`/api/m/approvals/${name}`, { scope: sc.scope, id }); }
    catch (e) { st.error = e.message; }
    st.busy = null;
    await Tanka.refresh();
  }

  const btn = (label, cls, fn, disabled) => el("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), disabled: !!disabled, on: { click: fn } }, label);

  // The request's own state comes from the module's data, so the card stays right after a reload.
  function chip(sc, f) {
    if (f.type !== "approval") return null;
    const mod = (sc.modules && sc.modules.approvals) || {}, a = (mod.approvals || []).find(x => x.id === f.id) || f;
    const status = a.status || f.status;
    if (status === "pending" || status === "running") {
      const busy = st.busy === f.id || status === "running";
      return el("div", { class: "confirm", role: "group", "aria-label": T.ask },
        el("div", { class: "what" }, el("span", { class: "k" }, T.ask), " ", el("strong", {}, a.title || f.title)),
        a.text ? el("pre", { class: "codeblock" }, a.text) : null,
        el("div", { class: "meta" }, T.once),
        el("div", { class: "row" },
          btn([icon("check"), busy ? T.running : T.approve], "primary", () => act(sc, "approve", f.id), busy),
          btn(T.dismiss, "", () => act(sc, "dismiss", f.id), busy)),
        st.error ? el("div", { class: "problem", role: "alert" }, st.error) : null);
    }
    const label = status === "approved" ? T.approved : status === "failed" ? T.failed : T.dismissed;
    return el("span", { class: "fx", title: a.result || "" }, el("span", { class: "k" }, label), el("strong", {}, a.title || f.title));
  }

  Tanka.module("approvals", {
    chips: chip,
    attention: (sc, mod) => ((mod && mod.approvals) || []).filter(a => a.status === "pending").length,
  });
})();
