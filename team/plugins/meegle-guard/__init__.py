import json
import subprocess


def register(ctx):
    schema = {
        "name": "meegle",
        "description": "All installed Meegle CLI commands are available. Read /opt/data/skills/meegle-team/SKILL.md first. No command whitelist or extra confirmation. Read /opt/data/team-settings.md for deployment-specific project and template defaults. Read back writes.",
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
        command = ["/usr/local/bin/meegle", resource]
        if action:
            command.append(action)
        command.extend(arguments)
        if any("\x00" in item for item in command):
            return json.dumps({"success": False, "error": "NUL argument rejected"})
        from team.governance import cli_denial, run_command
        denied = cli_denial(resource, command[2:])
        if denied:
            return json.dumps({"success": False, "error": denied}, ensure_ascii=False)
        try:
            result = run_command(command, capture_output=True, text=True, timeout=90, stdin=subprocess.DEVNULL, cwd="/workspace")
        except subprocess.TimeoutExpired:
            return json.dumps({"success": False, "error": "Timeout; verify remote state before retrying writes. Use device-code init/poll --once for login."})
        response = {"success": result.returncode == 0, "exit_code": result.returncode, "output": result.stdout[-40000:]}
        if result.stderr:
            response["diagnostics"] = result.stderr[-8000:]
        if result.returncode:
            response["error"] = result.stderr[-8000:] or "Command failed"
        return json.dumps(response, ensure_ascii=False)

    ctx.register_tool(name="meegle", toolset="meegle", schema=schema, handler=handle)
