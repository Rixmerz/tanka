export type Job = { id: string; agent: string; status: 'running' | 'done' | 'error' }

declare module 'claude-code' {
  interface PluginState {
    tanka: { jobs: Shaped<Job[]> }
  }
}
