// Boards on the page: each view's rows grouped and counted, a row's buttons writing a message (docs/page.md).
// A group is a card: its name, how many rows are in each state (a bar and counts), and what all its rows share.
// A row shows who or what it is, its short fields, its state as a coloured pill and the one action that fits
// that state; the rest (the long text, every field, the other actions, archiving) opens under it.
(() => {
  const { el, api, act, when, icon } = Tanka;
  const T = {
    tab: "Boards", board: "Board", filter: "Filter…", rows: n => `${n} row${n === 1 ? "" : "s"}`, all: "All",
    empty: s => `No boards yet. Declare one with /new-view in tanka dev ${s}, or start from the example: tanka boards example ${s} && tanka boards build ${s}`,
    broken: (v, p) => `${v} is not ready: ${p}. Check it with: tanka boards check`,
    noRows: "Nothing recorded yet. Ask the assistant to fill it.",
    noRowsTools: "Nothing recorded yet. Rows appear as the workspace's own tools write them.",
    noMatch: "No row matches.", loading: "Loading…", none: "—",
    open: "Show details", close: "Hide details", details: "Details", change: "Change",
    archive: "Archive", confirmArchive: "Confirm archive", archiveTitle: "Hides the row from the board; its data stays in the rows file",
    updated: (t, by) => `Updated ${when(t)}${by === "user" ? " by you" : by ? ` by ${by}` : ""}`,
    actionTitle: "Writes this in the chat box for you to read and send",
    chip: { created: "Recorded", updated: "Updated" },
    using: name => /boards_record_/.test(name) ? "recording on the board…" : /boards_rows/.test(name) ? "reading the board…" : null,
    hello: "I also fill your boards, and a row's buttons write what to ask me.",
  };
  // Colours for a choice field's values, in order, unless the view names them ("tones").
  const TONES = ["info", "warn", "good", "accent", "bad", "dev", "neutral"];
  const bs = { view: null, filter: "", state: null, open: null, menu: null, armed: null, rows: {}, loading: false };
  try { bs.view = localStorage.getItem("tanka.board"); } catch (e) { /* storage blocked */ }
  const viewsOf = mod => (mod && mod.views) || [];
  const currentView = mod => viewsOf(mod).find(v => v.name === bs.view) || viewsOf(mod)[0] || null;
  const rowsKey = (sc, v) => `${sc.scope}/${v.name}`;
  const text = v => v == null ? "" : String(v);
  const isLong = f => f.type === "text" && f.long;

  async function load(sc, mod) {
    const v = currentView(mod);
    if (!v || v.problems.length || bs.loading) return;
    bs.loading = true;
    try { bs.rows[rowsKey(sc, v)] = (await api(`/api/m/boards/rows?scope=${encodeURIComponent(sc.scope)}&view=${encodeURIComponent(v.name)}`)).rows; }
    catch (e) { /* the next poll tries again */ }
    bs.loading = false;
  }

  // A button's message, with the row's values (one line each, clipped): the user reads it before sending.
  const says = (tpl, row) => tpl.replace(/\{([a-z][a-z0-9_]*)\}/g, (m, k) => text(row.fields[k]).replace(/\s+/g, " ").trim().slice(0, 80) || m);
  const shows = (action, row) => Object.entries(action.when || {}).every(([k, vals]) =>
    [].concat(vals).map(x => String(x).toLowerCase()).includes(text(row.fields[k]).toLowerCase()));
  const shown = (v, row) => (v.actions || []).filter(a => shows(a, row));

  // The field that says where a row stands: the editable choice, else the first choice.
  function stateOf(v) {
    const choices = Object.entries(v.fields).filter(([, f]) => f.type === "choice");
    return (choices.find(([, f]) => f.editable) || choices[0] || [null])[0];
  }
  const toneOf = (f, val) => (f.tones && f.tones[val]) || TONES[Math.max(0, (f.choices || []).indexOf(val)) % TONES.length];
  const pill = (f, val) => val == null || val === ""
    ? el("span", { class: "pill empty" }, T.none)
    : el("span", { class: "pill tone-" + toneOf(f, val) }, el("span", { class: "tdot" }), val);

  function stateMenu(sc, v, row, k) {
    const f = v.fields[k], val = row.fields[k], open = bs.menu === row.id;
    const btn = el("button", { type: "button", class: "pill tone-" + (val ? toneOf(f, val) : "neutral"), "aria-haspopup": "menu",
      "aria-expanded": String(open), title: T.change, on: { click: e => {
        bs.menu = open ? null : row.id;
        bs.menuAt = e.currentTarget.getBoundingClientRect();
        Tanka.render();
      } } },
      el("span", { class: "tdot" }), val || T.none, icon("chevron"));
    const menu = open ? el("ul", { class: "pmenu", role: "menu" }, f.choices.map(c => el("li", { role: "none" },
      el("button", { type: "button", role: "menuitemradio", "aria-checked": String(c === val), on: { click: () => {
        bs.menu = null;
        act(api("/api/m/boards/set", { scope: sc.scope, view: v.name, id: row.id, field: k, value: c }));
      } } }, el("span", { class: "tdot tone-" + toneOf(f, c) }), c)))) : null;
    if (menu) placeMenu(menu, f.choices.length);
    return el("span", { class: "menu-wrap" }, btn, menu);
  }
  // The menu is fixed to the screen, not to its row: the group card and the table both clip what overflows
  // them, which would cut the menu off on a group's last row. Below the pill, or above it near the bottom.
  function placeMenu(menu, n, r = bs.menuAt) {
    const h = n * 34 + 12, w = 190;
    if (!r) return;
    menu.style.position = "fixed";
    menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8)) + "px";
    if (window.innerHeight - r.bottom < h + 8 && r.top > h + 8) {
      menu.style.top = "auto";
      menu.style.bottom = (window.innerHeight - r.top + 4) + "px";
    } else {
      menu.style.top = (r.bottom + 4) + "px";
    }
  }
  const closeMenu = () => { bs.menu = null; Tanka.render(); };
  document.addEventListener("pointerdown", e => { if (bs.menu && !e.target.closest(".menu-wrap")) closeMenu(); });
  // A fixed menu would stay put while its row scrolls: it follows its pill, and closes once the pill is out of sight.
  document.addEventListener("scroll", () => {
    const pill = bs.menu && document.querySelector('.menu-wrap > button[aria-expanded="true"]');
    const menu = pill && pill.parentNode.querySelector(".pmenu");
    if (!menu) return;
    const r = pill.getBoundingClientRect(), view = document.getElementById("view").getBoundingClientRect();
    if (r.bottom < view.top || r.top > view.bottom) return closeMenu();
    bs.menuAt = r;
    placeMenu(menu, menu.children.length, r);
  }, true);
  window.addEventListener("resize", () => { if (bs.menu) closeMenu(); });

  function actionBtn(a, row, cls) {
    return el("button", { type: "button", class: "btn" + (cls ? " " + cls : ""), title: T.actionTitle,
      on: { click: e => { e.stopPropagation(); Tanka.compose(says(a.says, row)); } } }, a.label);
  }

  function toggle(row) {
    bs.open = bs.open === row.id ? null : row.id;
    bs.menu = null; bs.armed = null;
    Tanka.render();
  }

  function detail(sc, v, row, span, state) {
    const longs = Object.entries(v.fields).filter(([, f]) => isLong(f));
    const short = Object.entries(v.fields).filter(([, f]) => !isLong(f));
    const armed = bs.armed === row.id;
    return el("tr", { class: "rdetail" }, el("td", { colspan: String(span) }, el("div", { class: "rdetail-grid" },
      el("div", {},
        longs.length ? longs.map(([k, f]) => [el("span", { class: "rlabel" }, f.label),
          el("p", { class: "rlong" + (row.fields[k] ? "" : " rmuted") }, text(row.fields[k]) || T.none)])
          : el("span", { class: "rlabel" }, T.details),
        el("div", { class: "ractions" },
          shown(v, row).map(a => actionBtn(a, row)),
          el("button", { type: "button", class: "btn danger" + (armed ? " armed" : ""), title: T.archiveTitle, on: { click: () => {
            if (!armed) { bs.armed = row.id; Tanka.render(); setTimeout(() => { if (bs.armed === row.id) { bs.armed = null; Tanka.render(); } }, 4000); return; }
            bs.armed = null; bs.open = null;
            act(api("/api/m/boards/archive", { scope: sc.scope, view: v.name, id: row.id }));
          } } }, armed ? T.confirmArchive : T.archive))),
      el("div", {},
        el("dl", { class: "kv" }, short.map(([k, f]) => [el("dt", {}, f.label),
          el("dd", {}, k === state && f.editable ? stateMenu(sc, v, row, k)
            : f.type === "choice" ? pill(f, row.fields[k]) : text(row.fields[k]) || T.none)])),
        row.updated ? el("p", { class: "meta" }, T.updated(row.updated, row.updated_by)) : null))));
  }

  function groupCard(sc, v, name, rows, state) {
    const g = v.group_by;
    const shortCols = v.columns.filter(k => k in v.fields && !isLong(v.fields[k]) && k !== g);
    // What every row of the group has in common goes on the card once, not on every row.
    const shared = rows.length > 1 ? shortCols.filter(k => k !== state && v.fields[k].type !== "number"
      && rows.every(r => text(r.fields[k]) === text(rows[0].fields[k]))) : [];
    let cols = shortCols.filter(k => !shared.includes(k));
    const lead = cols.find(k => k !== state && v.fields[k].type === "text") || cols[0];
    cols = cols.filter(k => k !== lead && k !== state);
    const preview = Object.keys(v.fields).find(k => isLong(v.fields[k]));
    const sf = state ? v.fields[state] : null;
    const counts = sf ? sf.choices.map(c => [c, rows.filter(r => r.fields[state] === c).length]).filter(([, n]) => n) : [];

    const bar = el("div", { class: "segbar", "aria-hidden": "true" }, counts.map(([c, n]) => {
      const seg = el("span", { class: "tone-" + toneOf(sf, c), title: `${c}: ${n}` });
      seg.style.flexGrow = String(n);  // CSSOM: the page's CSP refuses style attributes, not this
      return seg;
    }));
    const span = 2 + cols.length + (sf ? 1 : 0);
    const body = [];
    for (const row of rows) {
      const open = bs.open === row.id, primary = shown(v, row)[0];
      body.push(el("tr", { class: "r" + (open ? " open" : "") },
        el("td", {}, el("div", { class: "rname" },
          el("button", { type: "button", class: "link", "aria-expanded": String(open), on: { click: () => toggle(row) } },
            text(row.fields[lead]) || T.none),
          preview && row.fields[preview] && !open ? el("span", { class: "pv" }, text(row.fields[preview]).replace(/\s+/g, " ")) : null)),
        cols.map(k => {
          const f = v.fields[k], val = row.fields[k];
          if (f.type === "choice") return el("td", {}, pill(f, val));
          return el("td", { class: f.type === "number" ? "num" : "" }, val == null || val === "" ? el("span", { class: "rmuted" }, T.none) : text(val));
        }),
        sf ? el("td", {}, pill(sf, row.fields[state])) : null,
        el("td", { class: "end" },
          primary ? actionBtn(primary, row, "small") : null,
          el("button", { type: "button", class: "expand", "aria-expanded": String(open), "aria-label": open ? T.close : T.open,
            title: open ? T.close : T.open, on: { click: () => toggle(row) } }, icon("chevron")))));
      if (open) body.push(detail(sc, v, row, span, state));
    }
    const label = k => v.fields[k].label;
    return el("section", { class: "gcard" },
      el("div", { class: "gcard-h" }, el("span", { class: "t" }, g ? text(name) || T.none : v.title),
        el("span", { class: "n" }, [T.rows(rows.length), ...counts.map(([c, n]) => `${n} ${c}`)].join(" · "))),
      counts.length ? bar : null,
      shared.length ? el("div", { class: "gcard-shared" }, shared.map(k => el("span", {}, `${label(k)}: `, el("b", {}, text(rows[0].fields[k]))))) : null,
      el("div", { class: "rtable-wrap" }, el("table", { class: "rtable" },
        el("thead", {}, el("tr", {}, el("th", {}, label(lead)), cols.map(k => el("th", { class: v.fields[k].type === "number" ? "num" : "" }, label(k))),
          sf ? el("th", {}, sf.label) : null, el("th", {}, ""))),
        el("tbody", {}, body))));
  }

  function render(sc, mod) {
    const views = viewsOf(mod);
    if (!views.length) return [el("p", { class: "empty" }, T.empty(sc.scope))];
    const v = currentView(mod);
    const head = el("div", { class: "rec-h" },
      views.length > 1
        ? el("select", { "aria-label": T.board, on: { change: e => { bs.view = e.target.value; bs.open = null; bs.state = null;
            try { localStorage.setItem("tanka.board", bs.view); } catch (x) { /* blocked */ } Tanka.refresh(); } } },
            views.map(x => el("option", { value: x.name, selected: x.name === v.name }, x.title)))
        : el("h2", {}, v.title),
      v.problems.length ? null : el("span", { class: "count" }, T.rows(v.count)),
      el("span", { class: "grow" }),
      el("input", { class: "rec-search", id: "board-filter", type: "search", placeholder: T.filter, value: bs.filter, "aria-label": T.filter,
        on: { input: e => { const pos = e.target.selectionStart; bs.filter = e.target.value; Tanka.render();
          const i = document.getElementById("board-filter"); if (i) { i.focus(); i.setSelectionRange(pos, pos); } } } }));
    if (v.problems.length) return [head, el("p", { class: "problem" }, T.broken(v.title, v.problems[0]))];
    const sub = el("p", { class: "rec-sub" }, v.description);
    const all = bs.rows[rowsKey(sc, v)];
    if (!all) { load(sc, mod).then(() => Tanka.render()); return [head, sub, el("p", { class: "meta" }, T.loading)]; }
    if (!all.length) return [head, sub, el("p", { class: "empty" }, mod && mod.tools ? T.noRows : T.noRowsTools)];

    const state = stateOf(v), sf = state ? v.fields[state] : null;
    const q = bs.filter.trim().toLowerCase();
    const matching = q ? all.filter(r => Object.values(r.fields).some(x => text(x).toLowerCase().includes(q))) : all;
    if (bs.state && !(sf && sf.choices.includes(bs.state))) bs.state = null;
    const rows = bs.state ? matching.filter(r => r.fields[state] === bs.state) : matching;
    const facet = (val, label, n, tone) => el("button", { type: "button", class: "facet" + (tone ? " tone-" + tone : ""),
      "aria-pressed": String(bs.state === val), on: { click: () => { bs.state = val; Tanka.render(); } } },
      tone ? el("span", { class: "tdot" }) : null, label, el("span", { class: "n" }, String(n)));
    const facets = sf ? el("div", { class: "facets" }, facet(null, T.all, matching.length),
      sf.choices.map(c => [c, matching.filter(r => r.fields[state] === c).length]).filter(([, n]) => n)
        .map(([c, n]) => facet(c, c, n, toneOf(sf, c)))) : null;

    const out = [head, sub, facets];
    if (!rows.length) return [...out, el("p", { class: "empty" }, T.noMatch)];
    const g = v.group_by, groups = new Map();
    const sortKey = r => v.key.map(k => text(r.fields[k]).toLowerCase()).join("\u0000");
    for (const r of [...rows].sort((a, b) => sortKey(a).localeCompare(sortKey(b)))) {
      const name = g ? text(r.fields[g]) : "";
      if (!groups.has(name)) groups.set(name, []);
      groups.get(name).push(r);
    }
    for (const [name, rs] of [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])))
      out.push(groupCard(sc, v, name, rs, state));
    return out;
  }

  function chip(sc, f) {
    return el("button", { type: "button", class: "fx", title: `${f.title}: ${f.label}`,
      on: { click: () => { bs.view = f.view; bs.open = f.id; bs.filter = ""; bs.state = null; Tanka.go("boards"); Tanka.refresh(); } } },
      icon("table"), el("span", { class: "k" }, `${T.chip[f.type] || f.type} · ${f.title}`), el("span", { class: "x" }, f.label));
  }

  Tanka.module("boards", {
    tabs: [{ id: "boards", label: T.tab, render, wide: true,
             sig: (sc, mod) => { const v = currentView(mod); return [mod, bs.view, bs.filter, bs.state, bs.open, bs.menu, bs.armed, v ? bs.rows[rowsKey(sc, v)] : null]; } }],
    chips: chip,
    using: T.using,
    hello: (sc, mod) => mod && mod.tools ? T.hello : "",
    refresh: async (sc, mod) => { if (Tanka.ui.tab === "boards") await load(sc, mod); },
  });
})();
