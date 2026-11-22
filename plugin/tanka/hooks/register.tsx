import { atom, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Job } from '../types'

// The repository root: the agents live in <ROOT>/agents.
const ROOT = '/home/user/tanka'
const AGENTS = `${ROOT}/agents`
const TEMPLATE = `${ROOT}/templates/agent`
const MODELS = ['haiku', 'sonnet', 'opus', 'fable']
const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
const NAME = /^[a-z][a-z0-9-]{1,31}$/

type Agent = { name: string; description: string; category: string; model: string; effort: string }

const jobs = atom({ plugin: 'tanka', key: 'jobs' } as const, [], { shape: 'jobs-1' })

const run = ($: any, argv: string[], cwd = ROOT) => $.process.run(argv, { cwd, timeoutMs: 60_000 })

async function agents($: any): Promise<Agent[]> {
  const out: Agent[] = []
  let entries: any[] = []
  try { entries = await $.fs.list(AGENTS) } catch { return out }
  for (const e of entries) {
    if (e.kind !== 'dir' || e.name.startsWith('_')) continue
    try { out.push(JSON.parse(await $.fs.read(`${AGENTS}/${e.name}/agent.json`))) } catch {}
  }
  return out.sort((a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name))
}

const registry = (list: Agent[]) =>
  list.length
    ? list.map(a => `- ${a.name} [${a.category}] ${a.model}/${a.effort}: ${a.description}`).join('\n')
    : '(no agents yet: create one with create_agent)'

// Commit (and push) changes under one agent's folder: memory and config live in git.
async function save($: any, path: string, message: string) {
  await run($, ['git', 'add', '-A', path])
  const staged = await run($, ['git', 'diff', '--cached', '--quiet'])
  if (staged.exitCode === 0) return
  await run($, ['git', 'commit', '-q', '-m', message])
  const branch = (await run($, ['git', 'rev-parse', '--abbrev-ref', 'HEAD'])).stdout.trim()
  for (let i = 0; i < 3; i++) {
    if ((await run($, ['git', 'push', '-q', 'origin', branch])).exitCode === 0) return
    await run($, ['git', 'pull', '-q', '--no-rebase', '--no-edit', 'origin', branch])
  }
}

async function registerTools($: any) {
  const list = await agents($)
  const names = list.map(a => a.name)
  await $.tool.register({
    name: 'delegate',
    isDeferred: false,
    description:
      'Run a task on an agent, with its own model, effort, tools and memory. You get only a receipt; ' +
      'answer only "✓". When "📎 <agent> · <id>" arrives later, send the user the report file ' +
      `${AGENTS}/<agent>/reports/<id>.md with SendUserFile (status "proactive", display "render") ` +
      'without reading it, then answer only "✓".\n\nAgents:\n' + registry(list),
    inputSchema: {
      type: 'object',
      properties: {
        agent: names.length ? { type: 'string', enum: names } : { type: 'string' },
        task: { type: 'string', description: 'The full task, self-contained, in the user\'s words where possible.' },
      },
      required: ['agent', 'task'],
    },
  })
  const fields = {
    description: { type: 'string', description: 'One line: what the agent is for (the delegator routes on it).' },
    category: { type: 'string', description: 'A group, e.g. research, code, comms, ops.' },
    model: { type: 'string', enum: MODELS },
    effort: { type: 'string', enum: EFFORTS },
    instructions: { type: 'string', description: 'The agent\'s role and rules, in English, for its CLAUDE.md.' },
  }
  await $.tool.register({
    name: 'create_agent',
    isDeferred: false,
    description: 'Create an agent from the template: its folder, CLAUDE.md, settings, MCP file and memory. Confirm the fields with the user first.',
    inputSchema: { type: 'object', properties: { name: { type: 'string', description: 'lowercase, letters, digits, dashes' }, ...fields }, required: ['name', 'description', 'category', 'model', 'effort', 'instructions'] },
  })
  await $.tool.register({
    name: 'edit_agent',
    description: 'Change an agent\'s description, category, model, effort or instructions (replaces the Instructions section).',
    inputSchema: { type: 'object', properties: { name: { type: 'string', enum: names.length ? names : undefined }, ...fields }, required: ['name'] },
  })
  await $.tool.register({
    name: 'list_agents',
    description: 'The registry of agents: name, category, model/effort and description.',
    inputSchema: { type: 'object', properties: {} },
  })
  await $.tool.register({
    name: 'archive_agent',
    description: 'Move an agent to agents/_archived/. Confirm with the user first.',
    inputSchema: { type: 'object', properties: { name: { type: 'string', enum: names.length ? names : undefined } }, required: ['name'] },
  })
}

function fill(text: string, a: Agent & { instructions: string }) {
  return text.replaceAll('{{NAME}}', a.name).replaceAll('{{DESCRIPTION}}', a.description).replaceAll('{{INSTRUCTIONS}}', a.instructions)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await registerTools($)
    return next(e)
  })

  on('tool.run', { tool: 'mcp__tanka__list_agents' }, async $ => ({ result: registry(await agents($)) }))

  on('tool.run', { tool: 'mcp__tanka__create_agent' }, async ($, e) => {
    const a = e as unknown as Agent & { instructions: string }
    if (!NAME.test(a.name)) return { deny: 'Name: lowercase letters, digits and dashes, 2-32 characters, starting with a letter.' }
    if (!MODELS.includes(a.model) || !EFFORTS.includes(a.effort)) return { deny: `model: ${MODELS.join('|')}; effort: ${EFFORTS.join('|')}` }
    const dir = `${AGENTS}/${a.name}`
    if ((await run($, ['test', '-e', dir])).exitCode === 0) return { deny: `Agent ${a.name} already exists.` }
    const cp = await run($, ['cp', '-r', TEMPLATE, dir])
    if (cp.exitCode !== 0) return { deny: cp.stderr }
    await $.fs.write(`${dir}/CLAUDE.md`, fill(await $.fs.read(`${dir}/CLAUDE.md`), a))
    const meta: Agent = { name: a.name, description: a.description, category: a.category, model: a.model, effort: a.effort }
    await $.fs.write(`${dir}/agent.json`, JSON.stringify({ ...meta, created: new Date().toISOString() }, null, 2) + '\n')
    await save($, dir, `Create agent ${a.name}`)
    await registerTools($)
    return { result: `Created ${a.name} (${a.category}, ${a.model}/${a.effort}).` }
  })

  on('tool.run', { tool: 'mcp__tanka__edit_agent' }, async ($, e) => {
    const a = e as unknown as Partial<Agent & { instructions: string }> & { name: string }
    const dir = `${AGENTS}/${a.name}`
    let meta: any
    try { meta = JSON.parse(await $.fs.read(`${dir}/agent.json`)) } catch { return { deny: `No agent ${a.name}.` } }
    if (a.model && !MODELS.includes(a.model)) return { deny: `model: ${MODELS.join('|')}` }
    if (a.effort && !EFFORTS.includes(a.effort)) return { deny: `effort: ${EFFORTS.join('|')}` }
    for (const k of ['description', 'category', 'model', 'effort'] as const) if (a[k]) meta[k] = a[k]
    await $.fs.write(`${dir}/agent.json`, JSON.stringify(meta, null, 2) + '\n')
    let md: string = await $.fs.read(`${dir}/CLAUDE.md`)
    if (a.description) md = md.replace(/^(# Agent: .*\n\n)(.*)\n/, `$1${a.description}\n`)
    if (a.instructions) md = md.replace(/(## Instructions\n\n)[\s\S]*?(\n## How you work)/, `$1${a.instructions}\n$2`)
    await $.fs.write(`${dir}/CLAUDE.md`, md)
    await save($, dir, `Edit agent ${a.name}`)
    await registerTools($)
    return { result: `Updated ${a.name}.` }
  })

  on('tool.run', { tool: 'mcp__tanka__archive_agent' }, async ($, e) => {
    const { name } = e as unknown as { name: string }
    await run($, ['mkdir', '-p', `${AGENTS}/_archived`])
    const mv = await run($, ['git', 'mv', `agents/${name}`, `agents/_archived/${name}`])
    if (mv.exitCode !== 0) return { deny: mv.stderr }
    await save($, AGENTS, `Archive agent ${name}`)
    await registerTools($)
    return { result: `Archived ${name}.` }
  })

  on('tool.run', { tool: 'mcp__tanka__delegate' }, async ($, e) => {
    const { agent, task } = e as unknown as { agent: string; task: string }
    const meta = (await agents($)).find(a => a.name === agent)
    if (!meta) return { deny: `No agent ${agent}. Agents: ${(await agents($)).map(a => a.name).join(', ')}` }
    const id = `${agent}-${Date.now().toString(36)}`
    const cwd = `${AGENTS}/${agent}`
    await run($, ['mkdir', '-p', `${cwd}/reports`])
    await update($, jobs, list => [...list, { id, agent, status: 'running' as const }].slice(-50))

    void (async () => {
      let out = ''
      let err = ''
      let code: number | null = null
      try {
        const child = $.process.spawn({
          argv: ['claude', '-p', task, '--model', meta.model, '--effort', meta.effort, '--output-format', 'text'],
          cwd,
        })
        for await (const piece of child) {
          if ((piece as any).stream === 'stdout') out += (piece as any).text
          else if ((piece as any).stream === 'stderr') err += (piece as any).text
          else if ('code' in (piece as any)) code = (piece as any).code
        }
      } catch (x) {
        err += String(x)
      }
      const ok = out.trim().length > 0 && (code === null || code === 0)
      const body = ok ? out.trim() : `**Error** (exit ${code})\n\n${err.trim() || 'No output.'}`
      await $.fs.write(`${cwd}/reports/${id}.md`, `# ${agent}\n\n> ${task.replaceAll('\n', ' ').slice(0, 300)}\n\n${body}\n`)
      await update($, jobs, list => list.map(j => (j.id === id ? { ...j, status: ok ? 'done' as const : 'error' as const } : j)))
      try { await save($, `agents/${agent}/memory`, `${agent}: update memory`) } catch {}
      try {
        await $.prompt.submit({ text: `📎 ${agent} · ${id}` } as any)
      } catch (x) {
        $.ui.log(`tanka: submit failed: ${String(x)}`, { to: 'debug' })
      }
    })()

    return { result: `Delegated to ${agent} (id ${id}). Answer only "✓".` }
  })
}
