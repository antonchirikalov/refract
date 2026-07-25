// Properties of the selected thing — a container (loop/select) or one element inside
// it — with the two edits that matter: which model runs it, and how many rounds a loop
// takes. Written through the scoped node patch (SPEC §19.2.1), which validates the
// whole pipeline before committing, so an edit either lands or is refused with reasons.

import { useEffect, useState } from 'react'

import { api, ApiError } from '../api'
import type { PipelineGraph, ProviderInfo } from '../types'
import { ModelBadge } from './ModelBadge'

export interface Selection {
  nodeId: string
  block?: 'body' | 'critic' | 'selector'
}

const ROLE_TITLE: Record<string, string> = {
  body: 'Body — does the work',
  critic: 'Critic — judges the result',
  selector: 'Selector — picks the winner',
}

const PARAM_LABEL: Record<string, string> = {
  max_rounds: 'Max rounds',
  workers: 'Parallel workers',
  min_ok: 'Minimum successful items',
  min_sources: 'Minimum sources found',
  gate_retries: 'Retries on a bad output',
  timeout_s: 'Timeout, seconds',
}

const EDITABLE_PARAMS: Record<string, string[]> = {
  loop: ['max_rounds'],
  select: [],
  agent: ['workers', 'min_ok'],
  discover: ['min_sources'],
}

export function Inspector({
  project,
  pipeline,
  graph,
  selection,
  providers,
  onClose,
  onChanged,
}: {
  project: string
  pipeline: string
  graph: PipelineGraph
  selection: Selection
  providers: ProviderInfo[]
  onClose: () => void
  onChanged: () => void
}) {
  const node = graph.nodes.find((n) => n.id === selection.nodeId)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [rounds, setRounds] = useState('')

  useEffect(() => {
    setError(null)
    setRounds(node?.facts?.rounds?.replace('≤', '') ?? '')
  }, [selection.nodeId, selection.block, node?.facts?.rounds])

  if (!node) return null

  const block = selection.block
    ? node.blocks?.find((b) => b.role === selection.block)
    : undefined
  const kind = node.type.startsWith('builtin/')
    ? node.type.slice('builtin/'.length)
    : node.type
  const currentModel = block ? block.model : (node.models?.[0] ?? null)
  const boundModel = currentModel?.startsWith('@') ?? false
  const incoming = graph.edges.filter((e) => e.target === node.id)

  async function save(patch: Parameters<typeof api.patchNode>[3]) {
    setBusy(true)
    setError(null)
    try {
      await api.patchNode(project, pipeline, node!.id, patch)
      onChanged()
    } catch (e) {
      setError(
        e instanceof ApiError
          ? typeof e.detail === 'string'
            ? e.detail
            : JSON.stringify(e.detail, null, 2)
          : String(e),
      )
    } finally {
      setBusy(false)
    }
  }

  const editable = EDITABLE_PARAMS[node.type] ?? []
  const readOnlyFacts = Object.entries(node.facts ?? {}).filter(
    ([key]) => !(key === 'rounds' && editable.includes('max_rounds')),
  )

  return (
    <aside className="inspector">
      <header>
        <div>
          <span className="inspector-kind">
            {block ? `${kind} · ${block.role}` : kind}
          </span>
          <h3>{block ? block.agent.split('@')[0] : node.id}</h3>
        </div>
        <button type="button" className="button ghost" onClick={onClose}>
          ✕
        </button>
      </header>

      {block ? (
        <p className="muted">{ROLE_TITLE[block.role]}</p>
      ) : node.blocks?.length ? (
        <p className="muted">
          A container: it runs {node.blocks.map((b) => b.role).join(' and ')} inside
          itself. Click one to set its model.
        </p>
      ) : node.agents.length ? (
        <p className="muted">Runs {node.agents.map((a) => a.split('@')[0]).join(', ')}</p>
      ) : (
        <p className="muted">A builtin step — deterministic, runs no model.</p>
      )}

      {/* --- model ------------------------------------------------------------ */}
      {node.agents.length && (block || !node.blocks?.length) ? (
        <label className="field">
          <span>Model</span>
          {boundModel ? (
            <p className="meta">
              Bound to the winner of <code>{currentModel?.slice(1).split('.')[0]}</code>{' '}
              — the engine fills it in, so there is nothing to choose here.
            </p>
          ) : (
            <select
              value={currentModel ?? ''}
              disabled={busy}
              onChange={(e) =>
                void save(
                  e.target.value
                    ? { model: e.target.value, block: selection.block }
                    : { unset_model: true, block: selection.block },
                )
              }
            >
              <option value="">project default</option>
              {providers.flatMap((p) =>
                p.models.map((id) => (
                  <option
                    key={`${p.name}/${id}`}
                    value={`${p.name}/${id}`}
                    disabled={!p.available}
                  >
                    {p.name}/{id}
                    {p.available ? '' : ' — no key'}
                  </option>
                )),
              )}
            </select>
          )}
        </label>
      ) : null}

      {/* --- loop rounds ------------------------------------------------------ */}
      {!block && editable.includes('max_rounds') ? (
        <label className="field">
          <span>Rounds before it gives up</span>
          <span className="row">
            <input
              type="number"
              min={1}
              max={10}
              value={rounds}
              disabled={busy}
              onChange={(e) => setRounds(e.target.value)}
              style={{ width: '5rem' }}
            />
            <button
              type="button"
              className="button"
              disabled={busy || !rounds}
              onClick={() => void save({ params: { max_rounds: Number(rounds) } })}
            >
              Save
            </button>
          </span>
          <span className="meta">
            The critic can send the draft back this many times; then the run takes the
            last version ({node.facts?.['on max'] ?? 'pass'}).
          </span>
        </label>
      ) : null}

      {/* --- what this node reads / needs ------------------------------------- */}
      {!block ? (
        <>
          {incoming.length ? (
            <div className="inspector-facts">
              <span className="gnode-facts-key">reads</span>
              <ul className="inline">
                {incoming.map((e) => (
                  <li key={`${e.source}.${e.port}`}>
                    {e.port} <span className="muted">from {e.source}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {node.needs.length ? (
            <div className="inspector-facts">
              <span className="gnode-facts-key">may use</span>
              <ul className="inline">
                {node.needs.map((cap) => (
                  <li key={cap} className={cap === 'bash' ? 'warn' : undefined}>
                    {describeCapability(cap)}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {readOnlyFacts.length ? (
            <div className="inspector-facts">
              {readOnlyFacts.map(([key, value]) => (
                <p className="meta" key={key}>
                  {PARAM_LABEL[key] ?? key}: <strong>{value}</strong>
                </p>
              ))}
            </div>
          ) : null}

          {node.candidate_models?.length ? (
            <div className="inspector-facts">
              <span className="gnode-facts-key">chooses between</span>
              <span className="row">
                {node.candidate_models.map((m) => (
                  <ModelBadge key={m} model={m} />
                ))}
              </span>
            </div>
          ) : null}

          {node.checkpoint ? (
            <p className="warn">The run stops here and waits for your review.</p>
          ) : null}
        </>
      ) : null}

      {error ? <pre className="error">{error}</pre> : null}
    </aside>
  )
}

/** Capabilities in plain words — "mcp:tavily-remote" tells a user nothing. */
function describeCapability(cap: string): string {
  if (cap.startsWith('mcp:')) return `${cap.slice(4)} (external tool)`
  const plain: Record<string, string> = {
    read: 'read files',
    edit: 'write files',
    bash: 'run shell commands',
    webfetch: 'fetch web pages',
    websearch: 'search the web',
    vision: 'read images',
  }
  return plain[cap] ?? cap
}
