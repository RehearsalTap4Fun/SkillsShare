# 用户依赖与私有配置

共享包只包含方法、脚本和空模板。每位用户自行配置路径、服务连接及账号；安装 skill 不授予 Jenkins、游戏、文档或 Git 权限。首次使用只核对所选阶段需要的依赖，不通过部署或验收来试权限。

## 运行依赖

| 用途 | 用户需要准备 |
|---|---|
| 静态审核 | Python 3.10+、Git、Windows PowerShell；按需 Git LFS、openpyxl、config-table-editor |
| 运行验收 | 本轮版本 Unity Editor、Unity MCP、unity-mcp-orchestrator，以及授权账号与环境 |
| 自动建环境 | 本人 Jenkins Token、DWS CLI 登录、Node.js/mcporter、k1-mcp.ssh_exec、团队网络/VPN |
| 文档和任务表 | 本人钉钉连接及表权限；Word/PDF 按需使用对应 skill |
| 生成用例 | slg-testcase-generator；已有用例时不强制安装无关依赖 |

当前 PowerShell 与 mcporter 适配器以 Windows 为运行目标；Markdown/JSON 通用不代表整套执行端已支持 macOS。外部工具由各用户按团队支持版本安装，skill 不携带其账号或连接配置。

## 1. 用户路径

将 [user-profile.example.json](../assets/user-profile.example.json) 复制到仓库外，例如 `~/.config/k1-function-review/profile.local.json`，填写：

| 字段 | 用户提供 |
|---|---|
| schemaVersion | 1 |
| workspaceRoot | 验收工作区绝对路径，存放 .tmp、.cache、reports、.local |
| repositories.client | 客户端 Git 根目录 |
| repositories.server | 服务器 Git 根目录 |
| repositories.config | 配置 Git 根目录 |

三端路径均需声明以保护正式仓库；实际访问时检查本轮所需目录。工作区与正式仓库不能互相包含，不使用磁盘根目录或经过链接/重解析点的路径。Skill 安装目录与工作区独立。

```powershell
$env:K1_REVIEW_PROFILE = Read-Host '输入个人 profile.local.json 的绝对路径'
$skillRoot = Read-Host '输入 review-k1-function 实体目录的绝对路径'
python -X utf8 (Join-Path $skillRoot 'scripts/user_config.py') check
```

环境变量需在执行 Agent 的进程中可见。检查只报告状态，不输出配置值。缺失、空值或占位符会停止相关阶段，不回退作者环境。其他文档中的 `<workspaceRoot>`、`<repositories.client>` 等是字段引用，执行前必须解析为本机绝对路径。

## 2. 个人 MCP 与授权

MCP JSON/TOML 属于每位用户自己的客户端配置，skill 不覆盖全局配置或复制他人授权。

- 钉钉表格可使用用户提供的官方地址 `https://mcp-gw.dingtalk.com/oauth/server/sheet`，每人自行 OAuth 授权。运行时核对实际工具和目标表权限，连接存在不证明支持原子领取。
- K1 MCP 由维护者提供 `<K1_MCP_URL>` 与该用户的认证要求，配置到个人客户端和 mcporter。当前脚本使用连接名 `k1-mcp` 和工具 `ssh_exec`，不内置服务地址。
- DWS 使用本人账号登录，核对企业及目标通知群访问权；Token/登录态由 DWS 管理。
- Git 使用个人 SSH agent/key 或凭证管理器，从已配置仓库读取 origin；不把带凭证 URL 写进报告。

MCP 地址与游戏部署节点是不同字段，不能互相推导。凭证在本地填写，不在聊天中粘贴。

## 3. Jenkins 用户配置

复制 [jenkins-local-config.example.json](../assets/jenkins-local-config.example.json) 到仓库外，通过 `--config <私有文件>` 或 `K1_JENKINS_CONFIG` 指定绝对路径；没有默认凭证文件。

| 字段 | 获取方式 |
|---|---|
| baseUrl | 维护者提供 Jenkins 根地址，保留上下文路径，不含凭证/查询参数 |
| username / apiToken | 本人 Jenkins 用户 ID 与个人 API Token |
| jobs.deploy / jobs.backup / jobs.child | 维护者提供准确的部署、备份、下游部署 Job 名；当前只支持单层名称 |
| deploymentDefaults.ALIVE | 用户的保留天数偏好，须符合实际 Job 合同；未配置时每轮必须显式提供 aliveDays |
| environmentWorkflow.groupId | 已确认通知消息的 openConversationId |
| environmentWorkflow.sender / senderId | 同一备份机器人显示名与 senderOpenDingTalkId |
| environmentWorkflow.runtimeHost | 维护者确认的测试部署节点，须匹配探针目标 |
| environmentWorkflow.workflowBaseUrl | 维护者确认、且与 Jenkins 备份控制台输出一致的工作流链接固定前缀，不含某次运行 ID |

自动整链必须提供全部环境绑定；单部署按脚本字段检查填写。模板空值不能直接执行。群与机器人从维护者及已确认的通知取得，不随便取同名群或任意消息。

`inspect --config <私有文件>` 可只读核对 Job 元数据；不证明构建、备份或游戏权限。DownKey 每轮获取，不写进常驻配置；已有 Key 通过请求指定的环境变量传入。

计划冻结 Job、群/机器人、部署节点和工作流前缀。恢复时配置变化或旧计划缺少绑定会停止；先核对旧队列、构建和副作用再建立新计划，不能自动重新提交。

## 4. 每轮输入与留存

需求/任务表、分支与提交、SID/账号/活动/角色、授权范围及证据目录由本次计划提供。空值和 unknown 都不是有效身份。特定测试服的只读或禁止改配约束写入本轮计划，不绑定作者用过的 SID。

实际报告、截图、回执、通知内容及来源映射留在用户工作区；经验放在 `<workspaceRoot>/.local/features/`，遵循 [经验规则](feature-experience.md)。共享包只接收脱敏后的通用方法。

Git 忽略规则只是防误提交措施；发布仍使用明确清单排除 .local、.tmp、.cache、reports、凭证、登录态和缓存，不直接压缩整个工作目录。
