#!/usr/bin/env python3
"""Configure a team deployment without copying credentials into source."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin", action="append", default=[])
    parser.add_argument("--lingo-repo", action="append", default=[])
    args = parser.parse_args()
    if any(not value.startswith("ou_") for value in args.admin):
        parser.error("Administrators must be app-scoped Lark open_id values (ou_...).")
    state = Path(os.environ.get("HERMES_TEAM_STATE_DIR", str(Path.home() / ".local/share/hermes-team")))
    path = state / "data/config.yaml"
    config = yaml.safe_load(path.read_text())
    app_id = subprocess.check_output([
        "docker", "exec", "--user", "hermes", "hermes-team", "python", "-c",
        "import os; print(os.environ.get('FEISHU_APP_ID', ''))",
    ], text=True).strip()
    if not app_id:
        raise SystemExit("Running Lark app identity is missing; no configuration changed.")
    if config.get("terminal", {}).get("backend", "local") != "local":
        raise SystemExit("Team governance requires the local bubblewrap execution backend.")
    platform = config.setdefault("platforms", {}).setdefault("feishu", {})
    platform.setdefault("extra", {})["admins"] = args.admin
    for key in ("allow_admin_from", "group_allow_admin_from"):
        platform.pop(key, None)
        platform["extra"].pop(key, None)
    config["team_governance"] = {
        "enabled": True,
        "app_id": app_id,
        "lingo": {"repo_ids": args.lingo_repo},
    }
    toolsets = config.setdefault("platform_toolsets", {}).get("feishu", [])
    if isinstance(toolsets, str):
        toolsets = json.loads(toolsets)
    config["platform_toolsets"]["feishu"] = list(dict.fromkeys([*toolsets, "memory", "skills", "terminal"]))
    config["toolsets"] = list(dict.fromkeys([*config.get("toolsets", []), "lark_cli"]))
    hints = config.setdefault("platform_hints", {}).setdefault("feishu", {})
    note = "团队共享技能、长期记忆和画像仅管理员可以维护；普通成员使用已有能力，在 /workspace/artifacts 生成文件。遇到项目术语先读 lark-lingo 技能，用 lark_cli 的 project_terms 查询；查不到或失败明确说明，不编造定义。"
    current = hints.get("append", "")
    if note not in current:
        hints["append"] = current + "\n" + note
    workspace = Path(os.environ.get("HERMES_TEAM_WORKSPACE", str(Path.home() / "HermesTeamWorkspace")))
    (workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
        yaml.safe_dump(config, output, allow_unicode=True, sort_keys=False)
        temporary = Path(output.name)
    temporary.chmod(0o600)
    temporary.replace(path)
    print(json.dumps({"configured": True, "administrators": len(args.admin), "glossaries": len(args.lingo_repo), "restart_required": True}))


if __name__ == "__main__":
    main()
