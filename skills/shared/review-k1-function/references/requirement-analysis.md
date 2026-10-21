# Requirement and Issue Contract

## Requirement tree

Decompose the document as `功能 → 子模块 → 需求项 → 可验证条件`. Give every requirement a stable ID and retain it even when the change index contains no matching Diff.

## Evidence matrix

| Field | Required content |
|---|---|
| ID | Stable report identifier |
| module | Owning submodule |
| requirement | Concise document requirement |
| verifiable conditions | Static conditions that code or configuration can prove |
| expected ends | Client, server, and/or config |
| document evidence | Locatable document heading, paragraph, or quotation |
| commit evidence | Repository, commit SHA, file, and commit intent; use `无` when no matching commit exists |
| final-state evidence | Repository, resolved target SHA, file, symbol/line, or formal workbook row |
| unknown conditions and acceptance actions | Every unknown condition, why static evidence cannot decide it, and the pending manual or runtime action; use `无` when none |
| status | One of the four completion statuses below |
| confidence | `高`, `中`, or `低`, with the reason |
| issue IDs | Linked issue identifiers, or `无` |

## Completion status

First record every verifiable condition as `满足`, `已证实缺口`, or `未知`, and mark whether it is core or decisive. List every `未知` condition separately; an unknown condition never hides a proved gap. Then assign exactly one row status with this ordered decision table:

| First matching condition | Row status |
|---|---|
| The core implementation is proved completely absent or unreachable | `未实现` |
| Any gap is proved and some implementation remains | `部分实现` |
| No gap is proved, but at least one decisive condition is unknown | `无法确认` |
| No gap is proved, every core and decisive condition is satisfied, and only non-decisive unknowns remain | `已实现` |
| Every condition is proved satisfied, including cross-end contracts | `已实现` |

`无法确认` is not a defect and does not count as `已实现`. Non-decisive unknowns remain limitations or acceptance notes and do not replace a status supported by proved conditions. Any unknown condition keeps its acceptance action pending. Carry every unknown in the matrix field `unknown conditions and acceptance actions`, and repeat it in report Section 7 with the row status and executable acceptance action, even when the row status is `已实现` or `部分实现`.

## Issue model

Use exactly one of these issue types: `Bug`, `功能遗漏`, `需求不符`, `配置缺失`, `跨端不一致`, `待确认风险`.

- **P0**：核心流程不可用，或存在严重数据、安全风险。
- **P1**：关键需求遗漏、明显逻辑错误、跨端或配置契约断裂。
- **P2**：非核心路径、边界条件、异常恢复或校验不完整。
- **P3**：低影响风险或与当前功能直接相关的可维护性问题。

## Conclusion contract

Every issue conclusion contains these eight fields:

1. Issue type and severity.
2. Affected requirement IDs.
3. Document basis.
4. Code or configuration evidence.
5. Reasoning from evidence to conclusion.
6. Impact.
7. Suggested manual or runtime validation.
8. Confidence and reason.

A missing evidence slot cannot become a confirmed defect. When evidence shows only a suspicious path and cannot prove a requirement violation, use `待确认风险`; when static analysis or the document cannot decide the requirement status, use `无法确认`. A requirement with no Diff remains in the matrix and receives the status supported by final-state evidence.

## Overall verdict

Derive exactly one overall verdict from the completed matrix, confirmed issues, unknowns, and acceptance obligations. Apply the rows in order:

| First matching condition | Overall verdict |
|---|---|
| Any deterministic `P0`/`P1`, or any row is `未实现` | `不通过` |
| No blocker above, but any `P2`/`P3`, `部分实现`, decisive `无法确认`, or any pending unknown acceptance action (manual or runtime) remains | `有条件通过` |
| Every row is `已实现`, with no deterministic issue, no decisive unknown, and no pending acceptance action | `通过` |

The saved report and dialogue summary derive from this same decision result; neither may independently soften or strengthen the verdict.
