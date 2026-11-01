# UI 与交互动效设计方案

> 状态：**设计方案（分批实施中；本轮落地情况见第九节）**。本文基于现有 Android 客户端（Jetpack Compose + Material 3）与网关/核心架构编写，旨在将界面质感、AI 原生交互体验与金融级确定性拉齐到同一套可执行规范中。

---

## 一、核心设计哲学与总方向

**安静无边的色调画布 + 经核验的渐进式水墨晕染 + 处处等宽的数字 + 按语义分性格的克制动效。**

1. **对话优先，内联生成式 UI 为核（Chat-First & Generative UI）**
   - 本产品的灵魂是“言出即办”。聊天流与内联交互卡片（`AgentReplyCard`、`ApprovalCard`、内联财富/账单/测评卡）是第一工作台；二级面板（卡包、财富列表、联系人、任务/设备）定位于**辅助浏览与快捷预填器**（选好参数后预填话术回到聊天流，统一走核心审批链路），绝不把二级页面堆砌成传统手机银行的多级表单迷宫。
2. **金融级确定性高于视觉炫技（Financial Determinism）**
   - 任何时候都不允许为了动画效果牺牲数据真实性与防幻觉边界：数字变化全程等宽不抖动、AI 回复必须先完成服务端金额锚点绑定与独立核验再在端侧渐进呈现、骨架屏与真实内容等高零跳变、表单报错常驻不自动消失。
3. **动效克制换取信任感（Semantic & Restrained Motion）**
   - 银行产品的气质是“稳”。全面拒绝弹跳（Bounce）、3D 翻转、粒子彩带、飞入飞出与大面积实时高斯模糊；所有动效按交互语义分配物理性格，且严格受 `LocalBankMotionEnabled`（系统动画开关与自动化测试门控）统一管辖。

---

## 二、全局设计底座与视觉规范

### 2.1 等宽数字全覆盖（P0 · 正确性基线）

- **问题现状**：[`Theme.kt`](../android/app/src/main/java/com/aibanking/agent/ui/theme/Theme.kt) 中仅 `displayLarge/Medium/Small` 与 `headlineSmall` 配置了 `fontFeatureSettings = "tnum"`；正文、标签、表格、倒计时及数字滚动组件（`CountUpText`）未強制等宽，导致余额刷新、倒计时跳秒、滑块调节和列表对齐时发生横向抖动。
- **落地方案**：
  1. 在 [`Theme.kt`](../android/app/src/main/java/com/aibanking/agent/ui/theme/Theme.kt) 中为所有可能承载金额、百分比、净值、日期、倒计时的文本样式默认开启 `fontFeatureSettings = "tnum"`，并提供显式的 `TextStyle.tabular()` 扩展函数；
  2. 卡号、交易流水号、追踪号（`traceId`）、绑定码统一使用 `FontFamily.Monospace` + `tnum`；
  3. 计数过渡组件（`CountUpText`）强制合并 `tnum` 特性，确保数值插值全程零横向位移。

### 2.2 微对比色阶分层与卡片密度收敛（P2）

- **去装饰性描边**：维持 Gemini 系色调分层（`surfaceContainerLowest` → `surfaceContainerHighest`），不画装饰性边框。仅保留两类功能性线条：
  1. 输入框聚焦/失焦描边（交互态提示）；
  2. 密集回执单、账单明细表的内部细分隔线（信息分组边界）。
- **暗色模式色阶与微氛围校准**：
  - 1%~3% 的微对比在 OLED 深色底上容易糊成一片，暗色模式下适当拉开 `surfaceContainer` 与 `background`（`#131314`）的明度步进；
  - 品牌、问候语、卡片和后台使用纯色，不使用蓝紫渐变；暗色下提升纯色品牌标记的明度，关键金额提亮一档。次要说明通过透明度和对比度区分。
- **卡片密度审计（消除“卡中卡”冗余）**：
  - 区分**信息卡**（承载一组独立业务数据与操作，保留圆角卡容器）与**装饰卡**（仅为排版而套的卡）。
  - 审计并收敛超过两层的嵌套卡片（例如 [`WealthDetailScreen.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/WealthDetailScreen.kt) 中“风格特征”大卡内嵌四张小卡、[`ContactScreens.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ContactScreens.kt) 列表每行独立套厚卡），优先改用卡内色块分区或行式列表 + 细分隔线，降低长列表滚动时的视觉噪点。

### 2.3 动效性格矩阵与物理规则（P1）

借鉴 Material 3 Expressive `MotionScheme` 与主流金融 App 动效规范，按交互语义将全端动效收敛为四档性格：

| 交互语义 | 动效性格 | 物理/曲线约束 | 典型场景 |
| --- | --- | --- | --- |
| **手势跟随**（拖拽、侧滑、预测返回） | 1:1 实时跟手，零延迟、零过冲 | 手指在屏时实时映射位移；**过阈值才决断提交，未达阈值平滑回位（无过冲回弹）** | 预测返回预览、Bottom Sheet 拖拽、列表左滑操作、滑块调节 |
| **资金与事实**（金额、净值、审批摘要） | 克制精确，零弹性、零娱乐化 | `FastOutSlowIn` 或无回弹临界阻尼（`DampingRatioNoBouncy`）；审批卡进场仅淡入 | 金额 `CountUpText`、净值曲线过渡、审批卡与回执面板展示 |
| **状态确认**（办理成功、操作完成） | 单次有节制的收束表达 | 路径描线（Path Trim）或单次收束，不循环闪烁 | 结果页对勾徽章（`OutcomeBadge`）、主按钮完成态 |
| **微交互**（按钮按压、芯片选中、开关） | 瞬时响应，点到为止 | 按压缩放 `0.975f`（`bankPressScale`），时长 160–220ms | 按钮点击、FilterChip 切换、详情折叠展开 |

**两条全局硬性纪律**：
- **唯一动效主体**：一次用户交互只允许一个视觉主体作出核心动效反馈，触发点与反馈位置一一对应；
- **单屏并发上限**：同一屏幕同一时刻正在进行的非手势动画不超过 2 个，防止视觉干扰。

### 2.4 统一触觉反馈词汇表（P2）

全端收敛为三种克制的系统级触觉反馈（`HapticFeedbackConstants`），不做连续或花式震动：
1. **刻度/确认轻点**（`CLOCK_TICK` / `CONTEXT_CLICK`）：三列日期滚轮逐格滚过、整百刻度滑块吸附、风险测评点选选项；
2. **办理成功轻震**（`CONFIRM`）：审批通过、保存联系人成功、语音按下开始录音；
3. **阻断/失败重震**（`REJECT`）：红区验证失败、表单提交校验未通过、触发安全熔断拦截。

---

## 三、核心对话流与生成式 UI（Chat Stream & Generative UI）

### 3.1 服务端核验后 · 客户端渐进式水墨晕染（P0 · 核心体验升级）

- **背景与架构红线**：
  - 当前回复体验是“阶段进度跳动 → 整段文字瞬间蹦出”，缺少 AI 智能体“娓娓道来”的呼吸感。
  - 但后端 [`bank_agent/answering.py`](../bank_agent/answering.py) 有一条不可妥协的安全红线：**模型生成的草稿含 `{{value.N}}` 金额锚点，必须先通过服务端确定性校验（`_validate_draft`）与独立模型语义核验（`_review`），未通过核验的草稿绝不下发**。因此，绝不能改成后端 LLM 未经核验直接推流 token。
- **混合式落地方案（Client-side Progressive Reveal）**：
  1. **思考阶段**：继续通过 SSE 实时接收并展示 `StageProgress` 时间线，保持低感知延迟；
  2. **文字水墨晕染**：服务端完成核验并下发最终安全文本后，客户端在 [`AgentReplyCard.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/AgentReplyCard.kt) 中对最新到达的叙事段落做**客户端渐进式晕染展开**（按句/按行分批柔和淡入 + 微上移 `4.dp`，总展开时长控制在 300–450ms，避免半解析 Markdown 引发的表格/标题重排抖动）；
  3. **事实面板整块入场**：金额卡片、流水表、审批卡（`ApprovalCard`）、工作流进度面板不参与逐句流式，在文字晕染收尾时作为确定性整体平稳淡入；
  4. **无障碍与测试门控**：用户点击回复区域可立即跳过晕染显示全文；当 `LocalBankMotionEnabled == false`（含 UI 自动化测试环境）或属于历史恢复消息（`EntranceTracker` 已见）时，直接 `snap` 全量渲染，零延迟。

### 3.2 对话内联生成式 UI 卡片丰富化（P1）

避免用户频繁跳出聊天去二级页面，把高表现力的金融组件直接内联进对话回复卡中：
1. **内联风险测评结果仪表盘**：答完 8 道风险测评题后，在对话流生成的测评结果卡中增设**半圆风险仪表盘**——刻度分三段色（保守/稳健/进取），指针从左侧一次性平滑扫入落位（无回弹過冲），配合 `tnum` 等级分值与匹配产品芯片。
2. **内联资产/账单分段占比条**：在“查持仓”“上月账单分析”的内联回复卡中，增设**水平分段占比条（Segmented Bar）**，点击某一段可轻微抽出并高亮显示该类别/产品的名称、金额与百分比。

### 3.3 输入区联动、指令联想与语音条融合（P2）

1. **键盘与消息流同步动画加固**：审计 [`ConversationComposer.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ConversationComposer.kt) 与 [`ChatScreen.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ChatScreen.kt) 的 `imePadding` / `navigationBarsPadding` 组合，确保软键盘弹起与收起时，输入框与底部消息列表 1:1 同步平滑推移，消除导航栏内边叠加导致的瞬间跳变。
2. **输入实时联想浮层**：当用户在输入框键入触发词（如“转账给”“查”“申购”）时，在输入框上方轻量浮出匹配当前账户的**联系人姓名胶囊**或**高频指令补全项**，点选即刻补全槽位，比写死静态推荐更贴合即时意图。
3. **长按语音条形态融合**：在现有长按录音、脉冲环与上滑取消基础上，按住说话时让输入栏容器自然横向延展融合为实时音量波形条，松手平滑复原并填入识别文字。

### 3.4 多会话管理：保留对话首页，收进侧边抽屉（P1）

- **纠正与定位**：代码中的 [`ConversationList.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ConversationList.kt) 是单次对话内的消息列表，绝不能把它改成微信式的“会话列表首页”（打开 App 必须直接进入当前活跃对话）。
- **落地方案**：配合多会话管理功能，在**侧边导航抽屉（`BankNavigationDrawer`）**上半部分增设“历史会话”分区（或顶栏左侧会话切换弹层）：
  - 支持点击放大镜图标原地展开会话搜索框；
  - 支持会话条目**左滑删除 + 底部 5 秒【撤销】Snackbar**；
  - 支持长按弹出锚定菜单（置顶会话 / 重命名 / 删除）。

---

## 四、三级权限的反馈与确认摩擦阶梯

将“操作可逆性”与本系统的**绿 / 黄 / 红三级权限**严格对齐，建立清晰的交互摩擦阶梯（Friction Ladder），既防误触，又绝不制造多余的确认疲劳：

| 风险/可逆性分级 | 交互摩擦形态 | 具体规范与落点 |
| --- | --- | --- |
| **联系人删除** | **先弹窗确认，再提交服务器** | 弹窗展示姓名，取消或返回不发删除请求；确认后立即调用删除接口，以成功回执或服务器查询结果更新列表。不使用删除撤销 Snackbar。 |
| **其他可逆管理操作**（低风险、可后悔） | **零前置弹窗 → 直接执行 + 5 秒【撤销】Snackbar** | 适用于：删除历史会话、清空草稿。操作后界面立即乐观更新，底部淡入上移 `8.dp` 弹出带【撤销】按钮的 Snackbar，5 秒内未撤销才真正落盘/调接口。 |
| **绿区 · 只读查询** | **零确认，自动执行** | 查余额、流水、账单分析、产品咨询，结果经核验后直接水墨晕染呈现。 |
| **黄区 · 日常资金/变更确认** | **内联确认卡（`ApprovalCard`）一键确认** | **维持现状**：展示服务端完整摘要（`tnum` 金额）+ 剩余有效期进度条 + 屏幕边缘黄色呼吸光（`RiskGlow`），用户核对后**点一次“确认本次操作”即可提交**。<br>⚠️ **明确不在日常黄区卡上加“逐条勾选”或“滑动确认”**，保持“一句话 + 一次点击完成转账”的极简体验。 |
| **黄区 · 低频协议/首次开通确认** | **逐条勾选联动（进度底色前推）** | 仅用于**首次申请虚拟卡、高风险产品首次风险揭示**等低频高仪式感场景：列出 2~3 条核心条款，每勾选一条，卡片背景色按比例平滑前推填充；全勾后整卡轻微提亮并激活提交按钮。 |
| **红区 · 高风险不可逆操作** | **阻断式强验证 + 短信通知一键填入 + 生物识别签名** | 1. **防误关阻断**：红区验证期间，模态层禁止通过点击背景蒙版或边缘返回手势意外关闭，必须显式点击确认或取消；<br>2. **OTP 填入体验**：配合本地通知模拟短信，收到验证码通知时支持点击“一键填入”直达 [`ApprovalCard.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ApprovalCard.kt) 的 6 位验证码框；<br>3. **硬件签名**：调起系统 `BiometricPrompt` 完成 Keystore P-256 签名。 |

### 4.1 表单校验错误常驻规范（P1）

- **常驻不闪退**：所有输入框校验错误（如金额超限、卡号 Luhn 校验失败、手机号格式错误、必填项缺失）必须以输入框下方的常驻红字（Inline Alert，`AnimatedVisibility` 展开）呈现，**直到用户修改对应字段或重新提交时才清除**。
- **职责分离**：Toast 与 Snackbar 仅用于承载全局通知或可撤销提示，**严禁用于承载表单校验错误**。
- **可修复文案与无障碍**：报错文案必须指明修复动作（例如“起购金额为 1,000 元，请调整申购金额”而非笼统的“输入无效”），并标记 `LiveRegionMode.Polite` 供无障碍读屏播报。

### 4.2 主按钮三态化（P2）

关键提交按钮（[`BankPrimaryButton`](../android/app/src/main/java/com/aibanking/agent/ui/components/BankMaterialComponents.kt)）统一支持完整三态流转：
- **空闲态** → **加载态**（内嵌克制的加载指示器 + 文案切换为“正在提交…”，禁用重复点击，保留幂等键）→ **结果反馈态**（成功时对勾路径收束 + 轻震，随后进入结果页或复位；失败时恢复可点并保留用户已填内容）。

---

## 五、垂直业务面板与高频控件精修

### 5.1 联系人面板（[`ContactScreens.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ContactScreens.kt)）

1. **生日字段改为底部三列惯性滚轮（P1）**
   - **现状问题**：添加联系人表单中，生日字段为普通 `OutlinedTextField`（占位 `YYYY-MM-DD`，甚至使用了 `KeyboardType.Text` 全键盘手打），极易输错格式。
   - **改造方案**：将生日输入框改为只读点击触发器，点击弹出**底部三列滚轮选择器（年 / 月 / 日）**：
     - 范围约束：`1900-01-01` 至今日（禁止选择未来日期）；
     - 联动逻辑：日列随年（闰年 2 月 29 天）、月（大小月 30/31 天）实时联动收敛；
     - 物理手感：惯性滚动 + 松手磁吸对齐（SnapFlingBehavior）+ 滚过每格触发微触觉（`CLOCK_TICK`）；
     - 确认后回填标准 `YYYY-MM-DD` 文本，且完整支持 TalkBack 逐项播报与无动画门控。
2. **联系人列表 → 详情页共享元素转场（P1）**
   - 利用当前 Compose 版本已稳定的官方 `SharedTransitionLayout`，恢复联系人列表行点击进入详情页时**圆形姓名头像与卡片容器的连续缩放过渡**（无过冲、无弹跳，时长 240ms），并与预测返回手势（`PredictiveBackPreview`）平滑衔接。

### 5.2 财富面板（[`WealthScreens.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/WealthScreens.kt) / [`WealthDetailScreen.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/WealthDetailScreen.kt)）

1. **净值走势图范围切换平滑形变（P1）**
   - **现状基础**：[`WealthDetailScreen.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/WealthDetailScreen.kt) 已具备“近1月 / 近3月 / 近6月 / 近1年 / 成立以来”切换芯片与横向触摸查值。
   - **升级点**：在 [`WealthChart.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/WealthChart.kt) 中，当用户切换时间区间时，对重采样后的曲线控制点与下方渐变填充面积做平滑插值过渡（而非生硬闪切重绘）；首次打开详情页时曲线做一次性从左至右的描线入场（Path Trim）。
2. **申购底栏（`PurchaseBar`）三轨调节与费用实时联动（P2）**
   - **架构定位**：[`ChatWealth.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/ChatWealth.kt) 的 `startPurchase` 是将“申购 ProductId 金额”预填回聊天框并由核心发起红区审批。因此 `PurchaseBar` 的职责是**高效、专业的金额预填工作台**，不在此处重复做审批弹窗。
   - **升级点**：
     - **三轨金额调节**：在保留精确键盘输入的基础上，上方增设**快捷整百/整千刻度滑块（或快捷金额胶囊）+ 左右 `±100` 步进微调按钮**；
     - **实时数据联动面板**：随金额输入/拖动，上方实时计算并联动显示“起购差额提示 / 预估申购费 / 预计确认日期”（全部配 `tnum` 等宽数字，数值变化平稳过渡）。

### 5.3 卡包与模态弹层容器（[`CardsScreen.kt`](../android/app/src/main/java/com/aibanking/agent/ui/chat/CardsScreen.kt) / [`BankBottomSheet`](../android/app/src/main/java/com/aibanking/agent/ui/components/BankReferenceComponents.kt)）

1. **卡包叠卡预览与横滑切换（P2）**
   - 持有多张银行卡时，顶部卡面区采用**叠卡露边预览 + `HorizontalPager` 横滑切换主卡**（切换动效为纯位移 + 淡切，当前选中卡提亮、相邻卡轻微缩小 `0.95f`，不做 3D 旋转翻转），下方限额、渠道开关与操作面板随当前选中卡实时联动刷新。
2. **档位式 Bottom Sheet 与模态单层纪律（P2）**
   - **现状问题**：当前 `BankBottomSheet` 调用处普遍开启了 `skipPartiallyExpanded = true`，点开一律顶到全屏。
   - **改造方案**：任务中心、设备管理等承载列表内容的底部弹层支持**半屏常驻浏览（~55%）与上滑全屏两档吸附**，显式保留顶部拖拽把手（Drag Handle）并确保 1:1 跟手；同时严格执行**“模态不套叠”纪律**——同一时刻最多存在一个模态层，弹层内部的二次操作采用弹层内视图状态切换（`AnimatedContent`），绝不在 Bottom Sheet 之上再叠 AlertDialog。

---

## 六、明确拒绝的“反廉价”负向清单（Anti-Patterns）

在后续人工开发或使用 AI 生成新组件时，以下模式一律视为违规并直接过滤：

1. **禁止娱乐化与炫技动效**：禁止弹簧过冲回弹（Bouncy Spring）、禁止 3D 卡片翻转（Cube/Card Flip）、禁止粒子爆散与彩带（Konfetti）、禁止元素沿曲线飞入飞出。
2. **禁止高开销模糊与赛博风**：禁止实时背景毛玻璃（`RenderEffect.createBlurEffect`）与快速滚动运动模糊（避免低端机掉帧与渲染伪影，蒙版统一使用压暗 `scrim`）；暗色模式保持深灰安静画布，禁止高饱和霓虹发光边框。
3. **禁止生搬 Web 控件**：禁止在移动端使用原生下拉框（Select Dropdown）选少量固定项（应使用 `FilterChip` 或分段控件）；禁止用纯文本框手敲结构化日期（应使用三列滚轮）。
4. **禁止重资产动效管线**：不引入 Lottie / Rive 外部二进制动画资产管线，所有状态图形（对勾、风险环、脉冲、图表）统一用 Compose `Canvas` / `Path` 原生绘制，确保包体积轻量、主题色动态响应且受 `LocalBankMotionEnabled` 控制。

---

## 七、落地优先级与验收路线图

### P0：基石与核心体验（最先实施，全局收益）
| 事项 | 涉及模块/文件 | 验收标准 |
| --- | --- | --- |
| **1. 全局 `tnum` 等宽数字补全** | `Theme.kt`, `ChatMotion.kt`, 各业务卡片 | 所有金额、余额、净值、百分比、倒计时样式均含 `tnum`；`CountUpText` 动画及数值刷新全程零横向抖动 |
| **2. 客户端渐进式水墨晕染回复** | `AgentReplyCard.kt`, `AgentMarkdown.kt`, `ChatMotion.kt` | 严守服务端核验后下发；叙事文字按句/段 300–450ms 柔和淡入，数据面板整体入场；点击可瞬时全显；关闭动画或测试环境下 `snap` 直出 |

### P1：高频交互与原生组件（显著提升专业金融感）
| 事项 | 涉及模块/文件 | 验收标准 |
| --- | --- | --- |
| **3. 联系人生日三列惯性滚轮** | `ContactScreens.kt` + 新增 `BankDateWheelPicker` | 点击生日弹出年/月/日三列滚轮；范围 1900～今天，闰年与大小月联动正确；具备松手吸附、逐格轻触觉与无障碍播报 |
| **4. 内联风险仪表盘与净值图平滑形变** | `WealthChart.kt`, `AgentReplyCard.kt` / 内联财富卡 | 净值图切换“近1月/3月/1年”时曲线平滑插值过渡，首次进入描线入场；风险测评结果内联展示指针扫入式仪表盘 |
| **5. 列表 → 详情共享元素转场** | `ContactScreens.kt`, `WealthScreens.kt` | 基于 `SharedTransitionLayout` 实现联系人头像/卡片、理财产品卡进入详情时的连续缩放过渡，无过冲，与预测返回兼容 |
| **6. 表单错误常驻与联系人删除确认** | 全局表单页, `ContactScreens.kt`, `ChatScreen.kt` | 表单报错常驻显示直至修改输入；联系人删除通过弹窗确认后立即提交服务器，核对成功后更新列表 |
| **7. 侧边抽屉多会话列表交互** | `ChatScreen.kt`（抽屉内，配合多会话功能） | 保持打开 App 即对话首页；抽屉内支持会话原地搜索展开、左滑删除带撤销、长按置顶 |

### P2：面板打磨与反馈闭环（稳步推进）
| 事项 | 涉及模块/文件 | 验收标准 |
| --- | --- | --- |
| **8. 申购底栏三轨输入与费用实时联动** | `WealthDetailScreen.kt` (`PurchaseBar`) | 支持整百刻度滑块 + `±` 步进器 + 键盘输入；预估手续费与起购差额实时联动（`tnum`），点击申购预填回聊天流 |
| **9. 卡包叠卡横滑与档位式 Bottom Sheet** | `CardsScreen.kt`, `BankReferenceComponents.kt` | 多卡露边叠放并支持 `HorizontalPager` 横滑切卡；任务/设备 Bottom Sheet 支持半屏与全屏两档吸附，模态层不套叠 |
| **10. 键盘同步动画加固与输入联想浮层** | `ConversationComposer.kt`, `ChatScreen.kt` | 键盘弹收与消息列表同步无跳变；输入“转账给…”时浮出联系人与常用指令补全芯片 |
| **11. 主按钮三态化与统一触觉词汇表** | `BankMaterialComponents.kt`, 全局操作点 | 提交按钮具备空闲/加载/完成三态；成功轻震、失败重震、刻度轻点全端统一 |
| **12. 色阶暗色精修与卡片密度收敛** | `Theme.kt`, `ContactScreens.kt`, `WealthDetailScreen.kt` | 暗色模式容器色阶层次清晰；消除超过两层的“卡中卡”冗余嵌套 |

---

## 八、附录：AI 动效描述模板表（供生成新组件时引用）

当使用 AI 辅助编写新的 Compose 界面或动效时，直接引用下表约束，确保生成代码与现有动效体系同频：

| 动效名称 | 触发条件 | 视觉表现（大白话描述） | Compose 实现映射与物理参数 |
| --- | --- | --- | --- |
| **级联入场** | 列表/面板首次展示 | 条目依次从下方 `8dp` 处淡入上移落位，错峰间隔 `28ms`，单段 `220ms`，只播一次 | `Modifier.cascadeItem()` / `rememberCascadeEntrance`，`EmphasizedDecelerateEasing` |
| **水墨晕染展开** | 已核验的新回复到达 | 段落按序柔和浮现并微上移 `4dp`，像墨迹自然洇开；底部结构化卡片随后整块淡入 | `AgentReplyCard` 分段 `Animatable` alpha + translationY（300–450ms），受 `LocalBankMotionEnabled` 门控 |
| **按压微缩放** | 手指按下可点击控件 | 按住瞬间整体均匀缩小至 `97.5%`，松手立即无弹跳恢复原状 | `Modifier.bankPressScale()`，`spring(dampingRatio = DampingRatioNoBouncy, stiffness = StiffnessMedium)` |
| **横向页面推入** | 面板内层级前进/后退 | 新页面从右侧 `12%` 宽度处滑入并淡入，旧页面向左侧退出，`240ms` | `pageSlideSpec()` (`PageTransitions.kt`)，无旋转、无缩放过冲 |
| **边缘风险呼吸光** | 黄区/红区审批待决期间 | 光效从屏幕左右两侧中部向上下延伸合拢，随后在 `55%~100%` 透明度间缓慢呼吸，审批结束后淡出 | `Modifier.riskEdgeGlow(riskSignal)` (`RiskGlow.kt`) |
| **滚轮/滑块磁吸** | 手指拨动滚轮或刻度滑块 | 手指拖动时 1:1 实时跟手；经过刻度点触发一次轻点触觉；松手后按惯性滑行并平滑吸附到最近刻度，无回弹 | `LazyColumn` + `rememberSnapFlingBehavior` / `Slider` steps + `HapticFeedbackConstants.CLOCK_TICK` |
| **折叠详情展开** | 点击“回复详情 / 展开更多” | 容器高度随内容平滑撑开（`220ms`），内部文字延迟淡入（`160ms`），骨架与真实内容等高不跳动 | `AnimatedVisibility` (`fadeIn(160)` + `expandVertically(220)`) / `Modifier.animateContentSize()` |


## 九、交互闭环实施记录（2026-10-26）

本轮承接代码核对后的六项改进，沿用现有 Compose、会话隔离和 Core 授权边界：

| 项目 | 已实现行为 |
| --- | --- |
| 起购金额与步进 | 使用产品实际起购金额；1 元产品显示 1 元，不把快捷步长当起购门槛；金额与费用估算使用十进制计算 |
| 联系人删除确认 | 弹窗核对姓名，取消不变更；确认后立即删除服务器通讯录记录，成功或只读核对后更新列表。页面重建保留确认对象，重复确认最多提交一次；重启或重新登录后按服务器记录展示 |
| 提示条生命周期 | 状态消费与展示解耦；普通通知顺序展示；切换账号清理旧队列。联系人删除不再使用撤销提示 |
| 表单错误常驻 | 联系人、银行卡和审批保留服务端错误；金额、卡号、姓名、电话、验证码和处理原因提供就地反馈；编辑对应表单或重新提交后清除，失败保留输入 |
| 任务单层面板 | 人工处理确认在同一个 Bottom Sheet 内切换；系统返回先退到任务列表，提交失败保留原因，提交中阻止误关 |
| 卡片密度收敛 | 财富风格指标改为分组行与分隔线，保留名称、数值、说明和得分；大字体模式名称与数值上下排列；联系人列表改为轻量行式容器 |

验收结果及覆盖范围见 [验证记录](验证记录.md)。共享元素、多会话及其他尚未实施项继续按第七节路线图评估。
