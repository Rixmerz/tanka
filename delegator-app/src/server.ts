// Delegator as an MCP App: one model call starts a specialist; the card that
// call draws in the chat polls for the reply and shows it. The model only ever
// reads a receipt.
import { registerAppResource, registerAppTool, RESOURCE_MIME_TYPE } from "@modelcontextprotocol/ext-apps/server";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { z } from "zod";

const ROOT = process.env.SPECIALISTS_DIR ?? "/home/user/tanka/specialists";
const HERE = path.dirname(new URL(import.meta.url).pathname);
const UI = "ui://delegator/reply.html";

const specialists = () =>
  fs.readdirSync(ROOT, { withFileTypes: true }).filter(d => d.isDirectory()).map(d => d.name);
const replyPath = (s: string, id: string) => path.join(ROOT, s, "replies", `${id}.md`);
const statePath = (s: string, id: string) => path.join(ROOT, s, "replies", `${id}.json`);

const server = new McpServer({ name: "delegator", version: "0.1.0" });
const names = specialists();

registerAppTool(server, "delegate", {
  title: "Delegar",
  description:
    "Hand a task to a specialist. It runs in its own workspace and its reply is shown " +
    "to the user in this call's card. You get only a receipt: do not wait for, fetch or " +
    "summarise the reply; after calling, answer only \"✓\". Specialists: " + names.join(", ") + ".",
  inputSchema: {
    specialist: z.enum(names as [string, ...string[]]),
    task: z.string().describe("The full task, self-contained."),
  },
  _meta: { ui: { resourceUri: UI } },
}, async ({ specialist, task }) => {
  const id = `${specialist}-${Date.now().toString(36)}`;
  const cwd = path.join(ROOT, specialist);
  fs.mkdirSync(path.join(cwd, "replies"), { recursive: true });
  const st = statePath(specialist, id);
  fs.writeFileSync(st, JSON.stringify({ status: "running", task }));
  let out = "", err = "";
  const child = spawn("claude", ["-p", task, "--output-format", "text"], { cwd, stdio: ["ignore", "pipe", "pipe"] });
  child.stdout.on("data", b => (out += b));
  child.stderr.on("data", b => (err += b));
  child.on("close", code => {
    const ok = code === 0 && out.trim().length > 0;
    const reply = ok ? out.trim() : (err.trim() || `exit ${code}`);
    fs.writeFileSync(replyPath(specialist, id), reply + "\n");
    fs.writeFileSync(st, JSON.stringify({ status: ok ? "done" : "error", task }));
  });
  return {
    content: [{ type: "text", text: `Delegated to ${specialist} (id ${id}). Shown to the user; answer only "✓".` }],
    structuredContent: { id, specialist },
  };
});

registerAppTool(server, "get_reply", {
  description: "Status and text of a delegated job (for the card only).",
  inputSchema: { specialist: z.string(), id: z.string() },
  _meta: { ui: { resourceUri: UI, visibility: ["app"] } },
}, async ({ specialist, id }) => {
  let state = { status: "unknown", task: "" };
  try { state = JSON.parse(fs.readFileSync(statePath(specialist, id), "utf8")); } catch {}
  let reply = "";
  if (state.status !== "running") { try { reply = fs.readFileSync(replyPath(specialist, id), "utf8"); } catch {} }
  return { content: [{ type: "text", text: state.status }], structuredContent: { ...state, reply } };
});

registerAppResource(server, UI, UI, { mimeType: RESOURCE_MIME_TYPE }, async () => ({
  contents: [{ uri: UI, mimeType: RESOURCE_MIME_TYPE, text: fs.readFileSync(path.join(HERE, "reply.html"), "utf8") }],
}));

await server.connect(new StdioServerTransport());
