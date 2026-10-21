# K1 静态预审合同（先完成文档对齐）

本合同仅约束静态阶段。用户要求运行验收时，以本次 PrecheckResult、已确认决定和冻结 manifest 交接 `runtime-acceptance.md`；其测试环境写入由独立运行合同约束，不改变本合同的静态只读保证。

## 目录

- 输入与权威
- 远端快照合同
- 只读命令边界
- 固定执行顺序
- 证据边界
- PrecheckResult
- 降级映射
- 缓存与报告

## 1. 输入与权威

完整工作流输入包含功能名、开发文档、三端 target，可选 per-role base/target 覆盖、显式 source 和已有用例。预审必须接收 `requirement-alignment.md` 已完成的 alignment 记录、更新后原文档和同组三端 manifest。

权威顺序：

1. 已经用户确认、维护并回读核验的开发文档定义需求与期望。
2. 本次冻结的 Git target SHA 定义实现。
3. Config target SHA 上的正式 `.xlsx` 定义配置最终状态。
4. 已有或生成用例只补充检查视角，不修改、替代或覆盖开发文档。
5. 生成 JSON/TSV 是次级证据；《配置表结构》等文档仅定位 workbook、表、字段或 ID。

默认输入：

```json
{
  "source": "remote",
  "remote": "origin",
  "baseBranch": "dev",
  "targetBranch": "dev_NK-8604"
}
```

普通分支名必须走 live remote；完整 40 位 SHA 或显式 `source=local` 才允许冻结/离线本地对象模式。普通分支不要求用户切换或保持本地 checkout。

## 2. 时间合同

`deadlineSeconds=null`，需求对齐和静态预审均不限时，不设工作流或阶段总截止。记录实际耗时与进度，按需求覆盖情况补证，不以累计耗时结束审核。用例无数量上限，采集无轮次上限。

保留工具单次超时：远端准备默认 180 秒，LFS 物化默认 120 秒，每条代码定位默认 30 秒，Git 子调用最多 5 秒。超时先核查原因并按需重试同一冻结对象；权限、网络或运行行为形成实际障碍时明确记录未知及可执行补证动作。不限时不意味着无进展的无限重试。

## 3. 远端快照合同

调用 `scripts/prepare-remote-snapshots.ps1`：

```json
{
  "repositories": [
    {
      "role": "client",
      "localRepository": "<repositories.client>",
      "remote": "origin",
      "baseBranch": "dev",
      "targetBranch": "dev_NK-8604"
    }
  ]
}
```

输出 per-role：`source=remote`、用户工作区 缓存 `repository`、`remote=origin`、`baseRef`、`targetRef`、`baseSha`、`targetSha`。

硬规则：

- 远端 URL 只从正式仓库 `remote get-url origin` 读取，不接受用户 URL/refspec。
- 缓存位于 `<workspaceRoot>/.cache/fast-precheck/remote-git/<role>`，无 checkout。
- 三端并行，精确 fetch base/target，使用 `--filter=blob:none --no-tags`。
- 每次完整工作流开始时联系远端并冻结本次 SHA；同一次对齐与预审交接沿用已有 manifest，不刷新 refs。24 小时是分析缓存 TTL，不是已确认冻结证据的有效期。
- 禁止 tags、全分支、submodule、全量 LFS 和 full-clone fallback。
- 远端失败代码只降级该端：`REMOTE_BRANCH_MISSING`、`REMOTE_FETCH_FAILED`、`REMOTE_FETCH_TIMEOUT`、`REMOTE_FILTER_UNSUPPORTED`、`REMOTE_URL_UNSAFE`、`CACHE_LOCK_TIMEOUT`。
- 失败时禁止使用正式仓库中的 `refs/remotes/origin/*`、当前 `HEAD`、当前 checkout 或上次 manifest。

## 4. 只读命令边界

正式 K1 仓库只允许状态快照、`remote get-url origin` 和 object-dir 定位等只读命令。设置 `GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`，SSH 使用 BatchMode。

任何会写 refs/objects/LFS 的命令必须由受测脚本执行，且 Git `-C` 只能指向 用户工作区 缓存：

- `prepare-remote-snapshots.ps1`：精确 filtered fetch。
- `export-git-file.ps1 -MaterializeLfs`：精确 workbook 路径的 LFS fetch/smudge。

正式 K1 仓库禁止 fetch/pull/checkout/switch/reset/clean/stash/add/commit/merge/rebase、任何 LFS、编译、测试、启动、运行验收、导表、生成、安装、子模块更新和 Jenkins。

不得清理、隐藏或恢复用户状态。状态报告持久化时只保留 branch/head、dirty、entryCount、categories 和 digest，不保留无关路径。

## 5. 固定执行顺序

1. 在 用户工作区 `.tmp/<run-id>` 建立本次工作流边界并记录开始时间，不设总截止。
2. 用 `snapshot-repository-state.ps1` 记录正式三端 before digest。
3. 普通分支调用 `prepare-remote-snapshots.ps1`，得到三端 live remote manifest 和冻结 SHA。
4. 以 manifest 缓存路径和完整 SHA 构造 `collect-git-evidence.ps1` 请求；不得重新解析可移动 branch。
5. **先执行 `references/requirement-alignment.md`**：完整文档与三端累计提交/target 最终状态双向比对，向用户展示增删改清单，取得逐项决定，最小维护原文及变更记录并回读。校验 `alignment` 完成记录才进入下一步。
6. 使用回读后的同版文档建立需求矩阵；校准已有用例或用 `$slg-testcase-generator` 补齐 Markdown 用例，无条数上限。记录 `testcaseDocumentVersion`；实现问题和证据不足项必须通过 requirementIds 交接。
7. 用 `baseSha...targetSha` 的 changed paths/symbols 建立索引，沿需求相关调用链检查冻结 target 的完整业务路径，记录扩展理由和已查范围。可继续补证，不限一跳或一轮。
8. 为需求及关联调用命中的正式 workbook 调用 `export-git-file.ps1`。LFS pointer 使用 `-MaterializeLfs -Remote origin -ExpectedLfsPath <exact-path>`，输出到本次 `.tmp`。对物化副本使用 `config-table-editor` 只读解析；不得操作正式 Config 工作区或使用 `xcfg-tool`。
9. 正式 workbook 无法物化时，只对同一 config target SHA 中直接对应的生成 JSON 调用 `validate-config-json.py`；写入 `configurationChecks` 次级证据，不能替代正式 XLSX。
10. 按更新后文档顺序为所有需求赋值，建立用例—需求—证据双向矩阵；逐条给出前置工作流交接问题的核查结论。
11. 调用 `review_rule_matcher.py`，传入 `k1-review-rules.json`、当前结果和 collector 原始冻结输出。按仓库角色、需求已映射路径和需求信号叠加命中规则，为每个 selected checkId 完成一条 reviewChecks 审计记录。
12. 再次状态快照，核对原文档未在交接后漂移，计算只读合同和工作区变化，更新实际耗时。为代码证据保留完整行原文 snippet，以 --evidence-manifest 传入同一冻结 manifest，以 --git-evidence 传入 collector 输出。renderer 校验 alignment、文档/用例版本、规则与代码定位后发布三份报告。源文档漂移时先重新对齐受影响内容，不以旧用例发布新版本结论。

## 6. 需求驱动的证据边界

- 第一层：changed files、changed hunks、commit intent。
- 沿需求相关的 caller/callee、协议、状态和正式配置追踪完整路径，记录每次扩展理由，直到条件可判定或有明确证据缺口。
- 用明确的 symbol/ID/路径定位，禁止无关键词全仓扫描；不设置固定跳数。
- “未出现在 diff”不能单独证明缺失，使用 `证据不完整` 或 `无法确认`。
- 正式 `.xlsx` 只检查直接相关表、字段和 ID，不扫描全部表。
- LFS pointer 只证明对象标识；必须成功物化且 OID/size 校验通过，才能读取 workbook 内容。
- LFS 单次超时可在同一 SHA 上补证；正式表最终不可读时，相关配置需求为 `无法确认`；不得用生成 JSON/旧结构文档变成确定结论。
- 同一 config target SHA 的生成 JSON 可补充检查字段存在性、数据行、主键唯一性、区间连续性/边界和权重合计，但必须标为次级证据，不能替代正式 XLSX。

### 规则选择与强制留痕

机器可读目录是 `references/k1-review-rules.json`，选择器是 `scripts/review_rule_matcher.py`。每条规则可以同时使用仓库角色、路径 glob 和需求文本信号；所有命中的规则都加入结果，属于叠加命中，不使用 first-match-wins。

路径只来自该需求已经映射的 evidence，不从某个仓库的全部 changed paths 回填。这样没有定位证据的纯表现需求不会因为仓库里碰巧修改了 Request 文件而触发请求规则。glob 中的 `**/` 匹配零层或多层目录，必须同时覆盖仓库根目录文件和嵌套文件。角色决定规则是否适用于本次涉及的端，需求信号可以在尚无路径证据时独立触发语义检查。

collector 为 `error` 或 `requires_input` 时仍保留角色和请求的冻结 SHA，用需求信号选择规则，并把涉及失败角色的检查强制留痕为 `无法确认`；不得因为单端采集失败跳过该端应检查的规则，也不得借用其他成功端的证据标为 `已核对` 或 `不适用`。

先生成选择结果，供审查者逐项完成检查：

```powershell
python <skillRoot>/scripts/review_rule_matcher.py `
  --rules <skillRoot>/references/k1-review-rules.json `
  --input <workspaceRoot>/.tmp/<run-id>/result-draft.json `
  --git-evidence <workspaceRoot>/.tmp/<run-id>/git-evidence.json `
  --output <workspaceRoot>/.tmp/<run-id>/selected-review-rules.json
```

每个 selected `checkId` 在 `reviewChecks` 中必须恰好出现一次：

- `已核对`：已按 `evidenceExpectation` 检查，必须提供至少一条 evidence，并通过代码定位或正式表格合同。
- `不适用`：符合 `notApplicableWhen`，必须在 `reason` 说明为何不适用，evidence 必须为空。
- `无法确认`：现有冻结证据不足，必须在 `reason` 写明缺口和补证动作；可以不带 evidence。

`rolesAny`/`rolesAll` 是规则的触发角色和主证据角色；可选 `evidenceRoles` 仅扩展该规则允许引用的辅助仓库。例如配置表头规则可同时引用 Config 正式表和 Client/Server 消费者，但不能缺少 Config 主证据。跨端 `rolesAll` 的 `已核对` 证据必须覆盖列出的每个端。

缺失、重复或额外检查、借用白名单外仓库证据、缺少主角色证据都会阻止新报告发布。规则定义必须检查什么，规则及其状态不得直接决定需求结果、finding 类型或总体结论；这些判断仍由开发文档和已核验证据支持。

## 7. PrecheckResult

```text
metadata:
  feature, documentSource, documentVersion, startedAt, finishedAt,
  elapsedSeconds, deadlineSeconds=null, testcaseDocumentVersion, cache, disclaimer
  repositories[]:
    role, source=remote|local, remote?, baseRef?, targetRef?, base, target
  unavailableRepositories[]?: role, code（preparer.errors 的原样失败记录，无虚构 SHA）
alignment: 已完成的前置对齐记录（schemaVersion, status, comparisonSummary,
  document, repositories, changes；详见 requirement-alignment.md）
requirements[]:
  id, title, documentLocator, expectation, testcaseIds[], evidence[],
  result, explanation, runtimeAction
findings[]:
  id, type, requirementIds[], title, evidence[], impact, action
reviewChecks[]:
  checkId, ruleId, requirementIds[], status=已核对|不适用|无法确认,
  evidence[], reason
configurationChecks[]:
  id, title, authority=次级证据, sourceType=generated-json,
  repository=config, targetSha, sourcePath, workbook, table,
  affectedRequirementIds[], status, summary, limitation, checks[]
testcases[]:
  id, category, requirementIds[], precondition, action, expected, source, note
overall:
  verdict, reason
readonlyCheck:
  sourceMode=remote|local, contractPassed, workspaceChanged,
  beforeDigest, afterDigest
limits[]:
  source, reason, affectedRequirementIds[]
```

`metadata.repositories[].base/target` 是完整冻结 SHA。remote 模式同时保存 `origin` 和原始完整 refs，报告必须展示 source/ref/SHA 以便复现。

`contractPassed` 只表示所有潜在 Git/LFS 写入是否严格落在 用户工作区 自有缓存/临时目录；不得通过 before/after digest 猜测它。`workspaceChanged` 必须等于 before/after digest 是否不同。

结论规则：

- `contractPassed=false` → `预检信息不足`。
- `sourceMode=local` 且 `workspaceChanged=true` → `预检信息不足`。
- `sourceMode=remote` 且 `workspaceChanged=true` → 只披露用户并行工作；保持冻结 SHA 的语义结论。
- 文档不可读或核心需求全部无法确认 → `预检信息不足`。
- 任一 `阻断` → `建议修改后再提测`。
- 有 unavailableRepositories 且没有已证实阻断 → `预检信息不足`；仍展示成功端证据，缺失端的语义规则必须为无法确认。
- 其他情况 → `可进入运行验收`；未知项仍列入运行验收动作。

需求结果仅用 `静态证据充分`、`存在明显缺口`、`证据不完整`、`无法确认`。发现仅用 `阻断`、`风险`、`无法确认`。

### 发布前代码定位校验

此门禁由 `scripts/evidence_anchors.py` 实现，并在 renderer 发布任何目录前执行。没有通过位置校验的代码不能支持确定性结论。定位通过不代表业务判断成立；仍需按文档与完整需求上下文判断。

每条 evidence 保留原有五个必需字段 `repository/ref/path/locator/summary`，增加两个可选字段：

- `snippet`：代码/文本证据必填，复制目标文件中连续的完整行，保留缩进和空白。允许 LF/CRLF 差异和末尾一个换行，不允许省略号、改写或模糊匹配。
- `unverifiedReason`：无法定位时的具体原因和补证动作；只有所属 requirement.result 或 finding.type 为 `无法确认` 时才可用于降级发布。

`repository` 必须与 `metadata.repositories[].role` 一致，`ref` 必须等于该角色完整冻结 `target` SHA。新输入不得填写 `verification`，此状态由程序生成。例：

```json
{
  "repository": "server",
  "ref": "<本次完整40位target SHA>",
  "path": "src/reward.ts",
  "locator": "L120-L122",
  "summary": "领取状态检查",
  "snippet": "  if (claimed) {\n    return;\n  }"
}
```

远端模式直接使用 `prepare-remote-snapshots.ps1` 的本次输出；本地冻结模式构造同形 manifest，每项包含 `role/source=local/repository/targetSha`。manifest 中的 targetSha 必须与报告 target 完全一致，remote 路径必须位于 用户工作区 的 `.cache` 或 `.tmp`。不读取或重新解析移动 ref。

```powershell
python <skillRoot>/scripts/render-fast-precheck.py `
  --input <workspaceRoot>/.tmp/<run-id>/result.json `
  --evidence-manifest <workspaceRoot>/.tmp/<run-id>/remote-manifest.json `
  --git-evidence <workspaceRoot>/.tmp/<run-id>/git-evidence.json `
  --reports-root <workspaceRoot>/reports
```

校验行为：

| 情况 | 处理 |
|---|---|
| 片段唯一匹配 | 输出实际行号，保留原定位及修正说明 |
| `SNIPPET_NOT_FOUND` | 片段不存在，不采用 Agent 猜测位置 |
| `AMBIGUOUS_SNIPPET` | 多处匹配，即使原行号命中其中一处也要求补充上下文 |
| `FILE_UNAVAILABLE` / `LFS_POINTER` | 对象缺失或仅有指针，不回退工作区或自动联网 |
| `MANIFEST_REQUIRED` / `SNIPPET_REQUIRED` | 缺少对象来源或原文，不能绕过门禁 |
| `TARGET_MISMATCH` | 证据角色或 SHA 与本次报告不一致 |
| `ANCHOR_TIMEOUT` / `SOURCE_TOO_LARGE` | 超时或超过读取限额，保留为无法确认 |
| `.xlsx` / `.xls` | 显示不在代码定位校验范围，继续按正式表格合同检查 |

失败处理：优先补齐正确原文/来源；不能补齐时，将每个持有失败证据的需求、发现或 `已核对` 规则显式改为 `无法确认`，为 evidence 提供 `unverifiedReason`，并在检查 `reason` 中写明补证动作，同步更新结论和解释。程序不擅自改写业务结论。若仍以 `静态证据充分`、`存在明显缺口`、`阻断`、`风险` 或 `已核对` 等状态发布失败证据，renderer 返回退出码 4，且不创建报告目录；也不得删除全部证据来维持确定性条目。

每条引用独立默认 30 秒，与 metadata.elapsedSeconds 无关；调用前更新实际耗时。单次 Git 调用最多 5 秒、单文件最多 2 MiB、一次校验累计最多 32 MiB，引用共用冻结对象读取缓存。读取限额是工具保护，不代表该需求可以跳过；可按需补证，真实无法核实时说明原因。

只从 Git commit/tree/blob 读取，不读工作区、不采用 replace refs、不触发 lazy fetch。所需 blob 应在需求证据采集阶段取得；缺失时可在既有边界内使用 `export-git-file.ps1` 准备，定位校验自身不新增 fetch 权限。建议行号和程序生成的验证标识不能当作问题成立的依据。

旧 HTML/Markdown 无需迁移并保持可读；从旧 JSON 再次生成报告属于新发布，必须补充相应字段与来源，或明确降级，不提供跳过校验的兼容开关。

## 8. 测试用例

前置对齐完成后，逐条校准已有用例的前置条件、操作和期望，保留来源及稳定 ID；新增需求补用例、修改需求更新期望、已确认删除需求移出当前用例集合并保留变更记录。缺少覆盖时生成主流程、关键边界和明显异常 Markdown 用例，无时限或数量上限，只使用更新后文档支持的期望。记录 testcaseDocumentVersion 与回读后的文档版本相同；不能仅改版本号而沿用旧期望。

用例是证据链一部分：requirement 通过 `testcaseIds` 关联用例，用例通过 `requirementIds` 反向关联需求。用例不能代替开发文档，也不能把运行时表现改写成静态确定性结论。

## 9. 降级映射

| 情况 | 处理 |
|---|---|
| 远端 target 不存在 | 受影响端/需求 `无法确认`；不替换当前分支或 tracking ref |
| 远端网络、认证、filter 失败 | 受影响端 `无法确认`；不做 local fallback |
| 单端超时 | 仅该端相关需求 `无法确认`；成功端继续 |
| LFS/workbook 无法物化 | 相关配置需求 `无法确认`；同一 target SHA 的生成 JSON 可写入 `configurationChecks` 作次级证据，但不得替代正式 XLSX |
| 文档写入/回读失败或用户尚未确认 | 前置工作流未完成，保留差异与进度，不生成后续用例或发布预审报告 |
| 文档或审核对象改变 | 重新对齐受影响内容；新提交必须开始新对齐记录 |
| 开发文档不可读 | `预检信息不足` |
| remote 模式 workspace digest 不同 | 披露用户并行工作，不改变冻结 SHA 结论 |
| local 模式 workspace digest 不同 | `预检信息不足`，不恢复仓库 |
| 工作流只读合同失败 | `预检信息不足` |
| 报告失败 | 保留并披露本次 `.partial`，旧报告不变 |

## 10. 缓存与报告

分析缓存只位于 `<workspaceRoot>/.cache/fast-precheck`，TTL 24 小时；键包含合同版本 `alignment-v1-unlimited`、回读后的文档版本、base/target SHA 和相关配置 blob SHA。旧快速模式缓存失效；分析缓存不得替代本次对齐与用户确认记录。不得缓存凭证或完整在线文档正文。

远端 Git 对象缓存可复用本地已有 objects 和上次增量对象，但每次完整工作流开始时仍实时 fetch 指定 refs；同一次对齐和预审不刷新移动 refs。代码 blob 按需求相关调用链按需获取；LFS 只获取直接命中 workbook。

`render-fast-precheck.py` 只在 `reports` 创建唯一新目录，包含 `summary.html`、`review.md`、`testcases.md`。先写唯一 `.partial`，校验后一次重命名，不覆盖旧目录。HTML 是默认阅读入口，必须离线、安全转义、可筛选和打印。

发布的 HTML 和 review.md 展示前置对齐摘要、文档前后版本、差异决定与实际回写状态。旧 HTML/Markdown 保持可读；旧 JSON 重发必须补齐 alignment、testcaseDocumentVersion 并设置 deadlineSeconds=null，不提供跳过前置流程的兼容开关。
