export type Job = {
  id: string
  specialist: string
  task: string
  status: 'running' | 'done' | 'error'
  reply: string
}

declare module 'claude-code' {
  interface PluginState {
    delegator: { jobs: Shaped<Job[]> }
  }
}
