# 更新记录

## Unreleased

- 增加 RSS/Atom 官方博客来源，支持时间窗口、去重、响应大小限制和显式失败状态；
- 增加 CLI/API 的多来源编排、RSS feed 参数和窗口过滤；
- 使用 browser-use 原生 `ChatDeepSeek` wrapper，保留 OpenAI-compatible fallback；
- 锁定 FastAPI 与 Starlette 的兼容范围，并补充 Live 集成测试。

## 0.1.0 - 2026-09-29

- 增加 GitHub Releases/Issues 只读采集器；
- 增加 Browser Use 动态页面适配器和域名白名单；
- 增加 Pydantic 报告契约、证据引用、确定性风险评分和 Replay fixture；
- 增加 FastAPI 服务、CLI、本地 Dashboard、Docker 配置和离线测试；
- 增加 DeepSeek OpenAI-compatible 配置示例。
