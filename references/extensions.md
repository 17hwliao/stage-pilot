# 扩展 Skill 选型

仅在用户要求搜索、选择扩展 Skill 或批准相关研究时读取，不是日常 Review 的依赖。
检索日期：2026-10-11。以下判断基于当次读取的项目 README 和 SKILL.md，安装前需再核对版本。

| 候选 | 用途与适配判断 |
| --- | --- |
| [TeamRetroHQ · ai-session-retro](https://github.com/TeamRetroHQ/teamretro-skills/tree/main/skills/ai-session-retro) | 记录有证据的摩擦、具体改进和首选动作，最适合借鉴后续方向的组织思路。其入口还要求独立记录、提交及回顾会话；直接安装需先调整这些行为与本人的成本／授权偏好。 |
| [obra · requesting-code-review](https://github.com/obra/superpowers/tree/main/skills/requesting-code-review) | 适合需要独立审查的开发流程，要求提供变更范围与需求，并派出 reviewer 子 Agent。本 Skill 默认由当前 Agent 聚焦审查，明确授权独立审查时才考虑扩展。 |
| [giannimassi · agent-retro](https://github.com/giannimassi/agent-retro) | 侧重完整会话、摩擦与 token 成本复盘。当前声明依赖 Claude Code 会话格式，Codex 支持仍在计划中；不推荐直接用于当前 Codex 流程。 |

本次选择：借鉴 ai-session-retro 的“依据 → 可执行改进 → 优先一步”思路，自行编写 next-steps.md。
不导入源码、自动提交机制、完整会话解析或第三方依赖；不安装额外 Skill。此选择是个人流程判断，不是这些项目的质量排名。
