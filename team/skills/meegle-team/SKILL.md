---
name: meegle-team
description: Meegle 团队项目管理。涉及 Meegle、工作项、创建任务、需求、缺陷、待办、排期、节点或视图时使用，通过 meegle 工具调用官方 CLI。
---

# Meegle 团队操作

先读取 references/upstream-guide.md，再按操作读取对应 SOP。参考资料来源 larksuite/meegle-cli 1.0.23，提交 30aa38ef66ce47d232a84fbd731dbc156ac933a7。

## 本环境调用约定

参考资料中的 CLI 命令不通过 terminal 执行。统一转换为 meegle 工具：resource 是第一个命令词，action 是第二个命令词，arguments 是剩余参数字符串数组。例如 meegle workitem get --project-key P --work-item-id ID 转为 resource=workitem、action=get、arguments=["--project-key","P","--work-item-id","ID"]。复杂数据先 JSON 序列化，再作为 --params 的一个字符串值传入。

调用帮助使用同一 resource/action 并传 arguments=["--help"]。auth status 使用 resource=auth、action=status；url decode 使用 resource=url、action=decode。已开放已安装 CLI 的全部命令，包括附件、WBS、资源库、登录和配置。按用户要求执行，不额外确认。顶层命令省略 action。先通过 --help 或 inspect 确定参数。

从 /opt/data/team-settings.md 读取默认项目、工作项类型、模板和优先级。用户明确指定的值覆盖部署默认值。只有用户明确要求父工作项/节点下的子任务时才走 subtask。先读取元数据核实必填字段。

用户要求创建或修改就是执行授权，不再加确认码或第二次确认。仅缺少必要业务信息且无法从默认设置、元数据中确定时询问。用户明确要求预览时使用 --dry-run，并说明尚未执行。

field_value 一律为字符串；对象、数组需要再次 JSON 序列化为字符串。创建后必须 workitem get 回读，返回实际 ID 和链接。失败先看真实错误，最多针对性修正两次；不得把插件拦截说成服务端权限问题。

本文件的调用方式、默认设置、直接执行与回读要求优先于参考资料中的终端调用、登录、确认及链接展示约定。插件无业务白名单，实际能力由 CLI 版本和账号权限决定。
