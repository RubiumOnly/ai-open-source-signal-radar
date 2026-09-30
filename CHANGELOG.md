# 更新记录

## 0.2.0 - 2026-09-29

- 增加 RSS/Atom 官方博客来源，支持时间窗口、去重、响应大小限制和显式失败状态；
- 增加 CLI/API 的多来源编排、RSS feed 参数和窗口过滤；
- 使用 browser-use 原生 `ChatDeepSeek` wrapper，保留 OpenAI-compatible fallback；
- 锁定 FastAPI 与 Starlette 的兼容范围，并补充 Live 集成测试。
- Dashboard 增加关键事件、风险信号和证据回链；
- 收紧 fixture 路径、RSS 网络目标和 Browser Use 域名白名单边界；
- 动态抽取对不可核验的日期和正文降级为 metadata-only。
- 增加 SQLite 运行历史、Markdown 报告导出和离线评测 harness；
- 增加 Hacker News 社区来源、运行预算、协作式取消和请求级 CORS 白名单；
- 增加 Prompt Injection 回归 fixture、安全评测门禁、CI 工作流和 Dashboard 桌面/移动截图。

## Unreleased

- 启动工作台重构：新增 React + TypeScript 研究任务入口、计划预览、来源状态和证据报告界面；
- 来源编排改为有界并行，保留总预算、取消、部分成功和显式失败状态；
- 新增确定性的 `/api/plan` 研究计划接口，以及 `/api/runs/{run_id}/events` 和 SSE 运行流；
- 运行事件落 SQLite，支持重启后的 Trace 回放；报告可发起限定范围的补查任务；
- React 工作台增加持久化运行历史列表，可选择历史运行恢复报告和 Trace；
- 定时调度、Bearer Token、人工标注、Reddit 来源和离线安全评测已从可选方向变为当前能力。
- 增加公开来源的 SQLite 增量元数据缓存，支持 ETag/Last-Modified 条件请求和记录指纹去重；
- 为 GitHub、Hacker News、Reddit 增加有界分页，为来源状态和运行事件增加页数、延迟、缓存命中、新增与重复记录指标；
- React 工作台在来源面板和 Trace 事件中展示增量采集指标，并补充缓存配置示例。
- 离线评测增加来源性能/增量一致性指标，新增 `/api/metrics` 聚合当前与历史运行质量数据。
- 新增独立 Trace 回放区域，支持按阶段筛选完整结构化运行事件。
- React 工作台接入 Live 运行的协作式取消操作，并在运行轨迹中保留取消状态。
- GitHub 来源增加按需 Pull Request 元数据采集，复用分页、缓存和证据契约，不改变默认来源请求量。
- GitHub Discussions 增加按需公共 API 采集，仓库不开放时保留显式来源状态。
- GitHub 增加按需 PR review comments 来源，复用统一证据与增量缓存契约。
- 修复缓存命中后的报告快照，重复运行不再丢失当前窗口的证据与风险信号。
- 修复分页缓存命中后的提前退出，后续页仍会在预算内继续检查。
- 研究计划会保留用户明确粘贴的公开页面 URL，并在前端计划预览中展示，避免动态来源误采集默认页面。
- 明确页面 URL 会按路径自动选择动态浏览器回退，避免计划展示了 URL 却没有真正执行对应来源。
- React 报告页增加主题分布与时间趋势视图，并加入工作台导航入口。

## 0.1.0 - 2026-09-29

- 增加 GitHub Releases/Issues 只读采集器；
- 增加 Browser Use 动态页面适配器和域名白名单；
- 增加 Pydantic 报告契约、证据引用、确定性风险评分和 Replay fixture；
- 增加 FastAPI 服务、CLI、本地 Dashboard、Docker 配置和离线测试；
- 增加 DeepSeek OpenAI-compatible 配置示例。
