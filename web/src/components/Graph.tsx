// Pipeline graph: a layered DAG in SVG. Meta-nodes are drawn as CONTAINERS with their
// inner agents inside — a loop IS a body and a critic, and hiding that behind one box
// loses the thing a reader most wants to see.
//
// Read-only in v0.1 (SPEC-UI §4). The layout knows nothing about what an edge means,
// so when conditional branches arrive they need a stroke style and a label — not a
// different layout: edges already span layers and bow around the nodes between.

import { useMemo, useState } from 'react'

import type { GraphNode, NodeStatus, PipelineGraph, StepState } from '../types'
import { ModelBadge } from './ModelBadge'

const NODE_W = 320
const HEAD_H = 52 // kind row + id row
const PAD_V = 22 // the card's own vertical padding — must be in the height, or
// a short node (a builtin with no agent and no models) clips its own id
const AGENT_H = 20 // the agent line of a plain node
const FOOT_ROW = 24 // one footer row: models, or facts
const BLOCK_H = 36 // one inner agent card
const GAP_X = 46
const GAP_Y = 64
const PAD = 16

const KIND_LABEL: Record<string, string> = {
  agent: 'agent',
  loop: 'loop',
  select: 'select',
  discover: 'discover',
}

/** Footer rows a node needs: models on one line, params on the next.
 *
 * Always at least one: a run view puts the node's status there, and a builtin with
 * neither models nor params would otherwise be sized too short and clip its own id.
 */
function footRows(node: GraphNode): number {
  const hasModels =
    (node.candidate_models?.length ?? 0) > 0 ||
    ((node.blocks?.length ?? 0) === 0 && (node.models?.length ?? 0) > 0)
  const hasFacts = Object.keys(node.facts ?? {}).length > 0
  return Math.max(1, (hasModels ? 1 : 0) + (hasFacts ? 1 : 0))
}

function nodeHeight(node: GraphNode): number {
  const blocks = node.blocks?.length ?? 0
  const body = blocks ? blocks * BLOCK_H + 8 : node.agents.length ? AGENT_H : 0
  return HEAD_H + body + footRows(node) * FOOT_ROW + PAD_V
}

interface Placed {
  id: string
  x: number
  y: number
  w: number
  h: number
  layer: number
}

function layout(graph: PipelineGraph) {
  const parents = new Map<string, string[]>()
  for (const node of graph.nodes) parents.set(node.id, [])
  for (const edge of graph.edges) parents.get(edge.target)?.push(edge.source)

  const byId = new Map(graph.nodes.map((n) => [n.id, n]))
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
  let y = PAD
  rows.forEach((row, layer) => {
    if (!row) return
    const heights = row.map((id) => nodeHeight(byId.get(id) as GraphNode))
    const rowW = row.length * NODE_W + (row.length - 1) * GAP_X
    const startX = (width - rowW) / 2
    row.forEach((id, i) => {
      placed.set(id, {
        id,
        x: startX + i * (NODE_W + GAP_X),
        y,
        w: NODE_W,
        h: heights[i],
        layer,
      })
    })
    y += Math.max(...heights) + GAP_Y
  })
  return { placed, width, height: y - GAP_Y + PAD }
}

/** Bezier from the bottom of one node to the top of the next.
 *
 * An edge that skips layers bows out to the side: drawn straight it would pass under
 * the cards in between and read as a connection that is not there.
 */
function edgePath(from: Placed, to: Placed): string {
  const x1 = from.x + from.w / 2
  const y1 = from.y + from.h
  const x2 = to.x + to.w / 2
  const y2 = to.y - 9
  const span = to.layer - from.layer
  if (span > 1) {
    const bow = x1 + NODE_W * 0.64 + (span - 1) * 22
    return `M ${x1} ${y1} C ${bow} ${y1 + 30}, ${bow} ${y2 - 30}, ${x2} ${y2}`
  }
  const mid = (y1 + y2) / 2
  return `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`
}

function labelPoint(from: Placed, to: Placed, index: number) {
  const y1 = from.y + from.h
  const y2 = to.y
  const t = 0.5 + (index % 3) * 0.18 - 0.18
  if (to.layer - from.layer > 1) {
    return { x: from.x + from.w / 2 + NODE_W * 0.5, y: y1 + (y2 - y1) * t }
  }
  return { x: (from.x + to.x) / 2 + NODE_W / 2, y: y1 + (y2 - y1) * t }
}

interface Props {
  graph: PipelineGraph
  statuses?: Record<string, NodeStatus>
  /** Ledger steps, so a container can show which round is running (I7). */
  steps?: Record<string, StepState>
  onSelect?: (nodeId: string) => void
  selected?: string | null
}

/** The last step of one inner block, e.g. `refine.body:r2` → round "r2". */
function blockStep(
  steps: Record<string, StepState> | undefined,
  nodeId: string,
  role: string,
): { round: string; status: string } | null {
  if (!steps) return null
  const prefix = `${nodeId}.${role}`
  const found = Object.entries(steps)
    .filter(([id]) => id === prefix || id.startsWith(`${prefix}:`))
    .sort(([a], [b]) => a.localeCompare(b))
    .pop()
  if (!found) return null
  const [id, step] = found
  return { round: id.split(':')[1] ?? '', status: step.status }
}

/** How many element steps of a fan-out node are finished. */
function fanProgress(
  steps: Record<string, StepState> | undefined,
  nodeId: string,
): string | null {
  if (!steps) return null
  const elements = Object.entries(steps).filter(([id]) => id.startsWith(`${nodeId}:`))
  if (!elements.length) return null
  const done = elements.filter(([, s]) => s.status === 'done' || s.status === 'reused')
  return `${done.length}/${elements.length}`
}

export function Graph({ graph, statuses, steps, onSelect, selected }: Props) {
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

        {[...placed.values()].map((pos) => {
          const node = byId.get(pos.id)
          if (!node) return null
          return (
            <NodeCard
              key={pos.id}
              node={node}
              pos={pos}
              status={statuses?.[pos.id]}
              steps={steps}
              selected={selected === pos.id}
              onSelect={onSelect}
              onHover={setHovered}
            />
          )
        })}
      </div>
    </div>
  )
}

function NodeCard({
  node,
  pos,
  status,
  steps,
  selected,
  onSelect,
  onHover,
}: {
  node: GraphNode
  pos: Placed
  status?: NodeStatus
  steps?: Record<string, StepState>
  selected: boolean
  onSelect?: (id: string) => void
  onHover: (id: string | null) => void
}) {
  const kind = node.type.startsWith('builtin/')
    ? node.type.slice('builtin/'.length)
    : (KIND_LABEL[node.type] ?? node.type)
  const blocks = node.blocks ?? []
  const facts = Object.entries(node.facts ?? {})
  const models = node.models ?? []
  const candidates = node.candidate_models ?? []

  return (
    <button
      type="button"
      className={[
        'gnode',
        blocks.length ? 'is-container' : '',
        status ? `is-${status}` : '',
        selected ? 'is-selected' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      style={{ left: pos.x, top: pos.y, width: pos.w, height: pos.h }}
      onClick={() => onSelect?.(node.id)}
      onMouseEnter={() => onHover(node.id)}
      onMouseLeave={() => onHover(null)}
    >
      <span className="gnode-top">
        <span className="gnode-kind">{kind}</span>
        {node.fan_out ? (
          <span className="chip chip-fan">
            {node.fan_out}
            {fanProgress(steps, node.id) ? ` ${fanProgress(steps, node.id)}` : ''}
          </span>
        ) : null}
        {node.checkpoint ? (
          <span className="chip chip-stop" title="the run parks here for review">
            review
          </span>
        ) : null}
        {status ? <span className={`dot is-${status}`} /> : null}
      </span>

      <span className="gnode-id">{node.id}</span>

      {blocks.length ? (
        <span className="gnode-blocks">
          {blocks.map((block) => {
            const step = blockStep(steps, node.id, block.role)
            return (
              <span
                className={`gblock${step ? ` is-${step.status}` : ''}`}
                key={block.role}
              >
                <span className="gblock-role">{block.role}</span>
                <span className="gblock-agent">{block.agent.split('@')[0]}</span>
                {step?.round ? <span className="gblock-round">{step.round}</span> : null}
                {block.model ? <ModelBadge model={block.model} /> : null}
                {step ? <span className={`dot is-${step.status}`} /> : null}
              </span>
            )
          })}
        </span>
      ) : node.agents.length ? (
        <span className="gnode-agent">
          {node.agents.map((a) => a.split('@')[0]).join(' → ')}
        </span>
      ) : null}

      <span className="gnode-foot">
        {candidates.length ? (
          <span className="gnode-row">
            <span className="gnode-facts-key">between</span>
            {candidates.map((m) => (
              <ModelBadge key={m} model={m} />
            ))}
          </span>
        ) : blocks.length === 0 && models.length ? (
          <span className="gnode-row">
            {models.slice(0, 3).map((model) => (
              <ModelBadge key={model} model={model} />
            ))}
            {models.length > 3 ? (
              <span className="chip">+{models.length - 3}</span>
            ) : null}
          </span>
        ) : null}
        {facts.length ? (
          <span className="gnode-row gnode-facts">
            {facts.map(([key, value]) => (
              <span className="gnode-fact" key={key}>
                <span className="gnode-facts-key">{key}</span> {value}
              </span>
            ))}
            {status ? <span className="gnode-status">{status}</span> : null}
          </span>
        ) : status ? (
          <span className="gnode-row">
            <span className="gnode-status">{status}</span>
          </span>
        ) : null}
      </span>
    </button>
  )
}
