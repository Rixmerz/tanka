import { App, applyDocumentTheme, applyHostStyleVariables, type McpUiHostContext } from "@modelcontextprotocol/ext-apps";
import { marked } from "marked";

const ICONS: Record<string, string> = { investigador: "🔎", codigo: "💻" };
const $ = (id: string) => document.getElementById(id)!;
const app = new App({ name: "delegator", version: "0.1.0" });

function theme(ctx: McpUiHostContext) {
  if (ctx.theme) applyDocumentTheme(ctx.theme);
  if (ctx.styles?.variables) applyHostStyleVariables(ctx.styles.variables);
}
app.onhostcontextchanged = theme;
app.onerror = console.error;

let started = false;
app.ontoolinput = p => {
  const a = p.arguments as { specialist?: string; task?: string };
  if (a.specialist) $("who").textContent = `${ICONS[a.specialist] ?? "🤖"} ${a.specialist}`;
  if (a.task) $("task").textContent = a.task;
};
app.ontoolresult = r => {
  const s = r.structuredContent as { id?: string; specialist?: string } | undefined;
  if (!s?.id || !s.specialist || started) return;
  started = true;
  poll(s.specialist, s.id);
};

async function poll(specialist: string, id: string) {
  $("id").textContent = id;
  for (;;) {
    try {
      const r = await app.callServerTool({ name: "get_reply", arguments: { specialist, id } });
      const s = r.structuredContent as { status: string; reply: string };
      if (s.status === "done" || s.status === "error") {
        $("state").textContent = s.status === "done" ? "listo" : "error";
        $("state").className = `pill ${s.status}`;
        $("body").innerHTML = await marked.parse(s.reply);
        return;
      }
    } catch (e) { console.error(e); }
    await new Promise(res => setTimeout(res, 3000));
  }
}

app.connect().then(() => { const c = app.getHostContext(); if (c) theme(c); });
