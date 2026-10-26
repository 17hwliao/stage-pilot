# 旧接口迁移

现在使用 [stage-state.md](stage-state.md)。compaction_reminder.py 保留为已配置 Hook 的兼容入口；不再按第三次压缩或冷却阈值触发提醒。原计数和提醒证据保留，旧待展示建议不自动恢复。

新版接口不接受旧 prepare／evaluate 次数提醒命令；收到旧命令会明确报错且不改状态。现有 hooks.json 的命令定义保持原样；不修改原生信任记录，也不新增定时任务。新机器按打包附页配置并通过宿主信任。