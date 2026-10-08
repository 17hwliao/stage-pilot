# 设计与借鉴来源

作者：17hwliao。此页只在了解实现或维护 Skill 时读取。

## 设计

Agent 根据约定的交付范围、最低验收标准和已有验证证据识别真实阶段边界。
Python 不计算“完成概率”，不读完整聊天，也不按轮数判断结束；它只验证有证据的状态转换、记录用户选择并去重。
Skill 默认被动触发：发现多步骤开发／主要模块实现时采用流程，继续阶段时复用标准，阶段验收完成时才提示。
配置 `allow_implicit_invocation: true` 允许模型按描述自动选择，无需用户输入调用名；这并非每轮强制执行或定时监听。
首次介入进行中的开发只使用当前已知事实；不回扫全部历史或补发旧阶段提醒。

主流程：active → awaiting_choice → continued / closed / review_authorized / handoff_authorized / review_then_handoff。
Review 后交接先登记实际 Review 结果。完整交接依次为 saved → created → verified → dispatched → started。
用户选择、工具回执、验证证据由 Agent 如实提交；脚本无法独立证明这些语义事实。

每个阶段最多一个主动选择。普通回合不运行状态查询；阶段末只查本阶段缺失证据。
Hook 在本地处理短 JSON，普通回合不注入文本，压缩或恢复后只注入一次短状态。
真实自动压缩次数只说明观测到多少次事件，不能换算上下文占用率。

运行脚本仅用 Python 标准库。交接校验只读选中的记录和最多12个明确文件引用；
可选版本核对使用 Git 的只读查询，即便目标项目没有 Git 也可保存交接。
校验不替代业务验收、安全审计或用户授权。

## 思路参考

- [softaworks/agent-toolkit · session-handoff](https://github.com/softaworks/agent-toolkit/tree/main/skills/session-handoff)：参考保存关键上下文、核对接续材料的做法。曾评估的版本为 3027f20f3181758385a1bb8c022d4041dfb4de84；本版本没有导入其实现。
- [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files)：参考以文件保留任务状态和决定的做法。本版本复用已有载体，避免固定创建多份文件。
- [obra/superpowers](https://github.com/obra/superpowers)：参考在工作节点审查变更的做法。本版本保留先询问用户、一次聚焦 Review 的个人偏好。

这些是借鉴说明，不是运行依赖。日常调用无需联网查询、下载或加载它们。
