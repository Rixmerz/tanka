// Jira on the page: the assistant asks to delete an issue, and the chat shows Delete and Keep (docs/page.md).
// Only these two clicks act; the assistant's tool only leaves the request.
(() => {
  const { el, api, icon } = Tanka;
  const T = {
    ask: "Delete this issue?", del: "Delete", keep: "Keep",
    deleted: "Deleted", dismissed: "Kept", gone: "Cannot be undone.",
    using: name => /jira_delete/.test(name) ? "asking you to confirm a deletion…" : null,
  };
  const st = { busy: null, error: null };

  async function act(sc, name, id) {
    st.busy = id; st.error = null; Tanka.render();
    try { await api(`/api/m/jira/${name}`, { scope: sc.scope, id }); }
    catch (e) { st.error = e.message; }
    st.busy = null;
    await Tanka.refresh();
  }

  const btn = (label, cls, fn, disabled) => el("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), disabled: !!disabled, on: { click: fn } }, label);

  // The request's own state comes from the module's data, so the card stays right after a reload.
  function chip(sc, f) {
    if (f.type !== "deletion") return null;
    const mod = (sc.modules && sc.modules.jira) || {}, d = (mod.deletions || []).find(x => x.id === f.id), status = d ? d.status : f.status;
    if (status === "pending") {
      const busy = st.busy === f.id;
      return el("div", { class: "confirm", role: "group", "aria-label": T.ask },
        el("div", { class: "what" }, el("span", { class: "k" }, T.ask), " ", el("strong", {}, f.key), " ", f.text),
        el("div", { class: "meta" }, T.gone),
        el("div", { class: "row" },
          btn([icon("x"), T.del], "primary", () => act(sc, "delete", f.id), busy),
          btn(T.keep, "", () => act(sc, "dismiss", f.id), busy)),
        st.error ? el("div", { class: "problem", role: "alert" }, st.error) : null);
    }
    return el("span", { class: "fx" }, el("span", { class: "k" }, status === "deleted" ? T.deleted : T.dismissed), el("strong", {}, f.key));
  }

  Tanka.module("jira", {
    chips: chip,
    attention: (sc, mod) => ((mod && mod.deletions) || []).filter(d => d.status === "pending").length,
    using: T.using,
  });
})();
