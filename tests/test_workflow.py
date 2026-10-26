import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import stage_gate as gate
import handoff_check as checker


class StageWorkflow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / "中文项目"
        self.project.mkdir()
        self.states = self.root / "states"
        self.event = {"session_id": "session-a", "turn_id": "turn-1"}

    def tearDown(self):
        self.tmp.cleanup()

    def action(self, action, **data):
        return gate.process(self.event, self.states, action, data)

    def hook(self, name, **values):
        return gate.process(dict(self.event, hook_event_name=name, **values), self.states)

    def plan(self, key="module-a"):
        return self.action("plan", phase_key=key, title="主要模块", project_root=str(self.project),
                           criteria=["正常输入能完成任务", "断网时仍能启动"], confirmed=True)

    def finish(self, has_next=True, safe=True, benefit="下一阶段为独立模块", status="met"):
        return self.action("complete", phase_key="module-a", checks=[
            {"id": "c1", "status": "met", "evidence": "测试正常路径通过"},
            {"id": "c2", "status": status, "evidence": "断网检查结果"}],
            summary="相关行为验证通过。", safe=safe, has_next=has_next,
            next_action="开发下一模块", benefit=benefit)

    def choose(self, result, choice):
        return self.action("choose", phase_key="module-a", offer_id=result["offer_id"],
                           choice=choice, human_confirmed=True, approval_note="用户明确选择 " + choice)

    def test_status_has_no_filesystem_side_effects(self):
        result = self.action("status")
        self.assertEqual(result["auto_compactions"], 0)
        self.assertFalse(self.states.exists())

    def test_retired_count_commands_do_not_create_state(self):
        with self.assertRaisesRegex(ValueError, "retired"):
            self.action("prepare", reason="count")
        self.assertFalse(self.states.exists())

    def test_stale_host_turn_cannot_authorize_a_current_offer(self):
        self.hook("UserPromptSubmit", prompt="开发模块")
        self.plan()
        result = self.finish()
        old_event = dict(self.event, turn_id="turn-old")
        with self.assertRaisesRegex(ValueError, "current host turn"):
            gate.process(old_event, self.states, "choose", {
                "phase_key": "module-a", "offer_id": result["offer_id"], "choice": "both",
                "human_confirmed": True, "approval_note": "错误旧回合"})
        self.assertEqual(self.action("status")["phase"]["status"], "awaiting_choice")

    def test_many_compactions_do_not_trigger_a_prompt(self):
        self.plan()
        for _ in range(10):
            self.assertEqual(self.hook("PostCompact", trigger="auto"), {})
            self.assertEqual(self.hook("Stop", last_assistant_message="继续完成主要模块"), {})
        self.assertEqual(self.action("status")["auto_compactions"], 10)
        self.assertEqual(self.action("status")["phase"]["status"], "active")

    def test_manual_compaction_and_subagents_do_not_count(self):
        self.assertEqual(self.hook("PostCompact", trigger="manual"), {})
        self.assertEqual(self.hook("PostCompact", trigger="auto", agent_id="child"), {})
        self.assertFalse(self.states.exists())

    def test_host_turn_registered_without_context_injection(self):
        self.assertEqual(self.hook("UserPromptSubmit", prompt="开发模块"), {})
        self.assertEqual(self.action("status")["turn_id"], "turn-1")

    def test_normal_turns_are_silent_and_restore_is_once(self):
        self.plan()
        self.assertEqual(self.hook("UserPromptSubmit", prompt="继续"), {})
        self.hook("PostCompact", trigger="auto")
        result = self.hook("UserPromptSubmit", prompt="继续")
        self.assertIn("hookSpecificOutput", result)
        self.assertLess(len(result["hookSpecificOutput"]["additionalContext"]), 220)
        self.assertEqual(self.hook("UserPromptSubmit", prompt="继续"), {})

    def test_unverified_or_unsafe_work_does_not_offer(self):
        self.plan()
        self.assertFalse(self.finish(status="unverified")["eligible"])
        self.assertFalse(self.finish(safe=False)["eligible"])
        self.assertTrue(self.finish()["eligible"])

    def test_missing_acceptance_evidence_rejected_atomically(self):
        self.plan()
        path = gate.state_path(self.states, "session-a")
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.action("complete", phase_key="module-a", checks=[], safe=True, has_next=True)
        self.assertEqual(path.read_bytes(), original)

    def test_same_phase_offers_only_once(self):
        self.plan()
        result = self.finish()
        self.assertTrue(result["eligible"])
        self.assertFalse(self.finish()["eligible"])
        self.choose(result, "continue")
        self.assertFalse(self.finish()["eligible"])
        self.assertTrue(self.plan("module-b")["registered"])

    def test_scope_and_unfinished_phase_cannot_be_overwritten(self):
        self.plan()
        self.assertFalse(self.plan()["registered"])
        with self.assertRaises(ValueError):
            self.plan("module-b")
        with self.assertRaises(ValueError):
            self.action("plan", phase_key="module-a", title="改名", project_root=str(self.project),
                        criteria=["新标准", "其他标准"], confirmed=True)

    def test_review_and_handoff_require_human_choice(self):
        self.plan()
        result = self.finish()
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", summary="审查通过", verified=True)
        with self.assertRaises(ValueError):
            self.action("choose", phase_key="module-a", offer_id=result["offer_id"], choice="review")
        self.hook("UserPromptSubmit", prompt="好的，继续做")
        self.assertEqual(self.action("status")["phase"]["status"], "awaiting_choice")

    def test_review_does_not_authorize_handoff_or_repeat_review(self):
        self.plan()
        self.choose(self.finish(), "review")
        self.action("review-result", phase_key="module-a", summary="本范围无确定问题", verified=True)
        with self.assertRaises(ValueError):
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="saved", evidence="保存")
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", summary="再次审查", verified=True)

    def test_both_runs_review_before_ordered_handoff(self):
        self.plan()
        self.choose(self.finish(), "both")
        with self.assertRaises(ValueError):
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="saved", evidence="保存")
        self.action("review-result", phase_key="module-a", summary="有已确认问题，记录接续处理", verified=True)
        for step in ["saved", "created", "verified", "dispatched", "started"]:
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status=step,
                        target_thread_id="target-1" if step != "saved" else None,
                        evidence="实际工具回执／首个动作证据 " + step)
        self.assertEqual(self.action("status")["phase"]["status"], "handed_off")

    def test_handoff_cannot_skip_verification_or_change_target(self):
        self.plan()
        self.choose(self.finish(), "handoff")
        self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="saved", evidence="保存回读完成")
        with self.assertRaises(ValueError):
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="started", target_thread_id="t", evidence="动作")
        self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="created", target_thread_id="t", evidence="创建回执")
        with self.assertRaises(ValueError):
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status="verified", target_thread_id="other", evidence="改目标")

    def test_project_end_and_no_benefit_do_not_offer_handoff(self):
        self.plan()
        result = self.finish(has_next=False)
        with self.assertRaises(ValueError):
            self.choose(result, "handoff")
        self.choose(result, "close")
        self.assertEqual(self.action("status")["phase"]["status"], "closed")

    def test_no_switching_benefit_has_only_continue_and_review(self):
        self.plan()
        result = self.finish(benefit="")
        with self.assertRaises(ValueError):
            self.choose(result, "both")

    def test_muting_preserves_count_and_avoids_prompts(self):
        self.plan()
        self.hook("PostCompact", trigger="auto")
        self.action("mute", human_confirmed=True, approval_note="用户本项目不提醒")
        self.assertFalse(self.finish()["eligible"])
        self.assertEqual(self.action("status")["auto_compactions"], 1)

    def test_mute_pending_offer_stops_retry_and_allows_next_phase(self):
        self.plan()
        result = self.finish()
        self.action("mute", human_confirmed=True, approval_note="用户关闭本聊天提醒")
        self.assertEqual(self.hook("Stop", last_assistant_message="提醒已关闭"), {})
        self.assertEqual(self.action("status")["phase"]["status"], "dismissed")
        with self.assertRaises(ValueError):
            self.choose(result, "both")
        self.action("resume", human_confirmed=True, approval_note="用户恢复提醒")
        self.assertEqual(self.hook("Stop", last_assistant_message="已恢复"), {})
        self.assertTrue(self.plan("module-b")["registered"])

    def test_legacy_muted_pending_offer_does_not_retry(self):
        self.plan()
        self.finish()
        path = gate.state_path(self.states, "session-a")
        old = json.loads(path.read_text(encoding="utf-8"))
        old["muted"] = True
        path.write_text(json.dumps(old), encoding="utf-8")
        self.assertEqual(self.hook("Stop", last_assistant_message="停止提示"), {})
        self.assertEqual(self.action("status")["notice_count"], 0)

    def test_single_criterion_is_accepted_without_padding(self):
        self.action("plan", phase_key="single", title="独立模块", project_root=str(self.project),
                    criteria=["满足唯一的完整行为契约"], confirmed=True)
        result = self.action("complete", phase_key="single", checks=[
            {"id": "c1", "status": "met", "evidence": "契约测试通过"}],
            summary="已核实", safe=True, has_next=False)
        self.assertTrue(result["eligible"])

    def test_amend_keeps_history_and_invalidates_old_checks(self):
        self.plan()
        self.finish(status="unverified")
        self.action("amend", phase_key="module-a", criteria=["改为离线也可恢复"],
                    human_confirmed=True, approval_note="用户明确修改阶段范围")
        phase = self.action("status")["phase"]
        self.assertEqual(phase["status"], "active")
        self.assertEqual(phase["revision"], 2)
        self.assertIsNone(phase["checks"])
        self.assertNotEqual(phase["criteria"][0]["id"], "c1")
        full = gate.process(self.event, self.states, "status", full=True)
        history = full["phase_gate"]["phases"]["module-a"]["revisions"]
        self.assertEqual(history[0]["previous"]["criteria"][0]["id"], "c1")
        self.assertEqual(history[0]["previous"]["checks"][1]["status"], "unverified")
        with self.assertRaises(ValueError):
            self.finish()

    def test_amend_pending_invalidates_offer_without_repeating_prompt(self):
        self.plan()
        old = self.finish()
        self.action("amend", phase_key="module-a", criteria=["新的真实行为"],
                    human_confirmed=True, approval_note="用户明确修改验收条件")
        self.assertEqual(self.hook("Stop", last_assistant_message="按新范围继续"), {})
        with self.assertRaises(ValueError):
            self.choose(old, "review")
        criterion = self.action("status")["phase"]["criteria"][0]["id"]
        result = self.action("complete", phase_key="module-a", checks=[
            {"id": criterion, "status": "met", "evidence": "新契约通过"}],
            summary="修改后的范围完成", safe=True, has_next=False)
        self.assertFalse(result["eligible"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["why"], "already_offered")
        self.assertTrue(self.plan("module-b")["registered"])

    def test_scope_changes_require_human_authorization_and_are_atomic(self):
        self.plan()
        path = gate.state_path(self.states, "session-a")
        original = path.read_bytes()
        for action, data in [("amend", {"criteria": ["新的范围"]}), ("cancel", {}),
                             ("amend", {"criteria": [], "human_confirmed": True,
                                        "approval_note": "用户改变范围"})]:
            with self.subTest(action=action, data=data), self.assertRaises(ValueError):
                self.action(action, phase_key="module-a", **data)
            self.assertEqual(path.read_bytes(), original)

    def test_cancel_does_not_claim_completion_or_authorize_old_offer(self):
        self.plan()
        offer = self.finish()
        self.action("cancel", phase_key="module-a", human_confirmed=True, approval_note="用户取消此阶段")
        self.assertEqual(self.action("status")["phase"]["status"], "cancelled")
        self.assertEqual(self.hook("Stop", last_assistant_message="已取消"), {})
        with self.assertRaises(ValueError):
            self.choose(offer, "both")
        self.assertTrue(self.plan("module-b")["registered"])

    def test_scope_cannot_change_after_handoff_dispatch(self):
        self.plan()
        self.choose(self.finish(), "handoff")
        for step in ["saved", "created", "verified", "dispatched"]:
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status=step,
                        target_thread_id="target-1" if step != "saved" else None, evidence="回执 " + step)
        for action in ["amend", "cancel"]:
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.action(action, phase_key="module-a", criteria=["新的范围"],
                            human_confirmed=True, approval_note="用户改变范围")

    def test_review_modes_are_explicit_and_result_cannot_switch_scope(self):
        self.plan()
        offer = self.finish()
        with self.assertRaises(ValueError):
            self.action("choose", phase_key="module-a", offer_id=offer["offer_id"], choice="review",
                        review_mode="unknown", human_confirmed=True, approval_note="用户要求审查")
        self.action("choose", phase_key="module-a", offer_id=offer["offer_id"], choice="review",
                    review_mode="full", human_confirmed=True, approval_note="用户明确要求全量 Review")
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", summary="结果", mode="stage", verified=True)
        self.action("review-result", phase_key="module-a", summary="全量审查及定向修复复核已完成",
                    mode="full", verified=True)
        self.assertEqual(self.action("status")["phase"]["review"]["mode"], "full")

    def test_review_defaults_to_stage_for_old_requests(self):
        self.plan()
        self.choose(self.finish(), "review")
        self.action("review-result", phase_key="module-a", summary="阶段修复复核完成", verified=True)
        self.assertEqual(self.action("status")["phase"]["review"]["mode"], "stage")

    def test_same_scope_amend_is_idempotent_and_preserves_evidence(self):
        self.plan()
        self.finish(status="unverified")
        path = gate.state_path(self.states, "session-a")
        original = path.read_bytes()
        result = self.action("amend", phase_key="module-a", criteria=["正常输入能完成任务", "断网时仍能启动"],
                             human_confirmed=True, approval_note="重复交付同一已确认范围")
        self.assertFalse(result["amended"])
        self.assertEqual(path.read_bytes(), original)

    def test_active_muting_does_not_complete_or_cancel_work(self):
        self.plan()
        self.action("mute", human_confirmed=True, approval_note="只关提醒，工作继续")
        self.assertEqual(self.action("status")["phase"]["status"], "active")
        with self.assertRaises(ValueError):
            self.plan("module-b")
        self.action("cancel", phase_key="module-a", human_confirmed=True, approval_note="随后用户取消工作")
        self.assertTrue(self.plan("module-b")["registered"])

    def test_invalid_criteria_cannot_replace_scope(self):
        self.plan()
        path = gate.state_path(self.states, "session-a")
        original = path.read_bytes()
        for criteria in [[], [""], ["重复", " 重复 "], ["目标"] * 6]:
            with self.subTest(criteria=criteria), self.assertRaises(ValueError):
                self.action("amend", phase_key="module-a", criteria=criteria,
                            human_confirmed=True, approval_note="用户变更范围")
            self.assertEqual(path.read_bytes(), original)

    def test_stop_receipt_excludes_examples_and_retries_once(self):
        self.plan()
        result = self.finish()
        self.assertEqual(self.hook("Stop", last_assistant_message="```\n" + result["notice"] + "\n```")["decision"], "block")
        self.assertEqual(self.hook("Stop", last_assistant_message="> " + result["notice"], stop_hook_active=True), {})
        self.assertEqual(self.action("status")["notice_count"], 0)
        self.assertEqual(self.hook("Stop", last_assistant_message="完成结果。\n\n" + result["notice"]), {})
        self.assertEqual(self.action("status")["notice_count"], 1)
        self.hook("Stop", last_assistant_message=result["notice"])
        self.assertEqual(self.action("status")["notice_count"], 1)

    def start_review(self, request_id="review-full-1", mode="full", followup=False):
        return self.action("review-start", phase_key="module-a", request_id=request_id,
                           mode=mode, followup=followup, human_confirmed=True,
                           approval_note="用户直接要求本轮审查／修复复核")

    def test_direct_review_during_active_phase_does_not_complete_acceptance(self):
        self.plan()
        self.start_review()
        self.assertFalse(self.finish()["eligible"])
        self.action("review-result", phase_key="module-a", request_id="review-full-1",
                    mode="full", summary="全量审查完成，开发范围尚未完成", verified=True)
        self.assertEqual(self.action("status")["phase"]["status"], "active")
        self.assertTrue(self.finish()["eligible"])

    def test_direct_full_review_resolves_pending_offer_without_second_prompt(self):
        self.plan()
        self.finish()
        self.start_review()
        self.action("review-result", phase_key="module-a", request_id="review-full-1",
                    summary="已执行全量审查和修复复核", verified=True)
        self.assertEqual(self.hook("Stop", last_assistant_message="Review 完成"), {})
        self.assertTrue(self.plan("module-b")["registered"])

    def test_followup_preserves_full_scope_history_and_rejects_stale_result(self):
        self.plan()
        offer = self.finish()
        self.action("choose", phase_key="module-a", offer_id=offer["offer_id"], choice="review",
                    review_mode="full", human_confirmed=True, approval_note="全量 Review")
        self.action("review-result", phase_key="module-a", mode="full", summary="发现真实问题", verified=True)
        self.action("review-start", phase_key="module-a", request_id="followup-1", followup=True,
                    human_confirmed=True, approval_note="复核上轮修复")
        self.assertEqual(self.action("status")["phase"]["review_mode"], "full")
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", request_id="old-review", summary="旧回执", verified=True)
        self.action("review-result", phase_key="module-a", request_id="followup-1", summary="定向复核通过", verified=True)
        phase = gate.process(self.event, self.states, "status", full=True)["phase_gate"]["phases"]["module-a"]
        self.assertTrue(phase["review"]["followup"])
        self.assertEqual(phase["review_history"][0]["summary"], "发现真实问题")
        self.assertFalse(self.start_review("followup-1", followup=True)["authorized"])

    def test_explicit_scope_change_is_not_silently_treated_as_duplicate_choice(self):
        self.plan()
        offer = self.finish()
        self.choose(offer, "review")
        with self.assertRaises(ValueError):
            self.action("choose", phase_key="module-a", offer_id=offer["offer_id"], choice="review",
                        review_mode="full", human_confirmed=True, approval_note="这次要求全量")
        self.start_review()
        self.action("review-result", phase_key="module-a", request_id="review-full-1",
                    mode="full", summary="全量审查完成", verified=True)
        self.assertEqual(self.action("status")["phase"]["status"], "reviewed")

    def test_review_start_requires_human_choice_and_stable_request_scope(self):
        self.plan()
        path = gate.state_path(self.states, "session-a")
        original = path.read_bytes()
        with self.assertRaises(ValueError):
            self.action("review-start", phase_key="module-a", request_id="r1", mode="full")
        self.assertEqual(path.read_bytes(), original)
        self.start_review()
        self.assertFalse(self.start_review()["authorized"])
        with self.assertRaises(ValueError):
            self.start_review(mode="stage")

    def test_review_start_preserves_both_handoff_intent(self):
        self.plan()
        self.choose(self.finish(), "both")
        self.start_review()
        self.action("review-result", phase_key="module-a", request_id="review-full-1",
                    summary="全量审查完成", verified=True)
        self.assertEqual(self.action("status")["phase"]["status"], "handoff_authorized")

    def test_direct_review_cannot_reclaim_dispatched_handoff(self):
        self.plan()
        self.choose(self.finish(), "handoff")
        for step in ["saved", "created", "verified", "dispatched"]:
            self.action("handoff-progress", phase_key="module-a", handoff_id="h1", status=step,
                        target_thread_id="target-1" if step != "saved" else None, evidence="回执 " + step)
        with self.assertRaises(ValueError):
            self.start_review()

    def test_direct_review_then_stage_offer_remains_compatible_with_choose(self):
        self.plan()
        self.start_review()
        self.action("review-result", phase_key="module-a", request_id="review-full-1", summary="中途全量审查完成", verified=True)
        offer = self.finish()
        self.assertNotIn("尚未执行 AI Review", offer["notice"])
        self.choose(offer, "review")
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", request_id="review-full-1",
                        summary="前轮的迟到回执", verified=True)
        self.action("review-result", phase_key="module-a", summary="新增阶段改动复核完成", verified=True)
        self.assertEqual(self.action("status")["phase"]["review"]["mode"], "stage")

    def test_amend_after_active_review_invalidates_old_review_scope(self):
        self.plan()
        self.start_review()
        self.action("review-result", phase_key="module-a", request_id="review-full-1",
                    summary="旧范围全量审查完成", verified=True)
        self.action("amend", phase_key="module-a", criteria=["新的用户契约"],
                    human_confirmed=True, approval_note="用户改变阶段范围")
        phase = gate.process(self.event, self.states, "status", full=True)["phase_gate"]["phases"]["module-a"]
        self.assertNotIn("review", phase)
        self.assertNotIn("review_request", phase)
        self.assertEqual(phase["revisions"][0]["previous"]["review"]["summary"], "旧范围全量审查完成")

    def test_stale_result_cannot_overwrite_a_new_direct_request(self):
        self.plan()
        self.start_review()
        self.start_review("review-stage-2", mode="stage")
        with self.assertRaises(ValueError):
            self.action("review-result", phase_key="module-a", request_id="review-full-1",
                        mode="full", summary="前轮回执", verified=True)
        self.action("review-result", phase_key="module-a", request_id="review-stage-2",
                    summary="当前范围结果", verified=True)
        self.assertEqual(self.action("status")["phase"]["status"], "active")

    def test_legacy_state_preserves_counts_and_cancels_count_trigger(self):
        self.states.mkdir()
        path = gate.state_path(self.states, "session-a")
        original = {"schema": 1, "session_id": "session-a", "auto_count": 19, "muted": False,
                    "tracking_version": 3, "proposal": {"notice": "旧建议", "closed": False},
                    "reminder_count": 2, "next_reminder_at": 3}
        path.write_text(json.dumps(original), encoding="utf-8")
        before = path.read_bytes()
        self.assertEqual(self.action("status")["auto_compactions"], 19)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.hook("Stop", last_assistant_message="完成"), {})
        migrated = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(migrated["reminder_count"], 2)
        self.assertEqual(migrated["auto_count"], 19)

    def test_corrupt_state_is_not_overwritten(self):
        self.states.mkdir()
        path = gate.state_path(self.states, "session-a")
        path.write_text("invalid-json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.hook("PostCompact", trigger="auto")
        self.assertEqual(path.read_text(encoding="utf-8"), "invalid-json")

    def test_hook_compatibility_entry_accepts_real_wire_shape(self):
        event = dict(self.event, hook_event_name="PostCompact", trigger="auto")
        result = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPTS / "compaction_reminder.py"),
                                 "--state-dir", str(self.states)], input=json.dumps(event),
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(self.action("status")["auto_compactions"], 1)


class HandoffDocument(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "中文目录"
        self.root.mkdir()
        self.source = self.root / "成果.txt"
        self.source.write_text("完成成果", encoding="utf-8")
        self.meta = {"id": "h1", "phase_key": "module-a", "project_root": str(self.root),
                     "files": [{"path": "成果.txt", "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest()}]}

    def tearDown(self):
        self.tmp.cleanup()

    def document(self, omit=None):
        content = "```handoff-meta\n" + json.dumps(self.meta, ensure_ascii=False) + "\n```\n\n"
        for section in checker.SECTIONS:
            if section != omit:
                content += "## " + section + "\n\n已核实的任务事实和明确接续动作。\n\n"
        path = self.root / "PROJECT_STATE.md"
        path.write_text(content, encoding="utf-8")
        return path

    def test_valid_chinese_paths_and_short_sections(self):
        result = checker.validate(self.document())
        self.assertTrue(result["valid"], result)
        self.assertEqual(result["files_verified"], 1)

    def test_missing_section_is_failure_not_a_quality_score(self):
        self.assertFalse(checker.validate(self.document("接续第一步"))["valid"])

    def test_missing_reference_is_failure(self):
        self.meta["files"] = ["不存在.txt"]
        self.assertFalse(checker.validate(self.document())["valid"])

    def test_changed_uncommitted_file_is_detected(self):
        path = self.document()
        self.source.write_text("之后又修改了", encoding="utf-8")
        result = checker.validate(path, check_git=True)
        self.assertTrue(any("file changed" in w for w in result["warnings"]))

    def append(self, path, text):
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n" + text)
        return path

    def test_placeholder_rejected_but_pending_work_is_allowed(self):
        path = self.document()
        self.append(path, "TODO 后续补充另一个模块的测试，未包含在本阶段验收范围。")
        self.assertTrue(checker.validate(path)["valid"])
        self.append(path, "[待填写: 首个接续动作]")
        self.assertFalse(checker.validate(path)["valid"])

    def test_template_is_not_a_finished_handoff(self):
        template = SCRIPTS.parent / "assets" / "handoff-template.md"
        self.assertFalse(checker.validate(template)["valid"])

    def test_credentials_rejected_without_echoing_values(self):
        secret = "synthetic-only-do-not-use-987654321"
        path = self.append(self.document(), 'api_key = "' + secret + '"')
        result = checker.validate(path)
        self.assertFalse(result["valid"])
        self.assertNotIn(secret, json.dumps(result))

    def test_redacted_values_and_environment_references_allowed(self):
        path = self.append(self.document(), 'password: ***\napi_key: ${SERVICE_KEY}\nclient_secret: null\naccess_token: "<redacted>"')
        self.assertTrue(checker.validate(path)["valid"])

    def test_private_key_url_password_and_bearer_detected(self):
        for content in ["-----BEGIN PRIVATE KEY-----", "https://user:synthetic@example.invalid/path",
                        "Authorization: Bearer " + "x" * 24]:
            with self.subTest(content=content[:20]):
                self.assertTrue(checker.credential_labels(content))

    def test_metadata_credential_is_checked(self):
        self.meta["api_key"] = "synthetic-only"
        self.assertFalse(checker.validate(self.document())["valid"])

    def test_record_selection_does_not_scan_unrelated_records(self):
        path = self.document()
        self.append(path, '```handoff-meta\n{"id":"other"}\n```\napi_key: synthetic-other')
        self.assertTrue(checker.validate(path, record_id="h1")["valid"])
        with self.assertRaises(ValueError):
            checker.validate(path)

    def test_selected_record_survives_unrelated_malformed_metadata(self):
        path = self.document()
        self.append(path, '```handoff-meta\n{invalid-json}\n```\n历史损坏记录')
        self.assertTrue(checker.validate(path, record_id="h1")["valid"])
        with self.assertRaises(ValueError):
            checker.validate(path)

    def test_selected_record_survives_unrelated_non_object_metadata(self):
        path = self.document()
        self.append(path, '```handoff-meta\n[]\n```\n历史记录')
        self.assertTrue(checker.validate(path, record_id="h1")["valid"])

    def test_single_non_object_metadata_fails_cleanly(self):
        path = self.root / "invalid.md"
        path.write_text('```handoff-meta\n[]\n```\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            checker.validate(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
