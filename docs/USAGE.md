# 使用指南

[返回 README](../README.md) · [API 参考](API.md) · [离线评测](EVALUATION.md)

本页补充 README 的快速开始，说明如何选择来源、配置实时采集和使用本地工作流。所有命令默认在仓库根目录、已激活的 Python 虚拟环境中执行。

## 配置文件

首次配置时，从 [.env.example](../.env.example) 创建本地 `.env`；**已有 `.env` 时不要覆盖**。API 和 CLI 在安装 `python-dotenv` 后会加载该文件，进程环境变量优先。

以下为 Windows PowerShell 命令，仅在 `.env` 尚不存在时执行：

```powershell
Copy-Item .env.example .env
```

模型 Key、GitHub Token、API Bearer Token 的用途不同：

| 配置 | 用途 | 是否必需 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` | Browser Use 调用模型 | 动态浏览器路径需要 |
| `GITHUB_TOKEN` | GitHub API 身份与配额 | 公开仓库查询可不填 |
| `SIGNAL_RADAR_API_TOKEN` | 保护本项目的运行、历史、调度与标注接口 | 共享部署应配置 |

这些凭据不能互换。React 设置抽屉接收的是本项目 API Token，不是模型 Key 或 GitHub Token。

## 选择数据来源

CLI 的 `--source` 可重复指定；API 使用 `sources` 数组。自然语言计划默认展示 GitHub、RSS、Hacker News、Reddit 和 Stack Overflow，最终以计划预览为准，可在运行前关闭不需要的来源。没有 feed、OAuth 或 Browser Use 配置时，运行会保留 `disabled` / `blocked` / `auth_required` 状态和配置提示，不会把该来源伪装成成功。

| 来源标识 | 说明 |
| --- | --- |
| `github` | GitHub Releases / Issues |
| `github_prs` | 按需 Pull Request 元数据 |
| `github_discussions` | 按需 Discussions；仓库未开放时记录失败 |
| `github_pr_comments` | 按需 PR review comments，不等同于所有 PR 对话 |
| `rss` | RSS 2.0 / Atom，指定 feed URL |
| `hackernews` | Hacker News Algolia；`community` 是其别名 |
| `reddit` | Reddit 公共 JSON；403 时可用只读 OAuth 配置启用 |
| `stackoverflow` | Stack Exchange API，默认 Stack Overflow 站点 |
| `browser_use` | Browser Use 对白名单内各公开 URL 启动独立只读任务，再聚合去重；单次默认上限 60 条 |

### GitHub 与社区查询

```sh
python -m signal_radar --mode live --project browser-use/browser-use --source github --days 30 --limit 100
python -m signal_radar --mode live --project browser-use/browser-use --source github_prs --days 30 --limit 10
python -m signal_radar --mode live --project browser-use/browser-use --source hackernews --community-query "browser-use" --days 30 --limit 10
python -m signal_radar --mode live --project browser-use/browser-use --source reddit --reddit-query "browser-use" --days 30 --limit 10
python -m signal_radar --mode live --project browser-use/browser-use --source stackoverflow --stackoverflow-query "browser-use timeout" --days 30 --limit 10
```

这些命令实际访问网络，但不需要模型 Key。API 同样接受 `community_query`、`reddit_query` 和 `stackoverflow_query`。

`--limit` 是每个来源的总记录上限，范围为 1-500。适配器按公开 API 的单页上限分页；最终条数仍受时间窗口、来源配额和可访问性影响。工作台同样提供“每个来源记录上限”输入，默认 100。

### Reddit 访问配置

Reddit 默认尝试公开 JSON。若运行结果为 `blocked / HTTP 403`，可在本机 `.env` 填写 Reddit 只读脚本应用的 OAuth client credentials，服务会自动获取短期 token：

```dotenv
SIGNAL_RADAR_REDDIT_CLIENT_ID=your-client-id
SIGNAL_RADAR_REDDIT_CLIENT_SECRET=your-client-secret
SIGNAL_RADAR_REDDIT_USER_AGENT=signal-radar/0.3 (by /u/your-account)
```

凭据只用于访问 OAuth API，不会写入报告、能力探针、截图或日志。创建应用仍需遵守 Reddit 的服务条款；凭据无效时会显示 `auth_required`，不会重试或绕过访问策略。

### RSS / Atom

`--feed-url` 可重复指定。下面的域名只是格式示例，请替换为实际可访问的 feed：

```sh
python -m signal_radar --mode live --project browser-use/browser-use --source rss --feed-url https://example.com/feed.xml --days 30 --limit 10
```

API 可在单次请求中设置 `feed_urls`；工作台使用的默认 feed 来自 `SIGNAL_RADAR_RSS_FEEDS`，多个 URL 用逗号分隔。只读取公开 feed，不需要启动浏览器。未填写 URL 时显示 `disabled / not_configured`，失败、超时、无正文和不可访问情况也都会保留来源状态。

### 明确的页面 URL

计划会保留研究问题中明确粘贴的公开 `http(s)` URL。GitHub 仓库根链接仍优先走 API；具体 Issue/PR/Discussion 或非 GitHub 页面可能增加 Browser Use 待确认来源。

添加 URL 不代表已授权访问：动态路径仍校验域名白名单、显式开关、模型配置和本地会话授权，不绕过登录或访问限制。

## Browser Use 动态网页

该路径默认关闭，不影响 Replay 和结构化 API 来源。

### 依赖与模型

```sh
python -m pip install -e ".[api,live]"
```

还需要该 Browser Use 版本支持的 Chromium 环境；浏览器安装与系统依赖参照 [Browser Use 官方文档](https://docs.browser-use.com)。项目当前依赖范围见 [pyproject.toml](../pyproject.toml)，不要把最新上游版本的行为直接当成本项目的兼容承诺。

Browser Use 为每个已允许 URL 创建独立 Agent 任务并行运行，避免多个页面记录挤在单个结构化模型响应中。每页最多收集 20 条，单次默认最多 60 条、最多处理 20 个 URL；来源状态中的 `latency_ms` 为各页面任务耗时合计。对每个页面仍受单次运行总 deadline、步数上限、域名白名单和只读指令约束。

DeepSeek 配置示例，写入未提交的本地 `.env`：

```dotenv
MODEL_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
SIGNAL_RADAR_BROWSER_ENABLED=true
SIGNAL_RADAR_BROWSER_RUN_LIVE=true
SIGNAL_RADAR_BROWSER_ALLOWED_DOMAINS=github.com
SIGNAL_RADAR_BROWSER_MAX_STEPS=20
SIGNAL_RADAR_BROWSER_TIMEOUT_SECONDS=180
SIGNAL_RADAR_BROWSER_MAX_RECORDS=60
ANONYMIZED_TELEMETRY=false
```

使用 OpenAI 时配置 `MODEL_PROVIDER=openai`、`OPENAI_API_KEY` 和 `OPENAI_MODEL`。修改配置后重启 API。

CLI 也要求两个显式开关，并重复指定目标 URL：

```powershell
python -m signal_radar --mode live --project browser-use/browser-use --source browser_use `
  --url https://github.com/browser-use/browser-use/discussions `
  --enable-browser-use --browser-run-live --max-steps 6 --timeout-seconds 60 --limit 5
```

### 本地授权 Profile

需要读取已有登录态时，用户先在本机完成登录，然后显式授权本地 Profile。以下路径仅为示例，必须替换为已有的绝对目录，并将目标站点加入允许的域名：

```dotenv
SIGNAL_RADAR_BROWSER_AUTHORIZED=true
SIGNAL_RADAR_BROWSER_PROFILE_DIR=C:\Users\you\AppData\Local\Google\Chrome\User Data
SIGNAL_RADAR_BROWSER_PROFILE_NAME=Default
```

系统不自动登录、不提交、不点赞、不评论；不绕过验证码或付费墙。能力接口不返回 Profile 路径、Cookie 或模型密钥。不要把个人 Profile 挂载到共享/公网服务。

动态抽取只有在标记为可见原文时才保留摘录；没有明确观察到的日期不作为发布时间使用。不可信网页中的指令不能改变任务策略。详见 [SECURITY.md](../SECURITY.md)。

## 缓存、分页与预算

| 配置 | API 默认值 / 用途 |
| --- | --- |
| `SIGNAL_RADAR_CACHE_ENABLED` | `true`，启用本地增量元数据缓存 |
| `SIGNAL_RADAR_CACHE_DB` | `data/source-cache.sqlite3` |
| `SIGNAL_RADAR_HISTORY_DB` | `data/runs.sqlite3`，保存运行、报告、事件与标注 |
| `SIGNAL_RADAR_GITHUB_MAX_PAGES` | 10 页 |
| `SIGNAL_RADAR_HACKERNEWS_MAX_PAGES` | 10 页 |
| `SIGNAL_RADAR_REDDIT_MAX_PAGES` | 10 页 |
| `SIGNAL_RADAR_STACKEXCHANGE_MAX_PAGES` | 10 页 |
| `SIGNAL_RADAR_BROWSER_MAX_STEPS` | 20 步，服务端默认值（最大 40） |
| `SIGNAL_RADAR_BROWSER_TIMEOUT_SECONDS` | 180 秒，服务端浏览器预算 |
| `SIGNAL_RADAR_BROWSER_MAX_RECORDS` | 60 条动态记录默认上限（最大 100；每页最多 20 条） |

元数据缓存保存 ETag、Last-Modified、响应摘要和记录指纹，不保存 Cookie 或模型密钥。运行报告中的必要摘录则保存在历史库。重复运行仍保留当前时间窗口的报告快照，不把“没有新增”显示成“没有证据”。缓存命中也不会直接跳过所有后续页。

每个来源记录 `pages`、`latency_ms`、`cache_hit`、`new_records`、`duplicate_records` 和 `total_candidates` 等指标；`GET /api/metrics` 汇总当前与最近历史运行。

单次 API 请求可传入更小的 `max_steps` / `timeout_seconds`。取消是协作式的：同步 HTTP 请求不能被线程外硬中断；已完成来源可形成部分结果。Browser Use 路径支持超时与协作取消。没有针对所有真实站点的延迟或成功率保证。

## 定时监控

工作台的“本地设置”抽屉可设置间隔、最大次数和是否立即运行，并查看状态或停止。建议先在主页面解析并确认研究计划与来源，再启动监控。Live 每次运行都可能产生外部调用或费用。

调度默认关闭；`SIGNAL_RADAR_SCHEDULER_*` 变量只是默认参数，不会因导入 API 自动启动。当前只支持单个进程内计划，服务重启后不自动恢复。计划运行产生的报告与事件会持久化到历史库。

API 示例使用 Replay，不访问外部来源；已配置 API Token 时请求必须携带认证头，见 [API 参考](API.md#认证)：

```powershell
$payload = @{
  request = @{ mode = "replay"; project = "browser-use/browser-use"; sources = @("github"); window_days = 7; limit = 10 }
  interval_seconds = 3600
  max_runs = 2
  run_immediately = $false
} | ConvertTo-Json -Depth 5
Invoke-RestMethod http://localhost:8000/api/schedule -Method Post -ContentType "application/json" -Body $payload
Invoke-RestMethod http://localhost:8000/api/schedule
Invoke-RestMethod http://localhost:8000/api/schedule/stop -Method Post
```

API 间隔为 1 秒到 24 小时，最多 1000 次运行、8 个来源和各 20 个 URL/feed。支持 GitHub、RSS、Hacker News、Reddit、Stack Exchange、Browser Use 及受支持别名。

CLI 示例，最多执行一次且立即运行：

```sh
python -m signal_radar --mode replay --fixture fixtures/demo_report.json --schedule --schedule-interval 60 --schedule-max-runs 1 --schedule-run-immediately
```

## 历史、导出与人工复核

- 选择历史运行恢复报告和 Trace；工作台当前历史列表最多加载 20 条，搜索/筛选作用于已加载列表。
- 持久化运行支持 Markdown 和 JSON 导出。首屏固定 Replay 预览支持 JSON 快照，Markdown 需先完成运行或选择历史记录。
- 完成一次运行后可发起限定范围补查，不是无限制自动全网追问。
- 证据卡片支持正确性、风险、立场标注和备注；API 还接受 `claim` / `event` 标注目标。
- 标注独立保存，不改写报告；导出 JSON 可用于[离线评测](EVALUATION.md#使用人工标注)。没有持久化运行上下文时，前端标注/导出不使用运行过滤，不能把它当作严格的单报告隔离视图。

历史库、缓存、报告和标注可能包含使用记录或个人信息，不应提交到公开仓库。共享环境的复核接口与历史接口都受 API Token 保护。

## 部署与回退入口

### 构建后的单服务

完成 `npm --prefix frontend run build` 后，FastAPI 在 `/` 提供 React 产物；后端继续提供 `/api` 和 `/docs`。Live 应运行在能访问模型和浏览器的本地/受控服务中，不应把个人会话放入公开 Serverless 环境。

共享部署设置高熵 `SIGNAL_RADAR_API_TOKEN` 和 `SIGNAL_RADAR_CORS_ORIGINS`，并增加 HTTPS、网络隔离和限流。部分只读报告/观测接口仍公开，具体清单见 [API 认证](API.md#认证)。

### Docker Replay

[Dockerfile](../Dockerfile) 构建 React 与 Replay API，不安装 Live 浏览器依赖。[Compose 配置](../docker-compose.yml) 使用只读根文件系统和 `/tmp` tmpfs；启动前须创建本地 `.env` 并将数据库指向可写目录：

```dotenv
SIGNAL_RADAR_HISTORY_DB=/tmp/runs.sqlite3
SIGNAL_RADAR_CACHE_DB=/tmp/source-cache.sqlite3
```

```sh
docker compose up --build
```

访问 [http://localhost:8000](http://localhost:8000)。这两条 `/tmp` 配置适合临时 Replay 体验，容器停止后数据可能丢失；需要持久化时另行配置可写数据卷和对应数据库路径，不能直接将当前示例当作生产部署。

### 旧版静态页面

`web/` 是兼容回退，不是主要前端。保留 API 服务，另一个终端执行：

```sh
python -m http.server 4173 --directory web
```

打开 [旧版 Replay 入口](http://localhost:4173/?api=http%3A%2F%2Flocalhost%3A8000)。静态页面本身不运行 Python Agent 或浏览器。

## 故障排查

| 现象 | 检查与处理 |
| --- | --- |
| 浏览器打开 8000 没有 React 工作台 | 先构建 `frontend/dist`，再重启 API；开发时使用 Vite 4174 |
| `python` 不可用或导入包失败 | 使用真实 Python 3.11+，激活 `.venv`，通过 `python -m pip` 安装对应 extras |
| `dependency_missing` | 选择结构化来源，或安装 `.[api,live]` 并检查 Browser Use 的浏览器/系统依赖；该状态不是测试必须失败的理由 |
| `disabled` / `dry_run` | 检查 Browser Use 两个显式开关，修改后重启 API |
| `allowlist_missing` / `domain_not_allowed` | 仅将明确授权访问的目标域名加入白名单，不放宽为任意域名 |
| `auth_required` / Profile 错误 | 检查模型配置；登录态路径需已有绝对目录及明确授权，不自动登录 |
| HTTP 401 | 请求需本项目 Bearer Token；不是 DeepSeek Key，也不是 GitHub Token |
| 调度 HTTP 409 / 422 | 已有计划时先停止；检查来源支持范围、最多 8 个来源及数字上限 |
| 报告部分成功 / 限流 | 查看来源状态和 Trace，调整查询范围或重试时间，不推断不可见正文 |
| Markdown 导出不可用 | 首屏是固定预览，先完成 Replay 或选择带报告的历史运行 |

健康状态可用 `GET /api/health` 查看；Browser Use 配置与访问上限可用 `GET /api/capabilities` 查看，这两个接口不会返回模型密钥。
