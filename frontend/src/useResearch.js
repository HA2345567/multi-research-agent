import { useCallback, useEffect, useRef, useState } from 'react'

async function request(path, options = {}) {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
    signal: options.signal ?? AbortSignal.timeout(15000),
  })
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Cannot connect to the project. Run bun run dev in the frontend folder.')
  }
  const body = await response.json()
  if (!response.ok) {
    throw new Error(typeof body.detail === 'string' ? body.detail : 'Could not complete the request. Please try again.')
  }
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

  const refresh = useCallback(async () => {
    try {
      const [status, history] = await Promise.all([request('/health'), request('/research')])
      setHealth(status)
      setRuns(history)
      setConnectionError('')
      if (!initialized.current) {
        initialized.current = true
        let savedId
        try { savedId = localStorage.getItem('fieldnote-run') } catch { /* Storage is optional. */ }
        setSelectedId(history.find(item => item.status === 'running' || item.status === 'queued')?.id ?? history.find(item => item.id === savedId)?.id ?? history[0]?.id ?? null)
      }
    } catch (error) {
      setHealth(null)
      setConnectionError(error.message || 'Unable to reach the research backend.')
    }
  }, [])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  useEffect(() => {
    try {
      if (selectedId) localStorage.setItem('fieldnote-run', selectedId)
    } catch { /* Research remains saved on the backend. */ }
    if (!selectedId) return
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
        if (next.status === 'complete' || next.status === 'failed') {
          clearInterval(timer)
          refresh()
        }
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
    setSelectedId(id)
  }

  function clear() {
    try { localStorage.removeItem('fieldnote-run') } catch { /* Storage is optional. */ }
    setSelectedId(null)
    setRun(null)
    setRunError('')
  }

  async function start(topic) {
    setSubmitting(true)
    setRunError('')
    try {
      const next = await request('/research', { method: 'POST', body: JSON.stringify({ topic: topic.trim() }) })
      setRun(next)
      setSelectedId(next.id)
      await refresh()
    } catch (error) {
      setRunError(error.message || 'Could not start research.')
      await refresh()
    } finally { setSubmitting(false) }
  }

  return { runs, run, selectedId, health, connectionError, runError, submitting, refresh, select, clear, start }
}
