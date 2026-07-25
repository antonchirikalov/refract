import { useCallback, useEffect, useState } from 'react'

import { api, ApiError } from '../api'
import { Graph } from '../components/Graph'
import { href, navigate } from '../route'
import type {
  InputSummary,
  PipelineGraph,
  RunSummary,
  ValidationError,
} from '../types'

/** A project: its pipeline, its input, its runs, and the button that starts one. */
export function Project({ project }: { project: string }) {
  const [pipelines, setPipelines] = useState<string[]>([])
  const [pipeline, setPipeline] = useState<string | null>(null)
  const [graph, setGraph] = useState<PipelineGraph | null>(null)
  const [input, setInput] = useState<InputSummary | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [errors, setErrors] = useState<ValidationError[] | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [brief, setBrief] = useState('')

  const reload = useCallback(async () => {
    const [names, inputSummary, runList] = await Promise.all([
      api.pipelines(project),
      api.input(project).catch(() => null),
      api.runs(project).catch(() => [] as RunSummary[]),
    ])
    setPipelines(names)
    setInput(inputSummary)
    setRuns(runList)
    setPipeline((current) => current ?? names[0] ?? null)
  }, [project])

  useEffect(() => {
    void reload().catch((e) => setError(String(e)))
  }, [reload])

  useEffect(() => {
    if (!pipeline) return
    setGraph(null)
    void api
      .pipelineGraph(project, pipeline)
      .then(setGraph)
      .catch((e) => setError(String(e)))
    void api
      .validate(project, pipeline)
      .then((r) => setErrors(r.errors))
      .catch(() => setErrors(null))
  }, [project, pipeline])

  async function start() {
    if (!pipeline) return
    setBusy(true)
    setError(null)
    try {
      const { run_id } = await api.startRun(project, pipeline)
      navigate({ screen: 'run', project, runId: run_id })
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e))
    } finally {
      setBusy(false)
    }
  }

  const wantsBrief = graph?.input_mode === 'brief'
  const blocking = (errors ?? []).length > 0
  const hasInput = (input?.entries.length ?? 0) > 0

  return (
    <section>
      <header className="screen-head">
        <h1>{project}</h1>
        <div className="row">
          {pipelines.length > 1 ? (
            <select
              value={pipeline ?? ''}
              onChange={(e) => setPipeline(e.target.value)}
            >
              {pipelines.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
          ) : null}
          <button
            type="button"
            className="button primary"
            disabled={!pipeline || busy || blocking || !hasInput}
            onClick={() => void start()}
          >
            {busy ? 'starting…' : 'Run'}
          </button>
        </div>
      </header>

      {error ? <pre className="error">{error}</pre> : null}

      {blocking ? (
        <div className="panel error">
          <h3>This pipeline will not run</h3>
          <ul>
            {errors?.map((e, i) => (
              <li key={i}>
                <code>{e.code}</code> {e.node_id ? `[${e.node_id}] ` : ''}
                {e.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <h2>Input</h2>
      {input ? (
        <div className="panel">
          <p className="muted">
            {input.input_dir}
            {input.external ? ' (referenced folder)' : ''}
          </p>
          {input.entries.length ? (
            <ul className="inline">
              {input.entries.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          ) : wantsBrief ? (
            <div>
              <textarea
                rows={5}
                value={brief}
                placeholder="What should be researched?"
                onChange={(e) => setBrief(e.target.value)}
              />
              <button
                type="button"
                className="button"
                disabled={!brief.trim()}
                onClick={() =>
                  void api
                    .putBrief(project, brief)
                    .then(setInput)
                    .catch((e) => setError(String(e)))
                }
              >
                Save brief
              </button>
            </div>
          ) : (
            <p className="warn">
              No documents yet — copy some in before running.
            </p>
          )}
        </div>
      ) : null}

      <h2>Pipeline</h2>
      {graph ? (
        <>
          {graph.checkpoints.length ? (
            <p className="meta">
              stops for review after: {graph.checkpoints.join(', ')}
            </p>
          ) : null}
          <Graph graph={graph} onSelect={setSelected} selected={selected} />
          {selected ? <NodeDetail graph={graph} nodeId={selected} /> : null}
        </>
      ) : (
        <p className="muted">loading…</p>
      )}

      <h2>Runs</h2>
      {runs.length === 0 ? (
        <p className="muted">no runs yet</p>
      ) : (
        <ul className="rows">
          {runs.map((r) => (
            <li key={r.run_id}>
              <a href={href({ screen: 'run', project, runId: r.run_id })}>
                <code>{r.run_id}</code>
              </a>
              <span className={`status is-${r.status}`}>{r.status}</span>
              <span className="muted">{r.pipeline}</span>
              <span className="muted">{r.created_at}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function NodeDetail({
  graph,
  nodeId,
}: {
  graph: PipelineGraph
  nodeId: string
}) {
  const node = graph.nodes.find((n) => n.id === nodeId)
  if (!node) return null
  const incoming = graph.edges.filter((e) => e.target === nodeId)
  return (
    <div className="panel">
      <h3>
        {node.id} <span className="muted">{node.type}</span>
      </h3>
      {node.agents.length ? <p>agents: {node.agents.join(', ')}</p> : null}
      {node.needs.length ? <p className="meta">needs: {node.needs.join(', ')}</p> : null}
      {node.fan_out ? <p className="meta">fan-out: {node.fan_out}</p> : null}
      {incoming.length ? (
        <p className="meta">
          inputs: {incoming.map((e) => `${e.source}.${e.port}`).join(', ')}
        </p>
      ) : null}
    </div>
  )
}
