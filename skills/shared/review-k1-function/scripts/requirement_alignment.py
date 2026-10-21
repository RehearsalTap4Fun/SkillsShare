"""Validate the handoff from document alignment to K1 static precheck.

This checks recorded decisions, not the authenticity of user messages or remote
document writes. The workflow must obtain those and read back the source first.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


class AlignmentError(Exception):
    pass


def text_field(value: dict, field: str, code: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise AlignmentError(code)
    return result


def snapshot_identities(repositories: Any) -> dict:
    if not isinstance(repositories, list):
        raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
    identities = {}
    for repo in repositories:
        if not isinstance(repo, dict):
            raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        role = text_field(repo, "role", "ALIGNMENT_SNAPSHOT_MISMATCH")
        if role in identities or role not in {"client", "server", "config"}:
            raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        if repo.get("source", "local") not in ("local", "remote"):
            raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        for field in ("base", "target"):
            if not re.fullmatch(r"[0-9a-fA-F]{40}", text_field(repo, field, "ALIGNMENT_SNAPSHOT_MISMATCH")):
                raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        if repo.get("source", "local") == "remote":
            if repo.get("remote") != "origin" or any(
                not text_field(repo, field, "ALIGNMENT_SNAPSHOT_MISMATCH").startswith("refs/remotes/origin/")
                for field in ("baseRef", "targetRef")
            ):
                raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        identities[role] = (
            repo.get("source", "local"), repo.get("remote"),
            repo.get("baseRef"), repo.get("targetRef"),
            text_field(repo, "base", "ALIGNMENT_SNAPSHOT_MISMATCH"),
            text_field(repo, "target", "ALIGNMENT_SNAPSHOT_MISMATCH"),
        )
    return identities


def unavailable_identities(value: Any) -> dict[str, str]:
    if not isinstance(value, list):
        raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
    identities = {}
    for item in value:
        if not isinstance(item, dict) or set(item) != {"role", "code"}:
            raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        role = text_field(item, "role", "ALIGNMENT_SNAPSHOT_MISMATCH")
        code = text_field(item, "code", "ALIGNMENT_SNAPSHOT_MISMATCH")
        if role not in {"client", "server", "config"} or role in identities or not re.fullmatch(r"[A-Z][A-Z0-9_]*", code):
            raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
        identities[role] = code
    return identities


def validate_failure_manifest(metadata: dict, manifest: Any) -> None:
    """Failure receipts must come from the original preparer, never invented SHAs."""
    failed = unavailable_identities(metadata.get("unavailableRepositories", []))
    if manifest is None and not failed:
        return
    if not isinstance(manifest, dict):
        raise AlignmentError("ALIGNMENT_FAILURE_MANIFEST_MISMATCH")
    if failed != unavailable_identities(manifest.get("errors", [])):
        raise AlignmentError("ALIGNMENT_FAILURE_MANIFEST_MISMATCH")


def validate_alignment(alignment: Any, metadata: dict, requirements: list | None = None) -> None:
    if not isinstance(alignment, dict):
        raise AlignmentError("ALIGNMENT_REQUIRED")
    if type(alignment.get("schemaVersion")) is not int or alignment["schemaVersion"] != 1:
        raise AlignmentError("ALIGNMENT_SCHEMA_INVALID")
    if alignment.get("status") != "completed":
        raise AlignmentError("ALIGNMENT_NOT_COMPLETED")
    text_field(alignment, "comparisonSummary", "ALIGNMENT_COVERAGE_REQUIRED")
    identities = snapshot_identities(alignment.get("repositories"))
    if identities != snapshot_identities(metadata.get("repositories")):
        raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
    failed = unavailable_identities(alignment.get("unavailableRepositories", []))
    if failed != unavailable_identities(metadata.get("unavailableRepositories", [])):
        raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
    if set(failed) & set(identities) or set(failed) | set(identities) != {"client", "server", "config"}:
        raise AlignmentError("ALIGNMENT_SNAPSHOT_MISMATCH")
    document = alignment.get("document")
    if not isinstance(document, dict):
        raise AlignmentError("ALIGNMENT_DOCUMENT_REQUIRED")
    for field in ("source", "beforeVersion", "afterVersion"):
        text_field(document, field, "ALIGNMENT_DOCUMENT_REQUIRED")
    if document["source"] != metadata.get("documentSource") or document["afterVersion"] != metadata.get("documentVersion"):
        raise AlignmentError("ALIGNMENT_DOCUMENT_MISMATCH")
    if document.get("readBackVerified") is not True:
        raise AlignmentError("ALIGNMENT_READBACK_REQUIRED")
    if "testcaseDocumentVersion" in metadata and metadata["testcaseDocumentVersion"] != document["afterVersion"]:
        raise AlignmentError("ALIGNMENT_TESTCASE_VERSION_MISMATCH")
    changes = alignment.get("changes")
    if not isinstance(changes, list):
        raise AlignmentError("ALIGNMENT_CHANGES_REQUIRED")
    requirement_ids = None if requirements is None else {item["id"] for item in requirements}
    seen = set()
    applied_count = 0
    for change in changes:
        if not isinstance(change, dict):
            raise AlignmentError("ALIGNMENT_CHANGE_INVALID")
        identity = text_field(change, "id", "ALIGNMENT_CHANGE_INVALID")
        if identity in seen:
            raise AlignmentError("ALIGNMENT_DUPLICATE_ID")
        seen.add(identity)
        for field in ("documentLocator", "original", "implementation", "proposed", "followup"):
            text_field(change, field, "ALIGNMENT_CHANGE_INVALID")
        if change.get("changeType") not in ("新增", "删除", "修改", "待判定"):
            raise AlignmentError("ALIGNMENT_CHANGE_INVALID")
        decision = change.get("decision")
        if decision not in ("需求已调整", "实现有问题", "证据不足"):
            raise AlignmentError("ALIGNMENT_DECISION_REQUIRED")
        text_field(change, "confirmation", "ALIGNMENT_CONFIRMATION_REQUIRED")
        applied = change.get("applied")
        if not isinstance(applied, bool):
            raise AlignmentError("ALIGNMENT_WRITE_INCOMPLETE")
        if decision == "需求已调整":
            if not applied or change["changeType"] == "待判定":
                raise AlignmentError("ALIGNMENT_WRITE_INCOMPLETE")
            applied_count += 1
        elif applied:
            raise AlignmentError("ALIGNMENT_UNAPPROVED_WRITE")
        refs = change.get("requirementIds")
        if not isinstance(refs, list) or any(not isinstance(x, str) or not x.strip() for x in refs) or len(refs) != len(set(refs)):
            raise AlignmentError("ALIGNMENT_HANDOFF_MISSING")
        if not refs and not (decision == "需求已调整" and change["changeType"] == "删除"):
            raise AlignmentError("ALIGNMENT_HANDOFF_MISSING")
        if requirement_ids is not None and not set(refs).issubset(requirement_ids):
            raise AlignmentError("ALIGNMENT_HANDOFF_MISSING")
        evidence = change.get("evidence")
        if not isinstance(evidence, list) or (decision != "证据不足" and not evidence):
            raise AlignmentError("ALIGNMENT_EVIDENCE_REQUIRED")
        for entry in evidence:
            if not isinstance(entry, dict):
                raise AlignmentError("ALIGNMENT_EVIDENCE_REQUIRED")
            for field in ("repository", "ref", "path", "locator", "summary"):
                text_field(entry, field, "ALIGNMENT_EVIDENCE_REQUIRED")
            role = entry["repository"]
            if role not in identities or entry["ref"] != identities[role][-1]:
                raise AlignmentError("ALIGNMENT_EVIDENCE_SNAPSHOT_MISMATCH")
    if applied_count:
        if document.get("writeStatus") != "updated" or document["beforeVersion"] == document["afterVersion"]:
            raise AlignmentError("ALIGNMENT_WRITE_INCOMPLETE")
        text_field(document, "changeLogLocator", "ALIGNMENT_CHANGELOG_REQUIRED")
    elif document.get("writeStatus") != "unchanged" or document["beforeVersion"] != document["afterVersion"]:
        raise AlignmentError("ALIGNMENT_UNAPPROVED_WRITE")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate completed K1 document alignment before testcase generation")
    parser.add_argument("--input", required=True, help="JSON containing alignment and metadata; optionally requirements")
    parser.add_argument("--evidence-manifest", help="Original preparer manifest; required when a role has no frozen SHA")
    args = parser.parse_args()
    try:
        value = json.loads(Path(args.input).read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict) or not isinstance(value.get("metadata"), dict):
            raise AlignmentError("ALIGNMENT_INPUT_INVALID")
        validate_alignment(value.get("alignment"), value["metadata"], value.get("requirements"))
        manifest = None
        if args.evidence_manifest:
            manifest = json.loads(Path(args.evidence_manifest).read_text(encoding="utf-8-sig"))
        validate_failure_manifest(value["metadata"], manifest)
        print(json.dumps({"status": "ok", "code": "ALIGNMENT_COMPLETED"}))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, AlignmentError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
