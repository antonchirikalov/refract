import { useCallback, useEffect, useState } from 'react'

import { api, ApiError } from '../api'
import { Graph, type GraphSelection } from '../components/Graph'
import { Inspector } from '../components/Inspector'
import { href, navigate } from '../route'
import type {
  InputSummary,
  PipelineGraph,
  ProviderInfo,
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
  const [selected, setSelected] = useState<GraphSelection | null>(null)
  const [providers, setProviders] = useState<ProviderInfo[]>([])
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState(false)  // this pipeline is now a template
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
    void api.models().then(setProviders).catch(() => setProviders([]))
  }, [reload])

  const loadGraph = useCallback(() => {
    if (!pipeline) return
    void api
      .pipelineGraph(project, pipeline)
      .then(setGraph)
      .catch((e) => setError(String(e)))
    void api
      .validate(project, pipeline)
      .then((r) => setErrors(r.errors))
      .catch(() => setErrors(null))
  }, [project, pipeline])

  useEffect(() => {
    setGraph(null)
    loadGraph()
  }, [loadGraph])

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

  /**
   * Copy this project's pipeline into the user template library, so the next project
   * can start from it. The name has to be new — the server refuses to overwrite a
   * template (409), and silently replacing someone's template is worse than asking.
   */
  async function saveAsTemplate() {
    if (!pipeline) return
    const name = window.prompt('Template name', `${project}-${pipeline}`)?.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      await api.saveTemplate({ name, from_project: project, pipeline })
      setSaved(true)
    } catch (e) {
      setError(e instanceof ApiError ? JSON.stringify(e.detail) : String(e))
    } finally {
      setBusy(false)
    }
  }

  const parkedRun = runs.find((r) => r.awaiting_checkpoint) ?? null
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
            className="button"
            disabled={!pipeline || busy}
            onClick={() => void saveAsTemplate()}
            title="Reuse this pipeline in new projects"
          >
            {saved ? 'saved as template' : 'Save as template'}
          </button>
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

      {parkedRun ? (
        <div className="panel warn">
          <h3>A run is waiting for you</h3>
          <p>
            Parked at <code>{parkedRun.awaiting_checkpoint}</code>. Reopen it to
            review the output and continue where it stopped.
          </p>
          <a
            className="button primary"
            href={href({ screen: 'run', project, runId: parkedRun.run_id })}
          >
            Continue run
          </a>
        </div>
      ) : null}

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
          {(graph.checkpoints ?? []).length ? (
            <p className="meta">
              stops for review after: {(graph.checkpoints ?? []).join(', ')}
            </p>
          ) : null}
          <div className={`workbench${selected ? ' is-open' : ''}`}>
            <Graph graph={graph} onSelect={setSelected} selected={selected} />
            {selected ? (
              <Inspector
                project={project}
                pipeline={pipeline ?? ''}
                graph={graph}
                selection={selected}
                providers={providers}
                onClose={() => setSelected(null)}
                onChanged={loadGraph}
              />
            ) : (
              <p className="muted workbench-hint">
                Click a node — or an agent inside a loop — to see and change its
                properties.
              </p>
            )}
          </div>
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
              <span className={`pill is-${r.status}`}>
                {r.awaiting_checkpoint
                  ? `parked at ${r.awaiting_checkpoint}`
                  : r.status}
              </span>
              <span className="muted">{r.pipeline}</span>
              <span className="muted">{r.created_at}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
