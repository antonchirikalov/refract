// Pipeline graph: layered DAG drawn as SVG — real edges with arrowheads, port
// labels, fan-out and checkpoint markers, node status from the run ledger (I7).
//
// Read-only in v0.1 (SPEC-UI §4). The layout knows nothing about what an edge means,
// so when conditional branches arrive they need a stroke style and a label — not a
// different layout: edges already span layers and bow around the nodes between.

import { useMemo, useState } from 'react'

import type { NodeStatus, PipelineGraph } from '../types'
import { ModelBadge } from './ModelBadge'

const NODE_W = 232
const NODE_H = 96
const GAP_X = 34
const GAP_Y = 64
const PAD = 12

const KIND_LABEL: Record<string, string> = {
  agent: 'agent',
  loop: 'loop',
  select: 'select',
  discover: 'discover',
}

interface Placed {
  id: string
  x: number
  y: number
  layer: number
}

function layout(graph: PipelineGraph): { placed: Map<string, Placed>; width: number; height: number } {
  const parents = new Map<string, string[]>()
  for (const node of graph.nodes) parents.set(node.id, [])
  for (const edge of graph.edges) parents.get(edge.target)?.push(edge.source)

  const order = graph.order.length ? graph.order : graph.nodes.map((n) => n.id)
  const layerOf = new Map<string, number>()
  for (const id of order) {
    const up = parents.get(id) ?? []
    layerOf.set(
      id,
      up.length ? Math.max(...up.map((p) => (layerOf.get(p) ?? 0) + 1)) : 0,
    )
  }

  const rows: string[][] = []
  for (const id of order) {
    const layer = layerOf.get(id) ?? 0
    rows[layer] = rows[layer] ?? []
    rows[layer].push(id)
  }

  const widest = Math.max(1, ...rows.map((r) => r?.length ?? 0))
  const width = PAD * 2 + widest * NODE_W + (widest - 1) * GAP_X
  const placed = new Map<string, Placed>()
  rows.forEach((row, layer) => {
    if (!row) return
    const rowWidth = row.length * NODE_W + (row.length - 1) * GAP_X
    const startX = (width - rowWidth) / 2
    row.forEach((id, i) => {
      placed.set(id, {
        id,
        x: startX + i * (NODE_W + GAP_X),
        y: PAD + layer * (NODE_H + GAP_Y),
        layer,
      })
    })
  })
  const height = PAD * 2 + rows.length * NODE_H + (rows.length - 1) * GAP_Y
  return { placed, width, height }
}

/** Bezier from the bottom of one node to the top of the next.
 *
 * An edge that skips layers bows out to the side: drawn straight it would pass
 * under the cards in between and read as a connection that is not there.
 */
function edgePath(from: Placed, to: Placed): string {
  const x1 = from.x + NODE_W / 2
  const y1 = from.y + NODE_H
  const x2 = to.x + NODE_W / 2
  const y2 = to.y - 8
  const span = to.layer - from.layer
  if (span > 1) {
    const bow = x1 + NODE_W * 0.62 + (span - 1) * 18
    return `M ${x1} ${y1} C ${bow} ${y1 + 24}, ${bow} ${y2 - 24}, ${x2} ${y2}`
  }
  const mid = (y1 + y2) / 2
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`
}

/** Where to put an edge's port label: staggered so converging edges stay readable. */
function labelPoint(from: Placed, to: Placed, index: number): { x: number; y: number } {
  const y1 = from.y + NODE_H
  const y2 = to.y
  const span = to.layer - from.layer
  const t = 0.5 + (index % 3) * 0.16 - 0.16
  if (span > 1) {
    return { x: from.x + NODE_W / 2 + NODE_W * 0.5, y: y1 + (y2 - y1) * t }
  }
  return {
    x: (from.x + to.x) / 2 + NODE_W / 2,
    y: y1 + (y2 - y1) * t,
  }
}

interface Props {
  graph: PipelineGraph
  statuses?: Record<string, NodeStatus>
  onSelect?: (nodeId: string) => void
  selected?: string | null
}

export function Graph({ graph, statuses, onSelect, selected }: Props) {
  const [hovered, setHovered] = useState<string | null>(null)
  const { placed, width, height } = useMemo(() => layout(graph), [graph])
  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
  const active = selected ?? hovered

  return (
    <div className="graph-canvas">
      <div className="graph-stage" style={{ width, height }}>
      <svg
        className="graph-edges"
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
      >
        <defs>
          <marker
            id="arrow"
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" className="arrow-head" />
          </marker>
        </defs>
        {graph.edges.map((edge, i) => {
          const from = placed.get(edge.source)
          const to = placed.get(edge.target)
          if (!from || !to) return null
          const touched = active === edge.source || active === edge.target
          const label = labelPoint(from, to, i)
          return (
            <g key={i} className={`edge${touched ? ' is-active' : ''}`}>
              <path d={edgePath(from, to)} markerEnd="url(#arrow)" />
              <text x={label.x} y={label.y} textAnchor="middle">
                {edge.port}
              </text>
            </g>
          )
        })}
      </svg>

      <div className="graph-nodes" style={{ width, height }}>
        {[...placed.values()].map((pos) => {
          const node = byId.get(pos.id)
          if (!node) return null
          const status = statuses?.[pos.id]
          const kind = node.type.startsWith('builtin/')
            ? node.type.slice('builtin/'.length)
            : (KIND_LABEL[node.type] ?? node.type)
          return (
            <button
              type="button"
              key={pos.id}
              className={[
                'gnode',
                status ? `is-${status}` : '',
                selected === pos.id ? 'is-selected' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              style={{ left: pos.x, top: pos.y, width: NODE_W, height: NODE_H }}
              onClick={() => onSelect?.(pos.id)}
              onMouseEnter={() => setHovered(pos.id)}
              onMouseLeave={() => setHovered(null)}
            >
              <span className="gnode-top">
                <span className="gnode-kind">{kind}</span>
                {node.fan_out ? (
                  <span className="chip chip-fan">{node.fan_out}</span>
                ) : null}
                {node.checkpoint ? (
                  <span className="chip chip-stop" title="the run parks here for review">
                    review
                  </span>
                ) : null}
                {status ? <span className={`dot is-${status}`} /> : null}
              </span>
              <span className="gnode-id">{pos.id}</span>
              {node.agents.length ? (
                <span className="gnode-agent">
                  {node.agents.map((a) => a.split('@')[0]).join(' → ')}
                </span>
              ) : null}
              <span className="gnode-foot">
                {(node.models ?? []).slice(0, 2).map((model) => (
                  <ModelBadge key={model} model={model} />
                ))}
                {(node.models ?? []).length > 2 ? (
                  <span className="chip">+{(node.models ?? []).length - 2}</span>
                ) : null}
                {status ? <span className="gnode-status">{status}</span> : null}
              </span>
            </button>
          )
        })}
      </div>
      </div>
    </div>
  )
}
