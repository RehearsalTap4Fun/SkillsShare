from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import unicodedata
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evidence_anchors import AnchorError, NOTICE as ANCHOR_NOTICE, verify_report
from review_rule_matcher import RuleMatchError, resolve_rules
from requirement_alignment import AlignmentError, validate_alignment, validate_failure_manifest


from user_config import workspace_root, UserConfigError, check_write_path
DISCLAIMER = "这是静态预审结果，不代表功能验收通过。"
REQUIREMENT_RESULTS = {"静态证据充分", "存在明显缺口", "证据不完整", "无法确认"}
FINDING_TYPES = {"阻断", "风险", "无法确认"}
TESTCASE_CATEGORIES = {"主流程", "关键边界", "明显异常"}
VERDICTS = {"可进入运行验收", "建议修正后再提测", "预检信息不足"}
CONFIG_CHECK_STATUSES = {"通过", "发现异常", "无法确认"}
REVIEW_CHECK_STATUSES = {"已核对", "不适用", "无法确认"}
DEFAULT_REVIEW_RULES = Path(__file__).resolve().parent.parent / "references" / "k1-review-rules.json"
TOP_LEVEL_FIELDS = {
    "metadata",
    "requirements",
    "findings",
    "testcases",
    "overall",
    "readonlyCheck",
    "limits",
    "reviewChecks",
}
OPTIONAL_TOP_LEVEL_FIELDS = {"configurationChecks"}


class ContractError(Exception):
    pass


class PublishError(Exception):
    pass


def require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(code)
    return value


def require_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(code)
    return value


def require_string(mapping: dict[str, Any], field: str, code: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(code)
    return value


def require_string_list(mapping: dict[str, Any], field: str, code: str) -> list[str]:
    value = mapping.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ContractError(code)
    return value


def ensure_unique_ids(items: list[dict[str, Any]], code: str) -> set[str]:
    values = [require_string(item, "id", code) for item in items]
    if len(values) != len(set(values)):
        raise ContractError("DUPLICATE_ID")
    return set(values)


def validate_evidence(value: Any) -> list[dict[str, str]]:
    entries = require_list(value, "INVALID_EVIDENCE")
    validated: list[dict[str, str]] = []
    required = {"repository", "ref", "path", "locator", "summary"}
    for entry_value in entries:
        entry = require_mapping(entry_value, "INVALID_EVIDENCE")
        optional = {"snippet", "unverifiedReason"}
        if not required.issubset(entry) or not set(entry).issubset(required | optional):
            raise ContractError("INVALID_EVIDENCE")
        for field in optional & set(entry):
            require_string(entry, field, "INVALID_EVIDENCE")
        validated.append(
            {field: require_string(entry, field, "INVALID_EVIDENCE") for field in required}
        )
    return validated


def normalize_readonly_check(readonly: dict[str, Any]) -> dict[str, Any]:
    before = require_mapping(readonly.get("beforeDigest"), "INVALID_READONLY_CHECK")
    after = require_mapping(readonly.get("afterDigest"), "INVALID_READONLY_CHECK")
    if "unchanged" in readonly:
        unchanged = readonly.get("unchanged")
        if not isinstance(unchanged, bool) or unchanged != (before == after):
            raise ContractError("INCONSISTENT_READONLY_CHECK")
        return {
            "sourceMode": "local",
            "contractPassed": unchanged,
            "workspaceChanged": not unchanged,
            "beforeDigest": before,
            "afterDigest": after,
        }

    source_mode = readonly.get("sourceMode")
    contract_passed = readonly.get("contractPassed")
    workspace_changed = readonly.get("workspaceChanged")
    if source_mode not in {"local", "remote"}:
        raise ContractError("INVALID_READONLY_CHECK")
    if not isinstance(contract_passed, bool) or not isinstance(workspace_changed, bool):
        raise ContractError("INVALID_READONLY_CHECK")
    if workspace_changed != (before != after):
        raise ContractError("INCONSISTENT_READONLY_CHECK")
    return {
        "sourceMode": source_mode,
        "contractPassed": contract_passed,
        "workspaceChanged": workspace_changed,
        "beforeDigest": before,
        "afterDigest": after,
    }


def validate_payload(value: Any) -> dict[str, Any]:
    payload = require_mapping(value, "INVALID_PAYLOAD")
    if "alignment" not in payload:
        raise AlignmentError("ALIGNMENT_REQUIRED")
    if not TOP_LEVEL_FIELDS.issubset(payload) or not set(payload).issubset(
        TOP_LEVEL_FIELDS | OPTIONAL_TOP_LEVEL_FIELDS | {"alignment"}
    ):
        raise ContractError("INVALID_PAYLOAD_FIELDS")

    metadata = require_mapping(payload["metadata"], "INVALID_METADATA")
    for field in (
        "feature",
        "documentSource",
        "documentVersion",
        "startedAt",
        "finishedAt",
        "disclaimer",
    ):
        require_string(metadata, field, "INVALID_METADATA")
    if metadata["disclaimer"] != DISCLAIMER:
        raise ContractError("INVALID_DISCLAIMER")
    if "deadlineSeconds" not in metadata or metadata["deadlineSeconds"] is not None:
        raise ContractError("INVALID_DEADLINE")
    elapsed = metadata.get("elapsedSeconds")
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or elapsed < 0:
        raise ContractError("INVALID_ELAPSED_SECONDS")
    require_mapping(metadata.get("cache"), "INVALID_CACHE_METADATA")
    repositories = require_list(metadata.get("repositories"), "INVALID_REPOSITORY_METADATA")
    for repository_value in repositories:
        repository = require_mapping(repository_value, "INVALID_REPOSITORY_METADATA")
        for field in ("role", "base", "target"):
            require_string(repository, field, "INVALID_REPOSITORY_METADATA")
        source = repository.get("source", "local")
        if source not in {"local", "remote"}:
            raise ContractError("INVALID_REPOSITORY_METADATA")
        repository["source"] = source
        if source == "remote":
            if require_string(repository, "remote", "INVALID_REPOSITORY_METADATA") != "origin":
                raise ContractError("INVALID_REPOSITORY_METADATA")
            for field in ("baseRef", "targetRef"):
                ref = require_string(repository, field, "INVALID_REPOSITORY_METADATA")
                if not ref.startswith("refs/remotes/origin/"):
                    raise ContractError("INVALID_REPOSITORY_METADATA")
            for field in ("base", "target"):
                if not re.fullmatch(r"[0-9a-fA-F]{40}", repository[field]):
                    raise ContractError("INVALID_REPOSITORY_METADATA")

    requirement_values = require_list(payload["requirements"], "INVALID_REQUIREMENTS")
    requirements = [require_mapping(item, "INVALID_REQUIREMENT") for item in requirement_values]
    requirement_ids = ensure_unique_ids(requirements, "INVALID_REQUIREMENT_ID")
    for requirement in requirements:
        for field in (
            "title",
            "documentLocator",
            "expectation",
            "result",
            "explanation",
            "runtimeAction",
        ):
            require_string(requirement, field, "INVALID_REQUIREMENT")
        if requirement["result"] not in REQUIREMENT_RESULTS:
            raise ContractError("INVALID_ENUM:requirement.result")
        require_string_list(requirement, "testcaseIds", "INVALID_REQUIREMENT_TESTCASES")
        validate_evidence(requirement.get("evidence"))

    review_check_values = require_list(payload["reviewChecks"], "INVALID_REVIEW_CHECKS")
    review_checks = [require_mapping(item, "INVALID_REVIEW_CHECK") for item in review_check_values]
    seen_check_ids: set[str] = set()
    review_check_fields = {
        "checkId", "ruleId", "requirementIds", "status", "evidence", "reason"
    }
    for check in review_checks:
        if set(check) != review_check_fields:
            raise ContractError("INVALID_REVIEW_CHECK")
        check_id = require_string(check, "checkId", "INVALID_REVIEW_CHECK")
        require_string(check, "ruleId", "INVALID_REVIEW_CHECK")
        require_string(check, "reason", "INVALID_REVIEW_CHECK")
        if check_id in seen_check_ids:
            raise ContractError("DUPLICATE_REVIEW_CHECK")
        seen_check_ids.add(check_id)
        if check.get("status") not in REVIEW_CHECK_STATUSES:
            raise ContractError("INVALID_REVIEW_CHECK")
        references = require_string_list(
            check, "requirementIds", "INVALID_REVIEW_CHECK_REFERENCES"
        )
        if not references or not set(references).issubset(requirement_ids):
            raise ContractError("UNKNOWN_REQUIREMENT_REFERENCE")
        validate_evidence(check.get("evidence"))
    payload["reviewChecks"] = review_checks

    configuration_values = require_list(
        payload.get("configurationChecks", []), "INVALID_CONFIGURATION_CHECKS"
    )
    configuration_checks = [
        require_mapping(item, "INVALID_CONFIGURATION_CHECK")
        for item in configuration_values
    ]
    ensure_unique_ids(configuration_checks, "INVALID_CONFIGURATION_CHECK_ID")
    configuration_fields = {
        "id",
        "sourceType",
        "authority",
        "sourcePath",
        "targetSha",
        "workbook",
        "table",
        "status",
        "summary",
        "dataRowCount",
        "checks",
        "affectedRequirementIds",
        "limitation",
    }
    for configuration in configuration_checks:
        if set(configuration) != configuration_fields:
            raise ContractError("INVALID_CONFIGURATION_CHECK")
        for field in (
            "sourcePath",
            "targetSha",
            "workbook",
            "table",
            "status",
            "summary",
            "limitation",
        ):
            require_string(configuration, field, "INVALID_CONFIGURATION_CHECK")
        if (
            configuration.get("sourceType") != "generated-json"
            or configuration.get("authority") != "次级证据"
            or configuration["status"] not in CONFIG_CHECK_STATUSES
            or not re.fullmatch(r"[0-9a-fA-F]{40}", configuration["targetSha"])
            or not isinstance(configuration.get("dataRowCount"), int)
            or isinstance(configuration.get("dataRowCount"), bool)
            or configuration["dataRowCount"] < 0
        ):
            raise ContractError("INVALID_CONFIGURATION_CHECK")
        references = require_string_list(
            configuration,
            "affectedRequirementIds",
            "INVALID_CONFIGURATION_CHECK_REFERENCES",
        )
        if not references or not set(references).issubset(requirement_ids):
            raise ContractError("UNKNOWN_REQUIREMENT_REFERENCE")
        check_values = require_list(
            configuration.get("checks"), "INVALID_CONFIGURATION_CHECK_DETAILS"
        )
        checks = [
            require_mapping(item, "INVALID_CONFIGURATION_CHECK_DETAIL")
            for item in check_values
        ]
        if not checks:
            raise ContractError("INVALID_CONFIGURATION_CHECK_DETAILS")
        ensure_unique_ids(checks, "INVALID_CONFIGURATION_CHECK_DETAIL_ID")
        for item in checks:
            if set(item) != {"id", "label", "status", "detail"}:
                raise ContractError("INVALID_CONFIGURATION_CHECK_DETAIL")
            for field in ("label", "status", "detail"):
                require_string(item, field, "INVALID_CONFIGURATION_CHECK_DETAIL")
            if item["status"] not in CONFIG_CHECK_STATUSES:
                raise ContractError("INVALID_CONFIGURATION_CHECK_DETAIL")
        detail_statuses = {item["status"] for item in checks}
        expected_status = (
            "无法确认"
            if "无法确认" in detail_statuses
            else ("发现异常" if "发现异常" in detail_statuses else "通过")
        )
        if configuration["status"] != expected_status:
            raise ContractError("INCONSISTENT_CONFIGURATION_CHECK")
    payload["configurationChecks"] = configuration_checks

    testcase_values = require_list(payload["testcases"], "INVALID_TESTCASES")
    testcases = [require_mapping(item, "INVALID_TESTCASE") for item in testcase_values]
    testcase_ids = ensure_unique_ids(testcases, "INVALID_TESTCASE_ID")
    referenced_by_cases: dict[str, set[str]] = {identifier: set() for identifier in requirement_ids}
    for testcase in testcases:
        for field in ("category", "precondition", "action", "expected", "source", "note"):
            require_string(testcase, field, "INVALID_TESTCASE")
        if testcase["category"] not in TESTCASE_CATEGORIES:
            raise ContractError("INVALID_ENUM:testcase.category")
        references = require_string_list(testcase, "requirementIds", "INVALID_TESTCASE_REFERENCES")
        if not references or not set(references).issubset(requirement_ids):
            raise ContractError("UNKNOWN_REQUIREMENT_REFERENCE")
        for identifier in references:
            referenced_by_cases[identifier].add(testcase["id"])

    for requirement in requirements:
        declared = set(requirement["testcaseIds"])
        if not declared:
            raise ContractError("REQUIREMENT_WITHOUT_TESTCASE:" + requirement["id"])
        if not declared.issubset(testcase_ids):
            raise ContractError("UNKNOWN_TESTCASE_REFERENCE")
        if declared != referenced_by_cases[requirement["id"]]:
            raise ContractError("INCONSISTENT_TESTCASE_REFERENCE")

    finding_values = require_list(payload["findings"], "INVALID_FINDINGS")
    findings = [require_mapping(item, "INVALID_FINDING") for item in finding_values]
    ensure_unique_ids(findings, "INVALID_FINDING_ID")
    requirements_with_gap_finding: set[str] = set()
    for finding in findings:
        for field in ("type", "title", "impact", "action"):
            require_string(finding, field, "INVALID_FINDING")
        if finding["type"] not in FINDING_TYPES:
            raise ContractError("INVALID_ENUM:finding.type")
        references = require_string_list(finding, "requirementIds", "INVALID_FINDING_REFERENCES")
        if not references or not set(references).issubset(requirement_ids):
            raise ContractError("UNKNOWN_REQUIREMENT_REFERENCE")
        validate_evidence(finding.get("evidence"))
        if finding["type"] in {"阻断", "风险"}:
            requirements_with_gap_finding.update(references)
    for requirement in requirements:
        if (
            requirement["result"] == "存在明显缺口"
            and requirement["id"] not in requirements_with_gap_finding
        ):
            raise ContractError("GAP_WITHOUT_FINDING")

    limits_values = require_list(payload["limits"], "INVALID_LIMITS")
    for limit_value in limits_values:
        limit = require_mapping(limit_value, "INVALID_LIMIT")
        require_string(limit, "source", "INVALID_LIMIT")
        require_string(limit, "reason", "INVALID_LIMIT")
        references = require_string_list(limit, "affectedRequirementIds", "INVALID_LIMIT")
        if not set(references).issubset(requirement_ids):
            raise ContractError("UNKNOWN_REQUIREMENT_REFERENCE")

    readonly = normalize_readonly_check(
        require_mapping(payload["readonlyCheck"], "INVALID_READONLY_CHECK")
    )
    payload["readonlyCheck"] = readonly

    overall = require_mapping(payload["overall"], "INVALID_OVERALL")
    verdict = require_string(overall, "verdict", "INVALID_OVERALL")
    require_string(overall, "reason", "INVALID_OVERALL")
    if verdict not in VERDICTS:
        raise ContractError("INVALID_ENUM:overall.verdict")
    readonly_invalid = (
        not readonly["contractPassed"]
        or (readonly["sourceMode"] == "local" and readonly["workspaceChanged"])
    )
    if readonly_invalid or not requirements or all(
        requirement["result"] == "无法确认" for requirement in requirements
    ):
        expected_verdict = "预检信息不足"
    elif any(finding["type"] == "阻断" for finding in findings):
        expected_verdict = "建议修正后再提测"
    elif metadata.get("unavailableRepositories"):
        expected_verdict = "预检信息不足"
    else:
        expected_verdict = "可进入运行验收"
    if verdict != expected_verdict:
        raise ContractError("INCONSISTENT_VERDICT")
    validate_alignment(payload["alignment"], metadata, requirements)
    for change in payload["alignment"]["changes"]:
        validate_evidence(change["evidence"])
    if metadata.get("testcaseDocumentVersion") != metadata["documentVersion"]:
        raise AlignmentError("ALIGNMENT_TESTCASE_VERSION_MISMATCH")
    return payload


def validate_review_coverage(
    payload: dict[str, Any], selection: dict[str, Any]
) -> None:
    if selection.get("schemaVersion") != 1 or not isinstance(selection.get("selectedRules"), list):
        raise ContractError("INVALID_RULE_SELECTION")
    expected = {}
    for raw in selection["selectedRules"]:
        item = require_mapping(raw, "INVALID_RULE_SELECTION")
        check_id = require_string(item, "checkId", "INVALID_RULE_SELECTION")
        if check_id in expected:
            raise ContractError("DUPLICATE_RULE_SELECTION")
        expected[check_id] = item
    actual = {item["checkId"]: item for item in payload["reviewChecks"]}
    missing = [check_id for check_id in expected if check_id not in actual]
    if missing:
        raise ContractError("MISSING_REVIEW_CHECK:" + ",".join(missing))
    extra = [check_id for check_id in actual if check_id not in expected]
    if extra:
        raise ContractError("UNEXPECTED_REVIEW_CHECK:" + ",".join(extra))
    for check_id, check in actual.items():
        selected = expected[check_id]
        if (
            check["ruleId"] != selected["ruleId"]
            or check["requirementIds"] != selected["requirementIds"]
        ):
            raise ContractError("REVIEW_CHECK_SELECTION_MISMATCH:" + check_id)
        if check["status"] == "已核对" and not check["evidence"]:
            raise ContractError("CHECKED_RULE_WITHOUT_EVIDENCE:" + check_id)
        if check["status"] == "不适用" and check["evidence"]:
            raise ContractError("NOT_APPLICABLE_RULE_WITH_EVIDENCE:" + check_id)
        failure_roles = selected.get("collectorFailureRoles")
        repository_roles = selected.get("repositoryRoles")
        evidence_roles = selected.get("evidenceRoles")
        required_evidence_roles = selected.get("requiredEvidenceRoles")
        any_evidence_roles = selected.get("anyEvidenceRoles")
        role_lists = (
            failure_roles,
            repository_roles,
            evidence_roles,
            required_evidence_roles,
            any_evidence_roles,
        )
        if any(not isinstance(values, list) for values in role_lists) or any(
            not isinstance(role, str) for values in role_lists for role in values
        ):
            raise ContractError("INVALID_RULE_SELECTION")
        if failure_roles and check["status"] != "无法确认":
            raise ContractError("FAILED_RULE_MUST_BE_UNKNOWN:" + check_id)
        allowed_roles = set(evidence_roles)
        if any(
            entry["repository"] not in allowed_roles for entry in check["evidence"]
        ):
            raise ContractError("REVIEW_CHECK_EVIDENCE_ROLE_MISMATCH:" + check_id)
        present_roles = {entry["repository"] for entry in check["evidence"]}
        if check["status"] == "已核对" and (
            not set(required_evidence_roles).issubset(present_roles)
            or (any_evidence_roles and not present_roles.intersection(any_evidence_roles))
        ):
            raise ContractError("REVIEW_CHECK_PRIMARY_EVIDENCE_MISSING:" + check_id)
        check.update(
            scope=selected["scope"],
            title=selected["title"],
            repositoryRoles=repository_roles,
            evidenceRoles=evidence_roles,
            requiredEvidenceRoles=required_evidence_roles,
            anyEvidenceRoles=any_evidence_roles,
            collectorFailureRoles=failure_roles,
            matchedBy=selected["matchedBy"],
            evidenceExpectation=selected["evidenceExpectation"],
            notApplicableWhen=selected["notApplicableWhen"],
        )


def md_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def evidence_text(entries: list[dict[str, str]]) -> str:
    if not entries:
        return "无直接静态证据"
    parts = []
    for entry in entries:
        short_ref = entry["ref"][:12]
        parts.append(
            f"{entry['repository']}@{short_ref} {entry['path']}:{entry['locator']} — {entry['summary']}"
            + f" [{entry.get('verification', '未校验')}]"
        )
    return "<br>".join(md_cell(part) for part in parts)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    lines.extend("| " + " | ".join(md_cell(cell) for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def render_alignment_markdown(payload: dict[str, Any]) -> str:
    alignment = payload["alignment"]
    document = alignment["document"]
    lines = [
        "## 前置需求对齐", "",
        "- 状态：已完成；文档对齐与预审均不限时。",
        f"- 文档版本：`{md_cell(document['beforeVersion'])}` → `{md_cell(document['afterVersion'])}`",
        f"- 原文维护：{'已更新' if document['writeStatus'] == 'updated' else '无需修改'}；已回读核验。",
        f"- 变更记录：{md_cell(document.get('changeLogLocator', '无'))}",
        f"- 覆盖说明：{md_cell(alignment['comparisonSummary'])}", "",
    ]
    for change in alignment["changes"]:
        lines.extend([
            f"### {md_cell(change['id'])} · {md_cell(change['changeType'])} · {md_cell(change['decision'])}", "",
            f"- 原文（{md_cell(change['documentLocator'])}）：{md_cell(change['original'])}",
            f"- 最终实现：{md_cell(change['implementation'])}",
            f"- 建议修订：{md_cell(change['proposed'])}",
            f"- 确认依据：{md_cell(change['confirmation'])}",
            f"- 正文已应用：{'是' if change['applied'] else '否，保留原文'}",
            f"- 证据：{evidence_text(change['evidence']) or '证据不足，见后续动作'}",
            f"- 关联需求：{', '.join(map(md_cell, change['requirementIds'])) or '已确认删除'}",
            f"- 后续动作：{md_cell(change['followup'])}", "",
        ])
    for failure in alignment.get("unavailableRepositories", []):
        lines.append(f"- 未取得冻结版本：{md_cell(failure['role'])} · {md_cell(failure['code'])}；相关内容无法确认。")
    if not alignment["changes"]:
        lines.append("可核查范围内未发现需求差异，保留原文。")
    return "\n".join(lines)


def render_review(payload: dict[str, Any]) -> str:
    metadata = payload["metadata"]
    counts = Counter(requirement["result"] for requirement in payload["requirements"])
    finding_counts = Counter(finding["type"] for finding in payload["findings"])
    repo_rows = [
        [
            repo["role"],
            repo["source"],
            repo.get("remote", "-"),
            repo.get("baseRef", "-"),
            repo["base"],
            repo.get("targetRef", "-"),
            repo["target"],
        ]
        for repo in metadata["repositories"]
    ]
    requirement_rows = [
        [
            item["id"],
            item["title"],
            item["documentLocator"],
            ", ".join(item["testcaseIds"]) or "无",
            evidence_text(item["evidence"]),
            item["result"],
            item["explanation"],
            item["runtimeAction"],
        ]
        for item in payload["requirements"]
    ]
    review_check_rows = [
        [
            item["checkId"],
            item["scope"],
            item["title"],
            ", ".join(item["requirementIds"]),
            (
                "采集失败角色：" + ", ".join(item["collectorFailureRoles"])
                if item["collectorFailureRoles"]
                else "采集正常"
            ),
            "<br>".join(md_cell(value) for value in item["matchedBy"]),
            item["status"],
            item["reason"],
            evidence_text(item["evidence"]),
        ]
        for item in payload["reviewChecks"]
    ]
    configuration_rows = [
        [
            item["id"],
            item["authority"],
            f"{item['workbook']} / {item['table']}",
            item["targetSha"][:12],
            item["sourcePath"],
            item["dataRowCount"],
            item["status"],
            item["summary"],
            "<br>".join(
                md_cell(f"{check_item['label']}：{check_item['status']} — {check_item['detail']}")
                for check_item in item["checks"]
            ),
            ", ".join(item["affectedRequirementIds"]),
            item["limitation"],
        ]
        for item in payload["configurationChecks"]
    ]
    finding_rows = [
        [
            item["id"],
            item["type"],
            ", ".join(item["requirementIds"]),
            item["title"],
            evidence_text(item["evidence"]),
            item["impact"],
            item["action"],
        ]
        for item in payload["findings"]
    ]
    limit_rows = [
        [item["source"], item["reason"], ", ".join(item["affectedRequirementIds"]) or "无"]
        for item in payload["limits"]
    ]
    readonly = payload["readonlyCheck"]
    contract_label = "通过" if readonly["contractPassed"] else "未通过"
    readonly_invalid = (
        not readonly["contractPassed"]
        or (readonly["sourceMode"] == "local" and readonly["workspaceChanged"])
    )
    readonly_label = "未通过" if readonly_invalid else "通过"
    if not readonly["workspaceChanged"]:
        workspace_label = "未变化"
    elif readonly["sourceMode"] == "remote":
        workspace_label = "已变化（用户并行工作，不影响远端 SHA 结论）"
    else:
        workspace_label = "已变化（本地证据失效）"
    return f"""# {metadata['feature']} 静态预审

> {DISCLAIMER}

> {ANCHOR_NOTICE}

{render_alignment_markdown(payload)}

## 1. 预检元数据与范围

- 开发文档：{metadata['documentSource']}
- 文档版本：`{metadata['documentVersion']}`
- 开始：{metadata['startedAt']}
- 结束：{metadata['finishedAt']}
- 耗时：{metadata['elapsedSeconds']} 秒 / 不限时
- 缓存：`{json.dumps(metadata['cache'], ensure_ascii=False, sort_keys=True)}`

{markdown_table(['端', '来源', '远端', 'Base Ref', 'Base SHA', 'Target Ref', 'Target SHA'], repo_rows)}

## 2. 总体结论与统计

**{payload['overall']['verdict']}** — {payload['overall']['reason']}

- 需求：静态证据充分 {counts['静态证据充分']}，存在明显缺口 {counts['存在明显缺口']}，证据不完整 {counts['证据不完整']}，无法确认 {counts['无法确认']}
- 发现：阻断 {finding_counts['阻断']}，风险 {finding_counts['风险']}，无法确认 {finding_counts['无法确认']}

## 3. 需求—用例—证据矩阵

{markdown_table(['需求', '标题', '文档定位', '用例', '直接证据', '结果', '说明', '运行验收动作'], requirement_rows)}

## 4. 规则核对记录

{markdown_table(['检查 ID', '范围', '规则', '需求', '采集状态', '命中原因', '状态', '说明', '证据'], review_check_rows) if review_check_rows else '本次没有命中专项规则'}

## 5. 生成 JSON 次级证据

{markdown_table(['ID', '权威级别', '正式表 / 配置', 'Target SHA', 'JSON 路径', '数据行', '结果', '摘要', '检查项', '关联需求', '限制'], configuration_rows) if configuration_rows else '未使用生成 JSON 补充证据'}

## 6. 阻断、风险与无法确认

{markdown_table(['ID', '类型', '需求', '标题', '直接证据', '影响', '动作'], finding_rows) if finding_rows else '未发现'}

## 7. 运行验收建议、限制与只读终检

{markdown_table(['来源', '限制', '受影响需求'], limit_rows) if limit_rows else '未发现限制'}

只读终检：**{readonly_label}**

工作流只读合同：**{contract_label}**

本地工作区变化：**{workspace_label}**

- Before：`{json.dumps(payload['readonlyCheck']['beforeDigest'], ensure_ascii=False, sort_keys=True)}`
- After：`{json.dumps(payload['readonlyCheck']['afterDigest'], ensure_ascii=False, sort_keys=True)}`
"""


def render_testcases(payload: dict[str, Any]) -> str:
    metadata = payload["metadata"]
    lines = [
        f"# {metadata['feature']} 测试用例",
        "",
        f"> {DISCLAIMER}",
        "",
        "## 元数据",
        "",
        f"- 开发文档：{metadata['documentSource']}",
        f"- 文档版本：`{metadata['documentVersion']}`",
        f"- 用例校准文档版本：`{metadata['testcaseDocumentVersion']}`",
        "- 用例以已对齐文档为准，运行行为需另行验收。",
    ]
    headers = ["ID", "关联需求", "前置条件", "操作/静态检查", "预期", "来源", "备注"]
    for category in ("主流程", "关键边界", "明显异常"):
        rows = [
            [
                item["id"],
                ", ".join(item["requirementIds"]),
                item["precondition"],
                item["action"],
                item["expected"],
                item["source"],
                item["note"],
            ]
            for item in payload["testcases"]
            if item["category"] == category
        ]
        lines.extend(["", f"## {category}", "", markdown_table(headers, rows) if rows else "未发现"])
    coverage_rows = [
        [item["id"], ", ".join(item["testcaseIds"]) or "无", item["result"], item["runtimeAction"]]
        for item in payload["requirements"]
    ]
    lines.extend(
        [
            "",
            "## 需求覆盖",
            "",
            markdown_table(["需求", "用例", "静态结果", "运行验收动作"], coverage_rows),
            "",
            "## 冲突与缺口",
            "",
        ]
    )
    if payload["limits"]:
        lines.append(
            markdown_table(
                ["来源", "说明", "受影响需求"],
                [
                    [item["source"], item["reason"], ", ".join(item["affectedRequirementIds"]) or "无"]
                    for item in payload["limits"]
                ],
            )
        )
    else:
        lines.append("未发现")
    return "\n".join(lines) + "\n"


def h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def json_for_script(value: Any) -> str:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def evidence_html(entries: list[dict[str, str]]) -> str:
    if not entries:
        return '<span class="muted">无直接静态证据</span>'
    return "".join(
        f"""<div class="evidence-item"><code>{h(entry['repository'])}@{h(entry['ref'][:12])}</code>
<span class="evidence-path">{h(entry['path'])}:{h(entry['locator'])}</span>
<span class="muted">{h(entry['summary'])}</span>
<span class="muted">{h(entry.get('verification', '未校验'))}</span></div>"""
        for entry in entries
    )


def evidence_disclosure_html(entries: list[dict[str, str]]) -> str:
    if not entries:
        return '<p class="muted no-evidence">无直接静态证据</p>'
    return f"""<details class="evidence-disclosure"><summary>查看代码证据 <span>{len(entries)}</span></summary>
<div class="evidence">{evidence_html(entries)}</div></details>"""


def clarification_html(item: dict[str, Any]) -> str:
    finding_id = h(item["id"])
    hint = (
        "无法确认项仍需补证，审核选择不会自动将其移出未通过清单。"
        if item["type"] == "无法确认"
        else "仅调整验收者结论，不覆盖静态预检结论。"
    )
    return f"""<div class="clarification-zone">
<form class="clarification-form" data-finding-id="{finding_id}">
<div class="clarification-fields"><label>澄清说明（选填）<textarea name="note" rows="2" placeholder="可直接审核；如需说明，填写后点击标题行的审核按钮保存"></textarea></label></div>
<p class="clarification-hint">{hint}</p></form>
<div class="clarification-record" data-clarification-id="{finding_id}" hidden><span class="clarified-badge"></span><p class="clarification-copy"></p><button type="button" class="revoke-clarification" data-revoke-id="{finding_id}">撤销审核</button></div>
</div>"""


def finding_card_html(item: dict[str, Any]) -> str:
    kind_class = {"阻断": "block", "风险": "risk", "无法确认": "unknown"}[item["type"]]
    finding_id = h(item["id"])
    return f"""<details class="finding finding-{kind_class}">
<summary><span class="severity">{h(item['type'])}</span><span class="finding-title">{h(item['title'])}</span><code>{finding_id}</code>
<span class="finding-actions" role="group" aria-label="{finding_id} 审核"><span class="review-state" aria-live="polite">待审核</span><button type="button" class="review-action" data-review-decision="as-designed" data-review-id="{finding_id}" aria-pressed="false">设计如此</button><button type="button" class="review-action" data-review-decision="adopt-suggestion" data-review-id="{finding_id}" aria-pressed="false" title="采纳本项修正建议，待落实后复核">采纳建议</button><button type="button" class="review-action" data-review-decision="rejected" data-review-id="{finding_id}" aria-pressed="false">不通过</button></span></summary>
<div class="finding-body"><div class="finding-copy"><div><span class="label">影响</span><p>{h(item['impact'])}</p></div>
<div class="action-box"><span class="label">修正建议</span><p>{h(item['action'])}</p></div></div>
<p class="finding-reqs">关联需求：{h(', '.join(item['requirementIds']))}</p>
{evidence_disclosure_html(item['evidence'])}{clarification_html(item)}</div></details>"""


def requirement_card_html(item: dict[str, Any]) -> str:
    result_class = {
        "静态证据充分": "pass",
        "存在明显缺口": "block",
        "证据不完整": "partial",
        "无法确认": "unknown",
    }[item["result"]]
    cases = ", ".join(item["testcaseIds"]) or "无"
    return f"""<details class="requirement-card" data-result="{h(item['result'])}">
<summary><code>{h(item['id'])}</code><span class="requirement-title">{h(item['title'])}</span><span class="status status-{result_class}">{h(item['result'])}</span></summary>
<div class="requirement-body"><div class="requirement-meta"><span>{h(item['documentLocator'])}</span><span>关联用例：{h(cases)}</span></div>
<div class="requirement-grid"><div><span class="label">需求期望</span><p>{h(item['expectation'])}</p></div>
<div><span class="label">静态结论</span><p>{h(item['explanation'])}</p></div></div>
{evidence_disclosure_html(item['evidence'])}
<div class="runtime-action"><span class="label">运行验收动作</span><p>{h(item['runtimeAction'])}</p></div></div></details>"""


def configuration_check_card_html(item: dict[str, Any]) -> str:
    status_classes = {
        "通过": "pass",
        "发现异常": "block",
        "无法确认": "unknown",
    }
    status_class = status_classes[item["status"]]
    check_items = "".join(
        f"""<li><span class="status status-{status_classes[check_item['status']]}">{h(check_item['status'])}</span><div><strong>{h(check_item['label'])}</strong><p>{h(check_item['detail'])}</p></div></li>"""
        for check_item in item["checks"]
    )
    return f"""<article class="config-check-card"><div class="config-check-head"><div><code>{h(item['id'])}</code><strong>{h(item['workbook'])} / {h(item['table'])}</strong></div><span class="status status-{status_class}">{h(item['status'])}</span></div>
<div class="config-check-meta"><span>{h(item['authority'])}</span><span>数据行：{h(item['dataRowCount'])}</span><span>关联需求：{h(', '.join(item['affectedRequirementIds']))}</span></div>
<p>{h(item['summary'])}</p><div class="config-source"><code class="sha" title="{h(item['targetSha'])}">{h(item['targetSha'][:12])}</code><span>{h(item['sourcePath'])}</span></div>
<ul class="config-check-items">{check_items}</ul><p class="config-limitation">{h(item['limitation'])}</p></article>"""


def review_check_card_html(item: dict[str, Any]) -> str:
    status_class = {"已核对": "pass", "不适用": "partial", "无法确认": "unknown"}[
        item["status"]
    ]
    matched = "".join(f"<li>{h(value)}</li>" for value in item["matchedBy"])
    collector_status = (
        "采集失败角色：" + ", ".join(item["collectorFailureRoles"])
        if item["collectorFailureRoles"]
        else "采集正常"
    )
    return f"""<details class="rule-check-card"><summary class="rule-check-head"><span><code>{h(item['checkId'])}</code><strong>{h(item['title'])}</strong></span><span class="status status-{status_class}">{h(item['status'])}</span></summary>
<div class="rule-check-meta"><span>{h(item['scope'])}</span><span>关联需求：{h(', '.join(item['requirementIds']))}</span><span>{h(collector_status)}</span></div>
<p>{h(item['reason'])}</p><details><summary>命中原因与证据</summary><ul>{matched}</ul><p class="muted">应核对：{h(item['evidenceExpectation'])}</p>{evidence_html(item['evidence'])}</details></details>"""


def render_alignment_html(payload: dict[str, Any]) -> str:
    alignment = payload["alignment"]
    document = alignment["document"]
    cards = []
    for change in alignment["changes"]:
        rows = [
            ("文档定位", change["documentLocator"]), ("原规则", change["original"]),
            ("最终实现", change["implementation"]), ("建议修订", change["proposed"]),
            ("确认依据", change["confirmation"]),
            ("正文已应用", "是" if change["applied"] else "否，保留原文"),
            ("关联需求", ", ".join(change["requirementIds"]) or "已确认删除"),
            ("后续动作", change["followup"]),
        ]
        details = "".join(f"<dt>{h(label)}</dt><dd>{h(value)}</dd>" for label, value in rows)
        cards.append(f"<details><summary>{h(change['id'])} · {h(change['changeType'])} · {h(change['decision'])}</summary><dl>{details}</dl>{evidence_html(change['evidence'])}</details>")
    state = "已更新" if document["writeStatus"] == "updated" else "无需修改"
    failures = "".join(
        f"<p>未取得冻结版本：{h(item['role'])} · {h(item['code'])}；相关内容无法确认。</p>"
        for item in alignment.get("unavailableRepositories", [])
    )
    return (
        '<section class="panel alignment-panel"><h2>前置需求对齐</h2>'
        '<p>已完成 · 文档对齐与预审均不限时</p>'
        f"<p>{h(alignment['comparisonSummary'])}</p>"
        f"<p>文档版本：{h(document['beforeVersion'])} → {h(document['afterVersion'])}；{state}，已回读核验。</p>"
        f"<p>变更记录：{h(document.get('changeLogLocator', '无'))}</p>"
        + failures
        + ("".join(cards) or "<p>可核查范围内未发现需求差异，保留原文。</p>")
        + "</section>"
    )


def render_html(payload: dict[str, Any]) -> str:
    metadata = payload["metadata"]
    requirement_counts = Counter(item["result"] for item in payload["requirements"])
    finding_counts = Counter(item["type"] for item in payload["findings"])
    verdict_class = {
        "可进入运行验收": "pass",
        "建议修正后再提测": "block",
        "预检信息不足": "unknown",
    }[payload["overall"]["verdict"]]
    blockers = [item for item in payload["findings"] if item["type"] == "阻断"]
    supporting_findings = [
        item for item in payload["findings"] if item["type"] != "阻断"
    ]
    blocker_cards = "".join(
        finding_card_html(item) for item in blockers
    ) or '<p class="empty">未发现阻断项</p>'
    supporting_cards = "".join(
        finding_card_html(item) for item in supporting_findings
    )
    supporting_section = (
        f"""<section class="panel secondary-findings"><div class="section-head"><h2>风险与无法确认</h2><p>{len(supporting_findings)} 项 · 可直接审核</p></div>
<div class="findings supporting-grid">{supporting_cards}</div></section>"""
        if supporting_findings
        else ""
    )
    requirement_cards = "".join(
        requirement_card_html(item) for item in payload["requirements"]
    )
    review_check_cards = "".join(
        review_check_card_html(item) for item in payload["reviewChecks"]
    ) or '<p class="empty">本次没有命中专项规则</p>'
    configuration_cards = "".join(
        configuration_check_card_html(item) for item in payload["configurationChecks"]
    )
    configuration_section = (
        f"""<section class="panel config-evidence-panel"><div class="section-head"><div><p class="kicker">配置降级验证</p><h2>生成 JSON 次级证据</h2></div><p>{len(payload['configurationChecks'])} 项检查</p></div><div class="config-check-list">{configuration_cards}</div></section>"""
        if configuration_cards
        else ""
    )
    limit_items = "".join(
        f"<li><strong>{h(item['source'])}</strong>：{h(item['reason'])} <span class=\"muted\">({h(', '.join(item['affectedRequirementIds']) or '无')})</span></li>"
        for item in payload["limits"]
    ) or "<li>未发现限制</li>"
    repo_cards = "".join(
        f"""<article class="repo-card"><div><strong>{h(repo['role'])}</strong><span>{h(repo['source'])} · {h(repo.get('remote', '-'))}</span></div>
<dl><dt>Base</dt><dd><code>{h(repo.get('baseRef', '-'))}</code><code class="sha" title="{h(repo['base'])}">{h(repo['base'][:12])}</code></dd>
<dt>Target</dt><dd><code>{h(repo.get('targetRef', '-'))}</code><code class="sha" title="{h(repo['target'])}">{h(repo['target'][:12])}</code></dd></dl></article>"""
        for repo in metadata["repositories"]
    )
    readonly = payload["readonlyCheck"]
    readonly_invalid = (
        not readonly["contractPassed"]
        or (readonly["sourceMode"] == "local" and readonly["workspaceChanged"])
    )
    if not readonly["contractPassed"]:
        readonly_text = "工作流只读合同未通过"
    elif readonly["workspaceChanged"] and readonly["sourceMode"] == "remote":
        readonly_text = "工作流只读合同通过；用户并行工作，不影响远端 SHA 结论"
    elif readonly["workspaceChanged"]:
        readonly_text = "工作流只读合同通过；本地证据源已变化"
    else:
        readonly_text = "工作流只读合同通过；本地工作区未变化"
    cache_text = json.dumps(metadata["cache"], ensure_ascii=False, sort_keys=True)
    readonly_class = (
        "block"
        if readonly_invalid
        else ("unknown" if readonly["workspaceChanged"] else "pass")
    )
    review_findings = [
        {
            "id": item["id"],
            "type": item["type"],
            "title": item["title"],
            "impact": item["impact"],
            "action": item["action"],
        }
        for item in payload["findings"]
    ]
    review_key_material = "|".join(
        [metadata["feature"], metadata["documentVersion"]]
        + [repo["target"] for repo in metadata["repositories"]]
    )
    review_config = {
        "originalVerdict": payload["overall"]["verdict"],
        "findings": review_findings,
        "storageKey": "fast-precheck-review:"
        + hashlib.sha256(review_key_material.encode("utf-8")).hexdigest(),
    }
    unresolved_items = "".join(
        f"""<li data-unresolved-id="{h(item['id'])}"><span class="unresolved-type unresolved-{ {'阻断': 'block', '风险': 'risk', '无法确认': 'unknown'}[item['type']] }">{h(item['type'])}</span>
<div><strong>{h(item['title'])}</strong><code>{h(item['id'])}</code><p>{h(item['action'])}</p></div></li>"""
        for item in payload["findings"]
    ) or '<li class="all-clear">当前没有未通过项</li>'
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{h(metadata['feature'])} 静态预审</title>
<style>
:root{{--bg:#f4f6f8;--panel:#fff;--ink:#17202e;--muted:#667085;--line:#d9e0e8;--soft:#f7f9fb;--red:#b42318;--red-soft:#fff1f0;--amber:#a15c07;--amber-soft:#fff7e8;--green:#067647;--green-soft:#ecfdf3;--blue:#175cd3;--blue-soft:#eef4ff;--shadow:0 10px 28px rgba(16,24,40,.07)}}
*{{box-sizing:border-box}}[hidden]{{display:none!important}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 system-ui,"Microsoft YaHei",sans-serif}}button,select,input,textarea{{font:inherit}}code{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:13px;overflow-wrap:anywhere}}
.wrap{{max-width:1280px;margin:auto;padding:32px}}.hero,.panel{{background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}}.hero{{padding:30px;margin-bottom:22px;border-top:5px solid var(--red)}}.panel{{padding:26px;margin-bottom:22px}}
h1,h2,h3,p{{margin-top:0}}h1{{font-size:32px;line-height:1.25;margin-bottom:8px;letter-spacing:-.02em}}h2{{font-size:22px;margin-bottom:6px}}.muted{{color:var(--muted)}}.kicker,.label{{font-size:12px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}.kicker{{margin-bottom:6px}}.hero-top{{display:flex;justify-content:space-between;gap:24px;align-items:flex-start}}.hero-meta{{margin:0;color:var(--muted)}}
.readonly-pill{{flex:none;padding:7px 12px;border-radius:999px;font-size:13px;font-weight:750}}.readonly-pill.pass{{background:var(--green-soft);color:var(--green)}}.readonly-pill.block{{background:var(--red-soft);color:var(--red)}}.readonly-pill.unknown{{background:var(--amber-soft);color:var(--amber)}}
.verdict{{display:grid;grid-template-columns:minmax(210px,.7fr) 2fr;gap:28px;align-items:center;padding:22px 24px;border-radius:14px;margin:24px 0}}.verdict.block{{background:var(--red-soft);color:var(--red)}}.verdict.pass{{background:var(--green-soft);color:var(--green)}}.verdict.unknown{{background:var(--amber-soft);color:var(--amber)}}.verdict strong{{display:block;font-size:25px;line-height:1.25}}.verdict p{{margin:0;color:var(--ink)}}
.stats{{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}}.stat{{background:var(--soft);border:1px solid var(--line);border-radius:12px;padding:14px 16px}}.stat span{{display:block;color:var(--muted);font-size:13px}}.stat b{{display:block;font-size:28px;line-height:1.25;margin-top:4px}}.stat-danger b{{color:var(--red)}}.stat-risk b{{color:var(--amber)}}.notice{{margin:20px 0 0;padding:10px 14px;background:var(--blue-soft);border-radius:10px;color:#344054;font-size:14px}}
.review-decision-panel{{border-top:4px solid var(--blue)}}.decision-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.decision-card{{border:1px solid var(--line);border-radius:13px;padding:17px;background:var(--soft)}}.decision-card strong{{display:block;font-size:21px;line-height:1.3;margin:4px 0 7px}}.decision-card p{{margin:0;color:var(--muted)}}.decision-card.adjusted.block{{background:var(--red-soft);border-color:#fecdca}}.decision-card.adjusted.pass{{background:var(--green-soft);border-color:#abefc6}}.decision-card.adjusted.unknown{{background:var(--amber-soft);border-color:#fedf89}}.adjusted-counts{{display:flex;flex-wrap:wrap;gap:8px;margin-top:13px}}.adjusted-counts span{{background:#fff;border:1px solid var(--line);border-radius:999px;padding:3px 9px;font-size:13px}}.storage-status{{margin:14px 0 0;color:var(--muted);font-size:13px}}
.section-head{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:18px}}.section-head p{{margin:0;color:var(--muted)}}.priority-panel{{border-top:4px solid var(--red)}}.findings{{display:grid;gap:14px}}details>summary{{cursor:pointer}}.finding{{border:1px solid var(--line);border-radius:14px;overflow:hidden;background:#fff}}.finding-block{{border-left:5px solid var(--red)}}.finding-risk{{border-left:5px solid var(--amber)}}.finding-unknown{{border-left:5px solid #98a2b3}}.finding>summary{{display:grid;grid-template-columns:auto minmax(0,1fr) auto auto 20px;gap:12px;align-items:center;padding:16px 18px;list-style:none}}.finding>summary::-webkit-details-marker{{display:none}}.finding>summary::after{{content:"＋";font-size:20px;color:var(--muted)}}.finding[open]>summary::after{{content:"−"}}.finding>summary>code{{grid-column:3;grid-row:1}}.finding>summary::after{{grid-column:5;grid-row:1}}.severity{{font-size:12px;font-weight:800;border-radius:999px;padding:3px 9px}}.finding-block .severity{{background:var(--red-soft);color:var(--red)}}.finding-risk .severity{{background:var(--amber-soft);color:var(--amber)}}.finding-unknown .severity{{background:#f2f4f7;color:#475467}}.finding-title{{font-weight:800;font-size:17px}}.finding-body{{border-top:1px solid var(--line);padding:18px}}.finding-copy{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.finding-copy p,.runtime-action p,.requirement-grid p{{margin:5px 0 0}}.action-box,.runtime-action{{background:var(--blue-soft);border-radius:10px;padding:14px}}.finding-reqs{{font-size:13px;color:var(--muted);margin:14px 0 0}}
.evidence-disclosure{{margin-top:14px;border-top:1px dashed var(--line);padding-top:12px}}.evidence-disclosure>summary{{color:var(--blue);font-weight:750;list-style:none}}.evidence-disclosure>summary span,.secondary-findings>summary span{{display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:24px;padding:0 7px;border-radius:999px;background:var(--blue-soft);font-size:12px}}.evidence{{display:grid;gap:10px;margin-top:10px}}.evidence-item{{display:grid;grid-template-columns:auto 1fr;gap:4px 10px;background:var(--soft);border-radius:9px;padding:11px 12px}}.evidence-item .muted{{grid-column:1/-1}}.evidence-path{{overflow-wrap:anywhere}}.no-evidence{{margin:12px 0 0}}
.clarification-zone{{margin-top:16px;padding-top:16px;border-top:1px solid var(--line)}}.clarification-form>.label{{display:block;margin-bottom:8px}}.clarification-fields{{display:grid;grid-template-columns:1fr;gap:12px}}.clarification-fields label{{display:grid;gap:5px;color:var(--muted);font-size:13px}}.clarification-fields textarea{{width:100%;border:1px solid var(--line);border-radius:9px;background:#fff;color:var(--ink);padding:9px 10px;resize:vertical}}.clarification-fields textarea:focus{{outline:2px solid #84adff;outline-offset:1px;border-color:var(--blue)}}.clarification-form button{{margin-top:11px;background:var(--blue);border-color:var(--blue);color:#fff;font-weight:750}}.clarification-hint{{display:block;margin:8px 0 12px;color:var(--muted);font-size:12px}}.clarification-record{{background:var(--green-soft);border:1px solid #abefc6;border-radius:10px;padding:13px}}.clarified-badge{{font-size:12px;font-weight:800;color:var(--green)}}.clarification-copy{{margin:6px 0;color:#344054;white-space:pre-wrap}}.revoke-clarification{{padding:5px 9px;font-size:12px;color:var(--red)}}.finding.clarified{{border-left-color:var(--green)}}.finding.clarified .severity{{background:var(--green-soft);color:var(--green)}}
.secondary-findings{{margin:0 0 22px;background:var(--panel);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow);padding:0 24px}}.secondary-findings>summary{{padding:18px 0;font-weight:800;list-style:none}}.secondary-findings[open]>summary{{border-bottom:1px solid var(--line)}}.supporting-grid{{grid-template-columns:1fr;padding:0 0 24px}}
.config-evidence-panel{{border-top:4px solid var(--blue)}}.config-check-list{{display:grid;gap:14px;margin-top:18px}}.config-check-card{{border:1px solid var(--line);border-radius:13px;padding:17px;background:var(--soft)}}.config-check-head{{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}}.config-check-head>div{{display:grid;gap:5px}}.config-check-head strong{{font-size:17px}}.config-check-meta{{display:flex;flex-wrap:wrap;gap:8px 18px;margin:12px 0;color:var(--muted);font-size:13px}}.config-source{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;overflow-wrap:anywhere}}.config-check-items{{display:grid;gap:8px;margin:14px 0 0;padding:0;list-style:none}}.config-check-items li{{display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:start;background:#fff;border:1px solid var(--line);border-radius:9px;padding:10px 11px}}.config-check-items p{{margin:3px 0 0;color:var(--muted)}}.config-limitation{{margin:14px 0 0;padding:11px 12px;border-radius:9px;background:var(--amber-soft);color:var(--amber);font-weight:700}}
.rule-check-panel{{border-top:4px solid var(--blue)}}.rule-check-list{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}.rule-check-card{{border:1px solid var(--line);border-radius:12px;padding:15px;background:var(--soft)}}.rule-check-head{{display:flex;justify-content:space-between;gap:12px}}.rule-check-head>span:first-child{{display:grid;gap:4px}}.rule-check-meta{{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13px;margin:10px 0}}.rule-check-card details{{margin-top:10px}}.rule-check-card ul{{margin:8px 0;padding-left:20px}}
.requirement-panel{{padding-bottom:20px}}.toolbar{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:18px 0}}select,button{{padding:9px 12px;border:1px solid var(--line);border-radius:9px;background:white;color:var(--ink)}}button{{cursor:pointer}}button:hover{{border-color:#98a2b3}}.requirement-list{{display:grid;gap:10px}}.requirement-card{{border:1px solid var(--line);border-radius:12px;background:#fff;overflow:hidden}}.requirement-card[hidden]{{display:none}}.requirement-card>summary{{display:grid;grid-template-columns:90px 1fr auto;gap:14px;align-items:center;padding:15px 16px;list-style:none}}.requirement-card>summary::-webkit-details-marker{{display:none}}.requirement-card>summary::after{{content:"＋";color:var(--muted);font-size:19px}}.requirement-card[open]>summary::after{{content:"−"}}.requirement-card>summary .status{{grid-column:3;grid-row:1}}.requirement-card>summary::after{{grid-column:4;grid-row:1}}.requirement-title{{font-weight:750}}.status{{font-size:12px;font-weight:800;border-radius:999px;padding:4px 9px;white-space:nowrap}}.status-pass{{background:var(--green-soft);color:var(--green)}}.status-block{{background:var(--red-soft);color:var(--red)}}.status-partial{{background:var(--amber-soft);color:var(--amber)}}.status-unknown{{background:#f2f4f7;color:#475467}}.requirement-body{{border-top:1px solid var(--line);padding:18px}}.requirement-meta{{display:flex;flex-wrap:wrap;gap:8px 18px;color:var(--muted);font-size:13px;margin-bottom:15px}}.requirement-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.runtime-action{{margin-top:14px}}
.supporting-panel{{padding:0 24px}}.supporting-panel>summary{{padding:20px 0;font-size:19px;font-weight:800;list-style:none}}.supporting-panel[open]>summary{{border-bottom:1px solid var(--line)}}.supporting-content{{padding:18px 0 22px}}.limit-list{{margin:0;padding-left:22px}}.limit-list li+li{{margin-top:9px}}.readonly{{font-weight:800;color:{'var(--red)' if readonly_invalid else ('var(--amber)' if readonly['workspaceChanged'] else 'var(--green)')}}}.repo-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.repo-card{{border:1px solid var(--line);border-radius:12px;padding:14px;min-width:0}}.repo-card>div{{display:flex;justify-content:space-between;gap:10px}}.repo-card>div span{{color:var(--muted);font-size:13px}}.repo-card dl{{display:grid;grid-template-columns:auto 1fr;gap:8px 10px;margin:14px 0 0}}.repo-card dt{{color:var(--muted)}}.repo-card dd{{display:grid;gap:2px;margin:0;min-width:0}}.sha{{width:max-content;color:var(--blue)}}.document-link{{overflow-wrap:anywhere}}
.unresolved-panel{{border-top:4px solid #475467}}.unresolved-list{{display:grid;gap:10px;margin:18px 0 0;padding:0;list-style:none}}.unresolved-list li{{display:grid;grid-template-columns:auto 1fr;gap:12px;align-items:start;border:1px solid var(--line);border-radius:11px;padding:13px 14px}}.unresolved-list li>div{{display:grid;grid-template-columns:1fr auto;gap:2px 12px}}.unresolved-list li p{{grid-column:1/-1;margin:3px 0 0;color:var(--muted)}}.unresolved-type{{border-radius:999px;padding:3px 8px;font-size:12px;font-weight:800}}.unresolved-block{{background:var(--red-soft);color:var(--red)}}.unresolved-risk{{background:var(--amber-soft);color:var(--amber)}}.unresolved-unknown{{background:#f2f4f7;color:#475467}}.all-clear{{display:block!important;background:var(--green-soft);color:var(--green);font-weight:800}}
.finding-actions{{grid-column:4;grid-row:1;display:flex;flex-wrap:wrap;align-items:center;gap:7px}}.review-state{{font-size:12px;color:var(--muted);white-space:nowrap}}.review-action{{font-size:13px;padding:6px 10px;white-space:nowrap}}.review-action[aria-pressed="true"][data-review-decision="as-designed"]{{color:var(--green);border-color:var(--green);background:var(--green-soft)}}.review-action[aria-pressed="true"][data-review-decision="rejected"]{{color:var(--red);border-color:var(--red);background:var(--red-soft)}}.finding.rejected{{border-left-color:var(--red)}}.finding.rejected .clarification-record{{background:var(--red-soft);border-color:#fecdca}}.finding.rejected .clarified-badge{{color:var(--red)}}.clarification-record{{margin-top:12px}}.secondary-findings>.section-head{{padding-top:22px}}.rule-check-head{{list-style:none}}.rule-check-head::-webkit-details-marker{{display:none}}.rule-check-head::after{{content:"＋";color:var(--muted)}}.rule-check-card[open]>.rule-check-head::after{{content:"−"}}.report-toolbar{{margin:18px 0 0}}.report-toolbar .muted{{font-size:13px}}
.review-action[aria-pressed="true"][data-review-decision="adopt-suggestion"]{{color:var(--blue);border-color:var(--blue);background:var(--blue-soft)}}.finding.adopted{{border-left-color:var(--blue)}}.finding.adopted .clarification-record{{background:var(--blue-soft);border-color:#84adff}}.finding.adopted .clarified-badge{{color:var(--blue)}}
@media (max-width:900px){{.stats{{grid-template-columns:repeat(3,1fr)}}.repo-grid{{grid-template-columns:1fr}}.supporting-grid,.rule-check-list{{grid-template-columns:1fr}}}}
@media (max-width:760px){{body{{font-size:15px}}.wrap{{padding:16px}}.hero,.panel{{border-radius:14px}}.hero{{padding:20px}}.panel{{padding:20px}}h1{{font-size:26px}}.hero-top{{display:block}}.readonly-pill{{display:inline-block;margin-top:14px}}.verdict{{grid-template-columns:1fr;gap:8px;padding:18px}}.stats{{grid-template-columns:repeat(2,1fr)}}.decision-grid,.clarification-fields{{grid-template-columns:1fr}}.clarification-hint{{display:block;margin:8px 0 0}}.finding>summary{{grid-template-columns:auto 1fr auto}}.finding>summary>code{{display:none}}.finding-actions{{grid-column:1/-1;grid-row:2}}.finding>summary::after{{grid-column:3}}.finding-copy,.requirement-grid{{grid-template-columns:1fr}}.requirement-card>summary{{grid-template-columns:72px 1fr auto;gap:9px}}.requirement-card>summary .status{{grid-column:2;grid-row:2;width:max-content}}.requirement-card>summary::after{{grid-column:3;grid-row:1/3}}.evidence-item{{grid-template-columns:1fr}}.evidence-item .muted{{grid-column:1}}.toolbar label{{width:100%}}.unresolved-list li>div{{grid-template-columns:1fr}}.unresolved-list li code,.unresolved-list li p{{grid-column:1}}}}
@media print{{body{{background:#fff;font-size:12px}}.wrap{{max-width:none;padding:0}}.hero,.panel,.secondary-findings{{box-shadow:none;break-inside:avoid}}.toolbar,.clarification-form,.revoke-clarification,.finding-actions button{{display:none!important}}details>summary::after{{display:none}}details>*{{display:block!important}}.requirement-card,.finding{{break-inside:avoid}}}}
</style></head><body><main class="wrap">
<header class="hero"><div class="hero-top"><div><p class="kicker">K1 · 静态预审</p><h1>{h(metadata['feature'])}</h1><p class="hero-meta">文档版本：{h(metadata['documentVersion'])} · 耗时 {h(metadata['elapsedSeconds'])} 秒 · 不限时</p></div><span class="readonly-pill {readonly_class}">只读终检 · {'未通过' if readonly_invalid else '通过'}</span></div>
<div class="verdict {verdict_class}"><div><span class="label">总体结论</span><strong>{h(payload['overall']['verdict'])}</strong></div><p>{h(payload['overall']['reason'])}</p></div>
<div class="stats"><div class="stat stat-danger"><span>阻断</span><b>{finding_counts['阻断']}</b></div><div class="stat stat-risk"><span>风险</span><b>{finding_counts['风险']}</b></div><div class="stat"><span>无法确认</span><b>{finding_counts['无法确认']}</b></div><div class="stat"><span>证据充分需求</span><b>{requirement_counts['静态证据充分']}</b></div><div class="stat"><span>需求总数</span><b>{len(payload['requirements'])}</b></div></div>
<div class="notice">{DISCLAIMER}<br>{ANCHOR_NOTICE}</div><div class="toolbar report-toolbar"><button type="button" id="expand-all">展开全部</button><button type="button" id="collapse-all">全部收起</button><span class="muted">默认收起；标题行可直接审核，说明选填</span></div></header>
{render_alignment_html(payload)}
<section class="panel review-decision-panel"><div class="section-head"><div><p class="kicker">验收澄清</p><h2>审核结论</h2></div><p>澄清不会覆盖静态证据</p></div><div class="decision-grid">
<article class="decision-card"><span class="label">静态预检结论</span><strong>{h(payload['overall']['verdict'])}</strong><p>{h(payload['overall']['reason'])}</p></article>
<article class="decision-card adjusted {verdict_class}" id="adjusted-card"><span class="label">验收者调整后结论</span><strong id="adjusted-verdict">{h(payload['overall']['verdict'])}</strong><p id="adjusted-summary">尚未登记审核选择</p><div class="adjusted-counts"><span>阻断 <b id="adjusted-block-count">{finding_counts['阻断']}</b></span><span>风险 <b id="adjusted-risk-count">{finding_counts['风险']}</b></span><span>无法确认 <b id="adjusted-unknown-count">{finding_counts['无法确认']}</b></span></div></article>
</div><p class="storage-status" id="storage-status">审核选择与说明保存在当前浏览器，可修改或撤销。</p></section>
<section class="panel priority-panel"><div class="section-head"><div><p class="kicker">优先处理</p><h2>阻断项</h2></div><p>{len(blockers)} 项需要在提测前处理</p></div><div class="toolbar"><button type="button" id="expand-findings">展开问题</button><button type="button" id="collapse-findings">收起问题</button></div><div class="findings">{blocker_cards}</div></section>
{supporting_section}
{configuration_section}
<section class="panel requirement-panel"><div class="section-head"><div><p class="kicker">完整追踪</p><h2>需求—用例—证据矩阵</h2></div><p>默认收起，按需查看</p></div><div class="toolbar"><label>结果筛选 <select id="filter"><option value="">全部</option><option>静态证据充分</option><option>存在明显缺口</option><option>证据不完整</option><option>无法确认</option></select></label><button type="button" id="expand-requirements">展开可见项</button><button type="button" id="collapse-requirements">全部收起</button><button type="button" onclick="window.print()">打印</button></div>
<div class="requirement-list">{requirement_cards}</div></section>
<section class="panel rule-check-panel"><div class="section-head"><div><p class="kicker">规则覆盖</p><h2>规则核对记录</h2></div><p>{len(payload['reviewChecks'])} 项已留痕</p></div><div class="rule-check-list">{review_check_cards}</div></section>
<details class="panel supporting-panel"><summary>限制与运行验收建议</summary><div class="supporting-content"><ul class="limit-list">{limit_items}</ul><p class="readonly">只读终检：{h(readonly_text)}</p></div></details>
<details class="panel supporting-panel"><summary>审查对象与版本</summary><div class="supporting-content"><div class="repo-grid">{repo_cards}</div><p class="muted document-link">开发文档：{h(metadata['documentSource'])}</p><p class="muted">缓存：<code>{h(cache_text)}</code></p></div></details>
<section class="panel unresolved-panel"><div class="section-head"><div><p class="kicker">最终清单</p><h2>所有未通过项</h2></div><p id="unresolved-summary">未澄清的阻断、风险与无法确认</p></div><ul class="unresolved-list" id="unresolved-list">{unresolved_items}</ul></section>
</main><script id="review-config" type="application/json">{json_for_script(review_config)}</script><script>
/* REVIEW_STATE_LOGIC_START */
function getReviewDecision(record){{
  if(!record||typeof record.clarifiedAt!=='string'||!record.clarifiedAt.trim()){{return null;}}
  if(record.decision==='as-designed'||record.decision==='adopt-suggestion'||record.decision==='rejected'){{return record.decision;}}
  // Reports before explicit decisions stored only an explanation and timestamp.
  if(record.decision===undefined&&typeof record.note==='string'&&record.note.trim()){{return 'as-designed';}}
  return null;
}}
function hasValidClarification(record){{return getReviewDecision(record)==='as-designed';}}
function deriveReviewState(originalVerdict,findings,clarifications){{
  const unresolved=findings.filter(function(item){{
    const clarifiable=item.type==='阻断'||item.type==='风险';
    return !(clarifiable&&hasValidClarification(clarifications[item.id]));
  }});
  const counts={{'阻断':0,'风险':0,'无法确认':0}};
  unresolved.forEach(function(item){{if(Object.prototype.hasOwnProperty.call(counts,item.type)){{counts[item.type]+=1;}}}});
  const needsFollowup=findings.some(function(item){{const decision=getReviewDecision(clarifications[item.id]);return decision==='rejected'||decision==='adopt-suggestion';}});
  const verdict=originalVerdict==='预检信息不足'?'预检信息不足':(counts['阻断']>0||needsFollowup?'建议修正后再提测':'可进入运行验收');
  return {{verdict:verdict,unresolvedIds:unresolved.map(function(item){{return item.id;}}),counts:counts}};
}}
/* REVIEW_STATE_LOGIC_END */
const reviewConfig=JSON.parse(document.getElementById('review-config').textContent);
const requirementCards=Array.from(document.querySelectorAll('.requirement-card'));
function setDisclosureState(selector,open){{
  document.querySelectorAll(selector).forEach(function(card){{if(!open||!card.closest('[hidden]')){{card.open=open;}}}});
}}
document.getElementById('expand-all').addEventListener('click',function(){{setDisclosureState('details',true);}});
document.getElementById('collapse-all').addEventListener('click',function(){{setDisclosureState('details',false);}});
document.getElementById('expand-findings').addEventListener('click',function(){{setDisclosureState('.finding',true);}});
document.getElementById('collapse-findings').addEventListener('click',function(){{setDisclosureState('.finding',false);}});
document.getElementById('filter').addEventListener('change',function(){{const v=this.value;requirementCards.forEach(function(card){{card.hidden=Boolean(v)&&card.dataset.result!==v;}});}});
document.getElementById('expand-requirements').addEventListener('click',function(){{setDisclosureState('.requirement-card',true);}});
document.getElementById('collapse-requirements').addEventListener('click',function(){{setDisclosureState('.requirement-card',false);}});
const storageStatus=document.getElementById('storage-status');let storageAvailable=true;let clarifications={{}};
try{{
  const saved=localStorage.getItem(reviewConfig.storageKey);
  if(saved){{const parsed=JSON.parse(saved);if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed)){{clarifications=parsed;}}}}
}}catch(error){{storageAvailable=false;storageStatus.textContent='当前浏览器不允许本地保存；审核选择仅在本次页面会话有效。';}}
function persistClarifications(){{
  if(!storageAvailable){{return;}}
  try{{localStorage.setItem(reviewConfig.storageKey,JSON.stringify(clarifications));storageStatus.textContent='审核选择与说明已保存在当前浏览器。';}}
  catch(error){{storageAvailable=false;storageStatus.textContent='本地保存失败；后续审核选择仅在本次页面会话有效。';}}
}}
function verdictClass(verdict){{return verdict==='可进入运行验收'?'pass':(verdict==='预检信息不足'?'unknown':'block');}}
function renderUnresolved(state){{
  const list=document.getElementById('unresolved-list');list.textContent='';
  const unresolved=reviewConfig.findings.filter(function(item){{return state.unresolvedIds.includes(item.id);}});
  document.getElementById('unresolved-summary').textContent=unresolved.length?('共 '+unresolved.length+' 项待处理（含未审核、采纳待落实、不通过及待补证项）'):'当前没有未通过项';
  if(!unresolved.length){{const empty=document.createElement('li');empty.className='all-clear';empty.textContent='当前没有未通过项';list.appendChild(empty);return;}}
  unresolved.forEach(function(item){{
    const row=document.createElement('li');row.dataset.unresolvedId=item.id;
    const type=document.createElement('span');type.className='unresolved-type unresolved-'+(item.type==='阻断'?'block':(item.type==='风险'?'risk':'unknown'));type.textContent=item.type;
    const body=document.createElement('div');const title=document.createElement('strong');title.textContent=item.title;
    const code=document.createElement('code');code.textContent=item.id;
    const action=document.createElement('p');const decision=getReviewDecision(clarifications[item.id]);
    action.textContent=(decision==='rejected'?'审核：不通过。':(decision==='adopt-suggestion'?'审核：采纳建议；待落实后复核。':(decision==='as-designed'?'审核：设计如此；仍需补证。':'')))+item.action;
    body.append(title,code,action);row.append(type,body);list.appendChild(row);
  }});
}}
const reviewForms=Array.from(document.querySelectorAll('.clarification-form'));
function renderClarifications(){{
  const state=deriveReviewState(reviewConfig.originalVerdict,reviewConfig.findings,clarifications);
  const adjusted=document.getElementById('adjusted-card');adjusted.classList.remove('block','pass','unknown');adjusted.classList.add(verdictClass(state.verdict));
  document.getElementById('adjusted-verdict').textContent=state.verdict;
  document.getElementById('adjusted-summary').textContent='按已登记审核选择计算；原始静态结论保持不变。';
  document.getElementById('adjusted-block-count').textContent=state.counts['阻断'];
  document.getElementById('adjusted-risk-count').textContent=state.counts['风险'];
  document.getElementById('adjusted-unknown-count').textContent=state.counts['无法确认'];
  reviewForms.forEach(function(form){{
    const id=form.dataset.findingId;const record=clarifications[id];const decision=getReviewDecision(record);
    const finding=form.closest('.finding');const recordBox=finding.querySelector('.clarification-record');
    const label=decision==='as-designed'?'设计如此':(decision==='adopt-suggestion'?'采纳建议':(decision==='rejected'?'不通过':'待审核'));
    finding.querySelector('.review-state').textContent=label;
    finding.classList.toggle('clarified',decision==='as-designed');finding.classList.toggle('rejected',decision==='rejected');
    finding.classList.toggle('adopted',decision==='adopt-suggestion');
    finding.querySelectorAll('.review-action').forEach(function(button){{button.setAttribute('aria-pressed',String(button.dataset.reviewDecision===decision));}});
    recordBox.hidden=!decision;
    if(decision){{
      const parsedDate=new Date(record.clarifiedAt);const displayDate=Number.isNaN(parsedDate.getTime())?record.clarifiedAt:parsedDate.toLocaleString('zh-CN');
      recordBox.querySelector('.clarified-badge').textContent='已审核 · '+label;
      recordBox.querySelector('.clarification-copy').textContent=displayDate+(record.note?'\\n'+record.note:' · 未填写说明');
    }}
  }});
  renderUnresolved(state);
}}
function saveDecision(form,decision){{
  clarifications[form.dataset.findingId]={{decision:decision,note:form.elements.note.value.trim(),clarifiedAt:new Date().toISOString()}};
  persistClarifications();renderClarifications();
}}
reviewForms.forEach(function(form){{
  const record=clarifications[form.dataset.findingId];
  if(getReviewDecision(record)&&typeof record.note==='string'){{form.elements.note.value=record.note;}}
  form.addEventListener('submit',function(event){{
    event.preventDefault();const decision=getReviewDecision(clarifications[form.dataset.findingId]);
    if(decision){{saveDecision(form,decision);}}
  }});
}});
document.querySelectorAll('.review-action').forEach(function(button){{
  button.addEventListener('click',function(event){{
    event.preventDefault();event.stopPropagation();
    const form=button.closest('.finding').querySelector('.clarification-form');
    saveDecision(form,button.dataset.reviewDecision);
  }});
}});
document.querySelectorAll('.revoke-clarification').forEach(function(button){{
  button.addEventListener('click',function(){{
    delete clarifications[button.dataset.revokeId];
    button.closest('.finding').querySelector('.clarification-form').reset();persistClarifications();renderClarifications();
  }});
}});
renderClarifications();
let printOpenState=[];window.addEventListener('beforeprint',function(){{const details=Array.from(document.querySelectorAll('details'));printOpenState=details.map(function(item){{return item.open;}});details.forEach(function(item){{item.open=true;}});}});window.addEventListener('afterprint',function(){{document.querySelectorAll('details').forEach(function(item,index){{item.open=Boolean(printOpenState[index]);}});}});
</script></body></html>
"""


def safe_slug(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", ascii_value).strip("-_").lower()
    return slug[:64] or "feature"


def parse_finished_at(value: str) -> datetime:
    normalized = re.sub(
        r"\.(\d{7,})(?=(?:Z|[+-]\d{2}:\d{2})$)",
        lambda match: "." + match.group(1)[:6],
        value,
    )
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("INVALID_FINISHED_AT") from error
    if parsed.tzinfo is None:
        raise ContractError("INVALID_FINISHED_AT")
    return parsed.astimezone(timezone.utc)


def is_within(path: Path, boundary: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_boundary = boundary.resolve(strict=False)
    return resolved_path == resolved_boundary or resolved_boundary in resolved_path.parents


def require_paths(input_path: Path, reports_root: Path) -> tuple[Path, Path]:
    check_write_path(reports_root)
    if not is_within(input_path, workspace_root()) or not input_path.resolve().is_file():
        raise ContractError("INPUT_OUTSIDE_FUNCTIONPREVIEW")
    if not is_within(reports_root, (workspace_root() / "reports")):
        raise ContractError("REPORTS_ROOT_OUTSIDE_FUNCTIONPREVIEW")
    resolved_root = reports_root.resolve(strict=True)
    if not resolved_root.is_dir() or not is_within(resolved_root, (workspace_root() / "reports")):
        raise ContractError("INVALID_REPORTS_ROOT")
    return input_path.resolve(), resolved_root


def write_report_file(path: Path, content: str) -> str:
    data = content.encode("utf-8")
    if not data:
        raise PublishError(f"EMPTY_REPORT:{path.name}")
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return hashlib.sha256(data).hexdigest()


def publish(payload: dict[str, Any], reports_root: Path) -> dict[str, Any]:
    check_write_path(reports_root)
    metadata = payload["metadata"]
    timestamp = parse_finished_at(metadata["finishedAt"]).strftime("%Y%m%d-%H%M%S")
    leaf = f"{timestamp}-{safe_slug(metadata['feature'])}-{uuid.uuid4().hex[:8]}"
    partial = reports_root / f"{leaf}.partial"
    final = reports_root / leaf
    if partial.exists() or final.exists():
        raise PublishError("REPORT_DIRECTORY_COLLISION")
    partial.mkdir()
    contents = {
        "summary.html": render_html(payload),
        "review.md": render_review(payload),
        "testcases.md": render_testcases(payload),
    }
    hashes: dict[str, str] = {}
    fail_after = os.environ.get("FAST_PRECHECK_TEST_FAIL_AFTER")
    try:
        for name in ("summary.html", "review.md", "testcases.md"):
            hashes[name] = write_report_file(partial / name, contents[name])
            if fail_after == name:
                raise PublishError(f"SIMULATED_FAILURE_AFTER:{name}")
        if set(path.name for path in partial.iterdir()) != set(contents):
            raise PublishError("UNEXPECTED_REPORT_FILE")
        partial.rename(final)
    except Exception as error:
        if isinstance(error, PublishError):
            raise
        raise PublishError(str(error)) from error
    return {
        "status": "ok",
        "code": "FAST_PRECHECK_PUBLISHED",
        "directory": str(final),
        "sha256": hashes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render K1 fast static precheck reports")
    parser.add_argument("--input", required=True)
    parser.add_argument("--reports-root", required=True)
    parser.add_argument("--evidence-manifest", help="Frozen role/repository/targetSha manifest; required for code evidence")
    parser.add_argument("--git-evidence", required=True, help="Frozen collect-git-evidence JSON")
    parser.add_argument("--review-rules", default=str(DEFAULT_REVIEW_RULES))
    args = parser.parse_args()
    try:
        input_path, reports_root = require_paths(Path(args.input), Path(args.reports_root))
        try:
            raw = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ContractError("INVALID_INPUT_JSON") from error
        payload = validate_payload(raw)
        try:
            git_evidence = json.loads(Path(args.git_evidence).read_text(encoding="utf-8-sig"))
            review_rules = json.loads(Path(args.review_rules).read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ContractError("INVALID_RULE_INPUT") from error
        selection = resolve_rules(review_rules, payload, git_evidence)
        validate_review_coverage(payload, selection)
        manifest = None
        if args.evidence_manifest:
            try:
                manifest = json.loads(Path(args.evidence_manifest).read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ContractError("INVALID_EVIDENCE_MANIFEST") from error
        validate_failure_manifest(payload["metadata"], manifest)
        verify_report(payload, manifest)
        receipt = publish(payload, reports_root)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (ContractError, AnchorError, RuleMatchError, AlignmentError, UserConfigError) as error:
        print(str(error), file=sys.stderr)
        return 4
    except PublishError as error:
        print(f"PUBLISH_FAILED:{error}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())
