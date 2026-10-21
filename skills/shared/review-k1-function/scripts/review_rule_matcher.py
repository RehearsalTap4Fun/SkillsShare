"""Resolve additive K1 review rules from frozen change paths and requirements."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


ROLES = {"client", "server", "config"}
SCOPES = ROLES | {"cross-end"}
REQUIRED_RULE_FIELDS = {
    "id",
    "scope",
    "title",
    "trigger",
    "evidenceExpectation",
    "notApplicableWhen",
}
OPTIONAL_RULE_FIELDS = {
    "rolesAny",
    "rolesAll",
    "evidenceRoles",
    "pathGlobsAny",
    "requirementTermsAny",
}


class RuleMatchError(Exception):
    pass


def require_mapping(value: Any, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuleMatchError(code)
    return value


def require_list(value: Any, code: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuleMatchError(code)
    return value


def require_string(mapping: dict[str, Any], field: str, code: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuleMatchError(code)
    return value


def string_list(mapping: dict[str, Any], field: str, code: str) -> list[str]:
    value = mapping.get(field, [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise RuleMatchError(code)
    if len(value) != len(set(value)):
        raise RuleMatchError(code)
    return value


def validate_rules(value: Any) -> list[dict[str, Any]]:
    root = require_mapping(value, "INVALID_REVIEW_RULES")
    if root.get("schemaVersion") != 1 or set(root) != {"schemaVersion", "rules"}:
        raise RuleMatchError("INVALID_REVIEW_RULES")
    rules = []
    seen = set()
    for raw in require_list(root["rules"], "INVALID_REVIEW_RULES"):
        rule = require_mapping(raw, "INVALID_REVIEW_RULE")
        if not REQUIRED_RULE_FIELDS.issubset(rule) or not set(rule).issubset(
            REQUIRED_RULE_FIELDS | OPTIONAL_RULE_FIELDS
        ):
            raise RuleMatchError("INVALID_REVIEW_RULE")
        identifier = require_string(rule, "id", "INVALID_REVIEW_RULE")
        if identifier in seen:
            raise RuleMatchError("DUPLICATE_REVIEW_RULE")
        seen.add(identifier)
        if not re.fullmatch(r"[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*", identifier):
            raise RuleMatchError("INVALID_REVIEW_RULE")
        scope = require_string(rule, "scope", "INVALID_REVIEW_RULE")
        if scope not in SCOPES or rule.get("trigger") not in {"any", "all"}:
            raise RuleMatchError("INVALID_REVIEW_RULE")
        for field in ("title", "evidenceExpectation", "notApplicableWhen"):
            require_string(rule, field, "INVALID_REVIEW_RULE")
        roles_any = string_list(rule, "rolesAny", "INVALID_REVIEW_RULE")
        roles_all = string_list(rule, "rolesAll", "INVALID_REVIEW_RULE")
        if not set(roles_any + roles_all).issubset(ROLES):
            raise RuleMatchError("INVALID_REVIEW_RULE")
        evidence_roles = string_list(rule, "evidenceRoles", "INVALID_REVIEW_RULE")
        primary_roles = list(dict.fromkeys(roles_any + roles_all))
        if not evidence_roles:
            evidence_roles = primary_roles
        if (
            not set(evidence_roles).issubset(ROLES)
            or not set(primary_roles).issubset(evidence_roles)
        ):
            raise RuleMatchError("INVALID_REVIEW_RULE")
        paths = string_list(rule, "pathGlobsAny", "INVALID_REVIEW_RULE")
        terms = string_list(rule, "requirementTermsAny", "INVALID_REVIEW_RULE")
        if not roles_any and not roles_all:
            raise RuleMatchError("INVALID_REVIEW_RULE")
        if not paths and not terms:
            raise RuleMatchError("INVALID_REVIEW_RULE")
        rules.append(
            {
                **rule,
                "rolesAny": roles_any,
                "rolesAll": roles_all,
                "evidenceRoles": evidence_roles,
                "pathGlobsAny": paths,
                "requirementTermsAny": terms,
            }
        )
    return rules


def repository_inputs(
    payload: Any, evidence: Any
) -> tuple[dict[str, list[str]], set[str], set[str]]:
    report = require_mapping(payload, "INVALID_RULE_INPUT")
    metadata = require_mapping(report.get("metadata"), "INVALID_RULE_INPUT")
    report_repositories = require_list(metadata.get("repositories"), "INVALID_RULE_INPUT")
    targets = {}
    for raw in report_repositories:
        item = require_mapping(raw, "INVALID_RULE_INPUT")
        role = require_string(item, "role", "INVALID_RULE_INPUT")
        target = require_string(item, "target", "INVALID_RULE_INPUT")
        if role not in ROLES or role in targets or not re.fullmatch(r"[0-9a-fA-F]{40}", target):
            raise RuleMatchError("INVALID_RULE_INPUT")
        targets[role] = target.lower()

    collected = require_mapping(evidence, "INVALID_RULE_GIT_EVIDENCE")
    if (
        collected.get("schemaVersion") != 1
        or collected.get("status") not in {"ok", "error", "requires_input"}
    ):
        raise RuleMatchError("INVALID_RULE_GIT_EVIDENCE")
    changed: dict[str, list[str]] = {}
    failed_roles: set[str] = set()
    for raw in require_list(collected.get("repositories"), "INVALID_RULE_GIT_EVIDENCE"):
        item = require_mapping(raw, "INVALID_RULE_GIT_EVIDENCE")
        role = require_string(item, "role", "INVALID_RULE_GIT_EVIDENCE")
        if role not in targets or role in changed:
            raise RuleMatchError("INVALID_RULE_GIT_EVIDENCE")
        target_value = item.get("target")
        if isinstance(target_value, dict):
            target = target_value.get("sha")
            if not isinstance(target, str):
                failed_roles.add(role)
                target = target_value.get("requested")
        else:
            target = target_value
        if not isinstance(target, str) or target.lower() != targets[role]:
            raise RuleMatchError("RULE_EVIDENCE_TARGET_MISMATCH")
        paths = []
        for change_raw in require_list(item.get("changes"), "INVALID_RULE_GIT_EVIDENCE"):
            change = require_mapping(change_raw, "INVALID_RULE_GIT_EVIDENCE")
            path = require_string(change, "path", "INVALID_RULE_GIT_EVIDENCE")
            if path not in paths:
                paths.append(path)
        changed[role] = paths
    if set(changed) != set(targets):
        raise RuleMatchError("INVALID_RULE_GIT_EVIDENCE")
    for field in ("errors", "requiresInput"):
        for raw in require_list(
            collected.get(field, []), "INVALID_RULE_GIT_EVIDENCE"
        ):
            item = require_mapping(raw, "INVALID_RULE_GIT_EVIDENCE")
            role = require_string(item, "role", "INVALID_RULE_GIT_EVIDENCE")
            if role not in targets:
                raise RuleMatchError("INVALID_RULE_GIT_EVIDENCE")
            failed_roles.add(role)
    # Roles without a preparer SHA still participate in semantic rule selection.
    # They cannot supply paths/evidence and their checks must remain unknown.
    unavailable = metadata.get("unavailableRepositories", [])
    if not isinstance(unavailable, list):
        raise RuleMatchError("INVALID_RULE_INPUT")
    for raw in unavailable:
        item = require_mapping(raw, "INVALID_RULE_INPUT")
        role = require_string(item, "role", "INVALID_RULE_INPUT")
        require_string(item, "code", "INVALID_RULE_INPUT")
        if role not in ROLES or role in changed:
            raise RuleMatchError("INVALID_RULE_INPUT")
        changed[role] = []
        failed_roles.add(role)
    return changed, set(changed), failed_roles


def requirement_paths(requirement: dict[str, Any], changed: dict[str, list[str]]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = {role: [] for role in changed}
    evidence_entries = requirement.get("evidence", [])
    if not isinstance(evidence_entries, list):
        raise RuleMatchError("INVALID_RULE_REQUIREMENT")
    for raw in evidence_entries:
        entry = require_mapping(raw, "INVALID_RULE_REQUIREMENT")
        role = entry.get("repository")
        path = entry.get("path")
        if role in mapped and isinstance(path, str) and path and path not in mapped[role]:
            mapped[role].append(path)
    return mapped


def path_matches(path: str, pattern: str) -> bool:
    normalized_path = path.replace("\\", "/").casefold()
    normalized_pattern = pattern.casefold()
    if fnmatch.fnmatchcase(normalized_path, normalized_pattern):
        return True
    return normalized_pattern.startswith("**/") and fnmatch.fnmatchcase(
        normalized_path, normalized_pattern[3:]
    )


def resolve_rules(rules_value: Any, payload: Any, evidence: Any) -> dict[str, Any]:
    rules = validate_rules(rules_value)
    changed, changed_roles, failed_roles = repository_inputs(payload, evidence)
    report = require_mapping(payload, "INVALID_RULE_INPUT")
    requirements = require_list(report.get("requirements"), "INVALID_RULE_INPUT")
    selected = []
    seen_requirement_ids = set()
    for raw_requirement in requirements:
        requirement = require_mapping(raw_requirement, "INVALID_RULE_REQUIREMENT")
        identifier = require_string(requirement, "id", "INVALID_RULE_REQUIREMENT")
        if identifier in seen_requirement_ids:
            raise RuleMatchError("DUPLICATE_RULE_REQUIREMENT")
        seen_requirement_ids.add(identifier)
        text = "\n".join(
            require_string(requirement, field, "INVALID_RULE_REQUIREMENT")
            for field in ("title", "documentLocator", "expectation")
        ).casefold()
        mapped_paths = requirement_paths(requirement, changed)
        for rule in rules:
            if rule["rolesAny"] and not changed_roles.intersection(rule["rolesAny"]):
                continue
            if rule["rolesAll"] and not set(rule["rolesAll"]).issubset(changed_roles):
                continue
            relevant_roles = list(dict.fromkeys(rule["rolesAny"] + rule["rolesAll"]))
            path_hits = []
            for role in relevant_roles:
                for path in mapped_paths.get(role, []):
                    if any(path_matches(path, pattern) for pattern in rule["pathGlobsAny"]):
                        path_hits.append(f"path:{role}:{path}")
            term_hits = [
                f"signal:{term}"
                for term in rule["requirementTermsAny"]
                if term.casefold() in text
            ]
            selectors = []
            if rule["pathGlobsAny"]:
                selectors.append(bool(path_hits))
            if rule["requirementTermsAny"]:
                selectors.append(bool(term_hits))
            matched = all(selectors) if rule["trigger"] == "all" else any(selectors)
            if not matched:
                continue
            selected.append(
                {
                    "checkId": f"{identifier}::{rule['id']}",
                    "ruleId": rule["id"],
                    "scope": rule["scope"],
                    "title": rule["title"],
                    "requirementIds": [identifier],
                    "repositoryRoles": relevant_roles,
                    "evidenceRoles": rule["evidenceRoles"],
                    "requiredEvidenceRoles": rule["rolesAll"],
                    "anyEvidenceRoles": rule["rolesAny"],
                    "collectorFailureRoles": [
                        role for role in relevant_roles if role in failed_roles
                    ],
                    "matchedBy": path_hits + term_hits,
                    "evidenceExpectation": rule["evidenceExpectation"],
                    "notApplicableWhen": rule["notApplicableWhen"],
                }
            )
    return {"schemaVersion": 1, "selectedRules": selected}


def load_json(path: Path, code: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuleMatchError(code) from error


def main() -> int:
    from user_config import check_write_path, UserConfigError
    parser = argparse.ArgumentParser(description="Resolve additive K1 review rules")
    parser.add_argument("--rules", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--git-evidence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        output = check_write_path(args.output)
        rules_path = Path(args.rules)
        payload_path = Path(args.input)
        evidence_path = Path(args.git_evidence)
        result = resolve_rules(
            load_json(rules_path, "INVALID_REVIEW_RULES"),
            load_json(payload_path, "INVALID_RULE_INPUT"),
            load_json(evidence_path, "INVALID_RULE_GIT_EVIDENCE"),
        )
        result["rulesetSha256"] = hashlib.sha256(rules_path.read_bytes()).hexdigest()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0
    except (RuleMatchError, UserConfigError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
