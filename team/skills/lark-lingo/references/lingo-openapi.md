# Lingo OpenAPI Notes

## Core endpoints

### List repos

```bash
lark-cli api GET /open-apis/lingo/v1/repos --as user
```

### List classifications

```bash
lark-cli api GET /open-apis/lingo/v1/classifications --as user
```

### List entities

```bash
lark-cli api GET "/open-apis/lingo/v1/entities?repo_id=<repo_id>&page_size=100" --as user
```

Useful for:
- dedupe checks
- verifying existing outer IDs
- confirming target classification usage

### Create draft

```bash
lark-cli api POST /open-apis/lingo/v1/drafts?repo_id=<repo_id> --as user --data <json>
```

Default write path. This creates a review draft instead of directly publishing a glossary entity.

### Direct create entity

```bash
lark-cli api POST /open-apis/lingo/v1/entities?repo_id=<repo_id> --as user --data <json>
```

Use only when the user explicitly wants exempt-review behavior and the app has the matching scope.

## Relevant scopes

- `baike:entity`
  Use for reading entities and creating or updating drafts.
- `baike:entity:readonly`
  Read-only access.
- `baike:entity:exempt_review`
  Direct entity creation or update without the normal draft review flow.
- `search:docs:read`
  Useful when sourcing terms from docs or wiki before converting them into glossary entries.

## Important structure rules

- Repo and classification are different layers.
- `repo_id` points to the glossary repo, often something like `全员词库`.
- Lingo entries belong to second-level classifications.
- Import payloads should provide both:
  - `classifications[].id`
  - `classifications[].father_id`

## Payload mapping

Normalized glossary entry:

```json
{
  "main_key": "新印斯茅斯",
  "aliases": ["Neo Innsmouth"],
  "description": "围绕重水开采与饮用水提炼设施发展起来的海滨小镇。",
  "related_docs": [
    { "title": "FD 关卡背景设定", "url": "https://..." }
  ]
}
```

Draft payload fragment:

```json
{
  "main_keys": [
    {
      "key": "新印斯茅斯",
      "display_status": {
        "allow_highlight": true,
        "allow_search": true
      }
    }
  ],
  "aliases": [
    {
      "key": "Neo Innsmouth",
      "display_status": {
        "allow_highlight": true,
        "allow_search": true
      }
    }
  ],
  "description": "围绕重水开采与饮用水提炼设施发展起来的海滨小镇。",
  "related_meta": {
    "docs": [
      { "title": "FD 关卡背景设定", "url": "https://..." }
    ],
    "classifications": [
      { "id": "<second_level_id>", "father_id": "<first_level_id>" }
    ]
  },
  "outer_info": {
    "provider": "FDWorld",
    "outer_id": "fdworld_015"
  }
}
```

## Windows shell reliability note

Inline JSON for `--data` and `--params` can be fragile in Windows PowerShell because of quoting rules.

For batch imports on Windows:
- build one-line JSON payload files first
- escape inner quotes for `cmd`
- post them with the bundled `post_lingo_drafts.cmd`

## Recommended safety order

1. Read repos
2. Read classifications
3. Read entities and dedupe
4. Create drafts
5. Confirm CLI envelope has `ok: true`; inspect nested API result and real draft ID
