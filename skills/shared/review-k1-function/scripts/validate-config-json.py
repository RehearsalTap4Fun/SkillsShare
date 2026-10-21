from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any


from user_config import workspace_root, UserConfigError, check_write_path


class ValidationError(Exception):
    pass


def is_within(path: Path, boundary: Path) -> bool:
    resolved_path = path.resolve(strict=False)
    resolved_boundary = boundary.resolve(strict=False)
    return resolved_path == resolved_boundary or resolved_boundary in resolved_path.parents


def require_string(mapping: dict[str, Any], field: str) -> str:
    value = mapping.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"INVALID_REQUEST:{field}")
    return value


def normalized_field(raw: str) -> str:
    match = re.fullmatch(r"\d+_(.*)", raw)
    return match.group(1) if match else raw


def integer(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def check(identifier: str, label: str, passed: bool, detail: str) -> dict[str, str]:
    return {
        "id": identifier,
        "label": label,
        "status": "通过" if passed else "发现异常",
        "detail": detail,
    }


def validate(request: dict[str, Any]) -> dict[str, Any]:
    input_path = Path(require_string(request, "inputPath"))
    if not is_within(input_path, (workspace_root() / ".tmp")) or not input_path.resolve().is_file():
        raise ValidationError("INPUT_OUTSIDE_TMP")
    target_sha = require_string(request, "targetSha")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", target_sha):
        raise ValidationError("INVALID_TARGET_SHA")

    required_fields = request.get("requiredFields")
    affected = request.get("affectedRequirementIds")
    if (
        not isinstance(required_fields, list)
        or not required_fields
        or any(not isinstance(item, str) or not item for item in required_fields)
        or len(required_fields) != len(set(required_fields))
    ):
        raise ValidationError("INVALID_REQUIRED_FIELDS")
    if not isinstance(affected, list) or any(not isinstance(item, str) or not item for item in affected):
        raise ValidationError("INVALID_AFFECTED_REQUIREMENTS")

    try:
        rows = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError("INVALID_CONFIG_JSON") from error
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("INVALID_CONFIG_JSON_ROOT")

    first_keys = list(rows[0])
    field_keys = {normalized_field(key): key for key in first_keys}
    fields_present = all(field in field_keys for field in required_fields)
    checks = [
        check(
            "required-fields",
            "必需字段",
            fields_present,
            "字段齐全：" + ", ".join(required_fields)
            if fields_present
            else "缺少字段：" + ", ".join(field for field in required_fields if field not in field_keys),
        )
    ]
    if not fields_present:
        return build_result(request, target_sha, 0, checks)

    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if all(str(row.get(field_keys[field], "")) == field for field in required_fields)
        ),
        None,
    )
    if header_index is None:
        checks.append(check("header-row", "字段表头", False, "未找到 K1 字段名表头行"))
        return build_result(request, target_sha, 0, checks)

    primary_key = require_string(request, "primaryKey")
    if primary_key not in field_keys:
        raise ValidationError("PRIMARY_KEY_NOT_FOUND")
    data_rows = [
        row
        for row in rows[header_index + 1 :]
        if integer(row.get(field_keys[primary_key])) is not None
    ]
    primary_values = [integer(row[field_keys[primary_key]]) for row in data_rows]
    primary_passed = bool(data_rows) and len(primary_values) == len(set(primary_values))
    checks.append(
        check(
            "primary-key",
            "主键唯一性",
            primary_passed,
            f"识别 {len(data_rows)} 条数据，主键均唯一"
            if primary_passed
            else "数据行为空或存在重复主键",
        )
    )

    range_spec = request.get("range")
    if not isinstance(range_spec, dict):
        raise ValidationError("INVALID_RANGE_SPEC")
    min_field = require_string(range_spec, "minField")
    max_field = require_string(range_spec, "maxField")
    expected_start = range_spec.get("expectedStart")
    expected_end = range_spec.get("expectedEnd")
    if (
        min_field not in field_keys
        or max_field not in field_keys
        or not isinstance(expected_start, int)
        or isinstance(expected_start, bool)
        or not isinstance(expected_end, int)
        or isinstance(expected_end, bool)
    ):
        raise ValidationError("INVALID_RANGE_SPEC")
    ranges = [
        (integer(row.get(field_keys[min_field])), integer(row.get(field_keys[max_field])))
        for row in data_rows
    ]
    range_values_valid = bool(ranges) and all(
        low is not None and high is not None and low <= high for low, high in ranges
    )
    checks.append(
        check(
            "range-order",
            "区间上下限",
            range_values_valid,
            "所有区间均为整数闭区间且下限不大于上限"
            if range_values_valid
            else "存在非整数区间或下限大于上限",
        )
    )
    sorted_ranges = sorted(ranges) if range_values_valid else []
    continuous = range_values_valid and all(
        current[1] + 1 == following[0]
        for current, following in zip(sorted_ranges, sorted_ranges[1:])
    )
    checks.append(
        check(
            "range-continuity",
            "区间连续性",
            continuous,
            "区间连续且无重叠" if continuous else "区间存在空档或重叠",
        )
    )
    actual_start = sorted_ranges[0][0] if sorted_ranges else None
    actual_end = sorted_ranges[-1][1] if sorted_ranges else None
    boundary_passed = actual_start == expected_start and actual_end == expected_end
    checks.append(
        check(
            "range-boundary",
            "区间端点",
            boundary_passed,
            f"期望覆盖 {expected_start}–{expected_end}，实际覆盖 {actual_start}–{actual_end}",
        )
    )

    weight_spec = request.get("weight")
    if not isinstance(weight_spec, dict):
        raise ValidationError("INVALID_WEIGHT_SPEC")
    weight_field = require_string(weight_spec, "field")
    expected_total = weight_spec.get("expectedTotal")
    if (
        weight_field not in field_keys
        or not isinstance(expected_total, int)
        or isinstance(expected_total, bool)
    ):
        raise ValidationError("INVALID_WEIGHT_SPEC")
    weights = [integer(row.get(field_keys[weight_field])) for row in data_rows]
    positive_weights = bool(weights) and all(value is not None and value > 0 for value in weights)
    checks.append(
        check(
            "weight-values",
            "权重合法性",
            positive_weights,
            "所有权重均为正整数" if positive_weights else "存在非正数或非整数权重",
        )
    )
    weight_total = sum(value for value in weights if value is not None)
    checks.append(
        check(
            "weight-total",
            "权重合计",
            positive_weights and weight_total == expected_total,
            f"权重合计 {weight_total}",
        )
    )
    return build_result(request, target_sha, len(data_rows), checks)


def build_result(
    request: dict[str, Any], target_sha: str, data_row_count: int, checks: list[dict[str, str]]
) -> dict[str, Any]:
    failed = [item for item in checks if item["status"] != "通过"]
    return {
        "id": require_string(request, "id"),
        "sourceType": "generated-json",
        "authority": "次级证据",
        "sourcePath": require_string(request, "sourcePath"),
        "targetSha": target_sha.lower(),
        "workbook": require_string(request, "workbook"),
        "table": require_string(request, "table"),
        "status": "发现异常" if failed else "通过",
        "summary": f"生成 JSON 已解析 {data_row_count} 条数据；{len(failed)} 项检查发现异常",
        "dataRowCount": data_row_count,
        "checks": checks,
        "affectedRequirementIds": request["affectedRequirementIds"],
        "limitation": "生成 JSON 是目标 SHA 的次级证据，不能替代正式 XLSX 的源表、表头和导表完整性验证。",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a frozen K1 generated config JSON")
    parser.add_argument("--request", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        request_path = Path(args.request)
        output_path = Path(args.output)
        check_write_path(output_path)
        if not is_within(request_path, (workspace_root() / ".tmp")) or not request_path.resolve().is_file():
            raise ValidationError("REQUEST_OUTSIDE_TMP")
        if not is_within(output_path, (workspace_root() / ".tmp")) or output_path.exists():
            raise ValidationError("OUTPUT_OUTSIDE_TMP_OR_EXISTS")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValidationError("INVALID_REQUEST")
        result = validate(request)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_name(f".{output_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(output_path)
        finally:
            if temporary.exists():
                temporary.unlink()
        print(json.dumps({"status": "ok", "code": "CONFIG_JSON_VALIDATED"}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, UserConfigError) as error:
        print(str(error), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
