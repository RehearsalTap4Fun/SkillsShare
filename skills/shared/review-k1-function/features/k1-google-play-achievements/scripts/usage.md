# Google Play 成就只读探针说明



## 宿主、依赖与本轮参数

| 项目 | GPA-S01 | GPA-S02 |
|---|---|---|
| 宿主 | Unity Editor Play中的Unity MCP `execute_code`；历史编译器Roslyn | 相同 |
| 依赖 | `UI.CpeTaskMgr.I`、`tasks`、`GetConfig`、`IsOpen`、`redDots`、私有字段`_configInited`；任务/配置字段见源文件；`Logic.LPlayer.I`、`Logic.LoginMgrNew.I.loginData.serverid`、System集合/反射 | `UnityEngine.Object.FindObjectsOfType<MonoBehaviour>`、GameObject/Transform、`UnityEngine.UI.Text`、`UnityEngine.UI.Button`；按类型名称匹配`EventTriggerListener` |
| 必填代码参数 | 无；没有账号/SID/文件路径占位符，读取当前会话 | 无；固定UI过滤是历史业务依赖，不是本轮身份参数 |
| 本轮外部绑定 | 调用前选定已授权Unity实例；核对实际账号、服务器、版本与Play状态；回执保存目录由本轮调用方指定 | 相同；选定要观察的页面，确认页面加载结束 |
| 起始条件 | 登录与配置初始化已稳定；先确认`LPlayer`和`LoginMgrNew.loginData`存在。脚本自身没有完整空值防护或账号拦截 | Play稳定、目标UI已经呈现；不是自动导航或打开面板 |

两份模板不读取本机连接配置、不包含凭证、固定账号、SID或路径；不复制历史账号许可。每次使用先复核受影响接口与字段，将模板复制到本轮`.tmp/<run-id>`并记录实际客户端/配置/服务端版本。编译条件变动或反射字段不存在时先停止并核对，不能只填入新版本号就宣称兼容。


## 输出、完成信号与覆盖边界

| scriptId | 输出 / 采集完成信号 | 不能由此推导的结果 |
|---|---|---|
| GPA-S01 | 工具`success=true`；`data.result`有当前账号/服务器、登录服务器、`open`、`red`、`configInited`及`tasks`。每条含任务ID、状态、时间、配置字段与奖励。`tasks_not_ready`表明未就绪；`configMissing=true`表明该条配置缺失，均应补证 | 客户端缓存不等于网络Req/Ack、原始配置通知或服务端持久状态；任务字段`uid`不等于玩家ID。`red=0`不等于红点触发/清除验收通过，历史Reward不等于本轮领奖成功 |
| GPA-S02 | 工具`success=true`，返回组件数组（`id`、`type`、`path`、`text`）；017版本历史返回85条。返回空数组仍是有效执行结果，需检查页面/过滤/生命周期 | 仅采集Text、Button和名称为EventTriggerListener的活动组件；排除路径不含UI或含WorldMap的对象。不是完整UI树，不包含所有文本类型，不能证明不存在其他入口、物理触屏命中或实际面板已打开 |

保存完整请求文本、执行回执、执行时间、当前身份和版本到本轮证据目录；身份数据留在受控报告，分享Skill时不带回执。对UI判断还需截图及面板/业务协议互证。输出成功只表示采集执行成功，不自动把功能用例设为通过。


## 操作影响、停止和清理

- 两份代码仅读取当前对象、集合及字段，没有点击、业务请求、GM、发奖、状态赋值、文件写入或回调注册。运行时会读取本轮账号和界面文字，应按本轮授权保存回执。
- `CpeTaskMgr.I`等项目属性/单例的内部行为依赖当前实现；上述“只读”指片段没有显式写操作。版本变化需复核访问器，不能由历史读取结果保证任意新版无副作用。
- 脚本同步执行一次，没有循环、内部超时或持久任务。调用方使用现有工具的单次调用超时；历史材料未保存可复用的统一超时值，不编造秒数。工具超时或状态不明时先回读编辑器状态，不重复派发。
- 执行结束没有委托、事件或回调需要注销，也没有游戏状态需要回滚。只清理本轮自己创建的临时副本/回执，按留存约定保留证据；不要清理Unity资源或用户工作区。
- 若空引用、配置未就绪、重载或退出Play，暂停采集并重新确认生命周期。不要通过调用登录、发任务通知或修改字段“修复”探针前置；这些不属于这两份探针。


## 验证与记录

共享模板不包含可访问的历史执行证据。当前接口兼容性、编译、真实运行和业务结论均须在本轮独立验证；记录在私有计划和报告中。使用前计算当前模板 SHA256，绑定本轮参数与授权，保留执行回执。未绑定参数不执行；未知结果先回读。

## 当前模板文件

- [active-ui-inventory.cs.txt](active-ui-inventory.cs.txt)；当前内容 SHA256：`348503029989217eca476c167ea5d9538f83c3de941fea93c597854e401efb23`。
- [cpe-task-snapshot.cs.txt](cpe-task-snapshot.cs.txt)；当前内容 SHA256：`d895116750c02fd3ebca5c143311f9c170448236901050ab4dfd5b935cba72bd`。
