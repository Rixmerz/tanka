// The desk on the page: Your day beside the chat, fired reminders and the daily brief in it (docs/page.md).
(() => {
  const { el, svg, api, act, hm, isToday, shortDay, relOf, nameOf, focus, ui, ICON, LOCALE } = Tanka;
  const T = {
    using: name => /desk_card/.test(name) ? "updating your cards…" : /desk_pending/.test(name) ? "checking your cards…" : null,
    suggest: [["What's pending today?", true], ["Remind me in 15 min to ", false], ["I still have to ", false], ["I finished ", false]],
    hello: "Tell me what is pending or what to remind you of, and I keep the cards in Your day; I speak here on my own when a reminder comes due.",
    brief: "Your day", briefToday: "Due today", briefTodo: "To do", briefStale: "Stalled", nothingLeft: "All of it is done or archived now.",
    fx: { reminder: "Reminder", check: "To do", done: "Done" },
    reminder: "Reminder", doneAt: t => `✓ Done at ${t}`, snoozedTo: t => `Snoozed to ${t}`, gone: "Archived",
    done: "Done", s10: "+10 min", s60: "+1 h",
    dayTitle: "Your day",
    nDue: n => `${n} due`, nNext: n => `${n} upcoming`, nOpen: n => `${n} to do`, allClear: "All clear",
    secReminders: "Reminders", secChecks: "To do", doneToday: n => `Done today (${n})`,
    found: "Found on its own", project: "project",
    sideEmpty: "Nothing pending", sideEmptyText: n => `Tell ${n} what you have to do and it shows up here.`,
    archive: "Archive", archiveTopic: "Archive this topic and its items", markDone: "Mark done",
    snooze10: "Snooze 10 minutes", snooze60: "Snooze 1 hour",
  };
  let doneOpen = false;
  const cardAct = (sc, id, action, minutes) => act(api("/api/m/desk/card", { scope: sc.scope, id, action, minutes }));
  const flashCls = id => ui.flash && ui.flash.id === id ? " flash" : "";
  const found = x => x.evidence ? el("span", { class: "tag found", title: T.found + ": " + x.evidence }, "🔎") : null;
  const topicTag = t => t && t !== "general" ? el("span", { class: "tag" }, t) : null;
  const iconBtn = (content, title, fn, cls) => el("button", { type: "button", class: "icon" + (cls ? " " + cls : ""), title, "aria-label": title, on: { click: fn } }, content);

  function chip(sc, f) {
    const ico = { reminder: "⏰", check: "☐", done: "✓" }[f.type] || "•";
    const detail = f.type === "reminder" && f.at ? hm(f.at) + (isToday(f.at) ? "" : " · " + shortDay(f.at)) : (f.topic && f.topic !== "general" ? f.topic : "");
    return el("button", { type: "button", class: "fx", disabled: !!f.gone, title: f.text, on: { click: () => focus(f.id) } },
      el("span", { "aria-hidden": "true" }, ico), el("span", { class: "k" }, T.fx[f.type] + (detail ? " · " + detail : "")), el("span", { class: "x" }, f.text));
  }

  function reminderEvent(sc, m) {
    const st = m.state;
    return el("div", { class: "event" + (st === "done" ? " done" : st === "due" ? "" : " quiet") },
      el("div", { class: "head" }, el("span", { "aria-hidden": "true" }, "⏰"), el("span", { class: "label" }, T.reminder),
        m.topic && m.topic !== "general" ? el("span", { class: "tag" }, m.topic) : null, el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      m.text ? el("p", { class: "text" }, m.text) : null,
      st === "due"
        ? el("div", { class: "acts" },
            el("button", { type: "button", class: "btn primary", on: { click: () => cardAct(sc, m.id, "done") } }, "✓ " + T.done),
            el("button", { type: "button", class: "btn", title: T.snooze10, on: { click: () => cardAct(sc, m.id, "snooze", 10) } }, T.s10),
            el("button", { type: "button", class: "btn", title: T.snooze60, on: { click: () => cardAct(sc, m.id, "snooze", 60) } }, T.s60))
        : el("div", { class: "state" + (st === "done" ? " ok" : "") }, st === "done" ? T.doneAt(hm(m.done_at)) : st === "snoozed" ? T.snoozedTo(hm(m.at)) : T.gone));
  }

  function briefChip(x, ico, detail) {
    return el("button", { type: "button", class: "fx" + (x.done ? " done" : ""), disabled: !!x.gone, title: x.text || "", on: { click: () => focus(x.id) } },
      el("span", { "aria-hidden": "true" }, x.done ? "✓" : ico), el("span", { class: "k" }, detail), el("span", { class: "x" }, x.text || "—"));
  }

  function briefEvent(sc, m) {
    const rem = m.reminders || [], stale = m.stale || [], topics = Object.entries(m.topics || {});
    const open = rem.concat(stale).some(x => !x.done && !x.gone);
    return el("div", { class: "event" + (open ? "" : " quiet") },
      el("div", { class: "head" }, el("span", { "aria-hidden": "true" }, "☀"), el("span", { class: "label" }, T.brief),
        el("span", { class: "spacer" }), el("span", {}, hm(m.t))),
      rem.length ? el("div", { class: "sub" }, T.briefToday) : null,
      rem.length ? el("div", { class: "fxs" }, rem.map(r => briefChip(r, "⏰", r.at ? hm(r.at) : ""))) : null,
      topics.length ? el("div", { class: "sub" }, T.briefTodo) : null,
      topics.length ? el("p", { class: "topics" }, topics.map(([t, n]) => `${t} ${n}`).join(" · ")) : null,
      stale.length ? el("div", { class: "sub" }, T.briefStale) : null,
      stale.length ? el("div", { class: "fxs" }, stale.map(i => briefChip(i, "☐", i.topic && i.topic !== "general" ? i.topic : ""))) : null,
      open ? null : el("div", { class: "state ok" }, T.nothingLeft));
  }

  function remRow(sc, r) {
    const rel = relOf(r.at), done = !!r.done_at, due = !done && rel.due;
    return el("div", { class: "rem" + (due ? " due" : "") + (done ? " done" : "") + flashCls(r.id), "data-card": r.id },
      el("div", { class: "time" }, hm(done ? r.done_at : r.at), el("small", {}, done ? T.done : due || isToday(r.at) ? rel.text : shortDay(r.at))),
      el("div", {}, el("div", { class: "t" }, r.text), el("div", { class: "m" }, topicTag(r.topic), found(r))),
      done
        ? el("div", { class: "acts-side" }, iconBtn(svg(ICON.archive), T.archive, () => cardAct(sc, r.id, "archive")))
        : el("div", { class: "acts-side" },
            iconBtn(svg(ICON.check), T.markDone, () => cardAct(sc, r.id, "done"), "ok"),
            iconBtn("+10", T.snooze10, () => cardAct(sc, r.id, "snooze", 10)),
            iconBtn("+1h", T.snooze60, () => cardAct(sc, r.id, "snooze", 60))));
  }

  function topicBox(sc, cd) {
    return el("div", { class: "topic" + flashCls(cd.id), "data-card": cd.id },
      el("div", { class: "topic-h" }, el("span", {}, cd.topic), cd.project ? el("span", { class: "tag", title: cd.project }, T.project) : null,
        el("span", { class: "n" }, cd.open ? String(cd.open) : "✓"),
        iconBtn(svg(ICON.archive), T.archiveTopic, () => cardAct(sc, cd.id, "archive"))),
      el("ul", { class: "checks" }, cd.items.map(i => el("li", { class: (i.done_at ? "done" : "") + flashCls(i.id), "data-card": i.id },
        el("label", {},
          el("input", { type: "checkbox", checked: !!i.done_at, on: { change: e => {
            e.target.closest("li").classList.toggle("done", e.target.checked);
            act(api("/api/m/desk/item", { scope: sc.scope, id: i.id, done: e.target.checked }));
          } } }),
          el("span", {}, i.text), found(i))))));
  }

  function dayCounts(p) {
    const open = p.reminders.filter(r => !r.done_at);
    const due = open.filter(r => r.at * 1000 <= Date.now()).length;
    return { open, due, next: open.length - due, items: p.checks.reduce((a, c) => a + c.open, 0) };
  }
  const pendingOf = mod => (mod && mod.pending) || { reminders: [], checks: [] };

  function side(sc, mod) {
    const p = pendingOf(mod), n = dayCounts(p), doneR = p.reminders.filter(r => r.done_at);
    const parts = [n.due ? el("b", {}, T.nDue(n.due)) : null, n.next ? T.nNext(n.next) : null, n.items ? T.nOpen(n.items) : null].filter(Boolean);
    const kids = [
      el("div", { class: "day-h" }, el("h2", {}, T.dayTitle), el("span", { class: "date" }, new Date().toLocaleDateString(LOCALE, { weekday: "long", day: "numeric", month: "short" }))),
      el("p", { class: "summary" }, parts.length ? parts.flatMap((x, i) => i ? [" · ", x] : [x]) : T.allClear)];
    if (!n.open.length && !p.checks.length && !doneR.length)
      kids.push(el("div", { class: "side-empty" }, el("div", { class: "big", "aria-hidden": "true" }, "☀️"), el("strong", {}, T.sideEmpty), T.sideEmptyText(nameOf(sc))));
    if (n.open.length) kids.push(el("div", { class: "sec" }, T.secReminders), ...n.open.map(r => remRow(sc, r)));
    if (p.checks.length) kids.push(el("div", { class: "sec" }, T.secChecks), ...p.checks.map(cd => topicBox(sc, cd)));
    if (doneR.length) kids.push(el("details", { class: "donehist", open: doneOpen, on: { toggle: e => { doneOpen = e.target.open; } } },
      el("summary", {}, T.doneToday(doneR.length)), doneR.map(r => remRow(sc, r))));
    return kids;
  }

  Tanka.module("desk", {
    items: { reminder: reminderEvent, brief: briefEvent },
    chips: chip,
    side: {
      render: side,
      label: (sc, mod) => { const n = dayCounts(pendingOf(mod)); return [T.dayTitle, (n.due || n.next || n.items) ? el("span", { class: "badge" + (n.due ? "" : " soft") }, String(n.due + n.next + n.items)) : null]; },
      // "in 5 min" changes with the minute, so the minute is part of what makes it rebuild
      sig: (sc, mod) => JSON.stringify([pendingOf(mod).reminders, pendingOf(mod).checks, Math.floor(Date.now() / 60000)]),
    },
    attention: (sc, mod) => dayCounts(pendingOf(mod)).due,
    using: T.using,
    suggest: T.suggest,
    hello: () => T.hello,
  });
})();
