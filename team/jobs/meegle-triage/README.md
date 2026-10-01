# Meegle 每小时分类脚本

在原 Hermes 定时任务中使用原生 `no_agent` 模式运行。没有待处理缺陷时不调用模型；仅缺陷负责人判断使用 Hermes 当前默认模型及请求头。

业务设置存放于持久卷 `/opt/data/triage/config.json`，不包含密钥；登录复用现有 Meegle/Lark CLI。状态、去重恢复记录及负责人判断缓存也在 `/opt/data/triage`。源码由 `python3 team/manage.py install-assets` 部署至 `/opt/data/scripts/meegle_triage`，不依赖 `/workspace`。

标题忽略大小写并去除空白、连字符、下划线后，含 `featurerequest` 的转功能建议，其余转缺陷。转缺陷时，如果正文不含中文字符，则仅调用已配置的 Hermes 模型将正文翻译成简体中文；缺陷标题始终保持原样，不翻译标题。混合中英文正文不翻译。模型无权改变分类、执行工具、发通知或编辑规则。默认状态、关注人和人员映射由脚本校验；已有人为修改的目标不得重置。

创建结果不确定或回读失败时保留待处理，禁止盲目重建。来源只在目标正文、链接及必要字段验收后标记完成。模型请求失败作为任务错误保留，不伪装成无法确定职责。明确无匹配或负责人账号无效时使用已配置的 PM 兜底。

负责人结果区分 `matched`、`insufficient_evidence`、`document_unavailable`、`account_unavailable`，运行输出包含原因统计、候选人及待补证据。信息不足的新缺陷只保留一条 GitHub Issue 链接并置于最上方；其下插入加粗的“AI 分诊提示（未核实）”，并用 Markdown divider（`---`）与原始正文分隔。候选最多3人，待补信息最多3条、每条80字。该提示不代表正式派单或已确认根因。恢复时复用原决策并回读，避免重复插入；已有人工修改的提示不覆盖。已完成历史缺陷不自动回填，功能建议不增加此段。

这套脚本通过持久卷加载，每轮启动新进程。仅修改本目录时，测试通过后运行 `python3 team/manage.py install-assets`，核对源/部署一致性即可生效，无需重建镜像或重启正式服务。2026-09-23 验证记录位于 `team/.local/triage-evidence-20260923/`。

验证：`python3 -m unittest discover -s team/triage -p 'test_*.py' -v`。部署后在容器内运行主脚本 `--dry-run` 核对计划，再通过 Hermes 原定时任务入口执行实际轮次并回读。运行证据保存在 `team/.local/triage-script`；调度器正式输出位于 `/opt/data/cron/output`。
