"""Verify report citations against frozen Git objects, without network or checkout."""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from user_config import workspace_root, UserConfigError
MAX_BLOB_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
NOTICE = "代码定位校验只核对冻结提交与引用片段；定位通过不代表业务判断成立。"


class AnchorError(Exception):
    pass


def valid_path(path: str) -> bool:
    return (
        bool(path)
        and not any(ord(c) < 32 for c in path)
        and not any(c in path for c in "\\:")
        and all(part not in {"", ".", "..", ".git"} for part in path.split("/"))
    )


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise AnchorError("ANCHOR_TIMEOUT")


def locate(source: str, snippet: str, deadline: float) -> tuple[int, int]:
    check_deadline(deadline)
    # Normalize only line endings. Whitespace, indentation and spelling stay exact.
    lines = source.replace("\r\n", "\n").removesuffix("\n").split("\n")
    needle = snippet.replace("\r\n", "\n").removesuffix("\n").split("\n")
    # KMP over complete lines avoids quadratic slicing on repetitive generated code.
    prefix = [0] * len(needle)
    matched = 0
    for index in range(1, len(needle)):
        if index % 1024 == 0:
            check_deadline(deadline)
        while matched and needle[index] != needle[matched]:
            matched = prefix[matched - 1]
        if needle[index] == needle[matched]:
            matched += 1
        prefix[index] = matched
    matches = []
    matched = 0
    for index, line in enumerate(lines):
        if index % 1024 == 0:
            check_deadline(deadline)
        while matched and line != needle[matched]:
            matched = prefix[matched - 1]
        if line == needle[matched]:
            matched += 1
        if matched == len(needle):
            matches.append(index - len(needle) + 2)
            if len(matches) > 1:
                raise AnchorError("AMBIGUOUS_SNIPPET")
            matched = prefix[matched - 1]
    check_deadline(deadline)
    if not matches:
        raise AnchorError("SNIPPET_NOT_FOUND")
    return matches[0], matches[0] + len(needle) - 1


class FrozenReader:
    def __init__(self, deadline: float):
        self.deadline = deadline
        self.cache: dict[tuple[str, str, str], str | AnchorError] = {}
        self.commits: set[tuple[str, str]] = set()
        self.total_bytes = 0
        # Do not inherit another task's repository, object directory or injected Git config.
        self.env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        self.env.update(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1", GIT_NO_REPLACE_OBJECTS="1")

    def git(self, repo: str, *args: str) -> bytes:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise AnchorError("ANCHOR_TIMEOUT")
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", "--no-lazy-fetch", "--no-replace-objects",
                 "-c", "core.fsmonitor=false", "-c", "gc.auto=0", "-C", repo, *args],
                capture_output=True, env=self.env, timeout=min(5, remaining),
            )
        except subprocess.TimeoutExpired as error:
            raise AnchorError("ANCHOR_TIMEOUT") from error
        except OSError as error:
            raise AnchorError("GIT_UNAVAILABLE") from error
        if result.returncode:
            # Do not copy raw Git stderr, which can contain private paths or URLs.
            raise AnchorError("FILE_UNAVAILABLE")
        return result.stdout

    def read(self, repo: str, target: str, path: str) -> str:
        key = (repo, target, path)
        if key in self.cache:
            cached = self.cache[key]
            if isinstance(cached, AnchorError):
                raise cached
            return cached
        try:
            if (repo, target) not in self.commits:
                if self.git(repo, "cat-file", "-t", target).strip() != b"commit":
                    raise AnchorError("TARGET_NOT_COMMIT")
                self.commits.add((repo, target))
            records = self.git(repo, "ls-tree", "-z", target, "--", ":(literal)" + path).split(b"\x00")
            records = [entry for entry in records if entry]
            if len(records) != 1:
                raise AnchorError("FILE_UNAVAILABLE")
            header, actual_path = records[0].split(b"\t", 1)
            mode, kind, oid = header.split()
            if actual_path.decode("utf-8") != path or mode not in {b"100644", b"100755"} or kind != b"blob":
                raise AnchorError("NOT_REGULAR_FILE")
            blob = oid.decode("ascii")
            size = int(self.git(repo, "cat-file", "-s", blob))
            if size > MAX_BLOB_BYTES or self.total_bytes + size > MAX_TOTAL_BYTES:
                raise AnchorError("SOURCE_TOO_LARGE")
            self.total_bytes += size
            data = self.git(repo, "cat-file", "blob", blob)
            if b"\x00" in data:
                raise AnchorError("BINARY_SOURCE")
            source = data.decode("utf-8-sig")
            if source.startswith("version https://git-lfs.github.com/spec/v1"):
                raise AnchorError("LFS_POINTER")
            self.cache[key] = source
            return source
        except UnicodeError as error:
            failure = AnchorError("UNSUPPORTED_ENCODING")
            self.cache[key] = failure
            raise failure from error
        except AnchorError as error:
            self.cache[key] = error
            raise


def repository_sources(payload: dict[str, Any], manifest: Any) -> tuple[dict, dict]:
    identities = {}
    for item in payload["metadata"]["repositories"]:
        role = item["role"]
        if role in identities or not re.fullmatch(r"[0-9a-fA-F]{40}", item["target"]):
            raise AnchorError("INVALID_ANCHOR_REPOSITORY")
        identities[role] = item
    if manifest is None:
        return identities, {}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("repositories"), list):
        raise AnchorError("INVALID_EVIDENCE_MANIFEST")
    sources = {}
    for item in manifest["repositories"]:
        if not isinstance(item, dict):
            raise AnchorError("INVALID_EVIDENCE_MANIFEST")
        role = item.get("role")
        if not isinstance(role, str) or role not in identities or role in sources:
            raise AnchorError("INVALID_EVIDENCE_MANIFEST")
        identity = identities[role]
        if item.get("targetSha") != identity["target"]:
            raise AnchorError("MANIFEST_TARGET_MISMATCH")
        source = identity.get("source", "local")
        if item.get("source", "local") != source:
            raise AnchorError("MANIFEST_SOURCE_MISMATCH")
        raw_path = item.get("repository")
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise AnchorError("INVALID_EVIDENCE_MANIFEST")
        repo = Path(raw_path).resolve()
        if source == "remote" and not any(
            repo.is_relative_to((workspace_root() / directory).resolve()) for directory in (".cache", ".tmp")
        ):
            raise AnchorError("REMOTE_ANCHOR_OUTSIDE_FUNCTIONPREVIEW")
        sources[role] = str(repo)
    return identities, sources


def verify_report(payload: dict[str, Any], manifest: Any, budget_seconds: float = 30) -> None:
    identities, sources = repository_sources(payload, manifest)
    # The workflow has no global deadline. Each citation gets its own bounded
    # operation window; previously verified objects remain cached across entries.
    reader = FrozenReader(time.monotonic() + budget_seconds)
    failures = []
    sections = [
        ("requirements", payload["requirements"], "result", {"静态证据充分", "存在明显缺口"}),
        ("findings", payload["findings"], "type", {"阻断", "风险"}),
        ("reviewChecks", payload["reviewChecks"], "status", {"已核对"}),
    ]
    alignment_items = [
        {"id": change["id"], "evidence": change["evidence"],
         "result": "无法确认" if change["decision"] == "证据不足" else "静态证据充分"}
        for change in payload.get("alignment", {}).get("changes", [])
    ]
    sections.append(("alignment", alignment_items, "result", {"静态证据充分"}))
    for section, owners, status_field, confirmed_statuses in sections:
        for owner in owners:
            status = owner[status_field]
            owner_id = owner.get("id", owner.get("checkId", "unknown"))
            if not owner["evidence"] and status in confirmed_statuses:
                failures.append(f"{owner_id}:CONFIRMED_ITEM_WITHOUT_EVIDENCE")
            for evidence_index, entry in enumerate(owner["evidence"]):
                reader.deadline = time.monotonic() + budget_seconds
                original = entry["locator"]
                try:
                    identity = identities.get(entry["repository"])
                    if identity is None or entry["ref"] != identity["target"]:
                        raise AnchorError("TARGET_MISMATCH")
                    if not valid_path(entry["path"]):
                        raise AnchorError("INVALID_GIT_PATH")
                    if Path(entry["path"]).suffix.lower() in {".xlsx", ".xls"}:
                        entry["verification"] = "表格证据不在代码定位校验范围；沿用正式表格核对"
                        continue
                    if time.monotonic() >= reader.deadline:
                        raise AnchorError("ANCHOR_TIMEOUT")
                    repo = sources.get(entry["repository"])
                    if repo is None:
                        raise AnchorError("MANIFEST_REQUIRED")
                    snippet = entry.get("snippet")
                    if not snippet:
                        raise AnchorError("SNIPPET_REQUIRED")
                    if len(snippet.encode("utf-8")) > MAX_BLOB_BYTES:
                        raise AnchorError("SNIPPET_TOO_LARGE")
                    start, end = locate(reader.read(repo, entry["ref"], entry["path"]), snippet, reader.deadline)
                    check_deadline(reader.deadline)
                    corrected = f"L{start}" if start == end else f"L{start}-L{end}"
                    entry["locator"] = corrected
                    entry["verification"] = "代码定位已核对" + (f"（原定位 {original} → {corrected}）" if original != corrected else "")
                except AnchorError as error:
                    entry["verification"] = f"定位无法确认：{error}；{entry.get('unverifiedReason', '未提供补证说明')}"
                    if status != "无法确认" or not entry.get("unverifiedReason"):
                        failures.append(f"{section}/{owner_id}/evidence[{evidence_index}]:{error}")
    if failures:
        raise AnchorError("EVIDENCE_ANCHOR_FAILED:" + ";".join(dict.fromkeys(failures)))
