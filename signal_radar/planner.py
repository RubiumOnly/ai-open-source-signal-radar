"""把研究简报解析成可确认的采集计划。

计划解析是确定性的，不调用模型也不联网。它只负责消歧、时间窗口、来源
和主题的初步建议；真正的来源访问仍由运行请求和各自适配器执行。
"""

from __future__ import annotations

import re
import ipaddress
from urllib.parse import urlsplit

from .models import PlanRequest, ResearchPlan


_GITHUB_URL = re.compile(r"https?://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", re.I)
_REPOSITORY = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w.-])")
_WINDOW = re.compile(r"(?:最近|过去|last|past)\s*(\d{1,4})\s*(?:天|日|days?|d)?", re.I)
_URL = re.compile(r"https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+", re.I)

_FOCUS_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("安装与兼容性", ("安装", "兼容", "install", "compat")),
    ("性能与稳定性", ("性能", "稳定", "崩溃", "performance", "crash", "timeout")),
    ("安全与供应链", ("安全", "漏洞", "供应链", "security", "cve")),
    ("维护活跃度", ("维护", "响应", "issue", "release", "提交")),
    ("社区反馈", ("社区", "反馈", "口碑", "讨论", "community", "feedback")),
    ("版本变化", ("版本", "发布", "更新", "release", "changelog")),
)

_SOURCE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("github_discussions", ("github discussion", "github discussions", "github 讨论")),
    ("github_prs", ("pull request", "pull requests", "合并请求", "pr")),
    ("github", ("github", "issue", "issues", "discussion", "discussions", "pull request", "pr")),
    ("rss", ("rss", "atom", "博客", "blog", "官方")),
    ("hackernews", ("hacker news", "hackernews", "hn")),
    ("reddit", ("reddit", "subreddit")),
    ("browser_use", ("浏览器", "动态", "csdn", "知乎", "登录", "browser", "web")),
)


def _normalise_project(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().rstrip("/")
    match = _GITHUB_URL.search(text)
    if match:
        return match.group(1)
    match = _REPOSITORY.search(text)
    return match.group(1) if match else None


def _window_days(query: str, explicit: int | None) -> int:
    if explicit is not None:
        return explicit
    match = _WINDOW.search(query)
    return max(1, min(int(match.group(1)), 3650)) if match else 30


def _focus(query: str) -> list[str]:
    lowered = query.lower()
    found = [label for label, terms in _FOCUS_TERMS if any(term.lower() in lowered for term in terms)]
    return found or ["版本变化", "社区反馈", "维护活跃度"]


def _urls(query: str) -> list[str]:
    """提取用户明确粘贴的页面 URL，交给后续白名单校验。"""

    urls: list[str] = []
    for raw in _URL.findall(query):
        value = raw.rstrip(".,;!?)]}，。；！？）】")
        try:
            parsed = urlsplit(value)
            hostname = parsed.hostname
            if parsed.scheme not in {"http", "https"} or not hostname or parsed.username or parsed.password:
                continue
            lowered = hostname.rstrip(".").lower()
            if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
                continue
            try:
                address = ipaddress.ip_address(lowered)
            except ValueError:
                address = None
            if address is not None and (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved):
                continue
        except ValueError:
            continue
        if value not in urls:
            urls.append(value)
    return urls[:20]


def _needs_browser_url(url: str) -> bool:
    """判断明确 URL 是否超出普通 GitHub 仓库元数据来源的覆盖范围。"""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return True
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if hostname != "github.com" and not hostname.endswith(".github.com"):
        return True
    path_parts = [part for part in parsed.path.split("/") if part]
    return len(path_parts) > 2


def _sources(query: str, explicit: list[str]) -> list[str]:
    normalised = [str(item).strip().lower().replace("-", "_") for item in explicit if str(item).strip()]
    if normalised:
        return list(dict.fromkeys(normalised))[:8]
    lowered = query.lower()
    selected = [name for name, terms in _SOURCE_ALIASES if any(term.lower() in lowered for term in terms)]
    defaults = ["github", "rss", "hackernews"]
    return list(dict.fromkeys(defaults + selected))[:8]


def build_plan(request: PlanRequest) -> ResearchPlan:
    query = request.query.strip()
    project = _normalise_project(request.project) or _normalise_project(query) or "browser-use/browser-use"
    sources = _sources(query, request.sources)
    focus = _focus(query)
    urls = _urls(query)
    if urls and any(_needs_browser_url(url) for url in urls) and "browser_use" not in sources:
        sources.append("browser_use")
    days = _window_days(query, request.window_days)
    url_note = f"，包含 {len(urls)} 个明确页面" if urls else ""
    explanation = (
        f"已识别项目 {project}，时间窗口为最近 {days} 天{url_note}；"
        f"将优先使用 {', '.join(sources)}，围绕 {', '.join(focus)} 生成证据报告。"
    )
    return ResearchPlan(
        query=query,
        project=project,
        window_days=days,
        research_mode=request.research_mode,
        focus=focus,
        sources=sources,
        urls=urls,
        explanation=explanation,
    )


__all__ = ["build_plan"]
