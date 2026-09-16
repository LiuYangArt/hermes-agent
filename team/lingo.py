"""Read-only project glossary lookup for authorized Lark team chats."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Callable

from team import governance


MATCH_PATH = "/open-apis/lingo/v1/entities/match"
ENTITY_PATH = "/open-apis/lingo/v1/entities/{entity_id}"
MAX_MATCHES = 10


def _failure(message: str) -> dict[str, Any]:
    return {"success": False, "found": False, "error": message, "matches": []}


def _settings() -> tuple[list[str], frozenset[str]] | str:
    policy = governance.config().get("team_governance", {})
    lingo = policy.get("lingo", {}) if isinstance(policy, dict) else {}
    if not isinstance(lingo, dict):
        return "项目词典未配置。"
    repo_ids = lingo.get("repo_ids", [])
    allowed_chat_ids = lingo.get("allowed_chat_ids", [])
    if (
        not isinstance(repo_ids, list)
        or not repo_ids
        or not all(isinstance(item, str) and item.strip() for item in repo_ids)
    ):
        return "项目词典未配置可查询的词库。"
    if (
        not isinstance(allowed_chat_ids, list)
        or not allowed_chat_ids
        or not all(isinstance(item, str) and item.strip() for item in allowed_chat_ids)
    ):
        return "项目词典未配置授权群聊。"
    return list(dict.fromkeys(item.strip() for item in repo_ids)), frozenset(
        item.strip() for item in allowed_chat_ids
    )


def _invoke(run: Callable[..., subprocess.CompletedProcess], command: list[str]) -> dict[str, Any] | str:
    try:
        result = run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
            cwd="/workspace",
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"项目词典接口调用失败：{exc}"
    if result.returncode != 0:
        diagnostic = (result.stderr or result.stdout or "未知错误").strip()
        return f"项目词典接口调用失败：{diagnostic[:1000]}"
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return "项目词典接口返回了无法解析的数据。"
    if not isinstance(payload, dict):
        return "项目词典接口返回了无效数据。"
    code = payload.get("code", 0)
    if payload.get("ok") is False or payload.get("success") is False or code not in (0, None):
        return f"项目词典接口返回错误：{payload.get('msg') or code}"
    return payload


def _text_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    collected: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("key") or item.get("name") or item.get("value") or "").strip()
        else:
            text = ""
        if text and text not in collected:
            collected.append(text)
    return collected


def _sources(entity: dict[str, Any]) -> list[dict[str, str]]:
    related = entity.get("related_meta", {})
    if not isinstance(related, dict):
        return []
    sources: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind in ("docs", "links"):
        items = related.get(kind, [])
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("name") or "").strip()
            url = str(item.get("url") or item.get("link") or "").strip()
            if not url.startswith(("https://", "http://")):
                continue
            key = (title, url)
            if key in seen:
                continue
            seen.add(key)
            sources.append({"title": title, "url": url, "type": kind[:-1]})
    return sources


def _render_entity(repo_id: str, entity_id: str, entity_type: str, entity: dict[str, Any]) -> dict[str, Any]:
    names = _text_values(entity.get("main_keys"))
    aliases = _text_values(entity.get("aliases"))
    sources = _sources(entity)
    return {
        "repo_id": repo_id,
        "entity_id": entity_id,
        "type": entity_type,
        "names": names,
        "aliases": aliases,
        "description": entity.get("description") or "",
        "rich_text": entity.get("rich_text") or "",
        "sources": sources,
        "source_missing": not sources,
        "source_note": "该词条未提供来源链接。" if not sources else "",
    }


def query(word: str, run: Callable[..., subprocess.CompletedProcess] = governance.run_command) -> dict[str, Any]:
    """Return every matching glossary entity visible to the configured bot."""
    if not isinstance(word, str) or not 1 <= len(word.strip()) <= 100:
        return _failure("查询词长度必须为 1 到 100 个字符。")
    word = word.strip()
    if governance.bound("HERMES_SESSION_PLATFORM") != "feishu":
        return _failure("项目词典只能从授权的 Lark 群聊查询。")
    settings = _settings()
    if isinstance(settings, str):
        return _failure(settings)
    repo_ids, allowed_chat_ids = settings
    chat_id = governance.bound("HERMES_SESSION_CHAT_ID")
    if not chat_id or chat_id not in allowed_chat_ids:
        return _failure("当前群聊未获项目词典查询授权。")

    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    truncated = False
    for repo_id in repo_ids:
        command = [
            "/usr/local/bin/lark-cli", "api", "POST", MATCH_PATH,
            "--as", "bot",
            "--params", json.dumps({"repo_id": repo_id}, ensure_ascii=False, separators=(",", ":")),
            "--data", json.dumps({"word": word}, ensure_ascii=False, separators=(",", ":")),
        ]
        payload = _invoke(run, command)
        if isinstance(payload, str):
            return _failure(payload)
        data = payload.get("data", {})
        results = data.get("results", []) if isinstance(data, dict) else []
        if not isinstance(results, list):
            return _failure("项目词典匹配接口返回了无效数据。")
        for result in results:
            if not isinstance(result, dict):
                continue
            entity_id = str(result.get("entity_id") or "").strip()
            if not re.fullmatch(r"[A-Za-z0-9_-]+", entity_id):
                return _failure("项目词典匹配接口返回了无效词条编号。")
            if entity_id in seen:
                continue
            seen.add(entity_id)
            if len(candidates) == MAX_MATCHES:
                truncated = True
                continue
            candidates.append((repo_id, entity_id, str(result.get("type", ""))))

    if not candidates:
        return {
            "success": True,
            "found": False,
            "matches": [],
            "message": f"项目词典中未找到“{word}”。",
            "truncated": False,
        }

    matches: list[dict[str, Any]] = []
    for repo_id, entity_id, entity_type in candidates:
        command = [
            "/usr/local/bin/lark-cli", "api", "GET", ENTITY_PATH.format(entity_id=entity_id),
            "--as", "bot",
            "--params", json.dumps({"repo_id": repo_id}, ensure_ascii=False, separators=(",", ":")),
        ]
        payload = _invoke(run, command)
        if isinstance(payload, str):
            return _failure(payload)
        data = payload.get("data", {})
        entity = data.get("entity") if isinstance(data, dict) else None
        if not isinstance(entity, dict):
            return _failure(f"项目词典词条 {entity_id} 的详情缺失。")
        matches.append(_render_entity(repo_id, entity_id, entity_type, entity))

    response: dict[str, Any] = {
        "success": True,
        "found": True,
        "matches": matches,
        "ambiguous": len(matches) > 1,
        "truncated": truncated,
    }
    if len(matches) > 1:
        response["message"] = "找到多个词义，请根据名称、说明和来源选择，不能默认采用第一条。"
    if truncated:
        response["limit_message"] = f"匹配超过 {MAX_MATCHES} 条，仅返回前 {MAX_MATCHES} 条。"
    return response
