// WhatsApp on the page: the People tab, with this workspace's roles and contacts and each person's record
// (docs/page.md). What changes here is contacts.json, by the user's hand; nothing here opens WhatsApp.
(() => {
  const { el, api, icon } = Tanka;
  const T = {
    tab: "People", filter: "Filter by name, number, field…", people: n => `${n} contact(s)`, loading: "Loading…",
    noRegistry: why => `${why} It holds the WhatsApp roles and contacts; tanka install whatsapp creates an example.`,
    noRoles: (s, p) => `No WhatsApp role belongs to ${s} yet. Give one "workspace": "${s}" in ${p}.`,
    noPeople: "No contact has one of these roles yet. Add one above.", noMatch: "No contact matches.",
    read: "Read", reply: "Reply", auto: "Auto-reply", instructions: "has instructions",
    flagTitle: { read: "The assistant may read these chats", reply: "The assistant may answer, after you approve",
                 auto_reply: "The assistant may answer on its own, when you are not there" },
    autoNeedsReply: "Turn Reply on first",
    autoConfirm: role => `Turn on auto-reply for ${role}: the assistant will answer these people without asking you.`,
    turnOn: "Turn on", cancel: "Cancel",
    number: "Number, with country code", name: "Name", role: "Role", add: "Add contact",
    cols: { name: "Name", number: "Number", last: "Last note", acts: "" },
    readChat: "Read chat", readChatTitle: "Puts a request in the chat box; nothing is sent until you send it",
    ask: (name, num) => `Read my WhatsApp chat with ${name} (${num}) and tell me what is pending.`,
    remove: "Remove contact", confirmRemove: "Remove", rename: "Rename", save: "Save",
    record: "Record", noRecord: p => `No record yet (${p}).`, notes: n => `${n} note(s)`,
    using: name => /whatsapp_reply/.test(name) ? "writing on WhatsApp…" : /whatsapp_/.test(name) ? "reading WhatsApp…" : null,
  };
  const FLAGS = [["read", T.read], ["reply", T.reply], ["auto_reply", T.auto]];
  const ws = { filter: "", open: null, people: {}, person: {}, loading: false, err: "", confirmAuto: null, confirmRemove: null,
               form: { number: "", name: "", role: "" }, renameTo: "" };
  const text = v => v == null ? "" : String(v);
  const rolesOf = mod => (mod && mod.roles) || [];
  const q = (sc, extra) => `scope=${encodeURIComponent(sc.scope)}${extra || ""}`;

  // The number as WhatsApp knows it: "+" and its digits. Grouping them would mean guessing the country's format.
  const fmt = num => "+" + text(num).replace(/\D/g, "");

  async function load(sc) {
    if (ws.loading) return;
    ws.loading = true;
    try { ws.people[sc.scope] = await api(`/api/m/whatsapp/people?${q(sc)}`); }
    catch (e) { ws.err = e.message; }
    ws.loading = false;
  }
  async function loadPerson(sc, num) {
    try { ws.person[`${sc.scope}/${num}`] = await api(`/api/m/whatsapp/person?${q(sc, "&number=" + encodeURIComponent(num))}`); }
    catch (e) { ws.err = e.message; }
    Tanka.render();
  }
  // A refusal stays on the tab until the next change: act() would show it as the page being unreachable.
  async function change(sc, name, body, after) {
    try { await api(`/api/m/whatsapp/${name}`, { scope: sc.scope, ...body }); ws.err = ""; if (after) after(); }
    catch (e) { ws.err = e.message; }
    await Tanka.refresh();
  }

  function roleLine(sc, r) {
    const flag = ([f, label]) => {
      const blocked = f === "auto_reply" && !r.reply && !r.auto_reply;
      return el("button", { type: "button", class: "ghost", "aria-pressed": String(!!r[f]), disabled: blocked,
        title: blocked ? T.autoNeedsReply : T.flagTitle[f], on: { click: () => {
          if (f === "auto_reply" && !r.auto_reply) { ws.confirmAuto = r.name; Tanka.render(); return; }
          change(sc, "role_flags", { role: r.name, [f]: !r[f] });
        } } }, r[f] ? icon("check") : null, label);
    };
    return el("div", { class: "row" },
      el("span", { class: "chip on" }, `${r.name} · ${r.count}`),
      FLAGS.map(flag),
      r.instructions ? el("span", { class: "meta" }, T.instructions) : null,
      ws.confirmAuto === r.name ? [
        el("span", { class: "warning" }, T.autoConfirm(r.name)),
        el("button", { type: "button", class: "btn primary", on: { click: () => { ws.confirmAuto = null;
          change(sc, "role_flags", { role: r.name, auto_reply: true, confirm: true }); } } }, T.turnOn),
        el("button", { type: "button", class: "ghost", on: { click: () => { ws.confirmAuto = null; Tanka.render(); } } }, T.cancel),
      ] : null);
  }

  function addForm(sc, roles) {
    if (!roles.some(r => r.name === ws.form.role)) ws.form.role = roles[0].name;
    const input = (id, key, label, attrs) => el("input", { class: "filter", id, value: ws.form[key], placeholder: label, "aria-label": label,
      ...attrs, on: { input: e => { ws.form[key] = e.target.value; }, keydown: e => { if (e.key === "Enter") submit(); } } });
    const submit = () => change(sc, "add", { ...ws.form }, () => { ws.form.number = ""; ws.form.name = ""; });
    return el("div", { class: "board-h" },
      input("wa-add-number", "number", T.number, { inputmode: "tel", autocomplete: "off" }),
      input("wa-add-name", "name", T.name, { maxlength: "60", autocomplete: "off" }),
      roles.length > 1
        ? el("select", { "aria-label": T.role, on: { change: e => { ws.form.role = e.target.value; } } },
            roles.map(r => el("option", { value: r.name, selected: r.name === ws.form.role }, r.name)))
        : el("span", { class: "chip" }, roles[0].name),
      el("button", { type: "button", class: "btn", on: { click: submit } }, T.add));
  }

  // "3 quoted · 1 won": sale_status when the group has it, else the most common field with few values.
  function counts(fields, rows) {
    const pick = ["sale_status", ...fields].find(k => {
      const vals = rows.map(r => r.fields[k]).filter(Boolean);
      return vals.length && new Set(vals.map(v => v.toLowerCase())).size <= 5;
    });
    if (!pick) return "";
    const n = new Map();
    for (const r of rows) { const v = r.fields[pick]; if (v) n.set(v.toLowerCase(), (n.get(v.toLowerCase()) || 0) + 1); }
    return [...n.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4).map(([v, c]) => `${c} ${v}`).join(" · ");
  }

  function detail(sc, p, span) {
    const rec = ws.person[`${sc.scope}/${p.number}`];
    const kids = [
      el("div", { class: "row" },
        el("input", { class: "filter", id: "wa-rename", value: ws.renameTo, maxlength: "60", "aria-label": T.rename,
          on: { input: e => { ws.renameTo = e.target.value; },
                keydown: e => { if (e.key === "Enter") change(sc, "rename", { number: p.number, name: ws.renameTo }); } } }),
        el("button", { type: "button", class: "btn", on: { click: () => change(sc, "rename", { number: p.number, name: ws.renameTo }) } }, T.rename)),
    ];
    if (!rec) kids.push(el("p", { class: "meta" }, T.loading));
    else if (!Object.keys(rec.fields).length && !rec.notes) kids.push(el("p", { class: "meta" }, T.noRecord(rec.path)));
    else {
      kids.push(el("span", { class: "k" }, `${T.record} · ${rec.path}`));
      for (const [k, v] of Object.entries(rec.fields)) kids.push(el("div", {}, el("span", { class: "meta" }, `${k}: `), v));
      if (rec.notes) kids.push(el("pre", { class: "section" }, rec.notes));
    }
    return el("tr", { class: "detail" }, el("td", { colspan: String(span) }, kids));
  }

  function table(sc, roles, cols, rows) {
    const body = [];
    for (const p of rows) {
      const role = roles.find(r => r.name === p.role) || {};
      const open = ws.open === p.number;
      body.push(el("tr", { class: open ? "row-open" : "" },
        el("td", {}, el("button", { type: "button", class: "link", "aria-expanded": String(open), on: { click: () => {
          ws.open = open ? null : p.number; ws.renameTo = p.name; ws.confirmRemove = null;
          Tanka.render(); if (!open) loadPerson(sc, p.number); } } }, p.name || "—")),
        el("td", {}, el("span", { class: "mono" }, fmt(p.number))),
        cols.map(k => el("td", { class: "long", title: text(p.fields[k]) }, text(p.fields[k]) || "—")),
        el("td", { class: "num", title: T.notes(p.notes) }, p.last_note || "—"),
        el("td", { class: "acts" },
          roles.length > 1 ? el("select", { "aria-label": T.role, on: { change: e => change(sc, "set_role", { number: p.number, role: e.target.value }) } },
            roles.map(r => el("option", { value: r.name, selected: r.name === p.role }, r.name))) : null,
          role.read ? el("button", { type: "button", class: "btn", title: T.readChatTitle,
            on: { click: () => Tanka.compose(T.ask(p.name || fmt(p.number), p.number)) } }, T.readChat) : null,
          ws.confirmRemove === p.number
            ? el("button", { type: "button", class: "btn", on: { click: () => { ws.confirmRemove = null; ws.open = null;
                change(sc, "remove", { number: p.number }); } } }, T.confirmRemove)
            : el("button", { type: "button", class: "ghost", title: T.remove, "aria-label": T.remove,
                on: { click: () => { ws.confirmRemove = p.number; Tanka.render(); } } }, icon("x")))));
      if (open) body.push(detail(sc, p, cols.length + 4));
    }
    return el("div", { class: "tablewrap" }, el("table", { class: "board" },
      el("thead", {}, el("tr", {}, el("th", {}, T.cols.name), el("th", {}, T.cols.number), cols.map(k => el("th", {}, k.replace(/_/g, " "))),
        el("th", {}, T.cols.last), el("th", {}, T.cols.acts))),
      el("tbody", {}, body)));
  }

  function render(sc, mod) {
    // A poll that rebuilds the tab gives the focus back to the input that had it.
    const f = document.activeElement;
    if (f && f.id && f.id.startsWith("wa-")) {
      const [id, a, b] = [f.id, f.selectionStart, f.selectionEnd];
      setTimeout(() => { const i = document.getElementById(id); if (i && i !== document.activeElement) { i.focus(); try { i.setSelectionRange(a, b); } catch (e) { /* not a text input */ } } }, 0);
    }
    const reg = (mod && mod.registry) || "contacts.json";
    if (mod && mod.problem) return [el("p", { class: "problem" }, T.noRegistry(mod.problem))];
    const roles = rolesOf(mod);
    if (!roles.length) return [el("p", { class: "empty" }, T.noRoles(sc.scope, reg))];
    const data = ws.people[sc.scope];
    const out = [
      el("div", { class: "board-h" }, el("h2", {}, T.tab),
        el("input", { class: "filter", id: "wa-filter", type: "search", placeholder: T.filter, value: ws.filter, "aria-label": T.filter,
          on: { input: e => { ws.filter = e.target.value; Tanka.render(); } } }),
        data ? el("span", { class: "meta" }, T.people(data.people.length)) : null),
      ...roles.map(r => roleLine(sc, r)),
      addForm(sc, roles),
      ws.err ? el("p", { class: "problem" }, ws.err) : null,
    ];
    if (!data) { load(sc).then(() => Tanka.render()); return [...out, el("p", { class: "meta" }, T.loading)]; }
    if (!data.people.length) return [...out, el("p", { class: "empty" }, T.noPeople)];
    const needle = ws.filter.trim().toLowerCase();
    const shown = needle ? data.people.filter(p => [p.name, p.number, fmt(p.number), p.role, ...Object.values(p.fields)]
      .some(x => text(x).toLowerCase().includes(needle))) : data.people;
    if (!shown.length) return [...out, el("p", { class: "empty" }, T.noMatch)];
    const cols = data.fields.slice(0, 4);
    for (const r of roles) {
      const rows = shown.filter(p => p.role === r.name);
      if (!rows.length) continue;
      const tally = counts(data.fields, rows);
      out.push(el("section", { class: "group" },
        el("div", { class: "group-h" }, el("span", {}, r.name), el("span", { class: "meta" }, tally ? `${rows.length} · ${tally}` : String(rows.length))),
        table(sc, roles, cols, rows)));
    }
    return out;
  }

  Tanka.module("whatsapp", {
    tabs: [{ wide: true, id: "people", label: T.tab, render,
             sig: (sc, mod) => [mod, ws.filter, ws.open, ws.people[sc.scope], ws.open && ws.person[`${sc.scope}/${ws.open}`],
                                ws.err, ws.confirmAuto, ws.confirmRemove] }],
    using: T.using,
    refresh: async (sc) => { if (Tanka.ui.tab === "people") await load(sc); },
  });
})();
