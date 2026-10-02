// WhatsApp on the page: the People tab, with this workspace's roles and contacts and each person's record
// (docs/page.md). What changes here is contacts.json, by the user's hand; nothing here opens WhatsApp.
// It shares the look of the boards: a card per role, a clean row per person, everything else under the row.
(() => {
  const { el, api, icon } = Tanka;
  const T = {
    tab: "People", filter: "Filter…", people: n => `${n} contact${n === 1 ? "" : "s"}`, loading: "Loading…", all: "All",
    sub: "WhatsApp contacts whose role belongs to this workspace, with what the assistant keeps about each. Changes here edit contacts.json; WhatsApp is never opened.",
    noRegistry: why => `${why} It holds the WhatsApp roles and contacts; tanka install whatsapp creates an example.`,
    noRoles: (s, p) => `No WhatsApp role belongs to ${s} yet. Give one "workspace": "${s}" in ${p}.`,
    noPeople: "No contact has one of these roles yet. Add one.", noMatch: "No contact matches.",
    read: "Read", reply: "Reply", auto: "Auto-reply", instructions: "Has instructions the assistant reads with these chats.", rolesTitle: "Roles",
    flagTitle: { read: "The assistant may read these chats", reply: "The assistant may answer, after you approve",
                 auto_reply: "The assistant may answer on its own, when you are not there" },
    autoNeedsReply: "Turn Reply on first",
    autoConfirm: role => `Auto-reply for ${role}: the assistant will answer these people without asking you.`,
    turnOn: "Turn on", cancel: "Cancel",
    addOpen: "Add contact", number: "Number, with country code", name: "Name", role: "Role", add: "Add", none: "—",
    cols: { name: "Name", number: "Number", last: "Last note" },
    readChat: "Read chat", readChatTitle: "Writes the request in the chat box; nothing is sent until you send it",
    ask: (name, num) => `Read my WhatsApp chat with ${name} (${num}) and tell me what is pending.`,
    open: "Show details", close: "Hide details",
    remove: "Remove contact", confirmRemove: "Confirm remove", rename: "Name", save: "Save",
    record: "What the assistant keeps", noRecord: p => `No record yet (${p}).`, notes: n => `${n} note${n === 1 ? "" : "s"}`,
    using: name => /whatsapp_reply/.test(name) ? "writing on WhatsApp…" : /whatsapp_/.test(name) ? "reading WhatsApp…" : null,
  };
  const FLAGS = [["read", T.read], ["reply", T.reply], ["auto_reply", T.auto]];
  const TONES = ["info", "good", "accent", "warn", "dev", "bad", "neutral"];
  const ws = { filter: "", role: null, open: null, people: {}, person: {}, loading: false, err: "", confirmAuto: null, armed: null,
               adding: false, form: { number: "", name: "", role: "" }, renameTo: "" };
  const text = v => v == null ? "" : String(v);
  const rolesOf = mod => (mod && mod.roles) || [];
  const q = (sc, extra) => `scope=${encodeURIComponent(sc.scope)}${extra || ""}`;
  // The number as WhatsApp knows it: "+" and its digits. Grouping them would mean guessing the country's format.
  const fmt = num => "+" + text(num).replace(/\D/g, "");
  const toneOf = (roles, name) => TONES[Math.max(0, roles.findIndex(r => r.name === name)) % TONES.length];

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

  function roleCard(sc, r, roles) {
    const toggle = ([f, label]) => {
      const blocked = f === "auto_reply" && !r.reply && !r.auto_reply;
      return el("button", { type: "button", class: "toggle", "aria-pressed": String(!!r[f]), disabled: blocked,
        title: blocked ? T.autoNeedsReply : T.flagTitle[f], on: { click: () => {
          if (f === "auto_reply" && !r.auto_reply) { ws.confirmAuto = r.name; Tanka.render(); return; }
          change(sc, "role_flags", { role: r.name, [f]: !r[f] });
        } } }, r[f] ? icon("check") : null, label);
    };
    return el("div", { class: "rcard" },
      el("div", { class: "rcard-h" }, el("span", { class: "pill tone-" + toneOf(roles, r.name) }, el("span", { class: "tdot" }), r.name),
        el("span", { class: "n" }, T.people(r.count))),
      el("div", { class: "toggles" }, FLAGS.map(toggle)),
      r.instructions ? el("p", { class: "rcard-note" }, T.instructions) : null,
      ws.confirmAuto === r.name ? el("div", { class: "confirm-line" }, T.autoConfirm(r.name),
        el("button", { type: "button", class: "btn small danger armed", on: { click: () => { ws.confirmAuto = null;
          change(sc, "role_flags", { role: r.name, auto_reply: true, confirm: true }); } } }, T.turnOn),
        el("button", { type: "button", class: "btn small", on: { click: () => { ws.confirmAuto = null; Tanka.render(); } } }, T.cancel)) : null);
  }

  function addForm(sc, roles) {
    if (!roles.some(r => r.name === ws.form.role)) ws.form.role = roles[0].name;
    const submit = () => change(sc, "add", { ...ws.form }, () => { ws.form.number = ""; ws.form.name = ""; ws.adding = false; });
    const input = (id, key, label, attrs) => el("input", { id, value: ws.form[key], placeholder: label, "aria-label": label, ...attrs,
      on: { input: e => { ws.form[key] = e.target.value; }, keydown: e => { if (e.key === "Enter") submit(); } } });
    return el("div", { class: "rform" },
      input("wa-add-number", "number", T.number, { inputmode: "tel", autocomplete: "off" }),
      input("wa-add-name", "name", T.name, { maxlength: "60", autocomplete: "off" }),
      el("select", { "aria-label": T.role, on: { change: e => { ws.form.role = e.target.value; } } },
        roles.map(r => el("option", { value: r.name, selected: r.name === ws.form.role }, r.name))),
      el("button", { type: "button", class: "btn primary", on: { click: submit } }, T.add),
      el("button", { type: "button", class: "btn", on: { click: () => { ws.adding = false; Tanka.render(); } } }, T.cancel));
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

  function toggleRow(sc, p) {
    const open = ws.open === p.number;
    ws.open = open ? null : p.number; ws.renameTo = p.name; ws.armed = null;
    Tanka.render();
    if (!open) loadPerson(sc, p.number);
  }

  function detail(sc, p, span, roles) {
    const rec = ws.person[`${sc.scope}/${p.number}`], armed = ws.armed === p.number;
    const saveName = () => change(sc, "rename", { number: p.number, name: ws.renameTo });
    const left = [el("span", { class: "rlabel" }, T.record)];
    if (!rec) left.push(el("p", { class: "meta" }, T.loading));
    else if (!Object.keys(rec.fields).length && !rec.notes) left.push(el("p", { class: "rlong rmuted" }, T.noRecord(rec.path)));
    else {
      if (Object.keys(rec.fields).length) left.push(el("dl", { class: "kv" }, Object.entries(rec.fields).map(([k, v]) => [el("dt", {}, k.replace(/_/g, " ")), el("dd", {}, v)])));
      if (rec.notes) left.push(el("p", { class: "rlong" }, rec.notes));
    }
    return el("tr", { class: "rdetail" }, el("td", { colspan: String(span) }, el("div", { class: "rdetail-grid" },
      el("div", {}, left),
      el("div", {},
        el("dl", { class: "kv" },
          el("dt", {}, T.cols.number), el("dd", {}, el("span", { class: "mono" }, fmt(p.number))),
          el("dt", {}, T.role), el("dd", {}, roles.length > 1
            ? el("select", { "aria-label": T.role, on: { change: e => change(sc, "set_role", { number: p.number, role: e.target.value }) } },
                roles.map(r => el("option", { value: r.name, selected: r.name === p.role }, r.name)))
            : p.role),
          el("dt", {}, T.rename), el("dd", {}, el("div", { class: "inline-edit" },
            el("input", { id: "wa-rename", value: ws.renameTo, maxlength: "60", "aria-label": T.rename,
              on: { input: e => { ws.renameTo = e.target.value; }, keydown: e => { if (e.key === "Enter") saveName(); } } }),
            el("button", { type: "button", class: "btn small", on: { click: saveName } }, T.save)))),
        el("div", { class: "ractions" },
          el("button", { type: "button", class: "btn danger" + (armed ? " armed" : ""), on: { click: () => {
            if (!armed) { ws.armed = p.number; Tanka.render(); setTimeout(() => { if (ws.armed === p.number) { ws.armed = null; Tanka.render(); } }, 4000); return; }
            ws.armed = null; ws.open = null;
            change(sc, "remove", { number: p.number });
          } } }, armed ? T.confirmRemove : T.remove))))));
  }

  function groupCard(sc, role, roles, cols, rows, fields) {
    const span = 4 + cols.length, tally = counts(fields, rows);
    const body = [];
    for (const p of rows) {
      const open = ws.open === p.number, preview = fields.map(k => p.fields[k]).find(Boolean);
      body.push(el("tr", { class: "r" + (open ? " open" : "") },
        el("td", {}, el("div", { class: "rname" },
          el("button", { type: "button", class: "link", "aria-expanded": String(open), on: { click: () => toggleRow(sc, p) } }, p.name || fmt(p.number)),
          preview && !open && !cols.length ? el("span", { class: "pv" }, text(preview)) : null)),
        el("td", {}, el("span", { class: "mono" }, fmt(p.number))),
        cols.map(k => el("td", {}, p.fields[k] ? text(p.fields[k]) : el("span", { class: "rmuted" }, T.none))),
        el("td", { class: "rmuted", title: T.notes(p.notes) }, p.last_note || T.none),
        el("td", { class: "end" },
          role.read ? el("button", { type: "button", class: "btn small", title: T.readChatTitle,
            on: { click: () => Tanka.compose(T.ask(p.name || fmt(p.number), fmt(p.number))) } }, T.readChat) : null,
          el("button", { type: "button", class: "expand", "aria-expanded": String(open), "aria-label": open ? T.close : T.open,
            title: open ? T.close : T.open, on: { click: () => toggleRow(sc, p) } }, icon("chevron")))));
      if (open) body.push(detail(sc, p, span, roles));
    }
    return el("section", { class: "gcard" },
      el("div", { class: "gcard-h" },
        el("span", { class: "pill tone-" + toneOf(roles, role.name) }, el("span", { class: "tdot" }), role.name),
        el("span", { class: "n" }, [T.people(rows.length), tally].filter(Boolean).join(" · "))),
      el("div", { class: "rtable-wrap" }, el("table", { class: "rtable" },
        el("thead", {}, el("tr", {}, el("th", {}, T.cols.name), el("th", {}, T.cols.number),
          cols.map(k => el("th", {}, k.replace(/_/g, " "))), el("th", {}, T.cols.last), el("th", {}, ""))),
        el("tbody", {}, body))));
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
    const head = el("div", { class: "rec-h" }, el("h2", {}, T.tab),
      data ? el("span", { class: "count" }, T.people(data.people.length)) : null,
      el("span", { class: "grow" }),
      el("input", { class: "rec-search", id: "wa-filter", type: "search", placeholder: T.filter, value: ws.filter, "aria-label": T.filter,
        on: { input: e => { ws.filter = e.target.value; Tanka.render(); } } }),
      ws.adding ? null : el("button", { type: "button", class: "btn primary", on: { click: () => { ws.adding = true; Tanka.render();
        setTimeout(() => { const i = document.getElementById("wa-add-number"); if (i) i.focus(); }, 0); } } }, T.addOpen));
    const out = [head, el("p", { class: "rec-sub" }, T.sub),
      ws.adding ? addForm(sc, roles) : null,
      ws.err ? el("p", { class: "problem" }, ws.err) : null,
      el("span", { class: "rlabel" }, T.rolesTitle),
      el("div", { class: "rcards" }, roles.map(r => roleCard(sc, r, roles)))];
    if (!data) { load(sc).then(() => Tanka.render()); return [...out, el("p", { class: "meta" }, T.loading)]; }
    if (!data.people.length) return [...out, el("p", { class: "empty" }, T.noPeople)];
    const needle = ws.filter.trim().toLowerCase();
    const matching = needle ? data.people.filter(p => [p.name, p.number, fmt(p.number), p.role, ...Object.values(p.fields)]
      .some(x => text(x).toLowerCase().includes(needle))) : data.people;
    if (ws.role && !roles.some(r => r.name === ws.role)) ws.role = null;
    const facet = (val, label, n, tone) => el("button", { type: "button", class: "facet" + (tone ? " tone-" + tone : ""),
      "aria-pressed": String(ws.role === val), on: { click: () => { ws.role = val; Tanka.render(); } } },
      tone ? el("span", { class: "tdot" }) : null, label, el("span", { class: "n" }, String(n)));
    if (roles.length > 1) out.push(el("div", { class: "facets" }, facet(null, T.all, matching.length),
      roles.map(r => [r, matching.filter(p => p.role === r.name).length]).filter(([, n]) => n).map(([r, n]) => facet(r.name, r.name, n, toneOf(roles, r.name)))));
    const shown = ws.role ? matching.filter(p => p.role === ws.role) : matching;
    if (!shown.length) return [...out, el("p", { class: "empty" }, T.noMatch)];
    const cols = data.fields.slice(0, 3);
    for (const r of roles) {
      const rows = shown.filter(p => p.role === r.name);
      if (rows.length) out.push(groupCard(sc, r, roles, cols, rows, data.fields));
    }
    return out;
  }

  Tanka.module("whatsapp", {
    tabs: [{ wide: true, id: "people", label: T.tab, render,
             sig: (sc, mod) => [mod, ws.filter, ws.role, ws.open, ws.people[sc.scope], ws.open && ws.person[`${sc.scope}/${ws.open}`],
                                ws.err, ws.confirmAuto, ws.armed, ws.adding] }],
    using: T.using,
    refresh: async (sc) => { if (Tanka.ui.tab === "people") await load(sc); },
  });
})();
