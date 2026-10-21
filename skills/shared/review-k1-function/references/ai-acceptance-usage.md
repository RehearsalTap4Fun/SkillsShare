# AI 验收工作流使用说明

适用于 K1 验收。六项能力由同一 Python 模块、持久化台账和本机网页衔接。外部 GM、浏览器上传、部署继续由当前 AI 会话在既有授权内执行，页面生成动作单。代码测试、样例集成、真实联调和业务验收分别显示。

## 团队怎么用

1. 提供已确认的开发文档、待验反馈表及全部原图；指定文档版本、三端完整 SHA、服务器和账号。原图必须逐项查看并记录对应对象、错误码和预期。
2. 建立批次，归档证据，按影响分析生成新一轮断言。未知依赖必须补分析或重跑，旧证据候选不会自动通过。
3. 选择 GM 前置，核对当前身份、预算和副作用；先保存派发记录，再执行工具，再回读业务状态。未知结果先核对，不能重复发资源或部署。
4. 用正常业务入口录制并回放。回放按当前场景重新定位控件和等待状态，不能重发录下来的协议包。不同错误码分别保留，缺请求关联号时显示证据缺口。
5. 生成反馈增量补丁：图片追加到问题截图；只保留一个验收状态；必要说明合并到验收详情；保留开发状态和完整下拉验证。在线逐图打开并导出核对后才算同步完成。
6. 通过原有 runtime 的覆盖、证据、待办和清理检查后才能发布本轮业务报告。存在环境缺口时说明补验条件，不能把工具测试成功写成业务通过。

## 启动与恢复

在 `<workspaceRoot>` 执行，运行环境需要 Python 3.10+、openpyxl；测试使用 pytest。DWS、Unity MCP 和浏览器由当前会话提供，不把登录凭证放进请求 JSON。

```powershell
python -X utf8 <skillRoot>/scripts/acceptance_workflow.py serve --root reports/ai-acceptance-integration-20261019/workbench --host 127.0.0.1 --port 53810
```

输出本机 URL。前台按 Ctrl+C 关闭；使用相同 root 重新启动即可恢复任务，令牌每次启动重新生成。端口占用时改为 `--port 0`，使用新输出 URL。没有定时器、自动 Git hook 或无人值守部署。

通用 CLI：

```powershell
python -X utf8 <skillRoot>/scripts/acceptance_workflow.py <领域> <动作> --request request.json --run-dir <目录>
```

| 领域 | 动作 | 主要输入 / 输出 |
|---|---|---|
| run / evidence | init、show / add、review、verify | 版本身份；文件来源、类别、断言；输出哈希、查看记录 |
| operation | prepare、dispatch、receipt、next | 动作目标与输入；输出唯一 operationId 和恢复状态，禁止手工设 verified |
| feedback | snapshot、plan、apply、reconcile | DWS executable、真实 nodeId/sheetId、fieldMap、身份 sidecar；最小补丁与回读差异 |
| fixture / unity | list、plan、verify、cleanup-plan / build、receipt | 版本绑定定义、参数、当前快照、授权范围；输出固定 MCP 动作与后置核对 |
| recording / replay | start、import、finish、report / plan、next、receipt | 白名单事件、关键帧、注销证据；语义步骤、当前观察、等待条件 |
| impact / runtime | analyze / create、next、reuse、publish | 新旧基线与映射；完整新批次、沿用理由与现有 runtime 收尾门槛 |
| scenario | list、generate、prepare、verify、deploy | 七类条件、XLSX 副本、授权环境；部署回执与实际加载核对 |
| task | register、show、trigger、command | 工作台目录、完整批次、显式 eventId；重启可恢复，同事件重复不新建 |

`task` 的 `--run-dir` 是注册表根目录；其他多数命令是单个 RunStore 目录。`runtime` 使用独立 runtime 目录，不能覆盖 RunStore。样例合同在 `review-k1-function/assets/acceptance/`，测试中的虚构账号不能直接用于生产。

工作台命令白名单：next、feedback-plan、fixture-plan、replay-plan、impact-analyze、scenario-prepare、publish。外部动作显示 actionId 和待执行状态；同请求不重复建立动作。网页不接受任意本地文件路径、脚本、凭证或 shell 命令。文件由 CLI 显式归档后按 evidenceId 查看。

## 反馈同步的浏览器导出回退

DWS 导出失败后不自动重试。可用钉钉官方菜单「表格 → 下载为 → Excel」下载，再在 snapshot/apply/reconcile 请求中显式提供：

```json
{"exportReceipt":{"source":"browser_download","nodeId":"实际节点","path":"下载的绝对路径.xlsx","sha256":"文件 SHA256","exportedAt":"实际导出时间，带时区"}}
```

收据仅十分钟有效，核对节点和文件哈希；每次写后应重新下载，不能沿用写前文件。DWS 读取单元格和原始下拉对象，图片由 OOXML 关系提取；未知图片存储会阻止完整核验。追加仍使用浏览器单元格多图面板，禁止以替换接口代替追加。

导出文件和实时单元格可能处于不同版本。浏览器导出回退只生成 `verify_identity_and_set_cell`，由当前会话写前打开原图、核对目标行再写；不会自动按导出的物理行写 DWS。同名问题自动写入一律报冲突。原生 DWS 路径每格写前重新读取和定位。回读成功不能追认未经核对的写前身份。

影响分析接入新 runtime 批次时，先对完整 runtime baseline 取 canonical SHA256，放入分析输入 `new.runtimeBaselineSha256`；分析结果携带 `newBaseline`，创建批次和沿用时都与实际 baseline 比较。发生 SHA 变化的仓库需 `changes.coverage.repositories[role]={complete:true,oldSha,newSha}`，缺完整差异时保持 unknown。旧证据与等价证据各自标注 `assertionId` 和对应 `baselineHash`；相对目录也可用。

收费前置只有持久化预算预留绑定成功才允许派发；准备失败留下的操作显示 `reserve_budget`。派发时再次检查预算账本，已释放/冲突预留不能执行。

## 能力与验证边界

共享包不携带历史账号和验证回执。每位用户需分别完成代码检查、模拟集成、真实联调及业务验收，不把示例或旧报告当成本轮通过。

Unity 适配器是空模板：复制 assets/acceptance/unity/gold-miner-adapter.json 到本轮私有目录，填写并独立核对 sourceIdentity 的实际提交与文件哈希、identity 五项身份、runtimeAssembly、adapter version 和编辑器实例。空值或占位身份不能生成执行动作；校验相等不能替代实际身份和授权证据。保留当前版本接口核查和运行回执。

工作台只生成本轮会话的动作单，不跨会话自动执行外部工具。配置场景是否可部署由本轮计划明确；未授权时保持 needs_authorized_environment。

## 场景前置条件示例

| 断言 | 必需条件 |
|---|---|
| R4/published_speed | 获准隔离服/账号，配置不同的药水 A/B，同步发布后客户端和服务端实际加载证据，正常抓取速度对照 |
| R41/expired_retry | 可安排的个人活动自然到期时间、可信服务端时间、未完成关卡/重试入口及到期前后观察 |
| R43/repair_retry_once | 同一隔离目标先加载缺失/无效兑换配置，再修复并实际加载；资产快照、重试与只发一次的证据 |
| R43/expired_no_mail | 自然到期活动、待处理收入/兑换状态、邮件和资产前后快照；不能以强制结束替代 |
| R44/aggregate_floor | 分段收入能区分逐段取整与合计取整的配置、确定性收入输入、权威资产结果 |
| R44/large_values | 超过安全浮点整数界限的精确整数输入、有效配置、服务端整数计算与最终资源核对 |
| R49/expiry_exit | 自然到期窗口、进行中对局、可信时间和退出流程，服务端/UI/资源连续观察 |

R43/repair_retry_once 还依赖 R43/missing_invalid_preserves：错误配置阶段须先证明保留原资产。上述条件不等于已有部署授权；场景包会显式停在 needs_authorized_environment。

## 验证命令

```powershell
python -X utf8 -m pytest tests/test_acceptance_*.py tests/test_runtime_acceptance.py tests/test_jenkins_test_environment.py -q
```

PowerShell 原生命令不一定展开通配符；可用 `python -X utf8 -m pytest tests -q -k 'acceptance or jenkins_test_environment'`，或将具体路径逐个传入。最终交付报告保存真实执行结果，不把本文命令当作已执行证明。

录制 C# 模板要求参数 `workspaceRoot`：从本人的已校验 profile 读取，输出先经 `user_config.check_write_path` 校验再绑定，不能写入 skill 安装目录或源码仓库。录制与停止仍使用同一本轮目录和 recordingId。
