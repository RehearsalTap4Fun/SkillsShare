# Jenkins 准备 K1 测试环境

运行验收缺环境，或用户单独要求备份线上服并准备本人测试环境时使用；沿用用户已授予的范围，不重复确认部署。只有 Jira/分支不构成任意 SID、清档或数据导入授权。

分享给他人或首次使用，先按 [外部依赖与首次配置](dependencies-and-setup.md) 提醒并核验个人 Jenkins Token、DWS 登录、Git 凭证、mcporter/K1 MCP 及路径约定；密钥不随 skill 分发。

阅读导航：[自动整链入口](#自动备份恢复和校验入口优先) · [来源与参数](#来源与参数解析) · [已有 Key/空服入口](#低层单部署入口已有-key-或空服) · [功能验收](#从构建成功到可验收)。

## 自动备份、恢复和校验入口（优先）

用户提供线上 SID、代码分支、配表分支后，用 `scripts/jenkins_environment_workflow.py` 自动推进。代码与配表分支独立解析，不能默默同名、回退 dev 或复用旧 Key。单独建环境无需策划文档对齐；后续功能验收仍执行完整审核与验收合同。

已确认默认规则：

- `targetSid` 未指定时等于 `onlineSid`，重建当前 Jenkins 用户本人的同 SID 测试服并导入本次备份。另有目标 SID 时须来自用户授权，且应用类型必须一致。
- 游戏服 SID <10000，`app=gs`；战场服 SID ≥10000，`app=battle`。两者都先调用 `jobs.backup`，参数 `sid=onlineSid`；之后调用 `jobs.deploy`。
- 战场服 `kvk_keep_servers` **未指定就全量恢复**；指定非空游戏服 SID 列表则仅保留与列表有关的 KVK 战场。传 `[]`、非法 ID 或给 gs 传列表会拒绝，避免含糊的恢复范围。不要为已确认的默认规则再次索要批准。
- `aliveDays` 优先请求值，其次本地 `deploymentDefaults.ALIVE`，两者都未提供则停止并要求填写。
- 备份/导入/本人同 SID 重建授权引用写入 `authorization`；引用实际用户消息，不捏造授权。仅让脚本“准备计划”不触发构建。

请求模板：`assets/jenkins-environment-request.json`。例：

```json
{
  "onlineSid": "<ONLINE_SID>",
  "codeBranch": "<CODE_BRANCH>",
  "configBranch": "<CONFIG_BRANCH>",
  "kvk_keep_servers": ["<TEST_SID>", "<KEEP_SID>"],
  "authorization": "<AUTHORIZATION_REFERENCE>"
}
```

全量恢复时**省略** `kvk_keep_servers`。其他可选字段：`targetSid`、`app`、`aliveDays`；分支支持 `dev` 或 `origin/dev`。

脚本通过 `--config` 或 `K1_JENKINS_CONFIG` 读取用户私有连接配置。`jobs` 与 `environmentWorkflow` 按 [依赖说明](dependencies-and-setup.md) 填写；通知群、机器人、运行节点及工作流链接前缀均无默认值，缺失即停止。

请求文件放在已有 `.tmp` 下（例如 `.tmp/environment-request.json`）；`--run-dir` 必须是尚不存在的新目录，由 prepare 创建。在 `<workspaceRoot>` 执行：

```powershell
python <skillRoot>/scripts/jenkins_environment_workflow.py prepare --request <request.json> --run-dir <workspaceRoot>/reports/<new-run-id>
python <skillRoot>/scripts/jenkins_environment_workflow.py advance --run-dir <workspaceRoot>/reports/<same-run-id>
python <skillRoot>/scripts/jenkins_environment_workflow.py status --run-dir <workspaceRoot>/reports/<same-run-id>
```

`prepare` 自动调用远端快照脚本，在 用户工作区 缓存冻结两端分支，校验实时部署参数并保存计划；只读远端，不触发构建。准备失败、尚无 `workflow.json` 时可换新目录重做准备；一旦有提交回执就必须沿用原目录。

Agent 持续调用同一目录的 `advance`，每次最多一个 Jenkins POST。等待状态间隔约 15–30 秒查询，期间报告阶段进展；不要在等待消息/队列时结束任务或要求用户说“继续”。达到 `ready` 后读取 `environment-evidence.json`。无总时限；单次接口超时有界。`status` 只读本地回执，不代表实时健康检查；需要重新核验时用 `advance`。重新核验失败会在 `progress.json` 记录 requires_attention 和脱敏错误；旧环境证据保留其原 checkedAt，仅代表那次观察。

| 状态 | 下一步 |
|---|---|
| backup_submitted / waiting_backup_build | 原队列 → 原 build，继续 advance |
| waiting_backup_message | Jenkins SUCCESS 只是触发 Oops；等本次钉钉备份完成回执 |
| deployment_submitted / waiting_deployment_build | 原部署队列 → 原 build → 实际下游，继续 advance |
| ready | 读取完整环境证据，进入已授权的客户端/功能验证 |
| submission_unknown / submission_pending | 同目录 advance 先只读核对；唯一匹配才续跑，绝不再次 POST |
| RUNTIME_* 未就绪错误 | 保留原部署；检查实际进程/心跳/端口，启动期间可短暂等待后继续 advance |
| BACKUP_WORKFLOW_FAILED | 已收到备份失败/取消/终止回执，读取脱敏 backup-failure.json 后诊断，不继续等完成 |
| DEPLOYMENT_SUPERSEDED | 此 SID 已有后续部署或待执行替换；旧批次不能代表当前环境，核对新的实际批次 |

具体错误不能当作成功：构建失败/取消、其他用户占用、消息歧义、实际 SHA 不匹配时保留证据并诊断。不能自动换 SID、改分支、扩大恢复范围、删除回执或重触发。若确需另一次变更，先确认已有授权是否覆盖；未覆盖时才询问用户新决定。

### 关联、恢复和证据边界

- `workflow.json` 固定计划；`backup.json` / `deploy.json` 在 POST **之前**独占创建，保存队列、精确构建、参数与来源。CLI 使用操作系统锁防止同机并发推进；进程崩溃自动释放锁，回执保留。
- 提交超时/无 Location/队列过期时扫描可证明完整的构建时间窗口。已知队列 ID 优先按 queueId；否则严格比较操作者、参数、导入 Key 哈希和原 POST 接收时间窗（含时钟误差）。排队延迟的构建需仍可读的原 queue.inQueueSince 证明；队列已过期、构建开始在窗口外时不猜测关联。零个或多个候选均停止；不以 lastBuild 替代、不删除回执重试。历史不完整也是未核实。
- 钉钉仅只读查询指定群。去重、分页且保留同秒边界；无法完整翻页就报错，不跳过消息。必须是指定机器人、准确 SID 列表、本次时间窗内的唯一“完成”回执，且 Jenkins 同 SID 无并发备份。提交前还检查过去 24 小时同 SID 备份的完成回执，每条消息只能证明一个历史构建，避免上一份异步备份迟到误配。
- 当前备份通知不含 Jenkins/Oops ID，因此这是**有边界的唯一性关联**，不是服务端关联 ID。报告保存 Oops workflow ID、Jenkins build、群 ID、消息 ID、时间和 Key SHA256。窗口中存在竞争/消息延迟导致无法唯一归属时停止，不选“最新的一条”。超过 24 小时仍未完成的旧 Oops 作业是已知关联限制，遇到此类证据须先排除再运行。
- DownKey 不写请求、计划、命令行、临时文件或报告；DWS 输出、Jenkins 参数和控制台原文仅在进程内解析。仅内存中的 Key 发给部署 POST；落盘只存哈希。恢复到部署前时重新查询原消息并验证绑定不变；部署已提交后按回执继续，不需旧 Key 明文。
- 备份前、部署前都查完整 Docker 实例清单（含已停止实例）及 Jenkins 同 SID 活跃任务。允许本人预期实例，发现其他同 SID 名称就停止。检查与远端任务执行间没有跨系统原子锁；团队同时从外部手动操作仍有竞争风险，不应并行使用同一 SID。
- 构建后核对两仓库实际 SHA、固定下游 Job 的 upstream cause、参数、Key 哈希和 Mongo 恢复成功/失败计数。分支移动不自动重部署，不假称冻结 SHA 已生效。
- 运行探针只执行读取：核对宿主地址、本人容器、启动时间必须落在本次下游构建区间、镜像标签/ID、进程、dev 配置标签、SID、`game` / `game --battle`、目标容器当前心跳与主端口 TCP，并排除后续同 SID 父/子部署和队列。心跳必须 30 秒内且类型、SID、分支/用户吻合。运行镜像使用实际构建 SHA 的前 12 位标签，镜像完整 GitCommit 标签目前可能是 unknown，不能宣称运行二进制具备完整 SHA 的独立密码学证明。
- `ready` 只代表测试环境运行就绪。客户端登录、活动状态、账号归属和功能断言需要继续实际验证。

### 交接功能验收

将本次 `environment-evidence.json` 作为 `state.environmentEvidence` 的 `kind=runtime` 附件，路径可为绝对路径。沿用实际部署 SHA，比较原验收基线；版本改变时重新核对受影响需求/用例。保留创建者、目标 SID、线上源 SID、恢复列表/全量模式、ALIVE 天数和导入范围，在收尾记录测试环境保留状态。

```json
{"kind":"runtime","path":"<workspaceRoot>/reports/<run-id>/environment-evidence.json"}
```

证据缺失或状态非 ready 时不得把环境写成已就绪。环境准备成功也不能把任何用例自动标为 passed。

## 来源与参数解析

1. 查 Jira 开发关联/MR、三端远端分支和合入记录。单号和 `dev_NK-xxxx` 仅为检索线索，不硬拼为部署参数；服务端和配置分别解析，同名分支缺失时不默默回退 dev。
2. 用现有远端快照脚本在 用户工作区 缓存精确冻结目标 SHA。复用既有基线；要纳入新提交就重新对齐受影响需求并校准用例。
3. 读取项目本地 `<JENKINS_CONFIG_FILE>`：baseUrl、username、apiToken、deploymentDefaults。不得打印凭证、复制到报告或写在命令行。环境变量里的数据导入 key 只在提交时读取。
4. 用 `scripts/jenkins_test_environment.py inspect --config <私有配置>` 核对 `jobs.deploy` 对应的真实参数合同；buildable=true 不证明已取得构建权限。

当前参数映射（每次以 inspect 为准）：

| 参数 | 来源 |
|---|---|
| corebranch | 已核实服务端分支，例如 origin/dev |
| branchName | 已核实配置分支；可以与代码不同 |
| app | 本脚本仅测试游戏服 gs / 战场服 battle；gs 的 SID <10000，battle 的 SID >=10000 |
| server_id | 当前授权或明确分配的测试 SID，不依据单号推断，不占用其他人的服 |
| ALIVE | 1–5 天，按测试排期与本地偏好取值；当前 Job 默认 1、本地偏好 5，不隐式选默认 |
| DownKey | 默认空；需要数据导入时通过 dataImportKeyEnv 指定环境变量，并记录已有导入授权 |
| kvk_keep_servers | battle + DownKey 时按所列游戏服保留相关 KVK 战场；整链请求未指定时全量恢复，指定时由非空列表转换为逗号分隔值 |

任务 XML 无读取权限时可继续使用可读参数 API、历史回执和已核验文档；不能因此认定没有部署路径。参数有真实歧义且不能查证时才向用户问决定。

## 低层单部署入口（已有 Key 或空服）

复制 `assets/jenkins-test-request.json` 到本次运行目录，填写真实范围、分支、完整 SHA 和来源定位。`scope.authorization` 是实际用户消息的引用，不是脚本替用户生成的授权。

```powershell
python <skillRoot>/scripts/jenkins_test_environment.py inspect
python <skillRoot>/scripts/jenkins_test_environment.py prepare --request <deploy-request.json> --output <deploy-plan.json>
python <skillRoot>/scripts/jenkins_test_environment.py submit --plan <deploy-plan.json> --receipt <deploy-receipt.json>
python <skillRoot>/scripts/jenkins_test_environment.py poll --receipt <deploy-receipt.json>
```

- prepare 校验 SID 范围/类型、实时选项、代码与配置的独立来源、SHA 格式，产生带哈希的具体部署单；不发起构建。
- 核实范围已经授权后 submit。无需让用户重复批准同一范围。提交前再次核对实时参数，先独占创建回执再发 POST；计划改动必须重新 prepare。
- 仅支持该 dev 测试部署 Job，不能用正式热更 skill 替代。脚本不会切客户端分支、创建配置、清档或自动导入数据；这些若是用例需要，须在现有授权内按对应流程完成，或记录具体缺口。
- Jenkins 当前 API 接收移动分支，不承诺锁定源码 SHA；sources 中 SHA 是期望基线，构建后须查实际版本。如果构建时分支已移动，按实际版本建新批次并核对受影响范围，不能假称部署了期望 SHA。
- submit 的 Location 对应 queue item，poll 只跟踪该队列产生的具体 build。不得以 lastBuild/lastSuccessfulBuild 代替本次构建。每次 poll 是一次短查询，间隔安排独立工作；不因排队时间长重新提交。
- 回执已有、POST 超时/503、未取得 Location：保存 submission_unknown，先从队列/精确时间和参数匹配的构建记录核对。不能删除回执或换名字盲目重发；确证未接收后记录依据，另建关联的尝试回执。队列 404 时使用已保存 buildUrl；尚无 buildUrl 时继续只读核对构建来源。
- canceled / FAILURE 等保留具体构建记录，诊断日志时屏蔽凭证。脚本不输出原始错误响应体，不跨服务跟随重定向。

## 从构建成功到可验收

`build_succeeded_runtime_unverified` 只是构建结果，必须继续：

1. 查上游及其实际下游构建、节点进程/镜像与版本，核对目标 SID 和应用类型。
2. 核对配置发布标签/内容、运行时值、数据库/存储范围以及平台定向配置；Jenkins 下发配置与正式 XLSX 的对应关系需要证据。
3. 逐服读取时钟、活动实例及依赖服务。部署不会自动证明“新活动”或“时间归零”；禁止用重部署实现未经允许的回拨。
4. Unity 使用匹配客户端登录授权账号，核验实际玩家与服务器、联盟权限，再从补验队列继续正常业务操作。
5. 保存运行清单为 runtime 台账 environmentEvidence；仍有明确缺口时保留未确认范围，完成其他独立用例。

首选已有测试服中最小的准备动作，只有不能满足用例才部署新环境。服务器和账号变化、数据导入、资源消耗以及到期时间均纳入收尾记录。
