# <功能名>代码逻辑完成度审核

完成报告时删除占位提示。任何没有实际条目的章节只写 `未发现`；只记录证据支持的结论。

## 1. 审核元数据

| 字段 | 值 |
|---|---|
| 功能名称 | <功能名> |
| 文档来源 | <URL 或绝对路径；定位信息> |
| 审核时间 | <ISO 8601> |
| Client | <仓库路径；base ref/SHA 或 frozen SHA；target ref/SHA 或 frozen SHA；frozen 时注明 immutable snapshot、无 branch tracking 判断及原 requested ref（若有）；merge-base；dirty bool/数量/状态类别，不含无关路径名> |
| Server | <仓库路径；base ref/SHA 或 frozen SHA；target ref/SHA 或 frozen SHA；frozen 时注明 immutable snapshot、无 branch tracking 判断及原 requested ref（若有）；merge-base；dirty bool/数量/状态类别，不含无关路径名> |
| Config | <仓库路径；base ref/SHA 或 frozen SHA；target ref/SHA 或 frozen SHA；frozen 时注明 immutable snapshot、无 branch tracking 判断及原 requested ref（若有）；merge-base；dirty bool/数量/状态类别，不含无关路径名> |
| 用户裁决 | <无独立改动等裁决、对应仓库、原请求缺失 target、绑定的 base ref/SHA，以及 collector 对当前 local/origin 均缺失的验证结果；无则写“未发现”。不得声称 collector 证明了用户确认的来源> |
| 未读取成功的证据 | <证据与原因；无则写“未发现”> |

## 2. 执行摘要

- 整体结论：<通过/有条件通过/不通过；仅按固定决策表派生>
- 需求状态：已实现 <n>；部分实现 <n>；未实现 <n>；无法确认 <n>
- 问题级别：P0 <n>；P1 <n>；P2 <n>；P3 <n>
- P0/P1：<问题 ID；无则写“未发现”>
- 无法确认：<需求 ID；无则写“未发现”>

固定决策表：任一确定性 P0/P1 或任一 `未实现` → `不通过`；否则，存在 P2/P3、`部分实现`、决定性 `无法确认` 或任一尚未完成的 unknown acceptance action（人工或运行时）→ `有条件通过`；仅在无任何 pending acceptance action 时才可 → `通过`。报告和对话摘要必须从同一次决策结果派生。

## 3. 需求项证据矩阵

| ID | module | requirement | verifiable conditions | expected ends | document evidence | commit evidence | final-state evidence | unknown conditions and acceptance actions | status | confidence | issue IDs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| <REQ-ID> | <子模块> | <需求> | <条件> | <Client/Server/Config> | <文档定位> | <仓库、SHA、文件、意图；无则写“无”> | <仓库、target SHA、文件/符号/行或正式工作簿行> | <每个未知条件、静态证据为何不能判定、待执行验收动作；无则写“无”> | <已实现/部分实现/未实现/无法确认> | <高/中/低及原因> | <ISSUE-ID；无则写“无”> |

## 4. 问题详情

### <ISSUE-ID> <标题>

| 必填字段 | 内容 |
|---|---|
| 问题类型和严重级别 | <Bug/功能遗漏/需求不符/配置缺失/跨端不一致/待确认风险；P0/P1/P2/P3> |
| 受影响的需求项 | <REQ-ID> |
| 开发文档依据 | <标题、段落或可定位引用> |
| 代码或配置证据 | <仓库、target SHA、文件/符号/行或正式工作簿行> |
| 从证据到结论的推理 | <证据如何证明结论> |
| 影响范围 | <用户、状态、数据或链路> |
| 建议的人工或运行时验证方式 | <可执行验收步骤> |
| 置信度 | <高/中/低及原因> |

无实际问题时，以 `未发现` 替换本节全部占位内容。

## 5. 跨端一致性检查

| 检查项 | 涉及需求 | Client 证据 | Server 证据 | Config 证据 | 结论 |
|---|---|---|---|---|---|
| <协议/枚举/ID/默认值/归属/闭环/时间/缺端> | <REQ-ID> | <定位或不适用原因> | <定位或不适用原因> | <定位或不适用原因> | <一致/不一致/无法确认> |

无实际条目时写 `未发现`。

## 6. 未覆盖变更

| 仓库 | commit/文件 | 变更摘要 | 未映射原因 | 建议 |
|---|---|---|---|---|
| <Client/Server/Config> | <SHA；路径> | <摘要> | <为何不对应矩阵需求> | <纳入需求/确认范围/忽略及依据> |

无实际条目时写 `未发现`。

## 7. 无法确认项

Section 7 lists every unknown condition from every matrix row, including `已实现` and `部分实现` rows. 本节名称保持固定，但收载范围不是只有状态为 `无法确认` 的行。

| 需求 ID | 行状态 | 未知条件或文档歧义 | 当前可见证据 | 影响 | 待执行验收动作 |
|---|---|---|---|---|---|
| <REQ-ID> | <已实现/部分实现/未实现/无法确认> | <未知条件> | <定位> | <未知可能影响的结论> | <人工或运行时方法；完成前保持待定> |

无实际条目时写 `未发现`。

## 8. 建议验收清单

| 优先级 | 关联需求/问题 | 验收动作 | 预期结果 |
|---|---|---|---|
| <P0/P1/P2/P3/常规> | <REQ-ID/ISSUE-ID> | <步骤> | <结果> |

无实际条目时写 `未发现`。

## 9. 方法与静态分析限制

- 方法：<文档拆解、提交意图、Diff 索引、最终状态、正式配置与跨端复核>
- 未执行：运行时 UI、接口、性能、兼容性、构建、测试或部署验收。
- 限制：<静态分析不能证明的行为、不可读证据或其他边界；无则写“未发现”>
- 用户裁决限制：<无独立改动等裁决及其证据边界；无则写“未发现”>
- 临时证据清理：<已清理；或失败路径与精确错误。失败时不得写“已清理”>
