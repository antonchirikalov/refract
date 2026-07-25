// Typed client for the engine API (SPEC §15). Same-origin paths — dev goes through
// the Vite proxy, production is served by the engine itself.

import type {
  FsEntry,
  InputSummary,
  PipelineGraph,
  ProviderInfo,
  RunState,
  RunSummary,
  TemplateInfo,
  ValidationError,
} from './types'

export class ApiError extends Error {
  status: number
  detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `request failed (${status})`)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body && !(init.headers as Record<string, string>)?.['Content-Type']
        ? { 'Content-Type': 'application/json' }
        : {}),
      ...init?.headers,
    },
  })
  if (!resp.ok) {
    let detail: unknown = await resp.text()
    try {
      detail = JSON.parse(detail as string)
      if (detail && typeof detail === 'object' && 'detail' in detail) {
        detail = (detail as { detail: unknown }).detail
      }
    } catch {
      /* keep the raw text */
    }
    throw new ApiError(resp.status, detail)
  }
  if (resp.status === 204) return undefined as T
  return (await resp.json()) as T
}

export const api = {
  projects: () => request<string[]>('/api/projects'),

  createProject: (body: {
    name: string
    template?: string
    input?: string
    model?: string
  }) =>
    request<{ id: string }>('/api/projects', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  pipelines: (project: string) =>
    request<string[]>(`/api/projects/${project}/pipelines`),

  pipelineGraph: (project: string, name: string) =>
    request<PipelineGraph>(`/api/projects/${project}/pipelines/${name}/graph`),

  pipelineText: (project: string, name: string) =>
    request<{ name: string; yaml: string; hash: string }>(
      `/api/projects/${project}/pipelines/${name}`,
    ),

  validate: (project: string, name: string) =>
    request<{ ok: boolean; errors: ValidationError[] }>(
      `/api/projects/${project}/pipelines/${name}/validate`,
      { method: 'POST' },
    ),

  input: (project: string) =>
    request<InputSummary>(`/api/projects/${project}/input`),

  importDocuments: (project: string, path: string, replace = false) =>
    request<InputSummary>(`/api/projects/${project}/input/documents`, {
      method: 'POST',
      body: JSON.stringify({ path, replace }),
    }),

  putBrief: (project: string, text: string) =>
    request<InputSummary>(`/api/projects/${project}/input/brief`, {
      method: 'PUT',
      body: JSON.stringify({ text }),
    }),

  runs: (project: string) =>
    request<RunSummary[]>(`/api/projects/${project}/runs`),

  startRun: (project: string, pipeline: string, reuse_from?: string) =>
    request<{ run_id: string }>(`/api/projects/${project}/runs`, {
      method: 'POST',
      body: JSON.stringify({ pipeline, reuse_from }),
    }),

  run: (runId: string) => request<RunState>(`/api/runs/${runId}`),

  cancelRun: (runId: string) =>
    request<unknown>(`/api/runs/${runId}/cancel`, { method: 'POST' }),

  resumeRun: (runId: string) =>
    request<unknown>(`/api/runs/${runId}/resume`, { method: 'POST' }),

  answer: (runId: string, stepId: string, answer: string) =>
    request<unknown>(`/api/runs/${runId}/answers`, {
      method: 'POST',
      body: JSON.stringify({ step_id: stepId, answer }),
    }),

  artifacts: (runId: string, stepId: string) =>
    request<string[]>(`/api/runs/${runId}/steps/${stepId}/artifacts`),

  artifactUrl: (runId: string, stepId: string, path: string) =>
    `/api/runs/${runId}/steps/${stepId}/artifacts/${path}`,

  templates: () => request<TemplateInfo[]>('/api/templates'),

  saveTemplate: (body: { name: string; from_project: string; pipeline: string }) =>
    request<{ name: string }>('/api/templates', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  models: () => request<ProviderInfo[]>('/api/models'),

  browse: (path?: string) =>
    request<FsEntry[]>(
      `/api/fs/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`,
    ),
}

/** Live run events (SPEC §9/§15): replay from ``fromSeq`` then stream. */
export function openEvents(
  runId: string,
  fromSeq: number,
  onEvent: (e: unknown) => void,
  onClose?: () => void,
): WebSocket {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const ws = new WebSocket(
    `${proto}://${location.host}/api/runs/${runId}/events?from_seq=${fromSeq}`,
  )
  ws.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data as string))
    } catch {
      /* a frame we cannot parse is not worth killing the stream over */
    }
  }
  ws.onclose = () => onClose?.()
  return ws
}
