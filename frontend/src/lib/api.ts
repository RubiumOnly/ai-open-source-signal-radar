import type { Annotation, AnnotationRequest, CapabilitiesResponse, FollowUpRequest, MetricsResponse, PlanResponse, Report, ResearchPlan, Run, RunEvent, RunResponse, RunMode, ResearchMode, RunOptions, SchedulerRequest, SchedulerState } from '../types'

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

function headers(): HeadersInit {
  const token = window.localStorage.getItem('signal-radar-api-token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...headers(), ...(init.headers ?? {}) },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `API ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function createPlan(query: string, project: string, mode: ResearchMode): Promise<PlanResponse> {
  return request<PlanResponse>('/api/plan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, project: project || null, research_mode: mode }),
  })
}

export function startRun(plan: ResearchPlan, mode: RunMode, options: RunOptions = {}): Promise<RunResponse> {
  return request<RunResponse>('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      mode,
      query: plan.query,
      project: plan.project,
      window_days: plan.window_days,
      research_mode: plan.research_mode,
      focus: plan.focus,
      sources: plan.sources,
      urls: options.urls ?? plan.urls,
      feed_urls: options.feed_urls,
      community_query: options.community_query,
      reddit_query: options.reddit_query,
      stackoverflow_query: options.stackoverflow_query,
      limit: options.limit ?? 100,
    }),
  })
}

export async function streamRun(runId: string, onEvent: (event: RunEvent) => void): Promise<void> {
  const response = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}/stream`, {
    headers: { Accept: 'text/event-stream', ...headers() },
  })
  if (!response.ok || !response.body) throw new Error(`无法订阅运行流（HTTP ${response.status}）`)
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? ''
    for (const frame of frames) {
      const data = frame.split('\n').find((line) => line.startsWith('data: '))
      if (!data) continue
      try { onEvent(JSON.parse(data.slice(6)) as RunEvent) } catch { /* 保持流的容错性 */ }
    }
  }
}

export function fetchRun(runId: string): Promise<RunResponse> {
  return request<RunResponse>(`/api/runs/${encodeURIComponent(runId)}`)
}

export function fetchRuns(): Promise<Run[]> {
  return request<Run[]>('/api/runs?limit=20')
}

export function fetchTrace(runId: string): Promise<RunEvent[]> {
  return request<RunEvent[]>(`/api/runs/${encodeURIComponent(runId)}/trace`)
}

export function followUp(runId: string, payload: FollowUpRequest): Promise<RunResponse> {
  return request<RunResponse>(`/api/runs/${encodeURIComponent(runId)}/follow-up`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function cancelRun(runId: string): Promise<{ run_id: string; status: string }> {
  return request<{ run_id: string; status: string }>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
  })
}

export function fetchReport(): Promise<Report> {
  return request<Report>('/api/report')
}

export function fetchMetrics(): Promise<MetricsResponse> {
  return request<MetricsResponse>('/api/metrics')
}

export function fetchCapabilities(): Promise<CapabilitiesResponse> {
  return request<CapabilitiesResponse>('/api/capabilities')
}

export function fetchSchedule(): Promise<SchedulerState> {
  return request<SchedulerState>('/api/schedule')
}

export function startSchedule(payload: SchedulerRequest): Promise<SchedulerState> {
  return request<SchedulerState>('/api/schedule', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function stopSchedule(): Promise<SchedulerState> {
  return request<SchedulerState>('/api/schedule/stop', { method: 'POST' })
}

export function fetchAnnotations(runId?: string): Promise<Annotation[]> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
  return request<Annotation[]>(`/api/annotations${query}`)
}

export function createAnnotation(payload: AnnotationRequest): Promise<Annotation> {
  return request<Annotation>('/api/annotations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function fetchAnnotationExport(runId?: string): Promise<Annotation[]> {
  const query = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
  return request<Annotation[]>(`/api/annotations/export.json${query}`)
}

export async function fetchMarkdown(runId: string): Promise<string> {
  const response = await fetch(`${API_BASE}/api/runs/${encodeURIComponent(runId)}/markdown`, {
    headers: { Accept: 'text/markdown', ...headers() },
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail || `API ${response.status}`)
  }
  return response.text()
}

export { API_BASE }
