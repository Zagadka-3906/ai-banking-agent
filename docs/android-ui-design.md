# Android UI 设计与开源选型

> 当前仓库只保留 Android 原生 UI；IM 通过签名 webhook 复用同一 Agent，不包含独立可视界面。

本次 UI 重构保留现有业务与测试标签，统一登录、对话、银行卡、财富、联系人、授权、设备及待办等界面的视觉与交互基础。

## GitHub 调研与实际应用

| 项目 | 来源与许可 | 采用方式 |
| --- | --- | --- |
| compose-shimmer 1.3.1 | [valentinilk/compose-shimmer](https://github.com/valentinilk/compose-shimmer/tree/v1.3.1)，[Apache-2.0](https://github.com/valentinilk/compose-shimmer/blob/v1.3.1/LICENSE) | 实际引入 `com.valentinilk.shimmer:compose-shimmer:1.3.1`；所有 `SkeletonBlock` / `SkeletonRow` 通过 `shimmerSweep()` 使用其 `shimmer()` modifier。 |
| commonmark-java 0.30.0 | [commonmark/commonmark-java](https://github.com/commonmark/commonmark-java/tree/commonmark-parent-0.30.0)，[BSD-2-Clause](https://github.com/commonmark/commonmark-java/blob/commonmark-parent-0.30.0/LICENSE.txt) | 实际引入 `org.commonmark:commonmark:0.30.0`，将回复解析为段落、强调、列表、引用、代码与链接，再用原生 Compose 文本呈现。 |
| Android Compose Samples | [android/compose-samples](https://github.com/android/compose-samples)，[Apache-2.0](https://github.com/android/compose-samples/blob/main/LICENSE) | 参考 Jetsnack 的独立设计系统、Jetchat 的聊天状态与输入结构、Reply 的 Material 3 主题与导航组织。按本项目需求重新实现，没有复制示例源码。 |

### 版本选择

1.3.1 的[版本目录](https://github.com/valentinilk/compose-shimmer/blob/v1.3.1/gradle/libs.versions.toml)使用 Kotlin 2.0.10，Android Compose BOM 2024.06.00，minSdk 21。本项目使用 Kotlin 2.0.21、BOM 2024.10.01、minSdk 26，因此选择这一兼容基线版本，保留既有编译工具链。[该版本 README](https://github.com/valentinilk/compose-shimmer/blob/v1.3.1/README.md)明确列出 Maven Central 坐标。升级后的最终解析与兼容性以本项目 Gradle 编译结果为准。

依赖锁定于 `android/gradle/libs.versions.toml`，在 `android/app/build.gradle.kts` 中使用 catalog alias。没有增加远程仓库、引入整套示例应用或安装来源不明的 skill。

库 modifier 必须放在背景绘制之前，故骨架顺序为 `clip → shimmerSweep → background`。主题经 `LocalShimmerTheme` 全局注入。第三方库遵循其原有 Apache-2.0 许可；自绘图标与银行业务组件均为本项目实现。

## 视觉规范

- 主色为墨绿 `#124C3C`、辅助绿 `#176B52`，背景暖白 `#F5F6F1`，卡片白色，薄荷容器 `#E0EFE5`，金色 `#8A681E` 只用于辅助强调。
- 深色模式提供完整 Material 3 色槽，包括容器、轮廓、错误和逆色；禁止用浅色模式固定背景替代主题 surface。
- 页面主要间距为 20 / 24 dp，段落与控件间距为 8 / 12 / 16 dp。普通卡片 24 dp 圆角，输入与紧凑组件 12–20 dp，按钮 16 dp。
- 标题 24–30 sp，金额 24–42 sp，正文 14–16 sp。金额使用 `tnum` 数字特性；保留原始金额、精度与币种，不在视觉层改写业务数值。
- 金融涨跌继续遵循原有红涨绿跌语义。风险等级另有文本与状态标签，不单凭颜色表达。
- 可点击目标至少 48 dp；主要按钮最小高度 52 dp。允许大字体增高，不用固定高度裁切多行文字。
- 图标采用统一的 24 单位矢量网格、1.75 单位圆头笔触。独立图标按钮必须给出可访问名称；有同义文本的装饰图标不重复播报。

## 共享组件接口

既有 `BankSectionCard`、`BankSectionHeader`、`BankMetricCard`、`BankStatusChip`、`BankActionRow`、`BankLoadingState`、`BankEmptyState`、`BankErrorState`、`BankIconTextRow` 及动画函数签名保持兼容。

新增组件：

- `BankScreenHeading(title, subtitle?, eyebrow?, modifier, trailing?)`：页面标题、说明与可选右侧操作。
- `BankHeroCard(modifier, content: @Composable ColumnScope.() -> Unit)`：墨绿渐变资产卡与统一浅色前景。
- `BankPrimaryButton(text, onClick, modifier, enabled, loading)`：主要动作、防重复提交、按压与加载状态。
- `BankSecondaryButton(text, onClick, modifier, enabled)`：描边次要动作。
- `BankIcon(kind, modifier, tint, contentDescription?)`：银行、对话、卡片、财富、联系人及工具图标。
- `Modifier.bankPressScale(interactionSource, enabled)`：复用实际按钮的 interaction source；不增加点击识别器。

加载、空白和失败状态属于正式页面状态。失败区使用礼貌级 live region，提供与当前操作匹配的重试入口；加载占位从主题取色。

## 动效规范

| 场景 | 新规格 |
| --- | --- |
| 层级页面进入/返回 | 240 ms，进入位移为宽度的 1/12，退出 1/20，叠加淡入；退出淡出 140 ms。 |
| 旧 cubeFlip 调用 | 保留 API，改为 220 ms 淡入和 12 dp 上移；无透视旋转、暗角。 |
| 列表首次展示 | 最多前 6 项，220 ms，项间隔 28 ms，12 dp 上移；超过 450 ms 加载窗口的条目直接展示。 |
| 骨架扫光 | compose-shimmer，1400 ms 扫动 + 250 ms 间隔，12° 倾斜；仅骨架存在时运行。 |
| 骨架转内容 | 180 ms 淡入 + 8 dp 位移。 |
| 按钮按压 | 缩放至 0.975；无过冲弹簧，松手恢复。 |
| 验证焦点 | 180 ms、12% 主题 scrim；去掉全屏模糊。 |
| 预测性返回 | 顶层最多缩小 4%、横移 3%，底层位移最多 6%；取消采用无过冲弹簧。 |

`BankAgentTheme` 通过 `ContentObserver` 监听系统 `ANIMATOR_DURATION_SCALE`。关闭动画时，共享骨架停止扫光、列表不执行延迟入场、按压不缩放、验证层即时切换、预测返回使用普通 `BackHandler`；Compose 自身的有限动画同时遵循系统时长比例。返回预览不重播级联动画。业务请求、授权与导航均不依赖动画完成。

## 验证范围

### Agent 回复卡

正文使用 `AgentMarkdown` 渲染 Markdown，`**金额**` 显示为加粗金额，不直接暴露格式标记。解析保留脱敏卡号 `**** 1234`、算式、代码与转义星号；用户输入及结构化回执值仍按原文呈现。链接只允许用户点击打开 HTTP/HTTPS 地址，图片不在回复中自动下载。

银行文本中 `138****1234` 一类数字相邻的连续遮罩，由 CommonMark 行内扩展保留为字面内容，避免两个手机号的星号互相配对后消失。该扩展随固定版本依赖一起维护，`**100**` 等正常强调不受影响。

`AgentReplyCard` 统一品牌标题、圆角描边、段落行距与留白；结果卡突出返回的主要金额并保留其余字段，长值自动换行。技术性的回复编号移到默认收起的“回复详情”，仍可展开选择复制。浅色、深色、大字号共用主题。

回复卡专项验证：22 项 Markdown 解析回归与既有 4 项单元测试通过；API 35 模拟器上的 3 项回复卡测试通过（格式/遮罩保真、详情与金额、深色 160% 字号长文本）。本地截图位于 `build/reply-review/`，测试素材均为静态演示数据，不调用交易接口。

基础验收应覆盖：Debug 编译与现有单元测试；登录、主导航与业务弹层；亮色/深色；动画比例 1 与 0；加载、空白、失败、忙碌状态；大字体和窄屏下的滚动、输入框、底部导航；手势返回完成与取消。真机或模拟器截图验证与编译记录由整体 UI 集成验证汇总。

## 本次集成验证（2026-09-21）

- `assembleDebug`、`assembleDebugAndroidTest`、`testDebugUnitTest`、`lintDebug` 构建成功；4 项单元测试通过，Lint 为 0 errors / 21 warnings。警告包括依赖升级建议、框架属性兼容提示及 Compose 参数约定。
- 在 API 35 模拟器（1080×2400、420 dpi）分组验证，17 项不同的 instrumentation 场景全部得到通过结果：登录与查余额、黄色/红色审批、反诈、银行卡添加/卡号验证/卡操作、联系人导航与新增搜索/转账预填、理财详情/区间/披露、聊天草稿保留、登录忙碌态和 150% 字号。
- `UiRefactorSmokeTest` 的 2 项流程另在深色模式、`animator_duration_scale=0` 下复测通过。测试后系统模式与动画比例恢复到原值。
- 截图保存于本地 `build/ui-review/light/` 与 `build/ui-review/dark/`，覆盖登录、助手、卡包、理财列表/详情、联系人列表/新增/详情、设备面板。APK 位于 `android/app/build/outputs/apk/debug/app-debug.apk`。
- 交易测试使用独立本地演示数据。银行卡测试会增加卡片，因此审批/反诈测试按既有测试约定使用新的核心服务数据；设备验证使用模拟器已配置的测试凭据。
- 本次未测量帧率，也未完成多机型、平板或预测性返回取消过程的逐帧验收；这些不包含在上述通过结果内。

集成过程中统一了根级消息提示，避免切页重复显示同一通知；审批截止时间保存在稳定页面层并按实际经过时间计算；测评移入可滚动会话区域，输入栏保留固定空间。表单测试使用真实滚动定位控件，保留原有业务断言。

2026-09-22 的后续自然语言验收覆盖六类业务、57 个案例及 142 轮真实界面对话，离线与真实模型分别通过全量加定向复验。回复卡专项 3 项、Android JVM 26 项及后端 598 项回归通过。逐轮实际／预期对照、源码指纹、截图和验收边界见[自然语言验收报告](agent-natural-language-evaluation.md)。
