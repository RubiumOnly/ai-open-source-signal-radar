export type RunMode = 'replay' | 'live'
export type ResearchMode = 'quick' | 'standard' | 'deep'

export interface ResearchPlan {
  query: string
  project: string
  window_days: number
  research_mode: ResearchMode
  focus: string[]
  sources: string[]
  urls: string[]
  explanation: string
}

export interface PlanResponse {
  plan: ResearchPlan
}

export interface Run {
  run_id: string
  mode: RunMode
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled'
  subject: string
  window_days: number
  started_at: string
  completed_at?: string | null
  report_id?: string | null
  error?: string | null
  budget: { max_steps: number; timeout_seconds: number }
}

export interface RunEvent {
  id: string
  run_id: string
  type: 'queued' | 'started' | 'plan' | 'source_started' | 'source_completed' | 'completed' | 'partial' | 'cancelled' | 'failed'
  stage?: string
  source?: string | null
  status?: string | null
  records?: number
  latency_ms?: number | null
  pages?: number
  cache_hit?: boolean
  new_records?: number
  duplicate_records?: number
  total_candidates?: number
  message?: string
  created_at: string
}

export interface SourceStatus {
  source: string
  source_type: string
  status: string
  access_status: string
  records: number
  detail?: string | null
  error?: string | null
  latency_ms?: number | null
  authenticated?: boolean
  pages?: number
  cache_hit?: boolean
  new_records?: number
  duplicate_records?: number
  total_candidates?: number
  next_cursor?: string | null
}

export interface Evidence {
  id: string
  url: string
  source: string
  title?: string | null
  quote: string
  evidence_level: string
  confidence: number
  published_at?: string | null
  content_hash?: string | null
}

export interface Event {
  id: string
  title: string
  category: string
  summary?: string | null
  risk_level: 'low' | 'medium' | 'high' | 'critical'
  risk_score: number
  sentiment: string
  occurred_at?: string | null
  evidence_ids: string[]
}

export interface Report {
  report_id: string
  run_id?: string | null
  generated_at: string
  project: { name: string; repository: string; version?: string | null }
  window_days: number
  summary: {
    risk_score: number
    risk_level: string
    events_count: number
    source_count: number
    coverage_pct: number
    positive_count: number
    negative_count: number
    neutral_count: number
    unresolved_count: number
  }
  trends: Array<{ date: string; mentions: number; risk_score: number; positive: number; negative: number; neutral: number }>
  topics: Array<{ name: string; count: number; sentiment: string; risk_score: number }>
  sources: SourceStatus[]
  access_status: Array<{ source: string; status: string; reason?: string | null; evidence_level: string }>
  evidence: Evidence[]
  events: Event[]
}

export interface RunResponse {
  run: Run
  report?: Report | null
}

export interface MetricsSnapshot {
  source_count: number
  latency_ms: { avg?: number | null; p95?: number | null; max?: number | null }
  pages: number
  new_records: number
  duplicate_records: number
  total_candidates: number
  duplicate_rate_pct?: number | null
  cache_hit_pct?: number | null
}

export interface MetricsResponse {
  current: MetricsSnapshot
  history: MetricsSnapshot
  runs: number
}

export interface CapabilitiesResponse {
  service: string
  version: string
  read_only: boolean
  api_auth_enabled: boolean
  cache_enabled: boolean
  browser_use: {
    available: boolean
    reason: string
    enabled: boolean
    run_live: boolean
    profile_configured: boolean
    authorized_session: boolean
    profile_reason: string
    allowed_domains: string[]
    max_steps: number
    timeout_seconds: number
  }
  structured_sources: string[]
}

export type SchedulerStatus = 'disabled' | 'scheduled' | 'running' | 'stopping' | 'stopped' | 'completed' | 'failed'

export interface SchedulerState {
  enabled: boolean
  status: SchedulerStatus
  schedule_id?: string | null
  interval_seconds?: number | null
  max_runs?: number | null
  run_immediately: boolean
  runs_started: number
  runs_completed: number
  last_run_id?: string | null
  last_run_status?: string | null
  last_error?: string | null
  started_at?: string | null
  last_run_at?: string | null
  next_run_at?: string | null
  stopped_at?: string | null
}

export interface SchedulerRequest {
  request: {
    mode: RunMode
    query?: string
    project?: string
    window_days?: number
    research_mode?: ResearchMode
    focus?: string[]
    sources?: string[]
    urls?: string[]
    limit?: number
  }
  interval_seconds: number
  max_runs: number
  run_immediately: boolean
}

export type AnnotationLabel = 'stance' | 'risk' | 'correctness'

export interface Annotation {
  id: string
  run_id?: string | null
  target_type: 'evidence' | 'claim' | 'event'
  target_id: string
  label: AnnotationLabel
  value: string
  note?: string | null
  reviewer: string
  timestamp: string
}

export interface AnnotationRequest {
  run_id?: string | null
  target_type: 'evidence' | 'claim' | 'event'
  target_id: string
  label: AnnotationLabel
  value: string
  note?: string | null
  reviewer: string
}

export interface Run {
  run_id: string
  mode: RunMode
  status: 'queued' | 'running' | 'completed' | 'partial' | 'failed' | 'cancelled'
  subject: string
  window_days: number
  started_at: string
  completed_at?: string | null
  report_id?: string | null
  error?: string | null
  budget: { max_steps: number; timeout_seconds: number }
}

export interface FollowUpRequest {
  query: string
  sources?: string[]
  window_days?: number
  mode?: RunMode
}
