# API 参考

[返回 README](../README.md) · [使用指南](USAGE.md) · [离线评测](EVALUATION.md)

本地 API 默认地址为 `http://localhost:8000`。启动后，完整 OpenAPI 契约可在 [/docs](http://localhost:8000/docs) 与 [/openapi.json](http://localhost:8000/openapi.json) 查看。字段约束以 [models.py](../signal_radar/models.py) 和运行时 OpenAPI 为准。

## 认证

未设置 `SIGNAL_RADAR_API_TOKEN` 时，API 无认证，仅适合受控本地使用。设置后，下表中“需要 Token”的端点要求：

```http
Authorization: Bearer <your-api-token>
```

Token 不应进入 URL、截图或前端构建产物。401 响应包含 `WWW-Authenticate: Bearer`。共享部署还需 HTTPS、网络隔离和额外访问策略。

**公开端点并不限于健康检查**：当前报告、来源、质量指标、能力探针和计划解析也是公开的。单 Bearer Token 不隔离不同用户，不能直接视为多租户认证。

## 端点

“需要 Token”指服务已设置 API Token 的情况；未设置时这些端点也不要求认证。

### 计划与观测

| 方法 | 路径 | 作用 | 需要 Token |
| --- | --- | --- | --- |
| GET | `/api/health` | 健康检查与 Replay 可用状态 | 否 |
| POST | `/api/plan` | 规则解析研究计划，不访问外部网络 | 否 |
| GET | `/api/report` | 当前报告；可读取允许的 fixture | 否 |
| GET | `/api/sources` | 当前报告来源状态 | 否 |
| GET | `/api/metrics` | 当前及最近历史的来源质量聚合 | 否 |
| GET | `/api/capabilities` | 浏览器、缓存、认证与来源能力 | 否 |

能力探针不返回模型 Key、GitHub Token、Cookie 或 Profile 路径。`fixture` 参数仅接受仓库允许目录中的文件，不用于读取任意本地路径。

### 运行与归档

| 方法 | 路径 | 作用 | 需要 Token |
| --- | --- | --- | --- |
| POST | `/api/runs` | 启动后台运行，202 返回排队记录 | 是 |
| GET | `/api/runs` | 历史分页，`limit` / `offset` | 是 |
| GET | `/api/runs/{run_id}` | 运行与报告，JSON 导出数据来源 | 是 |
| GET | `/api/runs/{run_id}/stream` | SSE 事件流 | 是 |
| GET | `/api/runs/{run_id}/events` | 已保存的结构化事件 | 是 |
| GET | `/api/runs/{run_id}/trace` | 与事件接口相同的回放入口 | 是 |
| POST | `/api/runs/{run_id}/follow-up` | 基于已有报告创建有界补查 | 是 |
| POST | `/api/runs/{run_id}/cancel` | 请求协作式取消，202 | 是 |
| GET | `/api/runs/{run_id}/markdown` | Markdown 报告 | 是 |
| GET | `/api/runs/{run_id}/report.md` | Markdown 导出别名 | 是 |
| POST | `/api/run` | 同步执行一次运行 | 是 |
| GET | `/api/run` | 兼容入口，**会执行运行**，不是只读查询 | 是 |

历史分页默认 20 条，`limit` 范围 1–100。不存在的运行返回 404；没有报告的运行不能导出 Markdown 或发起补查。后台运行优先使用 `POST /api/runs`，不要用同步兼容入口模拟任务排队。

### 调度与标注

| 方法 | 路径 | 作用 | 需要 Token |
| --- | --- | --- | --- |
| GET | `/api/schedule` | 查询进程内计划状态 | 是 |
| POST | `/api/schedule` | 显式启动有界调度，202 | 是 |
| POST | `/api/schedule/stop` | 停止计划，202 | 是 |
| DELETE | `/api/schedule` | 停止计划的别名，不删除历史 | 是 |
| POST | `/api/annotations` | 新增独立标注，201 | 是 |
| GET | `/api/annotations` | 筛选与分页读取标注 | 是 |
| GET | `/api/annotations/export.json` | 导出 JSON 标注数组 | 是 |

标注列表支持 `run_id`、`target_type`、`target_id`、`label`、`limit` 和 `offset`；导出支持前四个过滤参数。不指定 `run_id` 时不会自动限制为当前报告。

## 一次 Replay 运行

下面示例不访问外部来源；如已启用认证，给各条 `Invoke-RestMethod` 请求加上 `-Headers $headers`，其中 `$headers` 为 `@{ Authorization = "Bearer $env:SIGNAL_RADAR_API_TOKEN" }`。

### 解析计划

```powershell
$body = @{
  query = "分析 browser-use/browser-use 最近 30 天的版本变化"
  research_mode = "standard"
} | ConvertTo-Json
$planned = Invoke-RestMethod http://localhost:8000/api/plan -Method Post -ContentType "application/json" -Body $body
$planned.plan
```

计划包含 `query`、`project`、`window_days`、`research_mode`、`focus`、`sources`、`urls` 和 `explanation`，默认时间窗口为 30 天。调用方应先确认/调整来源，不应把规则解析结果当作已完成采集。

### 创建并查看运行

```powershell
$body = @{
  mode = "replay"
  project = $planned.plan.project
  window_days = $planned.plan.window_days
  sources = $planned.plan.sources
} | ConvertTo-Json -Depth 5
$started = Invoke-RestMethod http://localhost:8000/api/runs -Method Post -ContentType "application/json" -Body $body
$runId = $started.run.run_id
Invoke-RestMethod "http://localhost:8000/api/runs/$runId"
Invoke-RestMethod "http://localhost:8000/api/runs/$runId/trace"
```

202 表示任务已被接受，未必已有最终报告。可订阅 `/stream` 或查询详情，直到状态变成 `completed`、`partial`、`failed` 或 `cancelled`。

## 请求与响应契约

### RunRequest

| 字段 | 默认值 / 约束 | 说明 |
| --- | --- | --- |
| `mode` | `replay` / `live` | Replay 使用固定输入；Live 发起外部请求 |
| `project` | 可选 | GitHub `owner/repository`；兼容 `repository` / `subject` |
| `query` | 可选，最多 4000 字符 | 研究问题 |
| `window_days` | 7，1–3650 | 采集时间窗口；不同于计划接口默认值 |
| `limit` | 20，1–100 | 来源采集条数上限，不保证获得足额记录 |
| `research_mode` | `standard` | `quick` / `standard` / `deep` |
| `focus` | 数组，最多 12 项 | 关注主题 |
| `sources` | `["github"]` | 来源标识，见[使用指南](USAGE.md#选择数据来源) |
| `urls` / `feed_urls` | 空数组 | 动态页面 / RSS 目标 |
| `community_query` / `reddit_query` / `stackoverflow_query` | 可选 | 对应来源查询词 |
| `max_steps` | 可选，1–40 | Browser Use 步数；受服务端上限约束 |
| `timeout_seconds` | 可选，>0 且 ≤900 | 运行预算；受服务端配置约束 |
| `budget` | 可选对象 | 结构化 `max_steps` / `timeout_seconds` |
| `run_id` | 可选，1–80 字符 | 提前指定追踪 ID；重复 ID 返回 409 |
| `fixture` | 可选 | Replay 输入，受路径限制 |

`RunResponse` 包含 `run` 和可空的 `report`。`run` 保存状态、项目、时间、预算、来源状态与取消标志；状态包括 `queued`、`running` 和上述终态。

### Report

| 字段 | 内容 |
| --- | --- |
| `report_id` / `run_id` / `generated_at` | 报告身份与生成时间 |
| `project` / `window_days` | 项目信息与时间窗口 |
| `summary` | 风险、事件数、覆盖、来源数等汇总 |
| `articles` / `claims` / `events` | 采集文章、观点与事件 |
| `topics` / `trends` | 主题与时间聚合 |
| `evidence` | URL、标题、摘录、发布时间、哈希、置信度与证据等级 |
| `sources` | 完成/部分成功/失败、访问状态、延迟、分页与增量指标 |
| `access_status` | 公开可读、需要授权、受阻或仅元数据等访问边界 |

证据等级为 `full_text`、`excerpt`、`metadata_only` 或 `none`；无法核验的正文/日期不应被猜测补齐。分数不是事实准确率或对模型表现的评测结论。

SSE 帧包含 `event: <type>` 与 JSON `data:`。事件记录阶段、来源、状态、数量、时间和指标，不承载内部思维链。

## 人工标注

`target_type` 可选 `evidence`、`claim`、`event`；工作台当前主要提供证据标注。

| 维度 | 接受的判断 |
| --- | --- |
| `correctness` | `correct`、`partially_correct`、`incorrect`、`uncertain` |
| `risk` | `low`、`medium`、`high`、`critical` |
| `stance` | `support`、`supportive`、`oppose`、`against`、`uncertain`、`mixed`、`neutral` |

```json
{
  "run_id": "replace-with-an-existing-run-id",
  "target_type": "evidence",
  "target_id": "replace-with-an-evidence-id-from-that-report",
  "label": "correctness",
  "value": "correct",
  "note": "原文能够直接支持该引用。",
  "reviewer": "local-reviewer"
}
```

服务端校验目标是否存在于相应报告；不存在返回 422，没有报告的运行不能标注。省略 `run_id` 时校验当前报告/Replay fixture，不形成持久化运行归属。标注 ID 和时间可由服务端生成，重复 ID 返回 409。JSON 标注集可输入[离线评测](EVALUATION.md#使用人工标注)。

调度 `SchedulerRequest` 使用 `request: RunRequest`、`interval_seconds`、`max_runs` 和 `run_immediately`；状态与边界见[定时监控](USAGE.md#定时监控)。
