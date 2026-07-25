import { useEffect, useMemo, useRef, useState } from 'react'

import { api, openEvents } from '../api'
import { Graph } from '../components/Graph'
import { href } from '../route'
import type { NodeStatus, PipelineGraph, RunEvent, RunState } from '../types'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])

/** A run: node statuses, the live event feed, artifacts, and the human's answer. */
export function Run({ project, runId }: { project: string; runId: string }) {
  const [state, setState] = useState<RunState | null>(null)
  const [graph, setGraph] = useState<PipelineGraph | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [answer, setAnswer] = useState('')
  const [error, setError] = useState<string | null>(null)
  const seqRef = useRef(0)

  // The ledger is the source of truth (I7): the socket tells us WHEN to re-read it.
  useEffect(() => {
    let alive = true
    const refresh = () =>
      api
        .run(runId)
        .then((s) => {
          if (alive) setState(s)
        })
        .catch((e) => alive && setError(String(e)))
    void refresh()
    const ws = openEvents(runId, seqRef.current + 1, (raw) => {
      const event = raw as RunEvent
      seqRef.current = Math.max(seqRef.current, event.seq)
      setEvents((prev) => [...prev.slice(-400), event])
      if (event.type !== 'heartbeat') void refresh()
    })
    const poll = window.setInterval(refresh, 5000) // WS closes on terminal states
    return () => {
      alive = false
      ws.close()
      window.clearInterval(poll)
    }
  }, [runId])

  useEffect(() => {
    if (!state) return
    void api
      .pipelineGraph(project, state.pipeline)
      .then(setGraph)
      .catch(() => setGraph(null))
  }, [project, state?.pipeline])

  const statuses = useMemo<Record<string, NodeStatus>>(() => {
    const out: Record<string, NodeStatus> = {}
    for (const [id, node] of Object.entries(state?.nodes ?? {})) {
      out[id] = node.status
    }
    return out
  }, [state])

  const waiting = Object.entries(state?.steps ?? {}).find(
    ([, s]) => s.status === 'waiting_human',
  )
  const checkpoint = state?.awaiting_checkpoint ?? null
  const done = state ? TERMINAL.has(state.status) : false

  // A checkpoint parks the run AFTER a node finished (SPEC §21): review its output —
  // editing it on disk is allowed — then continue, and the rest of the graph runs.
  async function decideCheckpoint(node: string, decision: string) {
    try {
      await api.answer(runId, node, decision)
      await api.resumeRun(runId)
    } catch (e) {
      setError(String(e))
    }
  }

  return (
    <section>
      <header className="screen-head">
        <div>
          <a href={href({ screen: 'project', project })}>← {project}</a>
          <h1>
            <code>{runId}</code>
          </h1>
        </div>
        <div className="row">
          <span className={`status is-${state?.status ?? 'pending'}`}>
            {state?.status ?? '…'}
          </span>
          {state && !done ? (
            <button
              type="button"
              className="button"
              onClick={() => void api.cancelRun(runId).catch((e) => setError(String(e)))}
            >
              Cancel
            </button>
          ) : null}
          {state?.status === 'failed' || state?.status === 'paused' ? (
            <button
              type="button"
              className="button"
              onClick={() => void api.resumeRun(runId).catch((e) => setError(String(e)))}
            >
              Resume
            </button>
          ) : null}
        </div>
      </header>

      {error ? <pre className="error">{error}</pre> : null}

      {checkpoint ? (
        <div className="panel warn">
          <h3>Checkpoint — review {checkpoint} before continuing</h3>
          <p className="muted">
            The run stopped here on purpose. Read the output below; you may edit the
            files in place, and the rest of the pipeline will read your version.
          </p>
          <Artifacts runId={runId} stepId={checkpoint} />
          <div className="row">
            <button
              type="button"
              className="button primary"
              onClick={() => void decideCheckpoint(checkpoint, 'continue')}
            >
              Continue
            </button>
            <button
              type="button"
              className="button"
              onClick={() => void decideCheckpoint(checkpoint, 'reject')}
            >
              Stop here
            </button>
          </div>
        </div>
      ) : null}

      {waiting && !checkpoint ? (
        <div className="panel warn">
          <h3>Waiting for you — {waiting[0]}</h3>
          <textarea
            rows={3}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="Your answer (or `approve` / `reject` for a capability request)"
          />
          <button
            type="button"
            className="button primary"
            disabled={!answer.trim()}
            onClick={() =>
              void api
                .answer(runId, waiting[0], answer)
                .then(() => setAnswer(''))
                .catch((e) => setError(String(e)))
            }
          >
            Send answer
          </button>
        </div>
      ) : null}

      {graph ? (
        <Graph
          graph={graph}
          statuses={statuses}
          onSelect={setSelected}
          selected={selected}
        />
      ) : null}

      <div className="split">
        <div>
          <h2>Steps</h2>
          <ul className="rows">
            {Object.entries(state?.steps ?? {}).map(([id, step]) => (
              <li
                key={id}
                className={selected && step.node !== selected ? 'dimmed' : ''}
              >
                <code>{id}</code>
                <span className={`status is-${step.status}`}>{step.status}</span>
                {step.outcome && step.outcome !== 'ok' ? (
                  <span className="warn">{step.outcome}</span>
                ) : null}
                {step.tries > 1 ? (
                  <span className="muted">tries {step.tries}</span>
                ) : null}
                {step.status === 'done' ? (
                  <Artifacts runId={runId} stepId={id} />
                ) : null}
                {step.error ? <span className="error">{step.error}</span> : null}
              </li>
            ))}
          </ul>
        </div>

        <div>
          <h2>Events</h2>
          <ul className="feed">
            {events
              // when a node is selected: its own events plus run-level ones
              .filter((e) => !selected || !e.step_id || e.step_id.startsWith(selected))
              .slice(-120)
              .reverse()
              .map((e) => (
                <li key={e.seq}>
                  <span className="muted">{e.ts}</span>{' '}
                  <span className="tag">{e.type}</span>{' '}
                  {e.step_id ? <code>{e.step_id}</code> : null}{' '}
                  <span className="muted">{summarize(e)}</span>
                </li>
              ))}
          </ul>
        </div>
      </div>
    </section>
  )
}

function summarize(event: RunEvent): string {
  const p = event.payload ?? {}
  switch (event.type) {
    case 'heartbeat':
      return `${p.elapsed_s ?? '?'}s`
    case 'step_state_changed':
    case 'run_state_changed':
      return `${p.from} → ${p.to}${p.outcome ? ` (${p.outcome})` : ''}`
    case 'node_state_changed':
      return `${p.node_id}: ${p.from} → ${p.to}`
    case 'tool_call':
      return `${p.tool} ${p.summary ?? ''}`
    default:
      return String(p.message ?? p.question ?? '')
  }
}

function Artifacts({ runId, stepId }: { runId: string; stepId: string }) {
  const [files, setFiles] = useState<string[] | null>(null)
  useEffect(() => {
    void api
      .artifacts(runId, stepId)
      .then(setFiles)
      .catch(() => setFiles([]))
  }, [runId, stepId])
  if (!files?.length) return null
  return (
    <span className="artifacts">
      {files.map((f) => (
        <a key={f} href={api.artifactUrl(runId, stepId, f)} target="_blank" rel="noreferrer">
          {f}
        </a>
      ))}
    </span>
  )
}
