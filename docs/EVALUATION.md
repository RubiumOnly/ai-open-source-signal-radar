# 离线评测

[返回 README](../README.md) · [使用指南](USAGE.md) · [API 参考](API.md)

项目的基础评测使用固定 JSON/HTML fixture，不调用真实模型、浏览器或外部来源。它验证报告契约、一致性和安全降级，**不能替代真实站点成功率、模型准确性或 Live 性能测试**。

## 开发回归

安装 `.[dev,api]` 后，在仓库根目录执行：

```sh
python -m pytest -q
python -m unittest discover -s tests -q
python -m compileall -q signal_radar tests
python -m pip check
npm --prefix frontend run build
```

测试通过 fake 适配器隔离可选 Browser Use 依赖，不要求安装 `.[live]`。GitHub Actions 在 Python 3.11/3.12 和 Node 22 上运行相应验证。

## 报告评测

```sh
python -m signal_radar.evaluate --fixture fixtures/demo_report.json --output reports/evaluation-demo.json
```

评测读取 `Report`，也接受包含 `report` 对象的运行详情导出。输出 JSON 的主要字段：

| 字段 | 检查内容 |
| --- | --- |
| `schema_valid` / `schema_errors` | 结构契约及输入错误 |
| `citation_coverage` | 引用是否指向有效证据 |
| `source_coverage` | 来源可用性与覆盖 |
| `event_consistency` | 事件计数和汇总一致性 |
| `performance` | 报告中已记录的来源延迟、分页与增量指标 |
| `security` | 未授权动作计数与仅元数据摘录规则 |
| `annotations` | 人工标注目标覆盖与孤立记录 |
| `checks` / `passed` | 各检查项与整体结果 |

**请读取 `passed`，不要只看命令退出码。** 当前 evaluator 的非零退出主要表示报告无法通过 schema 解析；结构正确但内容一致性失败时，命令仍可能退出 0。CI 会额外断言 JSON 中的 `passed`：

```sh
python -c "import json; from pathlib import Path; result=json.loads(Path('reports/evaluation-demo.json').read_text(encoding='utf-8')); assert result['passed'], result['checks']"
```

`schema_valid=true` 仅说明结构可解析，不等于结论真实。`fixtures/evaluation_inconsistent.json` 用于验证一致性失败检测；错误输入不会静默替换成演示报告。

## 安全门禁

- `unauthorized_action_target` 目标为 0。
- `metadata_only` 证据必须没有正文摘录。
- 安全检查不通过时，输出中的 `passed` 为 `false`。

固定注入样例为 [prompt_injection_browser_use.json](../fixtures/prompt_injection_browser_use.json)，可执行相关回归：

```sh
python -m unittest tests.test_prompt_injection_security -v
```

这验证代码与 fixture 的策略边界，不构成“所有真实网页均无法注入”的保证。

## 使用人工标注

从工作台或 `/api/annotations/export.json` 获取与目标报告匹配的标注数组，保存在本地 `reports/annotations.json` 后执行：

```sh
python -m signal_radar.evaluate --fixture fixtures/demo_report.json --annotations reports/annotations.json --output reports/evaluation-with-annotations.json
```

换成 Live/历史报告时，应同时替换 fixture 和匹配的标注文件，不把不同报告的复核记录混用。复核记录独立于原始报告，可能包含个人信息，不应提交公开仓库。

## 离线基准

```sh
python -m signal_radar.benchmark --fixture fixtures/prompt_injection_browser_use.json --fixture fixtures/demo_report.json --output reports/benchmark.json
```

输出包含每个 fixture 的检查结果、总体通过率，以及评测耗时的最小值、均值、P95 和最大值。该耗时是**本地评测执行耗时**，不是网页抓取延迟、Token 成本或模型响应性能；这些 Live 指标需要另行采集。
