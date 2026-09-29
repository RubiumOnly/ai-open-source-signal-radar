# AI 开源项目社区反馈与版本风险雷达

这是一个基于 `browser-use` 的可审计研究型 Agent：它围绕一个 AI 开源项目，汇总版本事实、开发者社区反馈和公开技术文章，生成带证据引用的风险报告。

项目的目标不是宣称“抓取全网舆情”，也不是把 GitHub 代码当成舆情。GitHub Issues、Discussions、Pull Requests 和 Releases 代表项目参与者的反馈与维护状态；外部网站用于补充使用体验。报告会区分**事实、观点、统计信号和推断**，并在证据不足时明确标注不确定性。

## 典型使用场景

输入一个项目和时间窗口，例如：

```text
项目：browser-use/browser-use
时间窗口：最近 30 天
```

输出包含：

- 版本和事件时间线；
- 安装、兼容性、性能、安全和维护响应等主题；
- 支持、反对或不确定的开发者立场；
- 风险等级、风险分数和覆盖率；
- 每个结论对应的 URL、标题、发布时间和原文摘录；
- 来源访问状态、需要人工复核的内容，以及运行追踪信息。

## 架构

```text
Dashboard / API
      |
Run Orchestrator  -- 运行 ID、预算、重试、超时、只读策略
      |
来源适配器：GitHub API / Replay fixture / Browser Use 动态网页（RSS 和官方博客可继续扩展）
      |
Browser Use 回退：动态页面、跨页上下文、授权后的本地会话
      |
结构化抽取：事件、主题、立场、证据、访问状态
      |
去重与聚类  ->  风险评分  ->  JSON / Markdown 报告
      |
Trace、URL、截图、失败记录、评测指标
```

**Browser Use 的职责**是规划和执行需要浏览器的只读动作，例如展开动态 Discussions、跨页面寻找上下文、在用户确认后浏览登录态页面。稳定的结构化数据优先使用 GitHub API、RSS 或普通 HTTP 请求，避免让浏览器承担可以确定性完成的工作。领域模型、去重、评分和报告契约由本项目实现。

## 数据来源与边界

每个来源都会归类为：

| 状态 | 处理方式 |
| --- | --- |
| `public` | API、RSS 或公开 HTML，直接读取并保存证据 |
| `dynamic` | 公开但依赖 JavaScript，使用 `browser-use` 只读浏览 |
| `auth_required` | 需要登录，优先使用官方 API；否则本地浏览器复用会话并要求人工确认 |
| `blocked` | 验证码、频率限制、robots 或服务条款阻断，记录原因并切换来源 |
| `metadata_only` | 只能取得标题/时间等元数据，不把它伪装成正文证据 |

CSDN、知乎等登录或反爬平台不是 MVP 的硬依赖。Agent 不绕过验证码、付费墙或访问控制，不保存账号密码和 Cookie，不自动点赞、评论、发帖或下单。Live 模式的浏览器会话应留在用户机器上；后端只接收经过筛选的证据。

## 快速开始

要求 Python 3.11+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev,api]"
Copy-Item .env.example .env
```

### Replay 模式（无需网络和 API Key）

Replay 使用 `fixtures/demo_report.json`，适合仓库内本地演示、首次体验和离线测试：

```powershell
python -m signal_radar --mode replay --fixture fixtures/demo_report.json
```

如果项目提供 Web 前端，可将生成的 JSON 放入静态报告目录；Replay 不会启动浏览器，也不会读取任何密钥。

### Live 模式

Live CLI 会读取 GitHub 的公开 Releases 和 Issues；GitHub Token 不是必需项，但可用于提高 API 速率限制。需要 Browser Use 动态采集时，请使用后面的 API 配置，并在本地或受控后端运行：

```powershell
python -m signal_radar --mode live --project browser-use/browser-use --days 30 --source github
```

具体命令以 `python -m signal_radar --help` 为准。没有凭据时请使用 Replay，不要把 Key 写入前端或提交到 Git。

启动本地 API（供 `web/` Dashboard 使用）：

```powershell
pip install -e ".[dev,api]"
uvicorn signal_radar.api:app --reload --port 8000 --env-file .env
```

另开一个终端提供本地 Dashboard；不带 `api` 参数时页面仍会自动回退到 Replay fixture：

```powershell
python -m http.server 4173 --directory web
```

打开 `http://localhost:4173/?api=http%3A%2F%2Flocalhost%3A8000`，即可让 Dashboard 请求本地 API。`api` 查询参数只用于本地演示，生产部署时应通过同源反向代理或显式配置 `window.SIGNAL_RADAR_API_BASE`。

Browser Use 动态采集是显式开启的可选路径。先安装额外依赖，在本地环境变量中配置模型 Key，
再把 `SIGNAL_RADAR_BROWSER_ENABLED` 和 `SIGNAL_RADAR_BROWSER_RUN_LIVE` 都设为 `true`：

```powershell
pip install -e ".[api,live]"
$env:DEEPSEEK_API_KEY = "your-key"  # 只在本机 .env 或环境变量中设置
$env:MODEL_PROVIDER = "deepseek"
$env:DEEPSEEK_MODEL = "deepseek-chat"
$env:SIGNAL_RADAR_BROWSER_ENABLED = "true"
$env:SIGNAL_RADAR_BROWSER_RUN_LIVE = "true"
uvicorn signal_radar.api:app --port 8000
```

然后只向白名单 URL 发起只读运行；页面中的指令不会被当作系统指令执行：

```powershell
Invoke-RestMethod http://localhost:8000/api/run -Method Post -ContentType "application/json" -Body (@{
  mode = "live"
  project = "browser-use/browser-use"
  sources = @("github", "browser_use")
  urls = @("https://github.com/browser-use/browser-use/discussions")
  window_days = 30
  limit = 5
} | ConvertTo-Json)
```

也可以使用 CLI 触发动态来源。必须同时显式开启适配器和实时执行，并重复传入需要访问的 URL：

```powershell
python -m signal_radar --mode live --project browser-use/browser-use --source browser_use `
  --url https://github.com/browser-use/browser-use/discussions `
  --enable-browser-use --browser-run-live --limit 5
```

本地运行时请将 `.env` 中的变量导出到当前进程（或安装 `.[live]` 后由应用加载）；Docker Compose 会通过 `env_file` 自动传入这些变量。

## 报告契约

`Report` 顶层字段如下：

```text
project{name, repository, description, version, last_release_at}
generated_at
window_days
summary{risk_score, risk_level, events_count, source_count, coverage_pct}
trends[]
topics[]
evidence[]
sources[]
access_status[]
```

证据至少包含 `id`、`url`、`title`、`source`、`published_at`、`quote`、`content_hash`、`confidence`。引用只对保存过的原文片段负责；系统不会因为某个页面不可访问就猜测正文。

## 评测

评测集使用固定 HTML/JSON fixture，不依赖网络。建议持续记录：

- 结构化输出通过率和字段完整率；
- URL、内容哈希和事件去重准确率；
- 事件/主题/立场分类的 Precision、Recall、F1；
- 引用支持率和来源覆盖率；
- 任务成功率、恢复率、P95 延迟和 Token 成本；
- 未授权动作次数（目标为 0）。

运行本地契约测试：

```powershell
python -m unittest discover -s tests -v
```

若已安装开发依赖，也可以运行：

```powershell
pytest -q
```

## 运行、展示与部署

GitHub 仓库包含完整源码、测试、fixture 和运行文档。推荐先运行 Replay，再切换到 Live；`web/` 是仓库内的本地 Dashboard，不承担 Python Agent、Chromium 或 API Key。

推荐拆分为：

1. 本地或受控云服务：Live API、模型调用和 Browser Use；
2. `web/`：可选的静态 Dashboard，默认加载 `fixtures/demo_report.json`；
3. 如需对外展示，可单独托管 `web/` 的 Replay 静态文件，但这不是项目运行前提；
4. 前端通过环境变量配置后端地址，绝不把模型 Key 放进浏览器包。

建议按以下顺序启动一个本地运行：

```text
git clone <repo>
pip install -e ".[dev]"
python -m signal_radar --mode replay --fixture fixtures/demo_report.json
```

随后可以查看源码、契约测试、fixture、架构图，以及 Live 模式的模型、浏览器会话、权限边界和失败降级。Live 运行应明确提示网络、成本和来源访问限制。

### Docker（Replay API）

仓库附带最小 API 镜像，默认只提供 Replay，不安装浏览器，也不需要模型 Key：

```powershell
Copy-Item .env.example .env
docker compose up --build
```

服务默认监听 `http://localhost:8000`。Live 部署应单独构建受控镜像并安装 `.[live]`，同时配置域名白名单、资源预算和人工确认策略；不要把宿主机 Chrome Cookie 挂载到公共服务。

主要 API：`GET /api/health` 检查服务，`GET /api/report` 读取当前报告，`GET /api/sources` 查看来源状态，`POST /api/run` 启动 Replay 或 Live 运行。接口返回的报告遵循上面的 `Report` 契约。

## 目录约定

```text
signal_radar/       后端编排、来源适配器、报告模型
web/                仓库内本地 Dashboard（可选静态 Replay 展示）
fixtures/           离线演示和评测输入
tests/              不需要网络/API Key 的契约与单元测试
```

## 参与与安全

- 贡献流程见 [`CONTRIBUTING.md`](CONTRIBUTING.md)；
- 凭据、浏览器权限和漏洞报告规则见 [`SECURITY.md`](SECURITY.md)；
- 版本变化见 [`CHANGELOG.md`](CHANGELOG.md)。

## 路线图

- MVP：GitHub Releases/Issues、确定性风险评分和 Browser Use 的显式动态页面适配器；
- 下一步：增加 RSS、官方博客和更多公开社区来源适配器；
- 增加事件聚类、趋势图、证据摘录和运行 Replay；
- 通过本地浏览器会话支持授权来源，并加入人工确认点；
- 增加网页 Prompt Injection 防护、域名白名单、成本预算和失败恢复；
- 建立带人工标注的回归评测集。

## 设计与工程要点

GitHub 是高质量的开发者反馈源，但不等于大众舆情；Browser Use 仅用于需要浏览器的动态任务。API、权限边界、证据引用、失败降级和离线评测共同决定报告是否可信。

