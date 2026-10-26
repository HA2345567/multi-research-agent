import { useCallback, useEffect, useRef, useState } from 'react'

const historyKey = 'fieldnote-cloud-history'
const accessKey = 'fieldnote-access'

function accessHeaders() {
  try {
    const value = sessionStorage.getItem(accessKey)
    return value ? { 'X-Research-Key': value } : {}
  } catch { return {} }
}

async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...accessHeaders(), ...options.headers },
    signal: options.signal ?? AbortSignal.timeout(15000),
  })
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Cannot connect to the research service. Please retry shortly.')
  }
  const body = await response.json()
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not complete the request.')
  return body
}

export function useResearch() {
  const [runs, setRuns] = useState([])
  const [run, setRun] = useState(null)
  const [selectedId, setSelectedId] = useState(null)
  const [health, setHealth] = useState(null)
  const [connectionError, setConnectionError] = useState('')
  const [runError, setRunError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const initialized = useRef(false)
  const mode = useRef('local')
  const cloudHistory = useRef([])
  const selection = useRef(null)

  const saveCloudRun = useCallback(next => {
    cloudHistory.current = [next, ...cloudHistory.current.filter(item => item.id !== next.id)].slice(0, 30)
    try { localStorage.setItem(historyKey, JSON.stringify(cloudHistory.current)) } catch { /* Keep in-memory history when storage is unavailable. */ }
    setRuns([...cloudHistory.current])
  }, [])

  const refresh = useCallback(async () => {
    try {
      const status = await request('/health')
      mode.current = status.mode ?? 'local'
      let history
      if (mode.current === 'cloud') {
        if (!initialized.current) {
          try {
            const saved = JSON.parse(localStorage.getItem(historyKey) ?? '[]')
            cloudHistory.current = Array.isArray(saved) ? saved.filter(item => item?.id && item?.topic).slice(0, 30) : []
          } catch { cloudHistory.current = [] }
          cloudHistory.current = cloudHistory.current.map(item => ['running', 'queued'].includes(item.status)
            ? { ...item, status: 'failed', error: 'This cloud run was interrupted. Please retry with this page open.' } : item)
        }
        history = cloudHistory.current
      } else history = await request('/research')
      setHealth(status)
      setRuns([...history])
      setConnectionError('')
      if (!initialized.current) {
        initialized.current = true
        let savedId
        try { savedId = localStorage.getItem('fieldnote-run') } catch { /* Storage is optional. */ }
        const id = history.find(item => ['running', 'queued'].includes(item.status))?.id ?? history.find(item => item.id === savedId)?.id ?? history[0]?.id ?? null
        selection.current = id
        setSelectedId(id)
      }
    } catch (error) {
      setHealth(null)
      setConnectionError(error.message || 'Unable to reach the research service.')
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    try { if (selectedId) localStorage.setItem('fieldnote-run', selectedId) } catch { /* Storage is optional. */ }
    if (!selectedId) return
    if (mode.current === 'cloud') {
      setRun(cloudHistory.current.find(item => item.id === selectedId) ?? null)
      return
    }
    let disposed = false
    let pending = false
    const controller = new AbortController()
    const timer = setInterval(load, 1500)
    async function load() {
      if (pending) return
      pending = true
      try {
        const next = await request(`/research/${selectedId}`, { signal: controller.signal })
        if (disposed) return
        setRun(next)
        setRunError('')
        if (next.status === 'complete' || next.status === 'failed') { clearInterval(timer); refresh() }
      } catch (error) {
        if (!disposed) setRunError(error.message)
      } finally { pending = false }
    }
    load()
    return () => { disposed = true; clearInterval(timer); controller.abort() }
  }, [selectedId, refresh])

  function select(id) {
    setRunError('')
    if (id !== selectedId) setRun(null)
    selection.current = id
    setSelectedId(id)
  }

  function clear() {
    try { localStorage.removeItem('fieldnote-run') } catch { /* Storage is optional. */ }
    selection.current = null
    setSelectedId(null)
    setRun(null)
    setRunError('')
  }

  async function unlock(value) {
    try {
      const status = await request('/health', { headers: { 'X-Research-Key': value.trim() } })
      if (status.authentication_required) throw new Error('That access code was not recognized.')
      sessionStorage.setItem(accessKey, value.trim())
      setHealth(status)
      setRunError('')
      return true
    } catch (error) { setRunError(error.message); return false }
  }

  async function start(topic) {
    setSubmitting(true)
    setRunError('')
    let lastSnapshot
    try {
      if (mode.current !== 'cloud') {
        const next = await request('/research', { method: 'POST', body: JSON.stringify({ topic: topic.trim() }) })
        selection.current = next.id
        setRun(next)
        setSelectedId(next.id)
        await refresh()
        return
      }
      const response = await fetch('/api/research', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...accessHeaders() },
        body: JSON.stringify({ topic: topic.trim() }),
        signal: AbortSignal.timeout(295000),
      })
      if (!response.ok || !response.headers.get('content-type')?.includes('application/x-ndjson')) {
        const error = await response.json().catch(() => ({}))
        throw new Error(error.detail ?? 'The cloud research service could not start. Please retry.')
      }
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      function applyLine(line) {
        if (!line.trim()) return
        const next = JSON.parse(line)
        if (!lastSnapshot) { selection.current = next.id; setSelectedId(next.id); setSubmitting(false) }
        lastSnapshot = next
        saveCloudRun(next)
        if (selection.current === next.id) setRun(next)
      }
      while (true) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        const lines = buffer.split('\n')
        buffer = lines.pop()
        lines.forEach(applyLine)
        if (done) { applyLine(buffer); break }
      }
      if (!lastSnapshot || !['complete', 'failed'].includes(lastSnapshot.status)) {
        throw new Error('The cloud connection ended before the run finished. Your partial output was saved; please retry.')
      }
    } catch (error) {
      const message = error.name === 'TimeoutError' ? 'This run reached the five-minute cloud limit. Try a narrower topic.' : error.message
      setRunError(message || 'Could not start research.')
      if (lastSnapshot) {
        const failed = { ...lastSnapshot, status: 'failed', error: message }
        saveCloudRun(failed)
        if (selection.current === failed.id) setRun(failed)
      }
    } finally { setSubmitting(false) }
  }

  return { runs, run, selectedId, health, connectionError, runError, submitting, refresh, select, clear, start, unlock }
}
