"""Local stage ledger and Codex hook adapter; standard library, no model/network calls."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl

TERMINAL = {"continued", "closed", "reviewed", "handed_off", "dismissed", "cancelled"}
EVENTS = {"SessionStart", "UserPromptSubmit", "PostCompact", "Stop"}
ACTIONS = {"hook", "status", "plan", "amend", "cancel", "complete", "choose", "review-start", "review-result", "handoff-progress", "mute", "resume"}


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + 2
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("state lock timed out")
                time.sleep(0.025)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def text_arg(value, name, limit=240):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("missing or invalid " + name)
    return value.strip()


def state_path(root, session):
    return Path(root) / (hashlib.sha256(session.encode()).hexdigest() + ".json")


def read_state(path, session):
    state = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {
        "schema": 1, "session_id": session, "auto_count": 0,
        "coverage": "no_host_events_observed", "muted": False,
    }
    if (state.get("schema") != 1 or state.get("session_id") != session or
            type(state.get("auto_count")) is not int or state["auto_count"] < 0 or
            type(state.get("muted", False)) is not bool):
        raise ValueError("invalid legacy state; original file preserved")
    # Additive migration. Keep old counters/receipts intact but never re-arm old proposals.
    state.setdefault("phase_gate", {"version": 1, "current": None, "phases": {},
                                  "restore_pending": False, "notice_count": 0})
    gate = state["phase_gate"]
    if (not isinstance(gate, dict) or gate.get("version") != 1 or
            not isinstance(gate.get("phases"), dict) or
            type(gate.get("notice_count")) is not int):
        raise ValueError("invalid stage state; original file preserved")
    return state


def current(state, key=None):
    gate = state["phase_gate"]
    key = key or gate["current"]
    if not key or key not in gate["phases"] or key != gate["current"]:
        raise ValueError("phase is not the current registered phase")
    return gate["phases"][key]


def summary(state):
    gate = state["phase_gate"]
    phase = gate["phases"].get(gate["current"])
    return {"session_id": state["session_id"], "turn_id": state.get("active_turn_id"),
            "auto_compactions": state["auto_count"], "coverage": state.get("coverage"),
            "muted": state.get("muted", False), "notice_count": gate["notice_count"],
            "phase": ({k: phase.get(k) for k in
                       ("key", "title", "project_root", "criteria", "checks", "status",
                        "offer_id", "has_next", "next_action", "choice", "review", "handoff",
                        "revision", "review_mode", "review_request")}
                      if phase else None)}


def require_human(data):
    if data.get("human_confirmed") is not True:
        raise ValueError("explicit human choice required")
    return text_arg(data.get("approval_note"), "approval_note")


def criteria_arg(criteria):
    if not isinstance(criteria, list) or not 1 <= len(criteria) <= 5:
        raise ValueError("use 1 to 5 acceptance criteria")
    criteria = [text_arg(c, "criterion", 180) for c in criteria]
    if len(set(criteria)) != len(criteria):
        raise ValueError("duplicate acceptance criteria")
    return criteria


def archive_review(phase):
    previous = phase.pop("review", None)
    if previous:
        phase.setdefault("review_history", []).append(previous)


def mutate(state, action, data, turn):
    gate = state["phase_gate"]
    if action in {"mute", "resume"}:
        note = require_human(data)
        state["muted"] = action == "mute"
        gate["reminder_preference"] = {"turn": turn, "action": action, "note": note}
        phase = gate["phases"].get(gate["current"])
        if action == "mute" and phase and phase["status"] == "awaiting_choice":
            phase.update(status="dismissed", dismissal={"turn": turn, "note": note})
        return {"muted": state["muted"]}
    if action == "plan":
        key = text_arg(data.get("phase_key"), "phase_key", 80)
        title = text_arg(data.get("title"), "title", 80)
        project = Path(text_arg(data.get("project_root"), "project_root", 1000))
        if data.get("confirmed") is not True:
            raise ValueError("minimum acceptance scope must be established with the user")
        if not project.is_absolute() or not project.is_dir():
            raise ValueError("project_root must be an existing absolute directory")
        criteria = criteria_arg(data.get("criteria"))
        files = data.get("related_files", [])
        if not isinstance(files, list) or len(files) > 12:
            raise ValueError("related_files must contain at most 12 paths")
        files = [text_arg(f, "related_file", 1000) for f in files]
        if key in gate["phases"]:
            old = gate["phases"][key]
            if (old["project_root"] != str(project.resolve()) or
                    [c["text"] for c in old["criteria"]] != criteria):
                raise ValueError("same phase cannot silently replace scope or project")
            return {"registered": False, "already_exists": True, "phase": old}
        old = gate["phases"].get(gate["current"])
        if old and old["status"] not in TERMINAL:
            raise ValueError("current phase has not been closed or handed off")
        phase = {"key": key, "title": title, "project_root": str(project.resolve()),
                 "task": str(data.get("task", ""))[:240], "baseline": data.get("baseline"),
                 "related_files": files,
                 "criteria": [{"id": "c" + str(i + 1), "text": c}
                              for i, c in enumerate(criteria)], "status": "active", "revision": 1}
        gate["phases"][key] = phase
        gate["current"] = key
        ended = [k for k, p in gate["phases"].items()
                 if k != key and p["status"] in TERMINAL]
        for stale in ended[:-20]:
            del gate["phases"][stale]
        return {"registered": True, "phase": phase}
    phase = current(state, data.get("phase_key"))
    if action == "review-start":
        note = require_human(data)
        request_id = text_arg(data.get("request_id"), "request_id", 80)
        followup = data.get("followup", False)
        if type(followup) is not bool:
            raise ValueError("followup must be a boolean")
        mode = data.get("mode", phase.get("review_mode", "stage") if followup else "stage")
        if mode not in {"stage", "full"}:
            raise ValueError("mode must be stage or full")
        previous_request = phase.get("review_request")
        if previous_request and previous_request["id"] == request_id:
            if previous_request["mode"] != mode or previous_request["followup"] != followup:
                raise ValueError("same review request cannot change scope or purpose")
            return {"authorized": False, "already_recorded": True, "status": phase["status"]}
        allowed = {"active", "awaiting_choice", "continued", "closed", "reviewed", "dismissed",
                   "review_authorized", "review_then_handoff", "handoff_authorized"}
        if phase["status"] not in allowed:
            raise ValueError("cannot start Review here; verify ownership or cancellation first")
        return_status = phase["status"]
        if return_status == "review_authorized":
            return_status = (previous_request or {}).get("return_status", "reviewed")
        elif return_status == "review_then_handoff":
            return_status = "handoff_authorized"
        elif return_status == "awaiting_choice":
            return_status = "reviewed"
        archive_review(phase)
        phase.update(status="review_authorized", review_mode=mode,
                     review_request={"id": request_id, "mode": mode, "followup": followup,
                                     "turn": turn, "note": note, "return_status": return_status})
        return {"authorized": True, "mode": mode, "followup": followup, "status": phase["status"]}
    if action in {"amend", "cancel"}:
        note = require_human(data)
        if action == "cancel" and phase["status"] == "cancelled":
            return {"cancelled": False, "already_recorded": True}
        allowed = {"active", "awaiting_choice"}
        if action == "cancel":
            allowed |= {"review_authorized", "review_then_handoff", "handoff_authorized", "handoff_in_progress"}
        if phase["status"] not in allowed or (phase.get("handoff") or {}).get("rank", -1) >= 3:
            raise ValueError("phase cannot change in this state; verify ownership after dispatch")
        if action == "cancel":
            phase.update(status="cancelled", cancellation={"turn": turn, "note": note})
            return {"cancelled": True, "status": phase["status"]}
        criteria = criteria_arg(data.get("criteria"))
        if criteria == [c["text"] for c in phase["criteria"]]:
            return {"amended": False, "already_exists": True, "revision": phase.get("revision", 1)}
        previous = {k: v for k, v in phase.items() if k not in {"revisions"}}
        revision = phase.get("revision", 1) + 1
        already_offered = phase.get("offer_issued", bool(phase.get("offer_id")))
        history = phase.setdefault("revisions", [])
        history.append({"turn": turn, "note": note, "previous": previous})
        for field in ("checks", "summary", "has_next", "next_action", "benefit", "allow_handoff",
                      "offer_id", "choices", "notice", "offer_turn", "delivered", "retry_used", "delivery_unknown",
                      "review", "review_mode", "review_request"):
            phase.pop(field, None)
        phase.update(status="active", revision=revision, offer_issued=already_offered,
                     criteria=[{"id": f"r{revision}-c{i + 1}", "text": c} for i, c in enumerate(criteria)])
        return {"amended": True, "revision": revision, "phase": phase}
    if action == "complete":
        if phase["status"] != "active":
            return {"eligible": False, "why": "already_evaluated", "status": phase["status"]}
        checks = data.get("checks")
        expected = {c["id"] for c in phase["criteria"]}
        if (not isinstance(checks, list) or len(checks) != len(expected) or
                any(not isinstance(c, dict) for c in checks) or
                {c.get("id") for c in checks} != expected):
            raise ValueError("provide exactly one check for each acceptance criterion")
        clean = []
        for check in checks:
            if check.get("status") not in {"met", "not_met", "unverified"}:
                raise ValueError("invalid criterion status")
            clean.append({"id": check["id"], "status": check["status"],
                          "evidence": text_arg(check.get("evidence"), "evidence", 400)})
        phase["checks"] = clean
        unmet = [c["id"] for c in clean if c["status"] != "met"]
        if unmet or data.get("safe") is not True:
            return {"eligible": False, "why": "incomplete_or_not_safe", "unmet": unmet}
        if type(data.get("has_next")) is not bool:
            raise ValueError("has_next must be a verified boolean")
        description = text_arg(data.get("summary"), "summary", 400)
        has_next = data["has_next"]
        next_action = text_arg(data.get("next_action"), "next_action") if has_next else ""
        benefit = str(data.get("benefit", "")).strip()[:240]
        allow_handoff = has_next and bool(benefit)
        phase.update(summary=description, has_next=has_next, next_action=next_action,
                     benefit=benefit, allow_handoff=allow_handoff)
        if state.get("muted"):
            phase["status"] = "continued" if has_next else "closed"
            return {"eligible": False, "why": "muted", "completed": True}
        if phase.get("offer_issued"):
            phase["status"] = "continued" if has_next else "closed"
            return {"eligible": False, "why": "already_offered", "completed": True}
        choices = ["continue", "review"] if has_next else ["close", "review"]
        if allow_handoff:
            choices += ["handoff", "both"]
        options = ("继续／仅 Review／保存交接并在同项目正确目录新建、打开、核对后继续／先 Review 后交接"
                   if allow_handoff else "继续／仅 Review" if has_next else "结束／仅 Review")
        review_note = "已有 Review 记录，覆盖范围以记录为准。" if phase.get("review") else "本次尚未执行 AI Review。"
        notice = (f"阶段检查：{phase['title']}的最低标准已满足（{len(clean)}/{len(clean)}）。"
                  f"{description} {review_note}请选择：{options}。")
        offer = hashlib.sha256((state["session_id"] + ":" + phase["key"]).encode()).hexdigest()[:24]
        phase.update(status="awaiting_choice", offer_id=offer, choices=choices, offer_issued=True,
                     notice=notice, offer_turn=turn, delivered=False, retry_used=False)
        return {"eligible": True, "offer_id": offer, "notice": notice,
                "auto_compactions": state["auto_count"]}
    if action == "choose":
        note = require_human(data)
        if data.get("offer_id") != phase.get("offer_id"):
            raise ValueError("choice must identify the current offer")
        choice = data.get("choice")
        if phase["status"] != "awaiting_choice":
            if phase.get("choice") == choice:
                if choice in {"review", "both"} and data.get("review_mode", "stage") != phase.get("review_mode", "stage"):
                    raise ValueError("new Review scope requires an explicit review-start request")
                return {"authorized": False, "already_recorded": True, "status": phase["status"]}
            raise ValueError("offer already answered")
        if choice not in phase["choices"]:
            raise ValueError("choice not available for this completed stage")
        if choice in {"review", "both"}:
            mode = data.get("review_mode", "stage")
            if mode not in {"stage", "full"}:
                raise ValueError("review_mode must be stage or full")
            archive_review(phase)
            phase.pop("review_request", None)
            phase["review_mode"] = mode
        phase.update(choice=choice, approval={"turn": turn, "note": note})
        phase["status"] = {"continue": "continued", "close": "closed",
                           "review": "review_authorized", "handoff": "handoff_authorized",
                           "both": "review_then_handoff"}[choice]
        return {"authorized": True, "review": choice in {"review", "both"},
                "handoff": choice in {"handoff", "both"}, "status": phase["status"]}
    if action == "review-result":
        if phase["status"] not in {"review_authorized", "review_then_handoff"}:
            raise ValueError("Review has not been authorized or is already recorded")
        if data.get("verified") is not True:
            raise ValueError("record an actual completed review, not an intention")
        mode = phase.get("review_mode", "stage")
        if data.get("mode", mode) != mode:
            raise ValueError("review result must match the authorized scope")
        request = phase.get("review_request")
        if request and data.get("request_id") != request["id"]:
            raise ValueError("review result must match the current request_id")
        if not request and data.get("request_id") is not None:
            raise ValueError("stale direct Review result cannot replace a stage-choice Review")
        phase["review"] = {"summary": text_arg(data.get("summary"), "review summary", 600),
                           "turn": turn, "mode": mode}
        if request:
            phase["review"].update(request_id=request["id"], followup=request["followup"])
            phase["status"] = request["return_status"]
        else:
            phase["status"] = "handoff_authorized" if phase["choice"] == "both" else "reviewed"
        return {"recorded": True, "status": phase["status"]}
    if action == "handoff-progress":
        if phase["status"] not in {"handoff_authorized", "handoff_in_progress"}:
            raise ValueError("handoff not authorized, Review still pending, or already finished")
        handoff_id = text_arg(data.get("handoff_id"), "handoff_id", 80)
        status = data.get("status")
        steps = ["saved", "created", "verified", "dispatched", "started"]
        if status not in steps + ["blocked"]:
            raise ValueError("invalid handoff status")
        record = phase.get("handoff")
        if record and record["id"] != handoff_id:
            raise ValueError("reuse the existing handoff identity")
        old_rank = record.get("rank", -1) if record else -1
        rank = steps.index(status) if status != "blocked" else old_rank
        if rank < old_rank or rank > old_rank + 1:
            raise ValueError("handoff steps must be recorded in order")
        target = data.get("target_thread_id") or (record.get("target") if record else None)
        if rank >= 1:
            target = text_arg(target, "actual target_thread_id", 160)
        if record and record.get("target") and record["target"] != target:
            raise ValueError("do not create or switch to another target chat")
        evidence = text_arg(data.get("evidence"), "handoff evidence", 400)
        phase["handoff"] = {"id": handoff_id, "status": status, "rank": rank,
                            "target": target, "evidence": evidence}
        phase["status"] = "handed_off" if status == "started" else "handoff_in_progress"
        return {"recorded": True, "status": phase["status"], "handoff": phase["handoff"]}
    raise ValueError("unsupported action; old count-based prepare/evaluate are retired")


def final_contains_notice(message, notice):
    if not isinstance(message, str):
        return False
    lines, fenced = [], False
    for line in message.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and not line.lstrip().startswith(">"):
            lines.append(line)
    normalize = lambda s: re.sub(r"[\s*`_]", "", s)
    paragraphs = re.split(r"\n\s*\n", "\n".join(lines))
    return any(normalize(p) == normalize(notice) for p in paragraphs if p.strip())


def hook(state, event):
    gate = state["phase_gate"]
    name, turn = event["hook_event_name"], event.get("turn_id")
    state["coverage"] = "observed_since_hook_enabled"
    if name == "PostCompact":
        state["auto_count"] += 1
        gate["restore_pending"] = bool(gate["current"])
        return {}
    if turn and name != "Stop":
        state["active_turn_id"] = turn
    phase = gate["phases"].get(gate["current"])
    if name == "Stop":
        if (state.get("muted") or not phase or phase["status"] != "awaiting_choice" or phase.get("delivered") or
                not turn or turn != phase.get("offer_turn")):
            return {}
        if final_contains_notice(event.get("last_assistant_message"), phase["notice"]):
            phase["delivered"] = True
            gate["notice_count"] += 1
            return {}
        if not phase["retry_used"] and not event.get("stop_hook_active"):
            phase["retry_used"] = True
            return {"decision": "block", "reason": "本阶段提示漏出最终答复。仅补上独立段落，不重做任务、不审查、不保存、不新建：" + phase["notice"]}
        phase["delivery_unknown"] = True
        return {}
    restore = gate.get("restore_pending") or (name == "SessionStart" and event.get("source") in {"resume", "compact"})
    gate["restore_pending"] = False
    if not restore or not phase or phase["status"] in TERMINAL or state.get("muted"):
        return {}
    context = (f"stage-pilot：阶段={phase['key']}，状态={phase['status']}，真实自动压缩={state['auto_count']}次。"
               "必要时仅读阶段状态；按既定最低标准推进，次数不触发提示。同阶段不重复询问，Review/交接须用户明确选择。")
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": context}}


def process(event, root, action="hook", data=None, full=False):
    if action not in ACTIONS:
        raise ValueError("unsupported action; old count-based prepare/evaluate are retired")
    session = text_arg(event.get("session_id"), "session_id", 256)
    name = event.get("hook_event_name")
    if action == "hook":
        if name not in EVENTS or event.get("agent_id") or event.get("agent_transcript_path"):
            return {}
        if name == "PostCompact" and event.get("trigger") != "auto":
            return {}
    path = state_path(root, session)
    if action == "status":
        state = read_state(path, session)
        return state if full else summary(state)
    if action == "hook" and not path.exists() and name == "Stop":
        return {}
    with locked(path.with_suffix(".lock")):
        state = read_state(path, session)
        before = json.dumps(state, sort_keys=True, ensure_ascii=False)
        if action == "hook":
            result = hook(state, event)
        else:
            turn = event.get("turn_id") or state.get("active_turn_id")
            if event.get("turn_id") and state.get("active_turn_id") and turn != state["active_turn_id"]:
                raise ValueError("mutation must use the current host turn")
            result = mutate(state, action, data or {}, turn)
        if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
            state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
            os.replace(temp, path)
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--session-id", default=os.environ.get("CODEX_THREAD_ID"))
    parser.add_argument("--turn-id")
    parser.add_argument("--action", default="hook")
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--full", action="store_true")
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin) if args.action == "hook" else {
            "session_id": args.session_id, "turn_id": args.turn_id}
        data = json.loads(args.input_file.read_text(encoding="utf-8-sig")) if args.input_file else {}
        if not isinstance(event, dict) or not isinstance(data, dict):
            raise ValueError("event and request must be JSON objects")
        result = process(event, args.state_dir, args.action, data, args.full)
        print(json.dumps(result, ensure_ascii=False))
    except (ValueError, OSError, TimeoutError, TypeError, KeyError) as exc:
        if args.action == "hook":
            print(json.dumps({"systemMessage": "stage-pilot 状态核验失败，原记录保留：" + str(exc)[:160]}, ensure_ascii=False))
        else:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
