<p align="center">
  <img src="assets/logo.svg" width="88" height="88" alt="Stage Pilot logo" />
</p>

<h1 align="center">Stage Pilot</h1>

<p align="center"><strong>Clear stages. Focused reviews. Reliable handoffs.</strong></p>

<p align="center">
  A compact workflow skill for Codex.<br />
  Define the scope, finish the stage, and choose the next step.
</p>

<p align="center">
  <img alt="Version 1.2.1" src="https://img.shields.io/badge/version-1.2.1-111111?style=flat-square" />
  <img alt="Python 3.10 or newer" src="https://img.shields.io/badge/python-3.10%2B-555555?style=flat-square" />
  <img alt="Python standard library" src="https://img.shields.io/badge/runtime-standard_library-777777?style=flat-square" />
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#review-with-context">Review</a> &nbsp;·&nbsp;
  <a href="#handoff-with-continuity">Handoff</a> &nbsp;·&nbsp;
  <a href="#under-the-hood">Under the hood</a>
</p>

---

Stage Pilot keeps multi-step development grounded in an agreed acceptance scope. It stays quiet during implementation, offers one decision when a major stage is complete, and preserves the context needed to continue.

| Define | Review | Continue |
| :--- | :--- | :--- |
| Reuse the requirements and acceptance criteria you already have. | Inspect a stage or the whole project, confirm real issues, and verify focused fixes. | Keep working in the current chat or explicitly hand off to a new one. |

## Quick start

Place the repository contents in your personal skill directory:

```text
<CODEX_HOME>/skills/stage-pilot/
├── SKILL.md
├── agents/
├── assets/
├── hooks/
├── references/
├── scripts/
└── tests/
```

When `CODEX_HOME` is unset, use `~/.codex`. Open a new chat if the skill list has not refreshed.

Then describe the work naturally:

```text
Build the version-checking module. For this stage, support checking for
updates, downloading the package, and reporting download failures.
```

You can also invoke `$stage-pilot` explicitly. Implicit invocation is enabled, but selection depends on the host and model; it is not a guaranteed background service.

### One stage, one decision

```text
Agree scope → Implement → Check acceptance → Choose the next step
                                              ├─ Continue
                                              ├─ Review
                                              ├─ Handoff
                                              └─ Review, then hand off
```

Existing requirements are reused. Missing acceptance details are clarified only when they matter. Questions, small fixes, and discussion of the skill itself do not create development stages. A handoff is offered only when there is more work and a concrete benefit to changing chats.

## Review with context

Two review scopes share one process:

| Scope | What it covers | Example request |
| :--- | :--- | :--- |
| **Stage review** · default | Current-stage changes, relevant callers, shared contracts, and affected boundaries. | “Review this stage.” |
| **Full-project review** · explicit | Major entry points, module relationships, data flow, shared state, conflicting features, and code defects. | “Run a full-project review.” |

The initial assessment follows six steps:

```text
Prepare → Review by layer → Run focused checks
        → Summarize risks → Propose fixes → Confirm the issues exist
```

Confirmed, in-scope defects receive minimal fixes and targeted verification. Read-only, plan-only, or design-before-implementation instructions take precedence. Optional refactoring, new features, public contract changes, and data migrations need their own scope and authorization.

### Verify the fix, then conclude

After either review scope, verification follows the original issue list and the repair diff:

- Confirm that the original failure is resolved.
- Check affected callers and shared state for new regressions.
- Recheck the relevant boundaries and high-risk paths.
- Run the small set of automated tests needed to support the conclusion.
- Report the evidence, remaining issues, and coverage limits.

“Verify the previous fixes” requests this follow-up. It does **not** start another complete project review. Broader inspection happens only when new evidence shows that the impact extends beyond the original scope.

Full-project reviews can also suggest internal cleanup, such as consolidating duplicate implementations or clarifying state ownership. Suggestions include the reason, a migration approach, and the behavior to protect; they are not silently implemented.

## Handoff with continuity

The wording determines the action:

| Request | Result |
| :--- | :--- |
| “Save the current progress.” | Save context in the project's existing state document; do not create a chat. |
| “Save a handoff, create and open a new chat in this project directory, then verify and continue.” | Run the complete handoff sequence using the host's available tools. |

```text
Save → Validate → Create → Verify the receiving chat → Dispatch → Confirm takeover
```

The receiving chat first checks the directory, handoff identity, artifacts, and authorization in read-only mode. Work continues after the source chat has recorded the handoff and sent the continuation instruction. An uncertain result is checked before retrying, and the same receiving chat is reused.

Missing host tools, an unmatched project directory, or inaccessible materials are reported as gaps. A created chat is not reported as a successful takeover.

## Under the hood

The model interprets requirements and evidence. Small Python helpers record state transitions and validate handoff structure; they cannot independently prove that acceptance criteria or human authorization are genuine.

| Component | Responsibility |
| :--- | :--- |
| [`SKILL.md`](SKILL.md) | Invocation boundaries, stage decisions, review routing, and handoff policy. |
| [`stage_gate.py`](scripts/stage_gate.py) | Per-chat stage state, explicit choices, review requests, deduplication, and ordered handoff progress. |
| [`handoff_check.py`](scripts/handoff_check.py) | Selected-record structure, explicit file references, and common credential patterns. |
| [`references/`](references/) | Detailed procedures, loaded when needed. These references currently use Chinese. |

An explicit scope amendment retains the stage identity and historical scope while invalidating old acceptance evidence and review results. Cancellation records a cancellation, not a successful completion. Muting dismisses pending prompts without authorizing review or handoff; the script's preference is local to the chat.

Direct reviews and later verification requests use stable request identities. Late results cannot overwrite a newer request, and an interim review cannot mark an unfinished development stage as accepted.

### Optional lifecycle hooks

The skill works without hooks. For event tracking and prompt-delivery checks, adapt the [hook configuration template](hooks/codex-hooks.example.json) to your machine and use the host's normal trust flow.

The configured events are `SessionStart`, `UserPromptSubmit`, automatic `PostCompact`, and `Stop`. Normal events process short local JSON. Automatic compactions are counted, but the count does not estimate context usage or trigger a handoff. `Stop` can request one continuation only for a valid, pending stage prompt that was omitted.

Legacy installations may keep the `project-handoff` forwarding entry and state directory. New installations can point the template directly at this skill. Keep backup skill folders outside the discovery directory.

## Validation

The helper scripts use **Python 3.10+ and the standard library**. Run the tests from the skill directory:

```sh
python -B -X utf8 -m unittest discover -s tests -v
```

The suite covers stage transitions, scope amendments, authorization boundaries, review requests, stale-result protection, prompt deduplication, handoff sequencing, and selected-record validation. `RELEASE.json` records resource hashes for source-integrity checks; it does not create a GitHub Release.

Passing these checks does not establish host-level skill discovery, real lifecycle delivery, or a successful cross-chat handoff. Coverage gaps remain explicit. Credential matching is limited to common patterns and does not replace a content review.

---

Created by **[17hwliao](https://github.com/17hwliao)**. See [authorship](AUTHOR.md) and [design references](references/design.md).
