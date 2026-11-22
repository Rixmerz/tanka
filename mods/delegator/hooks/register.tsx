import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

import type { Job } from '../types'

// Specialist workspaces: each subfolder holds its own CLAUDE.md and .claude/.
const ROOT = '/home/user/tanka/specialists'
const TOOL = 'mcp__delegator__delegate'

const jobs = atom({ plugin: 'delegator', key: 'jobs' } as const, [], {
  shape: 'jobs-1',
})

async function specialists($: any): Promise<string[]> {
  try {
    const entries = await $.fs.list(ROOT)
    return entries.filter((x: any) => x.kind === 'dir').map((x: any) => x.name)
  } catch {
    return []
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const names = await specialists($)
    await $.tool.register({
      name: 'delegate',
      isDeferred: false,
      description:
        'Hand a task to a specialist. It runs on its own, in its own workspace, and ' +
        'shows its reply directly to the user in the chat. You get only a receipt: ' +
        'do not wait for, ask about or summarise the reply. Specialists: ' +
        (names.join(', ') || '(none yet)') + '.',
      inputSchema: {
        type: 'object',
        properties: {
          specialist: { type: 'string', enum: names.length ? names : undefined },
          task: { type: 'string', description: 'The full task, self-contained.' },
        },
        required: ['specialist', 'task'],
      },
    })
    return next(e)
  })

  on('tool.run', { tool: TOOL }, async ($, e) => {
    const input = e as unknown as { specialist: string; task: string }
    const names = await specialists($)
    if (!names.includes(input.specialist)) {
      return { deny: `Unknown specialist "${input.specialist}". Known: ${names.join(', ')}` }
    }
    const id = `${input.specialist}-${Date.now().toString(36)}`
    const job: Job = { id, specialist: input.specialist, task: input.task, status: 'running', reply: '' }
    await update($, jobs, list => [...list, job].slice(-50))
    await $.ui.place({ id })

    const cwd = `${ROOT}/${input.specialist}`
    void (async () => {
      let out = ''
      let err = ''
      let code: number | null = null
      try {
        const child = $.process.spawn({ argv: ['claude', '-p', input.task, '--output-format', 'text'], cwd })
        for await (const piece of child) {
          if (piece.stream === 'stdout') out += piece.text
          else err += piece.text
        }
        code = 0
      } catch (x) {
        err += String(x)
        code = 1
      }
      const ok = code === 0 && out.trim().length > 0
      const reply = ok ? out.trim() : (err.trim() || 'No output.')
      await update($, jobs, list =>
        list.map(j => (j.id === id ? { ...j, status: ok ? 'done' : 'error', reply } : j)),
      )
      try {
        await $.fs.write(`${cwd}/replies/${id}.md`, `# ${input.task}\n\n${reply}\n`)
      } catch {}
      $.ui.toast(`${input.specialist}: ${ok ? 'listo' : 'falló'}`)
    })()

    return { result: `Delegated to ${input.specialist} (id ${id}). The reply goes to the user directly; you will not see it.` }
  })

  on('ui.render', { component: 'Placed' }, async ($, e, next) => {
    const p = e.props as { id: string; plugin: string }
    if (p.plugin !== 'delegator') return next(e)
    const job = (await read($, jobs)).find(j => j.id === p.id)
    if (!job) return next(e)
    const { Box, Text, Markdown } = $.ui.resolve(e)
    return (
      <Box flexDirection="column" borderStyle="round" paddingX={1}>
        <Text bold>
          {job.specialist} · {job.status === 'running' ? 'trabajando…' : job.status === 'done' ? 'respuesta' : 'error'}
        </Text>
        {job.status === 'running' ? <Text dimColor>{job.task.slice(0, 200)}</Text> : <Markdown text={job.reply} />}
      </Box>
    )
  })
}
