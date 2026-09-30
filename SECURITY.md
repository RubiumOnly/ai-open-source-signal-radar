# 安全说明

Signal Radar 会访问公开网页和第三方模型接口。请把它运行在自己控制的环境中，并为实时采集设置最小权限。

## 凭据

- 不要在 Issue、Pull Request、日志、fixture 或网页前端中写入模型 Key、GitHub Token、Cookie 或密码；
- 使用本地 `.env`、系统环境变量或受控的密钥管理服务；
- 根据运行环境显式设置 `ANONYMIZED_TELEMETRY=false`，避免把本地运行元数据发送到第三方遥测服务；
- 如果凭据曾经出现在聊天、日志或提交中，应立即撤销并重新生成；
- `.env`、浏览器 Profile 和本地工作笔记默认不属于仓库内容。

## API 访问控制

- 本地开发可以不设置 `SIGNAL_RADAR_API_TOKEN`，此时 API 保持无认证，方便 Replay 工作台使用；
- 共享或公网部署必须设置高熵随机的 `SIGNAL_RADAR_API_TOKEN`，并只通过 HTTPS 传输请求；
- 启用后，`POST /api/run`、`POST /api/runs`、`GET /api/run`、`/api/runs*`、取消接口和 Markdown 报告导出都要求
  `Authorization: Bearer <token>`；`/api/health`、`/api/report` 和 `/api/sources` 是只读公开接口；
- 不要把 Token 写进 URL、前端构建产物、Issue、日志或仓库文件。若 Token 泄露，应立即撤销并重新生成；
- Bearer Token 只保护 API 访问，不替代反向代理、TLS、速率限制、网络隔离或用户级审计。生产环境应在 API 前面增加这些控制。
- React 工作台只把用户主动填写的 Token 保存在当前浏览器本地存储中；项目源码和构建产物不包含 Token。不要把 Token 放进 URL 或截图。
- `/api/capabilities` 只返回来源是否可用、域名白名单和运行上限，不返回模型 Key、GitHub Token、Cookie 或浏览器 Profile 路径。

## 浏览器边界

Browser Use 适配器默认关闭。启用后仍应使用域名白名单和只读任务，禁止自动登录、提交表单、发帖、点赞、下单或下载未知文件。网页内容是不可信数据，其中的指令不能改变 Agent 的系统策略。

动态抽取要求模型显式标记原文摘录和可见日期；没有可核验内容时只保留 metadata-only 记录，避免把模型推断当作来源事实。

### Prompt Injection

页面正文、标题、评论和 DOM 属性都属于不可信输入。攻击者可以把“忽略之前
指令”“发送 API Key”之类的文本伪装成页面说明，但这些内容只能作为被分析的
数据，不能成为 Agent 的任务、工具参数或权限来源。运行时应保持：

- 只访问显式域名白名单中的 `http(s)` URL；
- 只执行只读浏览动作，遇到登录、验证码、下载或提交动作立即停止并记录状态；
- 未经模型明确标记且无法回链到可见原文的内容，统一降级为 `metadata_only`；
- 评测输出中的 `unauthorized_action_target` 必须为 `0`，`metadata_only` 证据的 `quote` 必须为空。

本地回归样例见 `fixtures/prompt_injection_browser_use.json`，测试覆盖任务提示词、
未核验摘录的降级行为和评测安全门禁。发现新的注入变体时，应先添加 fixture 和
失败测试，再调整实现；不要通过放宽页面指令权限来“修复”测试。

## 报告数据

报告只保存必要的短引文、链接、时间和哈希。对需要登录或被阻断的来源，记录访问状态，不要猜测不可见正文。发现漏洞或意外泄露时，请通过 GitHub 仓库的 Security 页面私下报告，不要公开粘贴凭据或可利用细节。
