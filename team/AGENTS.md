# Team 部署约束

- 此目录保存团队定制源文件。用户明确要求维护现有 main，不开新分支、不推送到上游仓库。
- 运行数据、配置、凭据、授权、聊天记录、任务记录不进 Git；`.local/` 同时被 Git 和 Docker 忽略。
- 主测试入口：`python3 team/manage.py verify`，调用已部署容器执行 16 项标准库 unittest；修改 Core 行为时追加相关上游 `scripts/run_tests.sh` 检查。
- 构建：`docker compose -f team/compose.yaml build --build-arg HERMES_GIT_SHA="$(git rev-parse HEAD)"`。
- 部署前安装受管资产：`python3 team/manage.py install-assets`；然后 `docker compose -f team/compose.yaml up -d --no-deps hermes-team`。
- 正式服务包括 hermes-team 和 com.hermes-team.lark-acp / com.hermes-team.lark-tasks，不能当调试进程结束。
- 保留 Tasks 专用身份与审批保护、模型 User-Agent 配置。不要把配置里的任务规则仅改成无效展示副本。
- 日志：运行状态目录 data/logs/gateway.log、task-bridge/runtime/service-*.log。输出前脱敏。
- 验证证据写在本地 `.local/` 或调用者交付目录，不发布真实群/任务 ID。
