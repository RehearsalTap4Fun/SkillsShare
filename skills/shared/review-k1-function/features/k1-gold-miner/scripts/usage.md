# 黄金矿工运行脚本档案



## 宿主、依赖和起始状态

- Unity Editor 已进入 Play，Unity MCP 能执行编辑器 C# 片段；依赖现有游戏程序集、`UnityEditor`、`UnityEngine`、`DeepUI.PopupManager`、`Newtonsoft.Json` 及执行环境的 LINQ 支持。
- 复核 `UI.GoldMinerResultPanel`、`UI.UIActivityGoldMiner`、`Game.GoldMiner.LGoldMinerMgr`、`GoldMinerBattleView`、`GoldMinerRuntimeSession`、`ElementSpriteView` 和正常事件/协议接口。客户端变化或方法签名不兼容时先复核，不能仅替换程序集 ID 强行运行。
- 输入模板读取的道具 ID 为 `50300001` 药水、`50300002` 炸弹、`50300003` 时钟；这是历史模板的固定依赖，先核对当前正式配置及运行值。
- 两个模板都写本轮日志目录。提前创建可写目录，字符串参数使用适当的 C# 字符串转义；目录可用正斜杠。不得把凭证或带历史账号绑定的执行副本写回模板。
- 局外或正常规则页暂停时准备输入；先安装输入辅助，再安装导航。导航默认 `phase=0`，适用于正在进行的普通关或正常结算页，不能承诺从空闲活动主页自行开局。若从主页开始，确认输入就绪后通过本轮获准的正常挑战入口开始首局。
- 导航按 `MaxPassLevel` 与上一关比较识别未通关停止；不要用它判断重打已通关关卡的失败或第 51 挑战关结果。挑战关单独核对结算回包和界面。


## 本轮必填参数

| 参数 | 类型 / 单位 | 使用模板 | 取值依据 |
|---|---|---|---|
| `__PLAYER_ID__` | 数字，按当前 C# 字段类型填写 | GM-S01 / GM-S02 | 本轮实际玩家，不是 Inspector 下次登录值 |
| `__SERVER_ID__` | 数字 | 两者 | 本轮获准服务器 |
| `__ACTIVITY_ID__` | long 数字，模板已有 `L` 后缀 | 两者 | 当前活动实例 |
| `__CLIENT_MODULE_ID__` | 字符串 | 两者 | 复核接口后读取 `typeof(UI.GoldMinerResultPanel).Assembly.ManifestModule.ModuleVersionId`；不代替服务端 SHA 或配置哈希 |
| `__RUN_DIRECTORY__` | C# 字符串内容，绝对目录 | 两者 | 已创建的本轮 `.tmp` 目录 |
| `__INPUT_READY_KEY__` | 字符串 | 两者 | 本轮独立输入回调键，两模板保持相同 |
| `__STOP_AT__` | 普通关目标，整数 1–50 | GM-S01 | 本轮授权的停止关卡，不涵盖挑战关 |
| `__MAX_POTIONS_PER_LEVEL__` | 每关次数，非负整数 | GM-S02 | 本轮药水使用上限；0 禁用 |
| `__MAX_BOMBS_PER_LEVEL__` | 每关次数，非负整数 | GM-S02 | 本轮炸弹使用上限；0 禁用 |
| `__MAX_CLOCKS_PER_LEVEL__` | 每关次数，非负整数 | GM-S02 | 本轮时钟使用上限；0 禁用 |
| `__POTION_RESERVE__` | 药水保留数量，非负整数 | GM-S02 | 本轮资源基线与约定 |
| `__CLOCK_RESERVE__` | 时钟保留数量，非负整数 | GM-S02 | 本轮资源基线与约定 |

模板没有炸弹保留库存参数，只有次数上限和当前库存检查；若需保留炸弹则禁用自动炸弹或另行验证支持该约束的模板，不能宣称已有保留保护。每关上限不等于跨重试/跨会话的总预算；补装辅助会重置内部计数，须按本轮资产流水继续核对总额。


## 操作影响、完成信号与失败处理

| 模板 | 操作影响 | 完成 / 停止依据 | 输出 |
|---|---|---|---|
| GM-S01 | 正常退出结算、打开活动、挑战下一关；新挑战可扣体力。不购买、不升级、不使用 GM | 新 `CurrentSession` 和 `EnterLevelAck`；达到目标、未通关、无体力、输入键缺失停止。总计 600 秒上限，进入导航阶段后 20 秒未完成停止 | `navigation.jsonl`（停止时写入） |
| GM-S02 | 正常发射、按配额使用道具，可能消耗资产；未知图案通过正常规则入口暂停。不是只读探针 | 原始请求/回包、资产、会话与画面互证；600 秒上限、身份/活动/Play 改变或异常停止 | `visible-inputs.jsonl`（停止时写入）、`learning-alerts.jsonl`、相关截图 |

两个模板都注册编辑器更新回调与程序集重载回调；关闭时注销。输入键存在只证明回调登记，实际输入就绪仍需回读本轮会话及日志。未知结果保持现场并回读，不能重发购买、升级、道具或挑战。未知图案截图是异步请求，要检查文件落地及有效画面；正常说明页暂停和渲染时序见功能档案。

需要主动停止时，在同一 Unity 执行环境中调用已注册的清理委托：

```csharp
// 先停止导航，防止在停止输入时又发起下一关。
(System.AppDomain.CurrentDomain.GetData("用户工作区.LearnedFlow.GoldMinerAdvance") as System.Action)?.Invoke();
// 此字符串必须替换为本轮实际输入键。
(System.AppDomain.CurrentDomain.GetData("__INPUT_READY_KEY__") as System.Action)?.Invoke();
```

随后回读两个键为空、日志出现 cleanup，确认编辑器回调已注销；清理不回滚自然进度或再次发奖。导航键是固定全局键，输入键独立；同一 Unity 实例不可并发安装两套控制同账号的辅助。


## 验证与记录

共享模板不包含可访问的历史执行证据。当前接口兼容性、编译、真实运行和业务结论均须在本轮独立验证；记录在私有计划和报告中。使用前计算当前模板 SHA256，绑定本轮参数与授权，保留执行回执。未绑定参数不执行；未知结果先回读。

## 当前模板文件

- [gold-miner-advance.cs.txt](gold-miner-advance.cs.txt)；当前内容 SHA256：`46e5bae8cd1e712a03b2ea7fb0615f0212baa89872d60f16fb5edb07ffe9cecb`。
- [gold-miner-visible-input.cs.txt](gold-miner-visible-input.cs.txt)；当前内容 SHA256：`4d655030f0193a288d0c379aa1493608136fd392f150a4e69e1425bc340e9d43`。
