import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronRight,
  Clock3,
  FileSearch,
  FolderGit2,
  Gauge,
  KeyRound,
  LayoutDashboard,
  ListFilter,
  LoaderCircle,
  MessageCircle,
  Play,
  Radar,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Send,
  TerminalSquare,
  XCircle,
} from 'lucide-react'
import type { Event, MetricsResponse, Report, ResearchMode, ResearchPlan, Run, RunEvent, RunMode, SourceStatus } from './types'
import { cancelRun, createPlan, fetchMetrics, fetchReport, fetchRun, fetchRuns, fetchTrace, followUp, startRun, streamRun } from './lib/api'

const initialQuery = '分析 browser-use/browser-use 最近 30 天的版本变化、安装兼容性和社区反馈'

const sourceLabels: Record<string, string> = {
  github: 'GitHub',
  github_prs: 'GitHub PR',
  rss: '官方 RSS',
  hackernews: 'Hacker News',
  reddit: 'Reddit',
  browser_use: '动态网页',
}

const statusLabels: Record<string, string> = {
  public: '公开可访问',
  ok: '已完成',
  partial: '部分完成',
  auth_required: '需要授权',
  blocked: '访问受阻',
  rate_limited: '触发限流',
  disabled: '未启用',
  error: '采集失败',
  unavailable: '暂不可用',
  metadata_only: '仅元数据',
}

const focusFallback = ['版本变化', '社区反馈', '维护活跃度']
type TraceFilter = 'all' | 'collect' | 'done'

function formatDate(value?: string | null) {
  if (!value) return '暂无时间'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '暂无时间' : date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

function formatTime(value?: string | null) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function sourceName(source: string) {
  return sourceLabels[source] ?? source
}

function riskTone(level?: string) {
  if (level === 'critical' || level === 'high') return 'danger'
  if (level === 'medium') return 'warn'
  return 'ok'
}

function statusTone(status?: string) {
  if (status === 'error' || status === 'blocked' || status === 'auth_required' || status === 'rate_limited') return 'danger'
  if (status === 'partial' || status === 'disabled' || status === 'metadata_only') return 'warn'
  return 'ok'
}

function sourceStatus(source: SourceStatus) {
  return source.status === 'ok' && source.access_status !== 'public' ? source.access_status : source.status
}

function App() {
  const [query, setQuery] = useState(initialQuery)
  const [project, setProject] = useState('')
  const [researchMode, setResearchMode] = useState<ResearchMode>('standard')
  const [runMode, setRunMode] = useState<RunMode>('replay')
  const [plan, setPlan] = useState<ResearchPlan | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [events, setEvents] = useState<RunEvent[]>([])
  const [activeRunId, setActiveRunId] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [token, setToken] = useState(() => window.localStorage.getItem('signal-radar-api-token') ?? '')
  const [showSettings, setShowSettings] = useState(false)
  const [followUpQuery, setFollowUpQuery] = useState('')
  const [runHistory, setRunHistory] = useState<Run[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [traceFilter, setTraceFilter] = useState<TraceFilter>('all')
  const [cancelRequested, setCancelRequested] = useState(false)

  useEffect(() => {
    fetchReport().then(setReport).catch(() => setError('API 尚未启动，运行 Replay 后即可加载报告。'))
    fetchMetrics().then(setMetrics).catch(() => undefined)
    refreshHistory()
  }, [])

  const latestEvents = useMemo(() => [...events].slice(-7).reverse(), [events])
  const traceEvents = useMemo(() => {
    if (traceFilter === 'collect') return events.filter((event) => event.stage === 'collect' || event.type === 'source_started' || event.type === 'source_completed')
    if (traceFilter === 'done') return events.filter((event) => ['completed', 'partial', 'cancelled', 'failed'].includes(event.type))
    return events
  }, [events, traceFilter])
  const sources = report?.sources ?? []
  const criticalEvents = useMemo(
    () => [...(report?.events ?? [])].sort((a, b) => b.risk_score - a.risk_score).slice(0, 5),
    [report],
  )

  async function refreshHistory() {
    setHistoryLoading(true)
    try {
      setRunHistory(await fetchRuns())
    } catch {
      // 本地未启用认证时正常加载；共享 API 没有 Token 时仅隐藏历史列表。
    } finally {
      setHistoryLoading(false)
    }
  }

  async function refreshMetrics() {
    try { setMetrics(await fetchMetrics()) } catch { /* 指标接口不可用时不影响主报告 */ }
  }

  async function makePlan() {
    if (!query.trim()) {
      setError('先写一句想研究的问题。')
      return null
    }
    setError('')
    try {
      const response = await createPlan(query.trim(), project.trim(), researchMode)
      setPlan(response.plan)
      return response.plan
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '研究计划生成失败')
      return null
    }
  }

  async function runResearch() {
    const nextPlan = plan ?? await makePlan()
    if (!nextPlan) return
    setRunning(true)
    setCancelRequested(false)
    setError('')
    setEvents([])
    try {
      const started = await startRun(nextPlan, runMode)
      setActiveRunId(started.run.run_id)
      await streamRun(started.run.run_id, (event) => setEvents((current) => [...current, event]))
      const finished = await fetchRun(started.run.run_id)
      setReport(finished.report ?? null)
      await refreshHistory()
      await refreshMetrics()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '运行失败，请检查 API、来源配置和权限。')
    } finally {
      setRunning(false)
      setCancelRequested(false)
    }
  }

  async function runFollowUp() {
    if (!activeRunId || !followUpQuery.trim()) {
      setError(activeRunId ? '写下需要核验或补查的问题。' : '请先完成一次运行，再发起补查。')
      return
    }
    setRunning(true)
    setCancelRequested(false)
    setError('')
    setEvents([])
    try {
      const started = await followUp(activeRunId, { query: followUpQuery.trim(), mode: runMode })
      setActiveRunId(started.run.run_id)
      await streamRun(started.run.run_id, (event) => setEvents((current) => [...current, event]))
      const finished = await fetchRun(started.run.run_id)
      setReport(finished.report ?? null)
      setFollowUpQuery('')
      await refreshHistory()
      await refreshMetrics()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '补查失败，请检查运行状态和来源权限。')
    } finally {
      setRunning(false)
      setCancelRequested(false)
    }
  }

  async function requestCancel() {
    if (!activeRunId || cancelRequested) return
    try {
      await cancelRun(activeRunId)
      setCancelRequested(true)
      setError('已请求取消当前运行，等待来源适配器完成收尾。')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '取消请求失败')
    }
  }

  async function reloadTrace() {
    if (!activeRunId) return
    try {
      setEvents(await fetchTrace(activeRunId))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Trace 回放加载失败')
    }
  }

  async function selectHistoryRun(runId: string) {
    setError('')
    try {
      const selected = await fetchRun(runId)
      setActiveRunId(runId)
      setReport(selected.report ?? null)
      setEvents(await fetchTrace(runId))
      jumpTo('workspace')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '历史运行加载失败')
    }
  }

  function saveToken(value: string) {
    setToken(value)
    if (value.trim()) window.localStorage.setItem('signal-radar-api-token', value.trim())
    else window.localStorage.removeItem('signal-radar-api-token')
  }

  function jumpTo(id: string) {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-lockup">
          <div className="brand-mark"><Radar size={19} /></div>
          <div>
            <div className="brand-name">SIGNAL RADAR</div>
            <div className="brand-subtitle">OPEN SOURCE INTELLIGENCE</div>
          </div>
        </div>

        <nav className="nav-group" aria-label="主导航">
          <span className="nav-caption">工作台</span>
          <button className="nav-item nav-item-active" onClick={() => jumpTo('overview')}><LayoutDashboard size={17} /> 总览 <span className="nav-dot" /></button>
          <button className="nav-item" onClick={() => jumpTo('history')}><FileSearch size={17} /> 运行历史</button>
          <button className="nav-item" onClick={() => jumpTo('evidence')}><BookOpen size={17} /> 证据库</button>
          <span className="nav-caption nav-caption-spaced">系统</span>
          <button className="nav-item" onClick={() => jumpTo('workspace')}><ShieldCheck size={17} /> 来源与权限</button>
          <button className="nav-item" onClick={() => jumpTo('trace')}><TerminalSquare size={17} /> Trace 回放</button>
        </nav>

        <div className="sidebar-footer">
          <div className="connection-status"><span className="status-pulse" /> API 就绪</div>
          <button className="settings-link" onClick={() => setShowSettings((value) => !value)}>
            <SlidersHorizontal size={15} /> 本地设置
          </button>
        </div>
      </aside>

      <main className="main-column">
        <header className="topbar">
          <div>
            <span className="eyebrow">RESEARCH WORKSPACE / 01</span>
            <h1>开源项目情报工作台</h1>
          </div>
          <div className="topbar-actions">
            <span className="live-chip"><span /> LOCAL REPLAY READY</span>
            <button className="icon-button" title="刷新当前报告" onClick={() => fetchReport().then(setReport).catch(() => setError('报告刷新失败'))}>
              <RefreshCw size={17} />
            </button>
          </div>
        </header>

        <section className="brief-section">
          <div className="section-heading">
            <div>
              <span className="eyebrow eyebrow-accent">RESEARCH BRIEF</span>
              <h2>你想知道什么？</h2>
            </div>
            <span className="section-index">01 / 04</span>
          </div>
          <div className="brief-grid">
            <div className="brief-composer">
              <div className="composer-label"><Sparkles size={15} /> 自然语言研究简报</div>
              <textarea value={query} onChange={(event) => { setQuery(event.target.value); setPlan(null) }} placeholder="例如：分析某个项目最近 30 天的安装问题和社区反馈" />
              <div className="composer-bottom">
                <div className="composer-hint"><Search size={14} /> 计划解析不会访问网络</div>
                <button className="secondary-action" onClick={makePlan} disabled={running}>解析计划 <ChevronRight size={15} /></button>
              </div>
            </div>
            <div className="brief-fields">
              <label>
                <span>GitHub 项目（可选）</span>
                <div className="input-with-icon"><FolderGit2 size={15} /><input value={project} onChange={(event) => { setProject(event.target.value); setPlan(null) }} placeholder="owner/repository" /></div>
              </label>
              <label>
                <span>调研深度</span>
                <select value={researchMode} onChange={(event) => { setResearchMode(event.target.value as ResearchMode); setPlan(null) }}>
                  <option value="quick">快速 · 轻量采集</option>
                  <option value="standard">标准 · 平衡覆盖</option>
                  <option value="deep">深度 · 交叉验证</option>
                </select>
              </label>
              <label>
                <span>运行模式</span>
                <div className="segmented-control">
                  <button className={runMode === 'replay' ? 'segment-active' : ''} onClick={() => setRunMode('replay')}>Replay</button>
                  <button className={runMode === 'live' ? 'segment-active' : ''} onClick={() => setRunMode('live')}>Live</button>
                </div>
              </label>
            </div>
          </div>
          {plan && (
            <div className="plan-preview">
              <div className="plan-summary"><Check size={16} /><strong>{plan.explanation}</strong></div>
              <div className="plan-meta">
                <span><Clock3 size={14} /> 最近 {plan.window_days} 天</span>
                <span><Gauge size={14} /> {plan.research_mode}</span>
                <span><FolderGit2 size={14} /> {plan.project}</span>
              </div>
              <div className="plan-tags">
                {(plan.sources.length ? plan.sources : ['github', 'rss', 'hackernews']).map((source) => <span key={source}>{sourceName(source)}</span>)}
                {(plan.focus.length ? plan.focus : focusFallback).map((focus) => <span className="tag-muted" key={focus}>{focus}</span>)}
              </div>
              <button className="primary-action" onClick={runResearch} disabled={running}>
                {running ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}
                {running ? '正在采集' : '开始研究'}
              </button>
            </div>
          )}
          {error && <div className="inline-alert"><AlertTriangle size={16} /> {error}</div>}
        </section>

        <section className="overview-section" id="overview">
          <div className="section-heading">
            <div><span className="eyebrow">CURRENT SIGNALS</span><h2>项目状态概览</h2></div>
            <span className="section-note">{report ? `最近生成于 ${formatTime(report.generated_at)}` : '等待一份报告'}</span>
          </div>
          <div className="metric-grid">
            <div className={`metric-card metric-card-primary tone-${riskTone(report?.summary.risk_level)}`}>
              <span className="metric-label">整体风险</span>
              <strong>{report ? Math.round(report.summary.risk_score) : '—'}</strong>
              <div className="metric-track"><span style={{ width: `${Math.min(100, report?.summary.risk_score ?? 0)}%` }} /></div>
              <span className="metric-foot">{report?.summary.risk_level ?? '等待运行'} / 100</span>
            </div>
            <MetricCard label="关键事件" value={report?.summary.events_count ?? 0} detail="已关联证据的事件" icon={<AlertTriangle size={16} />} />
            <MetricCard label="证据条目" value={report?.evidence.length ?? 0} detail="可回链的来源片段" icon={<FileSearch size={16} />} />
            <MetricCard label="覆盖率" value={`${Math.round(report?.summary.coverage_pct ?? 0)}%`} detail="来源与时间窗口覆盖" icon={<Activity size={16} />} />
          </div>
          {metrics && <div className="telemetry-strip">
            <TelemetryItem label="平均延迟" value={metrics.current.latency_ms.avg == null ? '—' : `${Math.round(metrics.current.latency_ms.avg)} ms`} detail={`P95 ${metrics.current.latency_ms.p95 == null ? '—' : `${Math.round(metrics.current.latency_ms.p95)} ms`}`} />
            <TelemetryItem label="缓存命中" value={metrics.current.cache_hit_pct == null ? '—' : `${Math.round(metrics.current.cache_hit_pct)}%`} detail={`${metrics.current.pages} 页采集`} />
            <TelemetryItem label="新增记录" value={metrics.current.new_records} detail={`${metrics.current.total_candidates} 条候选`} />
            <TelemetryItem label="重复率" value={metrics.current.duplicate_rate_pct == null ? '—' : `${Math.round(metrics.current.duplicate_rate_pct)}%`} detail={`${metrics.current.duplicate_records} 条重复`} />
          </div>}
        </section>

        <section className="workspace-section" id="workspace">
          <div className="section-heading">
            <div><span className="eyebrow eyebrow-accent">RUN MONITOR</span><h2>采集工作台</h2></div>
            <div className="workspace-actions">
              {running && activeRunId && <button className="text-action danger-action" onClick={requestCancel} disabled={cancelRequested}><XCircle size={14} /> {cancelRequested ? '取消中' : '取消运行'}</button>}
              {activeRunId && <button className="text-action" onClick={() => { reloadTrace(); jumpTo('trace') }}><TerminalSquare size={14} /> 回放 Trace</button>}
              {activeRunId && <span className="run-id">{activeRunId}</span>}
            </div>
          </div>
          <div className="workspace-grid">
            <div className="activity-panel">
              <div className="panel-heading"><span>结构化运行事件</span><span className="panel-count">{events.length} events</span></div>
              {latestEvents.length ? latestEvents.map((event) => <RunEventRow event={event} key={event.id} />) : (
                <div className="empty-panel"><Radar size={23} /><span>开始一次研究后，这里会显示来源状态、记录数量和预算事件。</span></div>
              )}
            </div>
            <div className="sources-panel">
              <div className="panel-heading"><span>来源状态</span><span className="panel-count">{sources.length} sources</span></div>
              {sources.length ? sources.map((source) => <SourceRow source={source} key={source.source} />) : (
                <div className="empty-panel"><ShieldCheck size={23} /><span>Replay 报告或 Live 运行完成后，来源状态会出现在这里。</span></div>
              )}
            </div>
          </div>
          <div className="followup-bar">
            <div className="followup-icon"><MessageCircle size={17} /></div>
            <input value={followUpQuery} onChange={(event) => setFollowUpQuery(event.target.value)} placeholder="对当前运行提出一个限定范围的核验或补查问题" disabled={running} />
            <button className="followup-action" onClick={runFollowUp} disabled={running || !activeRunId}><Send size={15} /> 补查</button>
          </div>
        </section>

        <section className="trace-section" id="trace">
          <div className="section-heading">
            <div><span className="eyebrow eyebrow-accent">TRACE REPLAY</span><h2>运行轨迹</h2></div>
            <div className="trace-heading-meta"><span className="section-note">{activeRunId || '等待运行'}</span><ListFilter size={15} /></div>
          </div>
          <div className="trace-toolbar">
            <div className="trace-filter" role="tablist" aria-label="Trace 筛选">
              <button className={traceFilter === 'all' ? 'trace-filter-active' : ''} onClick={() => setTraceFilter('all')}>全部</button>
              <button className={traceFilter === 'collect' ? 'trace-filter-active' : ''} onClick={() => setTraceFilter('collect')}>采集</button>
              <button className={traceFilter === 'done' ? 'trace-filter-active' : ''} onClick={() => setTraceFilter('done')}>完成</button>
            </div>
            <span className="panel-count">{traceEvents.length} / {events.length} events</span>
          </div>
          <div className="trace-panel">
            {traceEvents.length ? traceEvents.map((event, index) => <TraceRow event={event} index={index} key={event.id} />) : (
              <div className="empty-panel"><TerminalSquare size={23} /><span>选择一条运行历史或开始研究后，这里会保留完整的结构化轨迹。</span></div>
            )}
          </div>
        </section>

        <section className="history-section" id="history">
          <div className="section-heading">
            <div><span className="eyebrow">PERSISTED RUNS</span><h2>运行历史</h2></div>
            <button className="text-action" onClick={refreshHistory} disabled={historyLoading}><RefreshCw size={14} /> 刷新</button>
          </div>
          <div className="history-list">
            {runHistory.length ? runHistory.map((run) => <HistoryRow key={run.run_id} run={run} active={run.run_id === activeRunId} onSelect={selectHistoryRun} />) : (
              <div className="empty-wide"><Clock3 size={21} /><span>{historyLoading ? '正在读取历史运行…' : '完成一次运行后，这里会保留可回放记录。'}</span></div>
            )}
          </div>
        </section>

        <section className="findings-section">
          <div className="section-heading">
            <div><span className="eyebrow">EVIDENCE LED FINDINGS</span><h2>关键风险信号</h2></div>
            <button className="text-action" onClick={() => jumpTo('evidence')}>查看完整报告 <ArrowUpRight size={15} /></button>
          </div>
          <div className="findings-list">
            {criticalEvents.length ? criticalEvents.map((event) => <FindingRow event={event} key={event.id} />) : (
              <div className="empty-wide"><FileSearch size={21} /><span>还没有可展示的风险事件。先运行 Replay 或 Live 采集。</span></div>
            )}
          </div>
        </section>

        <section className="evidence-section" id="evidence">
          <div className="section-heading">
            <div><span className="eyebrow eyebrow-accent">TRACEABLE SOURCES</span><h2>最近证据</h2></div>
            <span className="section-note">点击标题打开原文</span>
          </div>
          <div className="evidence-grid">
            {(report?.evidence ?? []).slice(0, 6).map((item) => <EvidenceCard evidence={item} key={item.id} />)}
            {!report?.evidence?.length && <div className="empty-wide"><BookOpen size={21} /><span>证据会保留来源 URL、摘录、时间和内容哈希。</span></div>}
          </div>
        </section>

        <footer className="footer"><span>Signal Radar / evidence first</span><span>公开来源优先 · 授权浏览 · 可审计运行</span></footer>
      </main>

      {showSettings && <div className="settings-drawer">
        <div className="drawer-heading"><span><KeyRound size={16} /> 本地 API 设置</span><button className="icon-button" title="关闭" onClick={() => setShowSettings(false)}><XCircle size={17} /></button></div>
        <label><span>Bearer Token（只保存在当前浏览器）</span><input type="password" value={token} onChange={(event) => saveToken(event.target.value)} placeholder="共享 API 启用认证时填写" /></label>
        <p>Token 不会写入代码或 URL。Replay 本地运行通常不需要填写。</p>
      </div>}
    </div>
  )
}

function MetricCard({ label, value, detail, icon }: { label: string; value: string | number; detail: string; icon: ReactNode }) {
  return <div className="metric-card"><span className="metric-label">{icon}{label}</span><strong>{value}</strong><span className="metric-foot">{detail}</span></div>
}

function TelemetryItem({ label, value, detail }: { label: string; value: string | number; detail: string }) {
  return <div className="telemetry-item"><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>
}

function RunEventRow({ event }: { event: RunEvent }) {
  const isDone = event.type === 'completed' || event.type === 'source_completed'
  const metrics = [
    event.new_records || event.records ? `${event.new_records || event.records} 新` : '',
    event.duplicate_records ? `${event.duplicate_records} 重复` : '',
    event.latency_ms ? `${Math.round(event.latency_ms)} ms` : '',
    event.pages && event.pages > 1 ? `${event.pages} 页` : '',
    event.cache_hit ? '缓存命中' : '',
  ].filter(Boolean).join(' · ')
  return <div className="activity-row"><span className={`activity-icon ${isDone ? 'activity-icon-done' : ''}`}>{isDone ? <Check size={13} /> : <Activity size={13} />}</span><div><strong>{event.message || event.type}</strong><span>{event.source ? sourceName(event.source) : event.stage || 'system'}{metrics ? ` · ${metrics}` : ''}</span></div><time>{formatTime(event.created_at)}</time></div>
}

function TraceRow({ event, index }: { event: RunEvent; index: number }) {
  const terminal = ['completed', 'partial', 'cancelled', 'failed'].includes(event.type)
  const metrics = [
    event.source ? sourceName(event.source) : event.stage || 'system',
    event.new_records || event.records ? `${event.new_records || event.records} 新` : '',
    event.duplicate_records ? `${event.duplicate_records} 重复` : '',
    event.latency_ms ? `${Math.round(event.latency_ms)} ms` : '',
    event.pages && event.pages > 1 ? `${event.pages} 页` : '',
    event.cache_hit ? '缓存命中' : '',
  ].filter(Boolean).join(' · ')
  return <div className={`trace-row ${terminal ? 'trace-row-terminal' : ''}`}><span className="trace-index">{String(index + 1).padStart(2, '0')}</span><span className={`trace-node ${terminal ? 'trace-node-terminal' : ''}`} /> <div className="trace-main"><strong>{event.message || event.type}</strong><span>{metrics}</span></div><span className="trace-type">{event.type}</span><time>{formatTime(event.created_at)}</time></div>
}

function SourceRow({ source }: { source: SourceStatus }) {
  const status = sourceStatus(source)
  const metrics = [
    source.new_records || source.records ? `${source.new_records || source.records} 新` : '0 新',
    source.duplicate_records ? `${source.duplicate_records} 重复` : '',
    source.latency_ms ? `${Math.round(source.latency_ms)} ms` : '',
    source.pages && source.pages > 1 ? `${source.pages} 页` : '',
    source.cache_hit ? '缓存命中' : '',
  ].filter(Boolean).join(' · ')
  return <div className="source-row"><div className={`source-signal source-signal-${statusTone(status)}`} /><div className="source-name"><strong>{sourceName(source.source)}</strong><span>{source.source_type} · {metrics}</span></div><span className={`source-status source-status-${statusTone(status)}`}>{statusLabels[status] ?? status}</span><span className="source-count">{source.records}</span></div>
}

function HistoryRow({ run, active, onSelect }: { run: Run; active: boolean; onSelect: (runId: string) => void }) {
  const tone = run.status === 'completed' ? 'ok' : run.status === 'partial' ? 'warn' : run.status === 'failed' || run.status === 'cancelled' ? 'danger' : 'warn'
  return <button className={`history-row ${active ? 'history-row-active' : ''}`} onClick={() => onSelect(run.run_id)}>
    <span className={`history-dot history-dot-${tone}`} />
    <span className="history-main"><strong>{run.subject || '未命名项目'}</strong><span>{run.run_id} · {run.mode} · {run.window_days} 天</span></span>
    <span className={`history-status history-status-${tone}`}>{statusLabels[run.status] ?? run.status}</span>
    <span className="history-date">{formatDate(run.completed_at || run.started_at)}</span>
    <ChevronRight size={15} />
  </button>
}

function FindingRow({ event }: { event: Event }) {
  return <article className={`finding-row finding-${riskTone(event.risk_level)}`}><div className="finding-marker" /><div className="finding-main"><div className="finding-title"><strong>{event.title}</strong><span>{event.category}</span></div><p>{event.summary || '报告没有提供额外摘要。'}</p></div><div className="finding-score"><strong>{Math.round(event.risk_score)}</strong><span>/ 100</span><small>{formatDate(event.occurred_at)}</small></div><ChevronRight size={17} className="finding-arrow" /></article>
}

function EvidenceCard({ evidence }: { evidence: Report['evidence'][number] }) {
  return <article className="evidence-card"><div className="evidence-card-top"><span className="evidence-source">{evidence.source}</span><span className="evidence-confidence">{Math.round(evidence.confidence * 100)}%</span></div><a href={evidence.url} target="_blank" rel="noreferrer"><strong>{evidence.title || '未命名证据'}</strong><ArrowUpRight size={14} /></a><p>{evidence.quote || '只有元数据，暂无可引用正文。'}</p><div className="evidence-meta"><span>{formatDate(evidence.published_at)}</span><span>{evidence.evidence_level}</span></div></article>
}

export default App
