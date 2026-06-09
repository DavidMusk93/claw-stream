/**
 * useLogger — Frontend logging and Trace ID management
 *
 * Trace ID rules:
 *   1. Reuse from localStorage if available
 *   2. Generate 12-char random ID on first use
 *   3. Sync when backend response returns x-trace-id
 */

let _traceId = ''

function _getStored(): string {
  if (import.meta.client) {
    return localStorage.getItem('claw_trace_id') || ''
  }
  return ''
}

function _setStored(tid: string) {
  if (import.meta.client) {
    localStorage.setItem('claw_trace_id', tid)
  }
}

function _generate(): string {
  return Math.random().toString(36).slice(2, 8) + Date.now().toString(36).slice(-6)
}

export function getTraceId(): string {
  if (!_traceId) {
    _traceId = _getStored() || _generate()
    _setStored(_traceId)
  }
  return _traceId
}

export function setTraceId(tid: string) {
  _traceId = tid
  _setStored(tid)
}

export function makeLogHeaders(): Record<string, string> {
  return { 'x-trace-id': getTraceId() }
}

/** Read trace_id from response headers and sync */
export function syncTraceIdFromResponse(response: Response | undefined) {
  const tid = response?.headers?.get('x-trace-id')
  if (tid) setTraceId(tid)
}

function _prefix(tag: string): string {
  return `[${getTraceId()}] [${tag}]`
}

export function logInfo(tag: string, msg: string, data?: any) {
  const prefix = _prefix(tag)
  if (data !== undefined) console.log(prefix, msg, data)
  else console.log(prefix, msg)
}

export function logError(tag: string, msg: string, data?: any) {
  const prefix = _prefix(tag)
  if (data !== undefined) console.error(prefix, msg, data)
  else console.error(prefix, msg)
  _report('error', tag, msg, data)
}

export function logWarn(tag: string, msg: string, data?: any) {
  const prefix = _prefix(tag)
  if (data !== undefined) console.warn(prefix, msg, data)
  else console.warn(prefix, msg)
}

/** Report error logs to backend (fire-and-forget, non-blocking) */
async function _report(level: string, tag: string, msg: string, data?: any) {
  try {
    await fetch('/api/log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        trace_id: getTraceId(),
        level,
        tag,
        msg,
        data: data ? String(data) : undefined,
        ts: new Date().toISOString(),
      }),
    })
  } catch {
    // Report failure should not throw
  }
}
