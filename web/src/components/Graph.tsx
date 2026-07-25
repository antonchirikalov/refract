// Pipeline graph: nodes in dependency layers, statuses from the run ledger (I7).
// A read-only rendering — v0.1 does not edit the pipeline (SPEC-UI §4).

import type { NodeStatus, PipelineGraph } from '../types'

const KIND_LABEL: Record<string, string> = {
  loop: 'loop',
  select: 'select',
  discover: 'discover',
  agent: 'agent',
}

function layers(graph: PipelineGraph): string[][] {
  const incoming = new Map<string, string[]>()
  for (const node of graph.nodes) incoming.set(node.id, [])
  for (const edge of graph.edges) incoming.get(edge.target)?.push(edge.source)

  const depth = new Map<string, number>()
  // graph.order is the engine's topological order, so one pass suffices
  for (const id of graph.order.length ? graph.order : graph.nodes.map((n) => n.id)) {
    const parents = incoming.get(id) ?? []
    const d = parents.length
      ? Math.max(...parents.map((p) => (depth.get(p) ?? 0) + 1))
      : 0
    depth.set(id, d)
  }
  const out: string[][] = []
  for (const [id, d] of depth) {
    out[d] = out[d] ?? []
    out[d].push(id)
  }
  return out.filter(Boolean)
}

interface Props {
  graph: PipelineGraph
  statuses?: Record<string, NodeStatus>
  onSelect?: (nodeId: string) => void
  selected?: string | null
}

export function Graph({ graph, statuses, onSelect, selected }: Props) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const rows = layers(graph)

  return (
    <div className="graph">
      {rows.map((row, i) => (
        <div className="graph-layer" key={i}>
          {row.map((id) => {
            const node = byId.get(id)
            if (!node) return null
            const status = statuses?.[id]
            const kind = node.type.startsWith('builtin/')
              ? node.type.slice('builtin/'.length)
              : (KIND_LABEL[node.type] ?? node.type)
            return (
              <button
                type="button"
                key={id}
                className={[
                  'node',
                  status ? `is-${status}` : '',
                  selected === id ? 'is-selected' : '',
                ]
                  .filter(Boolean)
                  .join(' ')}
                onClick={() => onSelect?.(id)}
                title={node.agents.join(', ') || node.type}
              >
                <span className="node-id">{id}</span>
                <span className="node-kind">
                  {kind}
                  {node.fan_out ? ` · ${node.fan_out}` : ''}
                </span>
                {status ? <span className="node-status">{status}</span> : null}
              </button>
            )
          })}
        </div>
      ))}
    </div>
  )
}
