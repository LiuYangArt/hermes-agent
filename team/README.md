# Hermes Team：本机 Docker 定制源码

完整 Core 源码在仓库根目录，Lark 话题改动直接维护在 `plugins/platforms/feishu/adapter.py`。不再维护一份完整 adapter 覆盖文件。

## 上下游

- `origin`：LiuYangArt/hermes-agent，GitHub 官方 fork，保存我们的提交。
- `cn-upstream`：Eynzof/Hermes-CN-Core，当前部署的直接上游。
- `upstream`：NousResearch/hermes-agent，官方源头。
- 本次导入基线：`d0575cb0148b78bbb903781299345f5c0e97ad64`。第三方来源和版本见 `sources.json`。

沿 CN 这条已有历史同步：先 `git fetch cn-upstream`，审查差异后合并 `cn-upstream/main`，处理冲突，完成针对性回归和真实入口验证，再提交推送。不要对自有提交强制 reset，不要把官方 main 和 CN main 混用为日常同步源。需要向官方贡献时只提取相关小改动。

## 目录

- `plugins/`：Lark CLI、Meegle、Helius 生图工具插件。
- `skills/`：部署中安装的 Lark、Lingo、Meegle、Helius 技能源码快照。
- `task-bridge/`：macOS 上的 Lark Tasks 桥接脚本和精确依赖锁；不含 node_modules 或授权状态。
- `acp/`：容器内 Tasks 专用入口与任务编排规则。规则的实际生效位置仍是运行配置的 `platform_hints.acp.append`。
- `tests/`：话题与图片工具的已有针对性测试。
- `compose.yaml`：从本仓库完整源码构建的 Docker 入口。
- `.local/`：只在本机保存部署专属默认值和原始私有片段，Git 与 Docker 构建均排除。

## 运行数据与凭据

`~/.local/share/hermes-team/data` 继续保存用户配置、凭据、会话、授权、产物和状态；Tasks `runtime` 也保留在原位置。源码仓库不是数据备份。不要把这些内容加入 Git。
Meegle 项目和模板默认值保留在 `/opt/data/team-settings.md`；仓库只有 `team-settings.example.md` 模板。

## 构建、验证与部署

在仓库根目录：

```sh
docker compose -f team/compose.yaml build --build-arg HERMES_GIT_SHA="$(git rev-parse HEAD)"
python3 team/manage.py verify
```

`verify` 不发送消息、不调用收费生图接口，检查容器实际源码、运行资产与本仓库的一致性，并运行17 项针对性测试。

```sh
python3 team/manage.py install-assets
docker compose -f team/compose.yaml up -d --no-deps hermes-team
python3 team/manage.py verify
```

`install-assets` 只更新托管插件、技能、Tasks 源码，保留原 data、配置、授权和任务状态；从 `.local/team-settings.md` 安装本机业务默认值。安装后若 Tasks 代码有变化，再重启对应 launchd 服务。重装 Tasks 依赖后必须执行 `maintain-patch.py`，不要恢复 approve-all。

模型 Provider 需要保留运行配置里的 User-Agent 设置；配置仍放在本地，不提交密钥。上线前检查是否有正在执行的请求。真实 Lark 验证只在已授权的群/任务范围内进行，复用既有测试话题和任务。

## 原部署的旧流程

原先以本机预构建 Core 镜像为底座，通过部署目录 Dockerfile COPY 整份 adapter，再重建容器。插件、技能和 Tasks 脚本分散在 data 和 task-bridge 中。它能运行，但完整文件覆盖不利于发现上游冲突。
现在先改此仓库源码、审查 Git 差异、验证，再构建并部署。Docker 内部 `/opt/hermes` 是构建产物，不应作为长期编辑入口。

## Lark 话题参与规则

群里 @Hermes 开始话题；同一成员可免 @ 继续。@其他人（不含 Hermes）或发送 `/listen` 后，该成员进入旁听；再次 @Hermes 恢复。新成员默认旁听，需要首次 @Hermes。不同成员的参与状态互不影响。

话题映射、成员状态和待使用的旁听材料保存在运行目录的 `feishu_threads.sqlite`。旁听不启动模型或工具，恢复时仅带入本话题材料；新群消息和未接管话题不保存。普通群引用仍须 @Hermes；Lark 会把回复挂到被引用原文的话题，按该原文根消息统一会话归属。引用文字最多携带 12,000 字符，超限和读取失败明确提示。

共享话题按成员身份有序处理；暂停会丢弃尚未开始的普通免 @ 请求，已明确 @ 的请求保留。已执行中的任务不因对象切换自动取消；停止、审批和澄清回答限当前发起者。共享话题审批只允许本次批准，不提供会话或永久批准。

群聊中的 Lark CLI 明确使用机器人身份，拒绝个人授权、切换应用和修改共享登录。Tasks/ACP 保持自己的权限策略。公共知识允许进入上下文，禁止自动把话题讨论写成公共记忆；群平台不开放历史会话搜索。

开发回归：

```sh
uv run --with pytest --with pytest-asyncio --with aiohttp --with lark-oapi python -m pytest -q tests/gateway/test_feishu_thread_state.py tests/gateway/test_reply_to_injection.py tests/gateway/test_feishu.py
uv run --with aiohttp --with lark-oapi python team/tests/test_thread_conversations.py
uv run python team/tests/test_lark_identity.py
```

测试和部署日志放在 `team/.local/`。真实消息收发权限及事件推送必须另外通过 Lark 验收，模拟处理器测试不能代替真实端到端结果。
