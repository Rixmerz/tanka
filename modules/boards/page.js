// Boards on the page: each view's rows grouped and counted, a row's buttons writing a message (docs/page.md).
(() => {
  const { el, api, act, hm, when } = Tanka;
  const T = {
    tab: "Boards", board: "Board", filter: "Filter by name, course…", rows: n => `${n} row(s)`,
    empty: s => `No boards yet. Declare one with /new-view in tanka dev ${s}, or start from the example: tanka boards example ${s} && tanka boards build ${s}`,
    broken: (v, p) => `${v} is not ready: ${p}. Check it with: tanka boards check`,
    noRows: "Nothing recorded yet. Ask the assistant to fill it.", noMatch: "No row matches.",
    loading: "Loading…", archive: "Archive row", archived: "Archived", updated: (t, by) => `updated ${when(t)}${by === "user" ? " by you" : ""}`,
    chip: { created: "Recorded", updated: "Updated" },
    using: name => /boards_record_/.test(name) ? "recording on the board…" : /boards_rows/.test(name) ? "reading the board…" : null,
    hello: "I also fill your boards, and a row's buttons write what to ask me.",
  };
  const bs = { view: null, filter: "", open: null, rows: {}, loading: false };
  try { bs.view = localStorage.getItem("tanka.board"); } catch (e) { /* storage blocked */ }
  const viewsOf = mod => (mod && mod.views) || [];
  const currentView = mod => viewsOf(mod).find(v => v.name === bs.view) || viewsOf(mod)[0] || null;
  const rowsKey = (sc, v) => `${sc.scope}/${v.name}`;
  const text = v => v == null ? "" : String(v);

  async function load(sc, mod) {
    const v = currentView(mod);
    if (!v || v.problems.length || bs.loading) return;
    bs.loading = true;
    try { bs.rows[rowsKey(sc, v)] = (await api(`/api/m/boards/rows?scope=${encodeURIComponent(sc.scope)}&view=${encodeURIComponent(v.name)}`)).rows; }
    catch (e) { /* the next poll tries again */ }
    bs.loading = false;
  }

  // Fill a button's message with the row's values, one line each and clipped: the user reads it before sending.
  function says(tpl, row) {
    return tpl.replace(/\{([a-z][a-z0-9_]*)\}/g, (m, k) => text(row.fields[k]).replace(/\s+/g, " ").trim().slice(0, 80) || m);
  }
  function shows(action, row) {
    return Object.entries(action.when || {}).every(([k, vals]) =>
      [].concat(vals).map(x => String(x).toLowerCase()).includes(text(row.fields[k]).toLowerCase()));
  }

  function cell(sc, v, row, k) {
    const f = v.fields[k] || {}, val = row.fields[k];
    if (f.type === "choice" && f.editable)
      return el("td", {}, el("select", { "aria-label": f.label, on: { change: e => act(api("/api/m/boards/set",
        { scope: sc.scope, view: v.name, id: row.id, field: k, value: e.target.value })) } },
        val == null ? el("option", { value: "", selected: true }, "—") : null,
        f.choices.map(c => el("option", { value: c, selected: c === val }, c))));
    if (f.type === "text" && f.long)
      return el("td", { class: "long", title: text(val), on: { click: () => { bs.open = bs.open === row.id ? null : row.id; Tanka.render(); } } }, text(val) || "—");
    return el("td", { class: f.type === "number" ? "num" : "" }, val == null ? "—" : text(val));
  }

  function table(sc, v, rows) {
    const cols = v.columns.filter(k => k in v.fields);
    const longs = Object.entries(v.fields).filter(([, f]) => f.type === "text" && f.long).map(([k]) => k);
    const body = [];
    for (const row of rows) {
      body.push(el("tr", { class: bs.open === row.id ? "row-open" : "" },
        cols.map(k => cell(sc, v, row, k)),
        el("td", { class: "acts" },
          (v.actions || []).filter(a => shows(a, row)).map(a => el("button", { type: "button", class: "btn", title: says(a.says, row),
            on: { click: () => Tanka.compose(says(a.says, row)) } }, a.label)),
          el("button", { type: "button", class: "ghost", title: T.archive,
            on: { click: () => act(api("/api/m/boards/archive", { scope: sc.scope, view: v.name, id: row.id })) } }, "×"))));
      if (bs.open === row.id)
        body.push(el("tr", { class: "detail" }, el("td", { colspan: String(cols.length + 1) },
          longs.map(k => [el("span", { class: "k" }, v.fields[k].label), text(row.fields[k]) || "—"]),
          row.updated ? el("div", { class: "meta" }, T.updated(row.updated, row.updated_by)) : null)));
    }
    return el("div", { class: "tablewrap" }, el("table", { class: "board" },
      el("thead", {}, el("tr", {}, cols.map(k => el("th", {}, v.fields[k].label)), el("th", {}, ""))), el("tbody", {}, body)));
  }

  // "12 reviewed · 3 pending": the choice fields counted, which answers who is missing at a glance.
  function counts(v, rows) {
    const out = [];
    for (const [k, f] of Object.entries(v.fields)) {
      if (f.type !== "choice") continue;
      for (const c of f.choices) { const n = rows.filter(r => r.fields[k] === c).length; if (n) out.push(`${n} ${c}`); }
      const none = rows.filter(r => r.fields[k] == null).length;
      if (none) out.push(`${none} no ${f.label.toLowerCase()}`);
    }
    return out.join(" · ");
  }

  function render(sc, mod) {
    const views = viewsOf(mod);
    if (!views.length) return [el("p", { class: "empty" }, T.empty(sc.scope))];
    const v = currentView(mod);
    const head = el("div", { class: "board-h" },
      views.length > 1
        ? el("select", { "aria-label": T.board, on: { change: e => { bs.view = e.target.value; bs.open = null; try { localStorage.setItem("tanka.board", bs.view); } catch (x) { /* blocked */ } Tanka.refresh(); } } },
            views.map(x => el("option", { value: x.name, selected: x.name === v.name }, x.title)))
        : el("h2", {}, v.title),
      el("input", { class: "filter", id: "board-filter", type: "search", placeholder: T.filter, value: bs.filter, "aria-label": T.filter,
        on: { input: e => { const pos = e.target.selectionStart; bs.filter = e.target.value; Tanka.render();
          const i = document.getElementById("board-filter"); if (i) { i.focus(); i.setSelectionRange(pos, pos); } } } }),
      v.problems.length ? null : el("span", { class: "meta" }, T.rows(v.count)));
    if (v.problems.length) return [head, el("p", { class: "problem" }, T.broken(v.title, v.problems[0]))];
    const all = bs.rows[rowsKey(sc, v)];
    if (!all) { load(sc, mod).then(() => Tanka.render()); return [head, el("p", { class: "meta" }, T.loading)]; }
    if (!all.length) return [head, el("p", { class: "meta" }, v.description), el("p", { class: "empty" }, T.noRows)];
    const q = bs.filter.trim().toLowerCase();
    const rows = q ? all.filter(r => Object.values(r.fields).some(x => text(x).toLowerCase().includes(q))) : all;
    const g = v.group_by, groups = new Map();
    const sortKey = r => v.key.map(k => text(r.fields[k]).toLowerCase()).join("\u0000");
    for (const r of [...rows].sort((a, b2) => sortKey(a).localeCompare(sortKey(b2)))) {
      const name = g ? text(r.fields[g]) || "—" : "";
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(r);
    }
    const out = [head, el("p", { class: "meta" }, v.description)];
    if (!rows.length) out.push(el("p", { class: "empty" }, T.noMatch));
    for (const [name, rs] of [...groups.entries()].sort((a, b2) => a[0].localeCompare(b2[0])))
      out.push(el("section", { class: "group" },
        g ? el("div", { class: "group-h" }, el("span", {}, name), el("span", { class: "meta" }, `${rs.length} · ${counts(v, rs)}`)) : null,
        table(sc, v, rs)));
    return out;
  }

  function chip(sc, f) {
    return el("button", { type: "button", class: "fx", title: `${f.title}: ${f.label}`,
      on: { click: () => { bs.view = f.view; bs.open = f.id; bs.filter = ""; Tanka.go("boards"); Tanka.refresh(); } } },
      el("span", { "aria-hidden": "true" }, "▦"), el("span", { class: "k" }, `${T.chip[f.type] || f.type} · ${f.title}`), el("span", { class: "x" }, f.label));
  }

  Tanka.module("boards", {
    tabs: [{ id: "boards", label: T.tab, render,
             sig: (sc, mod) => { const v = currentView(mod); return [mod, bs.view, bs.filter, bs.open, v ? bs.rows[rowsKey(sc, v)] : null]; } }],
    chips: chip,
    using: T.using,
    hello: () => T.hello,
    refresh: async (sc, mod) => { if (Tanka.ui.tab === "boards") await load(sc, mod); },
  });
})();
