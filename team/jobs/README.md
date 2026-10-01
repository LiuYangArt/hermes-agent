# 定时任务资产边界

每个子目录对应一个独立任务，保存该任务的脚本、业务规则和测试。任务脚本不进入 Core 镜像，也不由 `install-assets` 发布；实际运行文件保存在 `/opt/data/scripts` 持久卷。调度记录、业务配置、去重状态和凭据不进入 Git。

| 任务包 | 现有调度入口 | 独立验证 |
| --- | --- | --- |
| meegle-triage | `meegle_triage/run.py` | `python3 -m unittest discover -s team/jobs/meegle-triage -p 'test_*.py' -v` |
| daily-summary | `cron_daily_summary.py` | `python3 -m unittest discover -s team/jobs/daily-summary -p 'test_*.py' -v` |

维护某个任务时先核对 `python3 team/manage.py verify-job <name>`。若运行目录有额外修改，先审查并保存到该任务的源码，不能直接用仓库旧副本覆盖。修改并测试后，仅对获授权的任务执行 `python3 team/manage.py install-job <name>`；命令回读文件核对结果，不改调度、业务状态或其他任务。多文件任务更新应避开运行中的轮次，逐文件写入不是整个任务包的原子切换。

Core 更新仍须验证任务所依赖的 Python、CLI 和 Hermes 接口兼容性。这项隔离解决发布时的意外覆盖，不代表任意终端权限下的任务获得操作系统级隔离，也不保证引擎接口变更永远无影响。

发布边界回归：`python3 -m unittest team.tests.test_job_deployment -v`。该测试在临时目录真实执行公共安装和单任务安装，检查已有任务修改、其他任务文件与调度/业务配置不被覆盖。
