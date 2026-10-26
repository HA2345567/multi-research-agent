import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowDownToLine, ArrowRight, ArrowUpRight, BookOpen, Check,
  ChevronDown, Clock3, FileText, Globe2, Layers3, LoaderCircle,
  MessageSquareText, Plus, X,
} from 'lucide-react'
import { useResearch } from './useResearch'

const stages = [
  { id: 'search', label: 'Search', icon: Globe2, field: 'search_results' },
  { id: 'reader', label: 'Read', icon: BookOpen, field: 'scraped_content' },
  { id: 'writer', label: 'Write', icon: FileText, field: 'report' },
  { id: 'critic', label: 'Review', icon: MessageSquareText, field: 'feedback' },
]
const inProgress = status => status === 'running' || status === 'queued'
const dateLabel = value => new Date(value).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
const markdownComponents = {
  a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>,
}

export default function App() {
  const { runs, run, selectedId, health, connectionError, runError, submitting, refresh, select, clear, start } = useResearch()
  const [topic, setTopic] = useState('')
  const [tab, setTab] = useState('report')
  const input = useRef(null)
  const history = useRef(null)
  const activeRun = runs.find(item => inProgress(item.status))
  const running = inProgress(run?.status)
  const ready = health?.status === 'ready'
  const sources = run?.sources ?? []
  const needsReview = run?.status === 'complete' && (sources.length === 0 || (run.score != null && run.score < 5))
  const error = connectionError || runError || run?.error || (health?.missing?.length ? `Add ${health.missing.join(' and ')} to your project .env and restart the app.` : '')
  const status = !run ? selectedId ? 'Opening research…' : 'Ready when you are' : running ? 'Research in progress' : run.status === 'failed' ? 'Run interrupted' : needsReview ? 'Needs a closer look' : 'Research finished'

  useEffect(() => {
    const onKey = event => {
      if ((event.ctrlKey || event.metaKey) && event.key === 'k') {
        event.preventDefault()
        if (history.current) history.current.open = false
        input.current?.focus()
      }
      if (event.key === 'Escape' && history.current?.open) {
        history.current.open = false
        history.current.querySelector('summary')?.focus()
      }
    }
    const onPointer = event => {
      if (history.current && !history.current.contains(event.target)) history.current.open = false
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer)
    }
  }, [])

  function newResearch() {
    clear()
    setTopic('')
    setTab('report')
    if (history.current) history.current.open = false
    input.current?.focus()
  }

  function openRun(item) {
    select(item.id)
    setTopic(item.topic)
    setTab('report')
    if (history.current) history.current.open = false
  }

  function submit(event) {
    event.preventDefault()
    if (topic.trim().length < 3 || activeRun || running || submitting || !ready) return
    setTab('report')
    start(topic)
  }

  function navigateTabs(event) {
    const tabs = ['report', 'review', 'sources']
    const index = tabs.indexOf(tab)
    const next = event.key === 'ArrowRight' ? (index + 1) % 3
      : event.key === 'ArrowLeft' ? (index + 2) % 3
      : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : null
    if (next == null) return
    event.preventDefault()
    setTab(tabs[next])
    document.getElementById(`tab-${tabs[next]}`)?.focus()
  }

  function download() {
    if (!run?.report) return
    const content = `# ${run.topic}\n\n${run.report}\n\n---\n\n## Critic review\n\n${run.feedback || 'Review pending.'}`
    const url = URL.createObjectURL(new Blob([content], { type: 'text/markdown;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${run.topic.replace(/[^a-z0-9]+/gi, '-').slice(0, 70)}.md`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <div className="app">
    <header className="header">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); newResearch() }} aria-label="Fieldnote home">
        <span className="brand-symbol"><Layers3 size={18} /></span>
        fieldnote<span className="brand-dot">.</span>
      </a>
      <nav className="header-actions" aria-label="Research navigation">
        <details className="history" ref={history}>
          <summary className="button button-quiet"><Clock3 size={15} /><span>History</span><ChevronDown size={12} /></summary>
          <div className="history-menu">
            <p className="menu-label">YOUR RESEARCH</p>
            {runs.length ? runs.map(item => <button className={`history-item ${item.id === selectedId ? 'is-current' : ''}`} key={item.id} onClick={() => openRun(item)}>
              <FileText size={15} />
              <span><strong>{item.topic}</strong><small>{dateLabel(item.created_at)} · {inProgress(item.status) ? 'In progress' : item.status === 'failed' ? 'Interrupted' : 'Finished'}</small></span>
              {item.id === selectedId && <Check size={13} />}
            </button>) : <p className="history-empty">Your saved research will appear here.</p>}
          </div>
        </details>
        <button className="button button-outline" aria-label="New research" onClick={newResearch}><Plus size={15} /><span>New research</span></button>
      </nav>
    </header>

    <main className="workspace">
      <section className="intro">
        <p className="eyebrow"><span /> A SPACE FOR YOUR CURIOSITY</p>
        <h1>A clearer view starts<br />with a <span>question.</span></h1>
        <p>Explore an idea. Follow the evidence. See what comes together.</p>
      </section>

      {error && <div className="notice notice-error" role="alert">
        <span>{error}</span>
        {run?.status === 'failed' && ready && !connectionError
          ? <button disabled={Boolean(activeRun) || submitting} onClick={() => { setTopic(run.topic); start(run.topic) }}>Try again <ArrowRight size={13} /></button>
          : <button onClick={refresh}>Reconnect <ArrowRight size={13} /></button>}
      </div>}
      {activeRun && activeRun.id !== selectedId && <button className="notice active-notice" onClick={() => openRun(activeRun)}>
        <LoaderCircle size={15} className="spin" /><span>A research run is in progress. Open it to follow along.</span><ArrowRight size={14} />
      </button>}

      <form className="composer" onSubmit={submit}>
        <label htmlFor="topic" className="sr-only">Research topic</label>
        <textarea ref={input} id="topic" rows={2} maxLength={1000} value={topic} onChange={event => setTopic(event.target.value)} placeholder="What would you like to understand?" />
        <div className="composer-footer">
          <span><Globe2 size={14} /> Research, with a wider perspective</span>
          <button className="button button-primary" disabled={topic.trim().length < 3 || Boolean(activeRun) || running || submitting || !ready}>
            {submitting ? 'Starting…' : activeRun || running ? 'Researching…' : 'Start research'}
            {submitting || activeRun || running ? <LoaderCircle size={15} className="spin" /> : <ArrowRight size={15} />}
          </button>
        </div>
      </form>
      <div className="composer-note"><span className={`connection-dot ${ready ? 'is-connected' : ''}`} /><span title={health?.model}>{ready ? 'Connected to your research pipeline' : connectionError ? 'Connection unavailable' : health ? 'Configuration needed' : 'Connecting…'}</span><span className="saved-note">Saved on this computer</span></div>

      <section className="research" aria-label="Research results">
        <div className="section-heading"><h2>The research process</h2><span className={`run-state ${running ? 'is-running' : ''} ${needsReview ? 'needs-review' : ''}`} aria-live="polite">{running && <span className="pulse-dot" />}{status}</span></div>
        <ol className="pipeline">
          {stages.map((stage, index) => {
            const state = run?.steps?.[stage.id]?.status ?? 'waiting'
            const Icon = state === 'complete' ? Check : state === 'failed' ? X : state === 'running' ? LoaderCircle : stage.icon
            return <li className={`stage stage-${state}`} key={stage.id}>
              <span className="stage-icon"><Icon size={17} className={state === 'running' ? 'spin' : ''} /></span>
              <span className="stage-text"><strong>{stage.label}</strong><small>{state === 'waiting' ? run ? 'Waiting' : `0${index + 1}` : state === 'complete' ? 'Finished' : state === 'failed' ? 'Interrupted' : 'In progress'}</small></span>
              {index < stages.length - 1 && <ArrowRight size={13} className="stage-arrow" />}
            </li>
          })}
        </ol>

        {selectedId && !run && <div className="loading-state" role="status"><LoaderCircle size={18} className="spin" /> Opening your research…</div>}
        {!selectedId && <div className="empty-state"><span className="empty-icon"><FileText size={21} strokeWidth={1.4} /></span><h3>A little curiosity goes a long way.</h3><p>Your report, source links, and review will appear here.</p></div>}

        {run && <article className="result">
          <div className="result-heading"><div><p className="result-meta">{dateLabel(run.created_at)}{run.word_count ? ` · ${run.word_count.toLocaleString()} words` : ''}</p><h2>{run.topic}</h2></div><button className="button button-quiet export-button" onClick={download} disabled={!run.report} aria-label="Export report"><ArrowDownToLine size={15} /><span>Export</span></button></div>
          <div className="result-toolbar"><div className="tabs" role="tablist" aria-label="Research output" onKeyDown={navigateTabs}>
            {[['report', 'Report'], ['review', 'Review'], ['sources', 'Sources']].map(([id, label]) => <button type="button" role="tab" id={`tab-${id}`} aria-controls="result-content" aria-selected={tab === id} tabIndex={tab === id ? 0 : -1} className={tab === id ? 'is-active' : ''} key={id} onClick={() => setTab(id)}>{label}{id === 'sources' && <span className="tab-count">{sources.length}</span>}</button>)}
          </div>{run.score != null && <span className={`quality-score ${needsReview ? 'needs-review' : ''}`}>Critic score <strong>{run.score}<small>/10</small></strong></span>}</div>
          <div className="result-content" id="result-content" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0}>
            {tab === 'sources' ? <div className="sources">{sources.length ? sources.map(source => <a key={source.url} href={source.url} target="_blank" rel="noopener noreferrer"><Globe2 size={17} /><span><strong>{source.title}</strong><small>{source.url}</small>{source.snippet && <p>{source.snippet}</p>}</span><ArrowUpRight size={15} /></a>) : <p className="output-placeholder">{running ? 'Source links will appear when the search returns them.' : 'No source links were returned in this run.'}</p>}</div>
              : <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{(tab === 'report' ? run.report : run.feedback) || (running ? tab === 'report' ? 'Your research is in progress. The report will appear when the writer finishes.' : 'The critic will review your report after the writer finishes.' : 'No output was returned for this stage.')}</Markdown></div>}
          </div>
          <details className="agent-output"><summary><span>Agent output</span><ChevronDown size={14} /></summary><div>{stages.slice(0, 2).map(stage => <section key={stage.id}><h3>{stage.label === 'Read' ? 'Source reader' : 'Web search'}</h3><div className="markdown"><Markdown remarkPlugins={[remarkGfm]} components={markdownComponents}>{run[stage.field] || 'No output yet.'}</Markdown></div></section>)}</div></details>
        </article>}
      </section>
      <footer className="footer"><span>fieldnote.</span><p>Room for questions. Space for discovery.</p></footer>
    </main>
  </div>
}
