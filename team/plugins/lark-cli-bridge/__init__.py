import json
import subprocess
import posixpath
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile
from urllib.parse import unquote, urlsplit


@contextmanager
def _task_attachment_command(command):
    if command[1:3] != ["task", "+upload-attachment"] or any(
        word in {"--help", "-h"} for word in command[3:]
    ):
        yield command, "/workspace"
        return
    from team.governance import ARTIFACTS, public_path

    command = list(command)
    files = []
    for index, word in enumerate(command[3:], 3):
        if word == "--file":
            if index + 1 >= len(command) or command[index + 1].startswith("--"):
                raise ValueError("任务附件缺少文件路径。")
            files.append((index + 1, command[index + 1], False))
        elif word.startswith("--file="):
            files.append((index, word.split("=", 1)[1], True))
    if len(files) != 1 or not files[0][1]:
        raise ValueError("每次任务附件上传必须指定一个 --file。")
    index, raw, inline = files[0]
    source = Path(raw)
    if not source.is_absolute():
        source = Path("/workspace") / source
    if not public_path(str(source)):
        raise ValueError("任务附件仅限团队公共文件和收到的原始素材，不能上传配置或凭据。")
    if not source.is_file() or source.stat().st_size > 50 * 1024 * 1024:
        raise ValueError("任务附件必须是存在的文件，且不超过 50 MB；请勿用图片描述替代原文件。")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    # Keep the original bytes while satisfying the CLI's cwd-relative file contract.
    with tempfile.TemporaryDirectory(prefix="task-attachment-", dir=ARTIFACTS) as temporary:
        staged = Path(temporary) / source.name
        shutil.copyfile(source, staged)
        relative = "./" + source.name
        command[index] = "--file=" + relative if inline else relative
        yield command, temporary


def _shared_group_command(command):
    from gateway.session_context import get_session_env
    from team.governance import enabled as team_enabled

    if (get_session_env("HERMES_SESSION_PLATFORM") != "feishu"
            or (not team_enabled() and get_session_env("HERMES_SESSION_CHAT_TYPE") not in {"group", "thread", "channel"})):
        return command
    resource = command[1]
    if resource in {"auth", "config", "profile", "update", "event"} or resource.startswith("-"):
        raise ValueError("群聊不能修改共享登录、应用配置或启动事件监听；请由管理员在部署端处理。")
    for index, word in enumerate(command[2:], 2):
        if word == "--":
            raise ValueError("群聊命令不能使用终止参数解析的标记。")
        if word == "--profile" or word.startswith("--profile="):
            raise ValueError("群聊不能切换应用或个人授权配置。")
        if word == "--as" and (index + 1 >= len(command) or command[index + 1] != "bot"):
            raise ValueError("团队群聊使用机器人身份，不能继承任何成员的个人授权。")
        if word.startswith("--as=") and word != "--as=bot":
            raise ValueError("团队群聊使用机器人身份，不能继承任何成员的个人授权。")
    if resource not in {"schema", "skills", "help", "whoami"}:
        command = [*command, "--as", "bot"]
    return command


def register(ctx):
    schema = {
        "name": "lark_cli",
        "description": "Lark CLI business commands using the team's bot identity. Read /opt/data/skills/lark-shared/SKILL.md and the relevant skill first. For read-only project glossary lookup use resource=project_terms, omit action, and pass one term in arguments. Shared configuration changes require administrator authority. Use literal command words and arguments.",
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "description": "First CLI word, e.g. docs, drive, im, task, schema, api; or project_terms for scoped read-only glossary lookup."},
                "action": {"type": "string", "description": "Optional second CLI word. Omit for top-level commands."},
                "arguments": {"type": "array", "items": {"type": "string"}, "description": "Remaining literal argv strings; JSON payload is one string. Use --help or inspect to discover commands."},
            },
            "required": ["resource"],
        },
    }

    def handle(params, **kwargs):
        del kwargs
        resource = params.get("resource", "")
        action = params.get("action", "")
        arguments = params.get("arguments", [])
        if not isinstance(resource, str) or not resource or not isinstance(action, str):
            return json.dumps({"success": False, "error": "Invalid command words"})
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            return json.dumps({"success": False, "error": "arguments must be strings"})
        if resource == "project_terms":
            if action or len(arguments) != 1:
                return json.dumps({"success": False, "error": "project_terms requires exactly one term in arguments and no action"})
            from team.lingo import query
            return json.dumps(query(arguments[0]), ensure_ascii=False)
        if resource == "api" and (action not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"} or not arguments):
            return json.dumps({"success": False, "error": "api requires an explicit HTTP action and path as arguments[0]"})
        command = ["/usr/local/bin/lark-cli", resource]
        if action:
            command.append(action)
        command.extend(arguments)
        if any("\x00" in item for item in command):
            return json.dumps({"success": False, "error": "NUL argument rejected"})
        try:
            from team.governance import cli_denial, enabled as team_enabled
            if team_enabled() and resource == "api" and arguments:
                path = arguments[0]
                for _ in range(3):
                    path = unquote(path)
                path = posixpath.normpath(urlsplit(path).path)
                if not arguments[0].startswith("/open-apis/") or path != arguments[0] or "%" in arguments[0] or "//" in arguments[0]:
                    raise ValueError("API 路径必须是未编码、无跳转和查询串的 /open-apis/... 路径。查询字段使用 --params。")
                if path.startswith("/open-apis/lingo/"):
                    raise ValueError("团队词典只允许通过 resource=project_terms 按配置词库查询；本阶段不开放词条写入。")
                if path.startswith("/open-apis/application/"):
                    from team.governance import shared_write_denial
                    denied = shared_write_denial("application", path)
                    if denied:
                        raise ValueError(denied)
            denied = cli_denial(resource, command[2:])
            if denied:
                raise ValueError(denied)
            command = _shared_group_command(command)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        try:
            from team.governance import run_command
            with _task_attachment_command(command) as (prepared, cwd):
                result = run_command(prepared, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL, cwd=cwd)
        except subprocess.TimeoutExpired:
            return json.dumps({"success": False, "error": "Timeout; verify remote state before retrying writes. Use device-code init/poll --once for login."})
        except (ValueError, OSError) as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        response = {"success": result.returncode == 0, "exit_code": result.returncode, "output": result.stdout[-40000:]}
        if result.stderr:
            response["diagnostics"] = result.stderr[-8000:]
        if result.returncode:
            response["error"] = result.stderr[-8000:] or "Command failed"
        return json.dumps(response, ensure_ascii=False)

    ctx.register_tool(name="lark_cli", toolset="lark_cli", schema=schema, handler=handle)
