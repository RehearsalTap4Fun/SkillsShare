# Google Play 成就：共享检查指南

featureId：`k1-google-play-achievements`；搜索词：CPE、CpeTask、LittleGameAchievement。

这是可复用的检查提示，不附带实际需求、账号、环境或历史验收结论。原始历史证据保留在使用者私有档案中；本轮状态默认待验证，由当前需求和证据确定。

## 本轮需要提供

需求与任务表引用、版本/冻结提交、授权测试环境与角色、计划及证据目录。具体值写入 `<workspaceRoot>/.local/features/k1-google-play-achievements/experience.md` 或本轮关联计划，不回填到共享包。

## 检查提示

- 读取当前账号与成就任务状态，确认存在可推进条件后再执行。
- Editor 内部状态不能替代 Android/Google Play 平台回执；平台不可用时明确记录覆盖缺口。
- 资源重载或版本变化需重新冻结基线；UI 事件已发送不代表界面已打开。

## 可复用脚本

先阅读 [脚本用法](scripts/usage.md)，复制模板到本轮目录，核对当前接口与参数后按授权范围使用。模板存在不证明当前版本已编译、已运行或业务通过。

经验读取与私有回写遵循 [经验规则](../../references/feature-experience.md)。
