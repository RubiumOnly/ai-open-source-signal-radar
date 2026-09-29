# 贡献指南

感谢参与 Signal Radar。项目欢迎新增数据源、改进报告契约、补充离线 fixture 和完善安全边界。

## 本地开发

要求 Python 3.11 或更高版本：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e ".[dev,api]"
```

需要真实浏览器采集时，再安装 `.[live]`。模型 Key 和 GitHub Token 只能通过本地环境变量或未提交的 `.env` 注入。

## 验证

提交前运行：

```powershell
python -m pytest -q
python -m unittest discover -s tests -v
python -m compileall -q signal_radar tests
```

新增采集器时，应优先增加无网络 fixture 测试，并覆盖超时、限流、登录要求、域名白名单和部分成功等状态。

## 数据源约定

采集器只能访问明确允许的域名和公开内容。不要绕过验证码、付费墙或访问控制；需要登录的来源必须由用户在本地完成授权，并保持只读操作。

## 提交变更

- 保持改动聚焦，避免把格式化和无关重构混入功能提交；
- 更新面向用户的 README 或配置说明；
- 不提交 `.env`、浏览器 Profile、Cookie、截图中的个人信息或真实 Token；
- 提交消息使用清晰的动词，例如 `feat: add rss source adapter`、`fix: preserve blocked source status`。
