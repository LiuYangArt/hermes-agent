# Team 部署约束

- 此目录保存团队定制源文件。用户明确要求维护现有 main，不开新分支、不推送到上游仓库。
- 运行数据、配置、凭据、授权、聊天记录、任务记录不进 Git；`.local/` 同时被 Git 和 Docker 忽略。
- 主测试入口：`python3 team/manage.py verify`，核对已部署源码并运行团队标准库 unittest，输出实际测试数量；修改 Core 行为时追加相关上游 `scripts/run_tests.sh` 检查。
- 构建：`docker compose -f team/compose.yaml build --build-arg HERMES_GIT_SHA="$(git rev-parse HEAD)"`。
- 部署前安装受管资产：`python3 team/manage.py install-assets`；然后 `docker compose -f team/compose.yaml up -d --no-deps hermes-team`。
- 正式服务包括 hermes-team 和 com.hermes-team.lark-acp / com.hermes-team.lark-tasks，不能当调试进程结束。
- 保留 Tasks 专用身份与审批保护、模型 User-Agent 配置。不要把配置里的任务规则仅改成无效展示副本。
- 日志：运行状态目录 data/logs/gateway.log、task-bridge/runtime/service-*.log。输出前脱敏。
- 验证证据写在本地 `.local/` 或调用者交付目录，不发布真实群/任务 ID。
- 团队治理要求 Linux 容器、local 执行后端、bubblewrap 和 compose 中的 seccomp 配置。不能关闭隔离来修复工具失败；普通产物写入 `/workspace/artifacts`。
- 管理员唯一配置是 `platforms.feishu.extra.admins`，绑定 `team_governance.app_id` 的 open_id；配置修改后重启生效。项目词典在所有已有入口可用，只配置词库，不设置群名单，不以个人授权替代机器人授权。
- Lark 进度覆盖专项回归：`.venv/bin/python team/tests/test_lark_reply_replacement.py`，使用临时运行目录；真实消息回读证据与日志保存在 `team/.local/progress-replacement/`，不提交真实群和消息 ID。
