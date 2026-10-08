# 阶段状态接口

仅在登记阶段、阶段末状态核对、记录明确选择或排查 Hook 时读取。常规回合不用运行 status。语义判断由 Agent 做，脚本验证状态转换与去重，不能证明验收证据或批准理由真实。

## 调用

Windows 运行 Python 及 scripts/stage_gate.py，参数：
--state-dir '<CODEX_HOME>/state/project-handoff' --action status

本机 Python 是 C:/Program Files/Python310/python.exe，技能目录是 C:/Users/17hwliao/.codex/skills/stage-pilot，CODEX_HOME 默认 C:/Users/17hwliao/.codex。PowerShell 对带空格的 Python 路径使用调用符 &。其他机器替换路径；脚本仅用标准库。session_id 优先取宿主 CODEX_THREAD_ID，也可显式 --session-id；turn_id 使用 Hook 已登记的真实值，或传宿主真实 --turn-id。缺少当前 turn 时不能编造编号，最终展示核验保持未知。

变更参数通过 UTF-8 JSON 文件传 --input-file '<临时请求文件>'。使用正常文件工具生成，避免拼接 JSON 到 shell；不写聊天原文或凭据。

| action | 请求字段与行为 |
| --- | --- |
| status | 无请求；只读本会话简短状态、压缩数和当前阶段。--full 仅排错时返回原状态，不写锁或迁移文件。 |
| plan | phase_key,title,project_root,criteria,confirmed:true；criteria 为2–5条明确标准，project_root 为已核实的绝对目录。可加 task,baseline,related_files（最多12个）。同阶段同标准幂等；不能覆盖旧未收拢阶段。 |
| complete | phase_key,checks:[{id:"c1",status:"met",evidence:"实际证据位置与结论"}],summary,safe:true,has_next,next_action,benefit。未满足用 not_met，未验证用 unverified。全部满足且安全才生成一次提示。无后续或无具体切换收益时不提供交接选项。 |
| choose | phase_key,offer_id,choice,human_confirmed:true,approval_note；choice 为 continue/review/handoff/both/close。只记录用户对当前选项的明确选择，不能预登记；普通“继续”不自动算选择。 |
| review-result | phase_key,summary,verified:true；仅已授权 Review 可以登记实际结果。发现问题不等于批准修复，完成后不自动重审。 |
| handoff-progress | phase_key,handoff_id,target_thread_id,status,evidence；仅已授权交接可登记。status 为 saved/created/verified/dispatched/started/blocked。目标编号只用实际回执，同次交接不更换编号或重复创建，started 必须有首个实际动作证据。 |
| mute/resume | human_confirmed:true,approval_note；只有用户明确关闭／恢复主动提醒时使用，不改压缩数。 |

plan 返回标准的稳定 id。complete 返回 eligible/offer_id/notice；只有 eligible 为 true 才展示实际 notice，作为最终正文的独立段落（无须首段）。Stop 排除代码块和引用后核对；缺失时最多续一轮补漏，再失败只记录未知。提问工具可用于用户选择，但不替代最终阶段状态说明。

## Hook 和迁移

SessionStart/UserPromptSubmit 仅登记真实回合；普通回合无附加输出。恢复、压缩后只输出一次短阶段状态。PostCompact(auto) 加一，手动事件不计，已识别子 Agent 跳过，不读会话正文。Stop 只核验当前已生成的提示，不扫描自然语言猜阶段、不从用户 prompt 自动批准动作。

状态按 session_id 的 SHA-256 文件名分隔，保留旧 auto_count 与历史字段，新增 phase_gate 命名空间。status 的兼容扩展只在内存中进行；下次合法写入才保存。未知旧数据不重置。脚本错误保留原文件，Hook 返回简短系统消息，不阻断业务。每会话保留最近最多20个已结束阶段；当前和未结束阶段不裁掉。

配置模板位于 [codex-hooks.example.json](../hooks/codex-hooks.example.json)，使用原有兼容入口。脚本模拟不等于真实宿主事件验证。新机器或改 Hook 定义时，按 [官方信任流程](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks) 审阅；不改信任数据库，不绕过信任。