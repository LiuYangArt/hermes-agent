"""Team deployment authorization, separate from human write approval."""

from __future__ import annotations

from functools import lru_cache
import logging
import json
import os
from pathlib import Path
import subprocess
import tempfile

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)
DENIED = "只有部署配置中的 Lark 管理员可以修改共享技能、长期记忆、用户画像、插件和机器人配置。当前请求未获管理权限。"
ARTIFACTS = Path("/workspace/artifacts")
MEMBER_TOOLS = frozenset({
    "read_file", "search_files", "write_file", "patch", "todo", "clarify",
    "skills_list", "skill_view", "skills_view", "lark_cli", "meegle",
    "helius_generate_image", "delegate_task", "project_terms",
})
MEMBER_COMMANDS = frozenset({
    "help", "whoami", "status", "stop", "approve", "deny", "listen",
    "new", "reset", "context", "retry", "undo", "queue", "steer",
})


@lru_cache(maxsize=16)
def _load(home: str) -> dict:
    path = Path(home) / "config.yaml"
    if not path.exists():
        return {}
    try:
        config = yaml.safe_load(path.read_text()) or {}
        if not isinstance(config, dict):
            raise ValueError("config is not a mapping")
        return config
    except Exception:
        # A broken policy must never become an unrestricted deployment.
        return {"team_governance": {"enabled": True}}


def config() -> dict:
    return _load(str(get_hermes_home()))


def enabled() -> bool:
    policy = config().get("team_governance", {})
    return not isinstance(policy, dict) or policy.get("enabled", False) is not False


def bound(name: str) -> str:
    from gateway.session_context import _VAR_MAP, _UNSET
    value = _VAR_MAP[name].get()
    return "" if value is _UNSET else str(value or "")


def admin_ids() -> frozenset[str]:
    cfg = config()
    policy = cfg.get("team_governance", {})
    if not isinstance(policy, dict):
        return frozenset()
    app_id = policy.get("app_id")
    if not app_id or app_id != os.environ.get("FEISHU_APP_ID"):
        return frozenset()
    extra = cfg.get("platforms", {}).get("feishu", {}).get("extra", {})
    ids = extra.get("admins", [])
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i.startswith("ou_") for i in ids):
        return frozenset()
    return frozenset(ids)


def is_admin(source=None) -> bool:
    try:
        if source is None:
            from tools.write_approval import is_background
            if is_background() or bound("HERMES_CRON_SESSION"):
                return False
            platform = bound("HERMES_SESSION_PLATFORM")
            user_id = bound("HERMES_SESSION_USER_ID")
            message_id = bound("HERMES_SESSION_MESSAGE_ID")
        else:
            platform = getattr(source.platform, "value", source.platform)
            user_id = source.user_id
            message_id = source.message_id
        return platform == "feishu" and bool(message_id) and user_id in admin_ids()
    except Exception:
        return False


def audit(operation: str, target: str, allowed: bool, source=None) -> None:
    requester = getattr(source, "user_id", "") if source else bound("HERMES_SESSION_USER_ID")
    logger.info("team_access requester=%s operation=%s target=%s allowed=%s", requester or "unbound", operation, target[:240], allowed)


def shared_write_denial(operation: str, target: str = "shared-assets") -> str | None:
    if not enabled():
        return None
    allowed = is_admin()
    audit(operation, target, allowed)
    return None if allowed else DENIED


def tool_denial(name: str, args: dict) -> str | None:
    if not enabled():
        return None
    if name in {"memory", "skill_manage"}:
        return shared_write_denial(name, str(args.get("name") or args.get("target") or "shared-assets"))
    if is_admin():
        audit("tool", name, True)
        return None
    if name in MEMBER_TOOLS:
        return None
    # Unknown tools are not assumed safe merely because a plugin registered them.
    audit("tool", name, False)
    return "团队普通请求只能使用已审计的工具；此工具可执行未隔离代码或修改共享配置，未获授权。"


def slash_denial(source, command: str) -> str | None:
    if not enabled() or getattr(source.platform, "value", source.platform) != "feishu":
        return None
    if command in MEMBER_COMMANDS:
        return None
    allowed = is_admin(source)
    audit("command", command, allowed, source)
    return None if allowed else DENIED


def confined() -> bool:
    return enabled() and not is_admin()


def file_write_denial(path: str, task_id: str = "default") -> str | None:
    if not confined():
        return None
    from tools.file_tools import _resolve_path_for_task
    resolved = Path(_resolve_path_for_task(path, task_id)).resolve()
    allowed = resolved.is_relative_to(ARTIFACTS.resolve())
    audit("file_write", str(resolved), allowed)
    return None if allowed else DENIED + " 普通成员生成与编辑文件请使用 /workspace/artifacts。"


def file_read_denial(path: str, task_id: str = "default") -> str | None:
    if not confined():
        return None
    from tools.file_tools import _resolve_path_for_task
    resolved = str(_resolve_path_for_task(path, task_id))
    return None if public_path(resolved) else "普通成员只能读取团队公共文件；机器人配置、凭据和运行状态不对成员开放。"


def public_roots() -> list[Path]:
    home = get_hermes_home()
    return [Path("/workspace"), *(home / name for name in ("skills", "memories", "imagegen", "image_cache", "audio_cache")), home / "team-settings.md"]


def public_path(path: str) -> bool:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path("/workspace") / candidate
    candidate = candidate.resolve()
    return any(candidate == root.resolve() or candidate.is_relative_to(root.resolve()) for root in public_roots())


def command(argv: list[str], *, temporary: str, outputs: tuple[str, ...] = (), credentials: tuple[str, ...] = ()) -> list[str]:
    if not confined():
        return argv
    from team.sandbox import sandbox_command
    readable = [path for path in [*public_roots(), *credentials] if Path(path).exists()]
    return sandbox_command(argv, [ARTIFACTS, temporary, *outputs], readable_paths=readable)


def run_command(argv: list[str], *, outputs: tuple[str, ...] = (), **kwargs):
    with tempfile.TemporaryDirectory(prefix="hermes-team-cli-") as temporary:
        env = dict(kwargs.pop("env", os.environ))
        env["TMPDIR"] = temporary
        credentials = ()
        home = get_hermes_home()
        if argv[0] == "/usr/local/bin/lark-cli":
            credentials = (str(home / ".lark-cli"), str(home / ".local/share/lark-cli"))
        elif argv[0] == "/usr/local/bin/meegle":
            credentials = (str(home / ".meegle"),)
        elif len(argv) > 1 and argv[1] == str(home / "skills/helius-imagegen/scripts/helius_imagegen.py"):
            credentials = tuple(str(home / p) for p in ("config.yaml", ".env", "auth.json"))
        return subprocess.run(command(argv, temporary=temporary, outputs=outputs, credentials=credentials), env=env, **kwargs)


def cli_denial(resource: str, arguments: list[str]) -> str | None:
    if not confined():
        return None
    if resource.startswith("-") or resource in {"auth", "config", "profile", "update", "event", "application", "plugin", "plugins", "install", "login", "logout", "preference", "completion"}:
        return DENIED
    if any(word == "--" or word == "--profile" or word.startswith("--profile=") for word in arguments):
        return DENIED
    for index, argument in enumerate(arguments):
        if resource == "api" and index == 1:
            continue
        denial = _local_input_denial(argument)
        if denial:
            return denial
    return None


def _local_input_denial(value) -> str | None:
    if isinstance(value, dict):
        values = value.values()
    elif isinstance(value, list):
        values = value
    elif isinstance(value, str):
        if value.startswith("--") and "=" in value:
            value = value.split("=", 1)[1]
        if "=" in value and not value.startswith(("{", "[")):
            field, candidate = value.split("=", 1)
            if field and candidate.startswith(("/", "~", "./", "../", "@", "file://")):
                value = candidate
        try:
            nested = json.loads(value)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, (dict, list)):
            return _local_input_denial(nested)
        candidate = value.removeprefix("@").removeprefix("file://")
        local = Path(candidate).expanduser()
        if not local.is_absolute():
            local = Path("/workspace") / local
        try:
            exists = local.exists()
        except OSError:
            exists = False
        if (candidate.startswith(("/", "~", "./", "../")) or exists) and not public_path(candidate):
            return "普通成员的本地文件输入仅限团队公共文件和产物，不能读取或上传机器人配置、凭据和运行状态。"
        if exists and local.is_dir():
            for parent, dirs, files in os.walk(local, followlinks=False):
                if any(Path(parent, name).is_symlink() and not public_path(str(Path(parent, name))) for name in [*dirs, *files]):
                    return "本地输入目录包含指向非公共文件的链接，不能上传。"
        return None
    else:
        return None
    return next((denied for item in values if (denied := _local_input_denial(item))), None)
