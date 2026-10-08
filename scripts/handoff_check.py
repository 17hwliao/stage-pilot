"""Validate one compact handoff record and its explicit references; never scan history."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

SECTIONS = ["任务与阶段", "目录与版本", "有效决定与限制", "成果与验证", "未完成与风险", "接续第一步"]


def placeholder_present(content):
    """Find unfilled template markers, without rejecting ordinary pending work."""
    return bool(re.search(r"\[(?:TODO|TBD|待填写|待补充)\s*(?::|：|\])|<待填写[^>]*>",
                          content, re.I))


def credential_labels(content):
    """Return labels only; never include matching credential values in diagnostics."""
    labels = set()
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", content):
        labels.add("private key")
    assignment = re.compile(
        r'''["']?\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password|passwd)\b["']?\s*[:=]\s*([^\n]+)''',
        re.I)
    for match in assignment.finditer(content):
        raw = match.group(2).strip().rstrip(",;")
        if raw[:1] in ("'", '"'):
            quote = raw[0]
            value = raw[1:].split(quote, 1)[0]
        else:
            value = raw.split()[0] if raw else ""
        if not value or value.lower() in {"null", "none", "redacted", "未记录", "已移除", "***", "<redacted>"}:
            continue
        if re.fullmatch(r"\*+", value) or value.startswith(("${", "$env:", "os.environ", "process.env")):
            continue
        labels.add("credential assignment")
    if re.search(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{20,})\b", content):
        labels.add("token-like literal")
    if re.search(r"\bBearer\s+[A-Za-z0-9_.+/=-]{20,}", content, re.I):
        labels.add("bearer token")
    for match in re.finditer(r"\b[a-z][a-z0-9+.-]*://[^\s<>\"']+", content, re.I):
        try:
            if urlsplit(match.group()).password:
                labels.add("URL password")
        except ValueError:
            pass
    return sorted(labels)


def git_value(root, *args):
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=3)
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def validate(path, record_id=None, check_git=False):
    path = Path(path)
    if path.stat().st_size > 262144:
        raise ValueError("handoff carrier too large; use a bounded current-record file")
    document = path.read_text(encoding="utf-8-sig")
    matches = list(re.finditer(r"^```handoff-meta\s*\n(.*?)\n```\s*$", document, re.M | re.S))
    records = [(match, json.loads(match.group(1))) for match in matches]
    if record_id:
        records = [(m, meta) for m, meta in records if meta.get("id") == record_id]
    if len(records) != 1:
        raise ValueError("select exactly one handoff-meta record, using --record-id if needed")
    match, meta = records[0]
    following = [m.start() for m in matches if m.start() > match.start()]
    content = document[match.end():min(following) if following else len(document)]
    errors, warnings = [], []
    for key in ("id", "project_root", "phase_key"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            errors.append("missing metadata " + key)
    root = Path(meta.get("project_root", ""))
    if not root.is_absolute() or not root.is_dir():
        errors.append("project_root must be an existing absolute directory")
    for title in SECTIONS:
        section = re.search(r"^## " + re.escape(title) + r"\s*\n(.*?)(?=^## |\Z)", content, re.M | re.S)
        if not section or len(section.group(1).strip()) < 4:
            errors.append("missing or empty section " + title)
    selected = document[match.start():min(following) if following else len(document)]
    if placeholder_present(selected):
        errors.append("unfinished TODO placeholders")
    for label in credential_labels(selected):
        errors.append("potential credential: " + label)
    files = meta.get("files", [])
    if not isinstance(files, list) or len(files) > 12:
        errors.append("files must be a bounded list of at most 12 explicit references")
        files = []
    verified = 0
    for item in files:
        item = {"path": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            errors.append("invalid file reference")
            continue
        reference = Path(item["path"])
        reference = reference if reference.is_absolute() else root / reference
        if not reference.is_file():
            errors.append("missing reference: " + item["path"])
            continue
        verified += 1
        if check_git and item.get("sha256"):
            if reference.stat().st_size > 2097152:
                warnings.append("fingerprint not verified for large file: " + item["path"])
            elif hashlib.sha256(reference.read_bytes()).hexdigest() != item["sha256"]:
                warnings.append("file changed since handoff: " + item["path"])
    if check_git and root.is_dir():
        head = git_value(root, "rev-parse", "HEAD")
        branch = git_value(root, "branch", "--show-current")
        if meta.get("head") and head != meta["head"]:
            warnings.append("Git HEAD changed or unavailable")
        if meta.get("branch") and branch != meta["branch"]:
            warnings.append("Git branch changed or unavailable")
        if not head:
            warnings.append("Git version not verified; project may not use Git")
    if len(content) > 12000:
        warnings.append("long handoff; keep only facts needed for the next step")
    return {"valid": not errors, "record_id": meta.get("id"), "files_verified": verified,
            "errors": errors, "warnings": warnings, "semantic_accuracy": "requires_agent_verification"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--record-id")
    parser.add_argument("--check-git", action="store_true")
    args = parser.parse_args()
    try:
        result = validate(args.path, args.record_id, args.check_git)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["valid"] else 1
    except (OSError, ValueError, TypeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
