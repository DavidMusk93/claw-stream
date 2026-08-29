/**
 * useEventLog — Session event log for the event panel
 *
 * Records user operations and their outcomes (play, like, sync, errors)
 * in a module-level ring buffer, newest first. Consumed by EventPanel.
 */

export interface EventLogEntry {
  ts: number
  kind: 'action' | 'sync' | 'error' | 'info'
  title: string
  detail?: string
  state?: 'success' | 'error' | 'running' | 'info'
}

const MAX_ENTRIES = 100
const entries = ref<EventLogEntry[]>([])

export function useEventLog() {
  function add(entry: Omit<EventLogEntry, 'ts'>) {
    entries.value.unshift({ ts: Date.now(), ...entry })
    if (entries.value.length > MAX_ENTRIES) {
      entries.value.length = MAX_ENTRIES
    }
  }

  return { entries, add }
}
