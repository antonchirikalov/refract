// Mirrors the engine's API models (SPEC §15, §19.1, SPEC-UI §5). Kept hand-written
// and small: only what the screens actually read.

export type RunStatus =
  | 'created'
  | 'validating'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'waiting_human'

export type StepStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'waiting_human'
  | 'cancelled'
  | 'reused'

export type NodeStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'skipped'
  | 'reused'
  | 'waiting_human'

export interface TemplateInfo {
  name: string
  source: 'library' | 'user'
  title: string
  description: string
  input_mode: 'documents' | 'brief'
  nodes: { id: string; type: string }[]
  agents: string[]
  needs: string[]
  reads_input_folder: boolean
}

export interface ProviderInfo {
  name: string
  api_key_env: string
  available: boolean
  max_concurrent: number
  models: string[]
}

export interface InputSummary {
  input_dir: string
  external: boolean
  entries: string[]
}

export interface RunSummary {
  run_id: string
  status: RunStatus
  pipeline: string
  created_at: string
  finished_at: string | null
}

export interface NodeState {
  status: NodeStatus
  error: string | null
  winner: string | null
  winner_model: string | null
}

export interface StepState {
  node: string
  status: StepStatus
  outcome: string | null
  tries: number
  started_at: string | null
  finished_at: string | null
  error: string | null
}

export interface RunState {
  run_id: string
  status: RunStatus
  pipeline: string
  created_at: string
  finished_at: string | null
  reuse_from: string | null
  force_nodes: string[]
  nodes: Record<string, NodeState>
  steps: Record<string, StepState>
}

export interface GraphNode {
  id: string
  type: string
  agents: string[]
  needs: string[]
  fan_out: 'map' | 'map_over' | null
}

export interface PipelineGraph {
  name: string
  input_mode: 'documents' | 'brief'
  order: string[]
  nodes: GraphNode[]
  edges: { source: string; target: string; port: string }[]
}

export interface ValidationError {
  code: string
  node_id: string | null
  message: string
}

export interface RunEvent {
  seq: number
  ts: string
  type:
    | 'run_state_changed'
    | 'step_state_changed'
    | 'node_state_changed'
    | 'heartbeat'
    | 'tool_call'
    | 'log'
    | 'question'
  step_id: string | null
  payload: Record<string, unknown>
}

export interface FsEntry {
  name: string
  is_dir: boolean
}
