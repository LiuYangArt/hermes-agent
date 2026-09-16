---
name: lark-lingo
description: Use when working with Feishu/Lark Lingo glossary entries through lark-cli raw APIs, especially for listing repos or classifications, checking existing terms, creating review drafts, batch-importing glossary content from structured JSON, or turning wiki/doc-derived terminology into Lingo entries. Trigger on requests mentioning lingo, 词典, 词条, glossary, baike, batch import, or when no dedicated lark-cli subcommand exists.
---

# Lark Lingo

## Overview

Use this skill to operate Feishu/Lark Lingo through `lark-cli api`, with a safe default of creating review drafts instead of direct exempt-review entities.

Lingo is not exposed as a dedicated `lark-cli lingo ...` command set, so most work goes through raw OpenAPI endpoints.

## Quick Start

1. Confirm auth and scopes before touching Lingo data.
2. Discover repo IDs and classification IDs.
3. List existing entities and dedupe by `main_key` or `outer_info`.
4. Default to `POST /open-apis/lingo/v1/drafts`.
5. For bulk import on Windows, use the bundled scripts instead of inline `--data` JSON.

## Preconditions

- Ensure `lark-cli` is installed and the user is logged in with `--as user` when operating tenant glossary data.
- Check that the app and logged-in user have `baike:entity`.
- If the source material comes from Lark docs or wiki, also ensure `search:docs:read` and use doc/wiki tooling first.
- Only use direct entity creation when the user explicitly wants exempt-review behavior and `baike:entity:exempt_review` is available.

## Discovery Workflow

### 1. List repos

```bash
lark-cli api GET /open-apis/lingo/v1/repos --as user
```

Use this to find the actual repo ID. Do not assume a classification name is a repo.

### 2. List classifications

```bash
lark-cli api GET /open-apis/lingo/v1/classifications --as user
```

Lingo entries belong to second-level classifications. In practice, import payloads should include both:
- `id`: second-level classification ID
- `father_id`: first-level classification ID

### 3. List existing entities

```bash
lark-cli api GET "/open-apis/lingo/v1/entities?repo_id=<repo_id>&page_size=100" --as user
```

On Windows PowerShell, prefer query strings embedded in the path for GET requests because inline `--params` JSON can be awkward.

### 4. Dedupe before writing

Check for:
- Existing `main_keys[].key`
- Existing `aliases[].key`
- Existing `outer_info.provider` + `outer_info.outer_id`

When bulk-importing a prepared glossary, assign stable `outer_id` values so later updates can target the same external records.

## Writing Workflow

### Default: create drafts

Use:

```bash
lark-cli api POST /open-apis/lingo/v1/drafts?repo_id=<repo_id> --as user --data ...
```

Drafts are reviewable and safer than direct entity writes.

### Direct write: only when explicitly requested

`POST /open-apis/lingo/v1/entities` should be treated as higher risk and requires the exempt-review scope path.

## Bulk Import Workflow

When the user already has a normalized glossary JSON file, use the bundled scripts:

1. Build per-entry payload files with [`scripts/build_lingo_payloads.py`](./scripts/build_lingo_payloads.py)
2. Post them with [`scripts/post_lingo_drafts.cmd`](./scripts/post_lingo_drafts.cmd)

### Build payload files

```powershell
python "$HOME/.codex/skills/lark-lingo/scripts/build_lingo_payloads.py" `
  --source "C:\path\to\glossary.json" `
  --output-dir "C:\path\to\payloads" `
  --classification-id "<second_level_id>" `
  --father-id "<first_level_id>" `
  --provider "FDWorld" `
  --outer-id-prefix "fdworld"
```

### Post drafts on Windows

```cmd
%USERPROFILE%\.codex\skills\lark-lingo\scripts\post_lingo_drafts.cmd <repo_id> <payload_dir> user
```

Add `--dry-run` as the fourth argument to preview requests without writing.

## Normalized Source Format

If the user asks for "先整理成词条列表" or "先转成 Lingo 可导入格式", normalize the source into this shape first:

```json
{
  "source_document": {
    "title": "FD 关卡背景设定",
    "url": "https://..."
  },
  "entries": [
    {
      "main_key": "新英格兰殖民地",
      "aliases": ["Neo England", "NeoEngland"],
      "description": "项目内定义的短释义",
      "related_docs": [
        { "title": "FD 关卡背景设定", "url": "https://..." }
      ]
    }
  ]
}
```

Keep descriptions project-facing and concise. If the source borrows names from another IP, do not overemphasize the original work unless the user explicitly wants cross-reference context.

## References

- Read [`references/lingo-openapi.md`](./references/lingo-openapi.md) for endpoint summaries, scopes, and field mapping.
- Use the bundled scripts instead of ad-hoc inline JSON when importing in Windows shells.

## Common Mistakes

- Treating a first-level classification such as `世界观词库` as if it were a repo.
- Omitting the second-level classification ID from import payloads.
- Creating direct entities when drafts were intended.
- Skipping dedupe and producing duplicate terms.
- Using fragile inline `--data` JSON in PowerShell on Windows when a batch import is involved.

## Docker Hermes Lark 入口
通过已安装的 lark_cli 原生工具调用：resource="api"，action 为 HTTP 方法，其余路径、--as user、--data 和JSON作为 arguments 中的独立字符串。不要用 terminal、Python 或 shell 执行 CLI。国际版 Lark，沿用当前用户身份；缺少权限时只申请实际所需 baike:entity，不因错误列出多个候选 scope 就申请免审核权限。默认草稿，先去重，写入成功须返回真实 ID 并回读。

当前容器 CLI 不允许路径中含 ? 查询串。所有 GET/POST 查询字段均通过 --params 独立JSON参数传入，例如路径 /open-apis/lingo/v1/entities，--params {"repo_id":"实际ID","page_size":100}。本规则替代以上旧版 URL 查询串示例；--data 只放请求正文。
