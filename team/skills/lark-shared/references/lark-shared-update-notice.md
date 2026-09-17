# 更新与 `_notice`

lark-cli 命令执行后，如果检测到新版本，JSON 输出中会包含 `_notice.update` 字段（含 `message`、`command` 等）。

除非用户明确询问更新、版本或 notice，否则不要在业务回答、任务评论或完成摘要中提及 `_notice.update` 或 `_notice.skills`，包括附带一句“另外发现新版”。不要为了这些维护提示中断任务、建议升级或改变成功判断。CLI 原始输出保留，后台维护负责升级。

需要稳定 JSON 给脚本或机器读取时，可以在命令前设置：

```bash
LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1 LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1 <lark-cli command>
```

仅当用户明确要求了解版本维护或执行升级时，说明官方更新命令：

```bash
lark-cli update
```

**重要**：始终使用 `lark-cli update` 更新，它会同时更新 CLI 和 AI Skills。

另外两类 notice：
- `_notice.skills`：本地 Skills 与当前 CLI 不同步。
- `_notice.deprecated_command`：后续调用改用 `replacement`。附带的升级建议仍留给后台维护，不向业务用户主动转述；真正影响当前任务的错误如实报告。
