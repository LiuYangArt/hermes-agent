import json
import subprocess


def _shared_group_command(command):
    from gateway.session_context import get_session_env

    if (get_session_env("HERMES_SESSION_PLATFORM") != "feishu"
            or get_session_env("HERMES_SESSION_CHAT_TYPE") not in {"group", "thread", "channel"}):
        return command
    resource = command[1]
    if resource in {"auth", "config", "profile", "update", "event"} or resource.startswith("-"):
        raise ValueError("群聊不能修改共享登录、应用配置或启动事件监听；请由管理员在部署端处理。")
    for index, word in enumerate(command[2:], 2):
        if word == "--profile" or word.startswith("--profile="):
            raise ValueError("群聊不能切换应用或个人授权配置。")
        if word == "--as" and (index + 1 >= len(command) or command[index + 1] != "bot"):
            raise ValueError("团队群聊使用机器人身份，不能继承任何成员的个人授权。")
        if word.startswith("--as=") and word != "--as=bot":
            raise ValueError("团队群聊使用机器人身份，不能继承任何成员的个人授权。")
    if resource not in {"schema", "skills", "help", "whoami"} and "--as" not in command and "--as=bot" not in command:
        command = [*command, "--as", "bot"]
    return command


def register(ctx):
    schema = {
        "name": "lark_cli",
        "description": "All installed Lark CLI commands. Read /opt/data/skills/lark-shared/SKILL.md and the relevant official lark skill first. Use literal command words and arguments. Brand is international Lark. No extra plugin confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "resource": {"type": "string", "description": "First CLI word: workitem, attachment, wbs, resource, config, auth, inspect, version, --help, etc."},
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
        command = ["/usr/local/bin/lark-cli", resource]
        if action:
            command.append(action)
        command.extend(arguments)
        if any("\x00" in item for item in command):
            return json.dumps({"success": False, "error": "NUL argument rejected"})
        try:
            command = _shared_group_command(command)
        except ValueError as exc:
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL, cwd="/workspace")
        except subprocess.TimeoutExpired:
            return json.dumps({"success": False, "error": "Timeout; verify remote state before retrying writes. Use device-code init/poll --once for login."})
        response = {"success": result.returncode == 0, "exit_code": result.returncode, "output": result.stdout[-40000:]}
        if result.stderr:
            response["diagnostics"] = result.stderr[-8000:]
        if result.returncode:
            response["error"] = result.stderr[-8000:] or "Command failed"
        return json.dumps(response, ensure_ascii=False)

    ctx.register_tool(name="lark_cli", toolset="lark_cli", schema=schema, handler=handle)
