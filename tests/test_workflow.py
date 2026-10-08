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


if __name__ == "__main__":
    unittest.main(verbosity=2)
