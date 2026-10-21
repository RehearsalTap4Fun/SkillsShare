from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


from user_config import workspace_root, UserConfigError, check_write_path
ALLOWED_KINDS = {"requirements", "testcases", "diff-index", "config-query"}
KEY_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SENSITIVE_FIELDS = {
    "accesstoken",
    "refreshtoken",
    "token",
    "password",
    "secret",
    "credential",
    "credentials",
    "authorization",
    "cookie",
    "cookies",
    "documentbody",
    "fulldocument",
    "rawdocument",
}


class ContractError(Exception):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def is_within(path: Path, boundary: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_boundary = boundary.resolve(strict=False)
    return resolved_path == resolved_boundary or resolved_boundary in resolved_path.parents


def require_project_path(path: Path, code: str, *, must_exist: bool = False) -> Path:
    if not is_within(path, workspace_root()):
        raise ContractError(code)
    resolved = path.resolve(strict=False)
    if must_exist and not resolved.is_file():
        raise ContractError(f"{code}_NOT_FOUND")
    return resolved


def require_cache_root(path: Path) -> Path:
    if not is_within(path, (workspace_root() / ".cache" / "fast-precheck")):
        raise ContractError("CACHE_ROOT_OUTSIDE_FUNCTIONPREVIEW")
    check_write_path(path)
    path.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve(strict=True)
    if not is_within(resolved, (workspace_root() / ".cache" / "fast-precheck")):
        raise ContractError("CACHE_ROOT_OUTSIDE_FUNCTIONPREVIEW")
    return resolved


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError("INVALID_JSON") from error


def find_sensitive_field(value: Any) -> str | None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in SENSITIVE_FIELDS:
                return str(key)
            found = find_sensitive_field(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_sensitive_field(child)
            if found is not None:
                return found
    return None


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("INVALID_NOW") from error
    if parsed.tzinfo is None:
        raise ContractError("INVALID_NOW")
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_kind_and_key(kind: str, key: str) -> None:
    if kind not in ALLOWED_KINDS:
        raise ContractError("INVALID_CACHE_KIND")
    if KEY_PATTERN.fullmatch(key) is None:
        raise ContractError("INVALID_CACHE_KEY")


def print_receipt(code: str, **extra: Any) -> None:
    receipt = {"status": "ok", "code": code, **extra}
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


def command_key(args: argparse.Namespace) -> int:
    input_path = require_project_path(
        Path(args.input), "KEY_INPUT_OUTSIDE_FUNCTIONPREVIEW", must_exist=True
    )
    value = load_json(input_path)
    if not isinstance(value, dict):
        raise ContractError("INVALID_KEY_PAYLOAD")
    sensitive = find_sensitive_field(value)
    if sensitive is not None:
        raise ContractError("SENSITIVE_CACHE_FIELD")
    print(sha256_bytes(canonical_bytes(value)))
    return 0


def entry_path(root: Path, kind: str, key: str) -> Path:
    directory = root / kind
    directory.mkdir(parents=True, exist_ok=True)
    resolved = directory.resolve(strict=True)
    if not is_within(resolved, root):
        raise ContractError("CACHE_KIND_OUTSIDE_ROOT")
    return resolved / f"{key}.json"


def write_immutable(path: Path, value: Any) -> bool:
    temporary = path.parent / f".{path.stem}-{uuid.uuid4().hex}.tmp"
    data = canonical_bytes(value) + b"\n"
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def command_put(args: argparse.Namespace) -> int:
    validate_kind_and_key(args.kind, args.key)
    root = require_cache_root(Path(args.root))
    input_path = require_project_path(
        Path(args.input), "CACHE_INPUT_OUTSIDE_FUNCTIONPREVIEW", must_exist=True
    )
    payload = load_json(input_path)
    sensitive = find_sensitive_field(payload)
    if sensitive is not None:
        raise ContractError("SENSITIVE_CACHE_FIELD")
    now = parse_now(args.now)
    payload_hash = sha256_bytes(canonical_bytes(payload))
    value = {
        "schemaVersion": 1,
        "kind": args.kind,
        "key": args.key,
        "createdAt": format_time(now),
        "expiresAt": format_time(now + timedelta(hours=24)),
        "payloadSha256": payload_hash,
        "payload": payload,
    }
    path = entry_path(root, args.kind, args.key)
    stored = write_immutable(path, value)
    print_receipt("CACHE_STORED" if stored else "CACHE_EXISTS", key=args.key)
    return 0


def command_get(args: argparse.Namespace) -> int:
    validate_kind_and_key(args.kind, args.key)
    root = require_cache_root(Path(args.root))
    output = require_project_path(
        Path(args.output), "CACHE_OUTPUT_OUTSIDE_FUNCTIONPREVIEW"
    )
    if output.exists():
        raise ContractError("OUTPUT_ALREADY_EXISTS")
    if not output.parent.is_dir():
        raise ContractError("OUTPUT_PARENT_MISSING")
    path = entry_path(root, args.kind, args.key)
    if not path.is_file():
        print_receipt("CACHE_MISS", key=args.key)
        return 2
    try:
        value = load_json(path)
        if not isinstance(value, dict):
            raise ContractError("INVALID_CACHE_ENTRY")
        required = {
            "schemaVersion",
            "kind",
            "key",
            "createdAt",
            "expiresAt",
            "payloadSha256",
            "payload",
        }
        if set(value) != required:
            raise ContractError("INVALID_CACHE_ENTRY")
        if (
            value["schemaVersion"] != 1
            or value["kind"] != args.kind
            or value["key"] != args.key
        ):
            raise ContractError("INVALID_CACHE_ENTRY")
        payload_bytes = canonical_bytes(value["payload"])
        if sha256_bytes(payload_bytes) != value["payloadSha256"]:
            raise ContractError("INVALID_CACHE_ENTRY")
        expires = parse_now(value["expiresAt"])
    except ContractError:
        print_receipt("CACHE_INVALID", key=args.key)
        return 3
    if parse_now(args.now) >= expires:
        print_receipt("CACHE_MISS", key=args.key, reason="expired")
        return 2
    try:
        with output.open("xb") as stream:
            stream.write(payload_bytes + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise ContractError("OUTPUT_ALREADY_EXISTS") from error
    print_receipt("CACHE_HIT", key=args.key, expiresAt=value["expiresAt"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="K1 fast precheck cache")
    commands = parser.add_subparsers(dest="command", required=True)

    key_parser = commands.add_parser("key")
    key_parser.add_argument("--input", required=True)
    key_parser.set_defaults(handler=command_key)

    for name, handler in (("get", command_get), ("put", command_put)):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True)
        command.add_argument("--kind", required=True)
        command.add_argument("--key", required=True)
        command.add_argument("--now")
        if name == "get":
            command.add_argument("--output", required=True)
        else:
            command.add_argument("--input", required=True)
        command.set_defaults(handler=handler)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except (ContractError, UserConfigError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
