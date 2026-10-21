# 雷达怪物击杀计数优化：只读探针使用说明




## 唯一维护源与哈希


| scriptId | 唯一源文件 | 原文件 | 当前模板 SHA256 |
|---|---|---|---|

| scriptId | 原文件 SHA256 | 本次变更范围 |
|---|---|---|

未新增保护、协议、状态修改或其他执行逻辑。RM-S02～05 参数化后未编译或实测，须在本轮版本下重新复核。RM-S01/06/07 虽与现存原文件字节一致，历史只证明当时版本的读取行为，当前兼容性仍待确认。以后改动模板，应同步更新本表哈希、变更原因、回执和验证状态。


## 宿主、依赖与开始状态


所有模板开始前须在本轮批准的测试环境中进入 Play 并完成登录，读取实际玩家身份并确认当前 Unity 实例与项目。只查 UI 的模板没有 Play/账号守卫；RM-S04 只比对 UID/SID；RM-S03/05 没有 Play 守卫；RM-S02 不拒绝 `IsGoldEnv=true`，仅返回该字段。这些限制不是整理时新增的保护，必须由操作者先检查。不能把 Inspector 的下一次快捷登录设置当作实际身份，也不能以历史参数替代当前授权。

| scriptId | 具体依赖 / 使用前复核 |
|---|---|
| RM-S01 | `UnityEditor.EditorApplication`、`Logic.LoginMgrNew`、`Logic.LPlayer`、`Logic.CollectAtlasDataMgr`、`Cfg.C.CD2NpcTroopClass`、`Game.Config.MetaConfig`；`GetKillOrderData(id,false)`、`GetFirstKillLevel`、`IsFirstKillTaken`、`IsFirstKillEnabled`、`GetFirstKillMaxLevel` 的语义 |
| RM-S02 | 上述玩家/图鉴/怪物配置类型，另依赖 `Logic.PlayerAssetsMgr`、`K1D2Config`、`Game.Data.GameData.I.LineUpData`、`Logic.LSwitchMgr` 和 `Config.SwitchConfig.AtlasDispatchTroop`。四类怪 cfg 缺失时可能报空引用，不能强行跳过配置检查 |
| RM-S03 | `Game.Data.GameData.I.LineUpData` 的 `lockedList`、`GetLineUpDataByIndex`、`GetNewLineupStateInfo`、`IsLineupIdleForMarch`、`CanChangeLineUpState`、`GetIdleMarchLineUpData`；`cspb.LineupState`、`Config.SwitchConfig.NewTroopState` |
| RM-S04 | `Logic.LPlayer.GetMainCityScene`、`Logic.LCrossServer.CurrSceneType`、`Logic.WMapUtil`、`Logic.LMapEntityManager.GetEntitiesInView`；当前地图/跨服状态已初始化 |
| RM-S05 | `CollectAtlasDataMgr.tasks`、`Cfg.C.CEnergyAtlasMission.I`、`Cfg.C.CD2Search.I`、`LMapEntityManager.GetEntitiesInView`、`NpcTroop.exploreAtlasExt.callerId`；任务配置/搜索配置须存在，未增加 null 容错 |
| RM-S06 | `UnityEngine.Object.FindObjectsOfType<MonoBehaviour>`、`UnityEngine.UI.Text`/`Button`、`EventTriggerListener`；仅按活跃对象路径中的 Atlas/Radar/SearchMonster/CollectDebris 筛选 |
| RM-S07 | RM-S06 的 UI 类型、`DeepUI.SceneManager1.I.FindScene(typeof(UI.UIMain2))`、反射私有字段 `m_IsDispatchingTroop/m_TargetId/m_MarchAct/selectedLineUpIndex`；路径筛选 LineUp/Operation/Dispatch/GM，可能包含 GM 面板的可见文字 |

接口或字段变化时先核对当前代码和配置，不只替换版本标识继续执行。默认不把这些模板用作完整配置检查、服务端权威状态证明或截图替代。


## 全部必填参数与固定业务范围

| 占位参数 | 类型 / 单位 | 使用模板 | 取值与替换要求 |
|---|---|---|---|
| `__PLAYER_ID__` | 数字字面量，按当前 C# PlayerID 字段兼容类型填写；无单位 | RM-S02/03/04/05 | 本轮已核验并获准的实际玩家。RM-S05 出现两次，身份检查和 callerId 过滤必须一致；不要加引号 |
| `__SERVER_ID__` | 数字字面量；无单位 | RM-S02/03/04/05 | 本轮实际 `LPlayer.ServerId`（本服身份），不能拿当前战场/跨服场景 ID 替换；不要加引号 |

RM-S01/06/07 没有模板参数。所有模板没有命令行参数、环境变量或路径参数；不读外部文件，不需要凭证。执行前确认工作副本里没有任何 `__...__` 标记；不把绑定身份后的工作副本写回本目录。

以下是固定业务选择，不是待替换身份参数；其兼容性仍须本轮核对，改变则形成新的未验证模板：

- RM-S01/02 的四类怪 ID 为 `13330001`～`13330004`；RM-S01 仅遍历首杀 1～10 位，RM-S02 仅遍历 1～50 位，不能据此宣称更高等级完整覆盖。
- RM-S02 仅采样资产 `3320046/3320041/20010000/3320057/21110017`、货币 `11151001` 与行动力；不是完整背包快照，也不覆盖全部 GM 恢复奖励。`uiFields` 是历史保留的空字典，不代表读取了派遣字段。
- RM-S05 遍历当前任务和视野内归属本人的含 `exploreAtlasExt` NPC，并未按普通/精英分类；使用 `NpcType`、实际配置与本轮规则判定对象。空列表可能只是目标不在视野或还未下发，不能单凭它断言刷怪失败。
- RM-S06/07 的实例 ID 与 UI 路径随运行改变；不要保存后跨会话复用。活跃对象和文字输出不证明可交互、最终视觉状态或完整业务链路。


## 正常完成、输出与异常处理

片段本身均不写磁盘、不注册回调、不安装后台轮询、不主动发协议或 GM、不更改队列/任务/首杀状态、不消耗资产。输出由宿主返回；调用者在本轮 `.tmp`/报告保存工具回执及必要证据，可能含身份、资产、NPC 和队列数据，应留在本轮来源目录，不能复制到共享 Skill。当前版本若 getter 实现变化，重新核对只读性。

| scriptId | 正常完成信号 / 输出 | 早退或不完整结果的含义 |
|---|---|---|
| RM-S01 | 宿主 success=true 且返回 `uid/server/city/threshold/configVariant/loggedIn/atlas/counters` 对象；核对真实身份 | `NOT_PLAYING` / `NOT_LOGGED_IN` 是成功执行了早退，不能算采样完成；只返回 1～10 位 |
| RM-S02 | 返回身份、四类 counters、energy/maxEnergy、coin/指定 assets、atlas、lineups、atlasDispatch；同一场景前后对照 | `ABORT_IDENTITY` 表示前置或身份不符；资产不是全量，不能据此证明完整背包恢复 |
| RM-S03 | 返回 newTroopState、idleId、逐队列新旧状态和 idle/canChange | `ABORT_IDENTITY` 早退；客户端 idle=true 不能取代本服/战场一致性或真实发兵结果 |
| RM-S04 | 返回身份、cityScene/currentScene/awayFromHome/homeScene/entityCount | `ABORT_WRONG_ACCOUNT` 早退；只证明当前读取场景，不能证明目标可达或战斗成功 |
| RM-S05 | 返回 configs（实际任务/Exp/EnergyCost/Reward/searchType）与 npcs | `ABORT_WRONG_ACCOUNT` 早退；空 npcs 只是该时刻视野筛选结果。核对 owner/type/level，不保存旧实体 ID 作为新目标 |
| RM-S06 | 返回匹配的活跃 UI 列表，每项 id/type/path/text | 空列表或字段缺少只是查找结果；配合当前画面与正常入口，不按旧坐标/实例 ID 点击 |
| RM-S07 | 返回 fields、匹配 UI rows 和 timeScale；检查字段是否实际存在 | FindScene/反射未命中时 fields 可能为空或部分缺失；timeScale 仅回读，不改变时间，不表明派遣成功 |

这 7 个片段均为同步一次性采样，没有脚本内部超时、轮询、异步回执或注销函数；宿主单次超时由本轮工具控制，历史材料未记录统一时限，不能声称已有定时保护。调用结束无需清理游戏状态或注销回调；宿主异常/超时则保留日志、确认是否返回及当前 Unity 连接状态，不通过改内存/删实体“修复”读数。UI 的关闭必须按正常 Close 生命周期操作，探针本身不打开或隐藏 UI。工作副本与回执按本轮留存约定处理，清理文件不会回滚任何游戏进度。


## 验证与记录

共享模板不包含可访问的历史执行证据。当前接口兼容性、编译、真实运行和业务结论均须在本轮独立验证；记录在私有计划和报告中。使用前计算当前模板 SHA256，绑定本轮参数与授权，保留执行回执。未绑定参数不执行；未知结果先回读。

## 当前模板文件

- [radar-account.cs.txt](radar-account.cs.txt)；当前内容 SHA256：`52616b63af154ee01bd16c4989d04093c827dd752466b59d0fc7359add1886ec`。
- [radar-data.cs.txt](radar-data.cs.txt)；当前内容 SHA256：`597330128275378e3791800cbfec43356744e78ad275fa79902e7ebc94a88505`。
- [radar-dispatch-state.cs.txt](radar-dispatch-state.cs.txt)；当前内容 SHA256：`630165c47b11211cc4cea5dc096cbe5799db1cc62b7c907e5130430f3d8ee6ae`。
- [radar-full-state.cs.txt](radar-full-state.cs.txt)；当前内容 SHA256：`add3f832f175ae0956d94ef6846e3b80cdb022b59543ea3e5bc25aa666909a95`。
- [radar-lineup-state.cs.txt](radar-lineup-state.cs.txt)；当前内容 SHA256：`daeb11353f0096740b33d40dcef795ca799cf6ca72c0e840b52d9780364fe1b6`。
- [radar-map-state.cs.txt](radar-map-state.cs.txt)；当前内容 SHA256：`f1159e899ba008a2e897fc0077f09919016fe1a41549f8b69f538db5a19be3b3`。
- [radar-ui.cs.txt](radar-ui.cs.txt)；当前内容 SHA256：`4c1cffb550111c7388d87876e63466c4788c76f4c04c5c649a7c6a5ad1b5a099`。
