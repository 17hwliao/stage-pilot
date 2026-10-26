# 阶段状态接口

仅在登记阶段、阶段末状态核对、记录明确选择或排查 Hook 时读取。常规回合不用运行 status。语义判断由 Agent 做，脚本验证状态转换与去重，不能证明验收证据或批准理由真实。

## 调用

Windows 运行 Python 及 scripts/stage_gate.py，参数：
--state-dir '<CODEX_HOME>/state/project-handoff' --action status

使用本机 Python 3.10+（以 <PYTHON_EXE> 表示），技能目录以 <SKILL_DIRECTORY> 表示；<CODEX_HOME> 取实际配置，未设置时默认 ~/.codex。PowerShell 对带空格的 Python 路径使用调用符 &。按实际安装位置替换占位符；脚本仅用标准库。session_id 优先取宿主 CODEX_THREAD_ID，也可显式 --session-id；turn_id 使用 Hook 已登记的真实值，或传宿主真实 --turn-id。缺少当前 turn 时不能编造编号，最终展示核验保持未知。

变更参数通过 UTF-8 JSON 文件传 --input-file '<临时请求文件>'。使用正常文件工具生成，避免拼接 JSON 到 shell；不写聊天原文或凭据。

| action | 请求字段与行为 |
| --- | --- |
| status | 无请求；只读本会话简短状态、压缩数和当前阶段。--full 仅排错时返回原状态，不写锁或迁移文件。 |
| plan | phase_key,title,project_root,criteria,confirmed:true；criteria 为1–5条完整标准，不凑数，project_root 为已核实的绝对目录。可加 task,baseline,related_files（最多12个）。同阶段同标准幂等；不能覆盖旧未收拢阶段。 |
| amend | phase_key,criteria,human_confirmed:true,approval_note；仅 active/awaiting_choice 的明确范围修订。保留原阶段编号和历史快照，revision 递增，新标准 id 使用 r2-c1 等；旧 checks 与 offer 失效，不继承旧验收通过。同标准重复请求幂等；同一阶段已有主动提示时不再发第二次。 |
| cancel | phase_key,human_confirmed:true,approval_note；如实取消未收拢阶段，不记成完成。取消待选或未派发 Review/交接授权后旧选择不可用；交接 dispatched 后拒绝，必须先核实修改权。取消账本不自动中断任何后台任务。 |
| complete | phase_key,checks:[{id:"c1",status:"met",evidence:"实际证据位置与结论"}],summary,safe:true,has_next,next_action,benefit。未满足用 not_met，未验证用 unverified。全部满足且安全才生成一次提示。无后续或无具体切换收益时不提供交接选项。 |
| choose | phase_key,offer_id,choice,human_confirmed:true,approval_note；choice 为 continue/review/handoff/both/close。Review 可带 review_mode:stage/full，省略为 stage；full 只记录用户明确的全量要求。只记录当前明确选择，不能预登记；普通“继续”不自动算选择。 |
| review-start | phase_key,request_id,human_confirmed:true,approval_note；仅已存在且匹配的当前阶段，用于直接要求 Review、明确改变审查范围或修复复核。mode 为 stage/full，普通初评缺省 stage；followup:true 时未给 mode 则沿用旧范围。request_id 为本次授权的稳定标识，不是伪造的 turn_id；同请求幂等，同编号不得换范围或目的。不能为登记而制造阶段。 |
| review-result | phase_key,summary,verified:true，可带 mode；经 review-start 发起时必须回传同 request_id，经 choose 发起时不带 request_id。须匹配已授权 stage/full，旧请求默认 stage。summary 保留实际修复、定向复核结论、剩余风险及证据位置；verified 仅表示流程确实执行，不表示所有问题修好。完成后不自动重新全量审查。 |
| handoff-progress | phase_key,handoff_id,target_thread_id,status,evidence；仅已授权交接可登记。status 为 saved/created/verified/dispatched/started/blocked。目标编号只用实际回执，同次交接不更换编号或重复创建，started 必须有首个实际动作证据。 |
| mute/resume | human_confirmed:true,approval_note；仅用户明确关闭／恢复提醒时使用，按当前 session 保存，不改压缩数。mute 把 awaiting_choice 记为 dismissed，旧选项不再授权动作；active 不记为完成。resume 不恢复旧提示。项目级静默需由已有项目载体在接续时继承，不宣称脚本会跨聊天自动继承。 |

plan 返回标准的稳定 id。complete 返回 eligible/offer_id/notice；只有 eligible 为 true 才展示实际 notice，作为最终正文的独立段落（无须首段）。Stop 排除代码块和引用后核对；缺失时最多续一轮补漏，再失败只记录未知。提问工具可用于用户选择，但不替代最终阶段状态说明。

amend 后必须按新 criteria id 提交新证据。若本阶段已发出过主动选择，后续 complete 仍可记录真实完成但 eligible=false、why=already_offered，不重复提示；用户显式 Review／保存请求仍按其范围直接执行，不制造 offer。cancelled/dismissed 是已收拢状态，不代表验收成功、同意继续或授权 Review。新聊天接续时采用已核实阶段事实，不复制旧 offer 当作新授权。

直接 Review 或已做过 Review 后再复核，优先通过 review-start 记录当前明确授权，不重复 choose 旧 offer。旧结果保留在 review_history，当前结果带 mode、request_id、followup；followup 是定向复核标记，不是第三种审查范围。active 中途审查结束后回到 active，不能替代验收；待选阶段直接要求 Review 后收拢待选状态，不补问。已有“先 Review 后交接”意图保留，结果仍转入 handoff_authorized；已进入交接执行或取消的阶段不直接重新取得修改权。无匹配阶段时只在已有载体／回复记录，不调用这些动作制造阶段。

amend 同时使当前 Review 结果和请求失效，旧内容仍保存在修订快照；不能把旧范围审查当作新范围通过。旧 request_id 也不能写入后续通过 choose 发起的 Review。

## Hook 和迁移

SessionStart/UserPromptSubmit 仅登记真实回合；普通回合无附加输出。恢复、压缩后只输出一次短阶段状态。PostCompact(auto) 加一，手动事件不计，已识别子 Agent 跳过，不读会话正文。Stop 只核验当前已生成的提示，不扫描自然语言猜阶段、不从用户 prompt 自动批准动作。

状态按 session_id 的 SHA-256 文件名分隔，保留旧 auto_count 与历史字段，新增 phase_gate 命名空间。status 的兼容扩展只在内存中进行；下次合法写入才保存。未知旧数据不重置。脚本错误保留原文件，Hook 返回简短系统消息，不阻断业务。每会话保留最近最多20个已结束阶段；当前和未结束阶段不裁掉。

配置模板位于 [codex-hooks.example.json](../hooks/codex-hooks.example.json)，使用原有兼容入口。脚本模拟不等于真实宿主事件验证。新机器或改 Hook 定义时，按 [官方信任流程](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks) 审阅；不改信任数据库，不绕过信任。
