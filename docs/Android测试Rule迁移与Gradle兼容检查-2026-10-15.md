# Android Compose 测试 Rule 迁移与 Gradle 兼容检查（2026-10-15）

## 修改

- 18 个测试文件的 21 处 Compose Rule 工厂调用改用 `androidx.compose.ui.test.junit4.v2`，保留默认 `StandardTestDispatcher`，不回退到 Unconfined、不抑制弃用警告。
- `ComposeContentTestRule` 是 v1/v2 共用且未弃用的接口，仍从 `junit4` 导入。已有 `waitForIdle`、语义操作与 `waitUntil` 分别处理 Compose 队列和真实网络/Keystore 等异步工作。
- Kotlin Gradle Plugin 与 Compose compiler 从 2.2.10 同步升级到稳定版 2.4.21。按 AGP 官方方式在根 `buildscript` 中声明 KGP runtime dependency，保留 AGP 内置 Kotlin，不重新应用 `kotlin-android`。
- 清理新编译器报告的多余 `Unit` 表达式，将风险评估原始 JSON 对象保留为可实际校验的 `Map<*, *>`，移除不安全的泛型转型；密钥删除仍在原锁内完成。
- API 35 回归暴露账号隔离、结果页等专用登录函数未等待 IME 收起的问题（首轮 5 条登录步骤超时）；将所有测试的登录提交统一到共享 `submitLoginForm`，关闭键盘、等待空闲并断言按钮可见/可用，避免系统输入法 inset 动画移动按钮造成漏点，不添加重试或放宽超时。
- 将银行卡操作区及待办/风险问卷生命周期用例加入设备回归标记，覆盖 v2 下的点击状态更新、任务取消与 Activity 重建后的问卷恢复。
- 设备回归复现 AGP 9.4.1 主机端监听器 `ArrayList.add` 并发异常（b/565986136）。通过 Gradle 公共依赖解析 API 将 `com.android.tools.androidtest` 的 1.0.1 请求统一升级至官方稳定补丁 1.0.8，包含测试引擎、监听器和两种结果协议。限制匹配旧版本，不覆盖未来更高版本；不改变设备端 AndroidX Test 依赖。首次旧引擎回归 XML 为 65 条通过，但底层设备与汇总 protobuf 实际仅 64 条，且控制台仍为构建成功。实际检查 1.0.8 监听器已使用 `ConcurrentLinkedQueue`，最终回归同时核对 XML / protobuf 数量及收集异常。
- CI 使用 `--warning-mode=all` 展示具体弃用项与移除版本，不隐藏已知上游问题。
- AGP 9.4.1、Gradle 9.8.1、JDK 17、compile/target SDK 37、Build Tools 36.0.0 和其余依赖保持不变。

## Gradle 警告来源与限制

对配置阶段执行 `help --warning-mode=all -Dorg.gradle.deprecation.trace=true`，升级前记录到 60 次 `Configuration.setVisible(boolean)` 调用，其中 9 次来自 Kotlin 插件；升级后为 51 次，全部来自 AGP 内部，Kotlin 调用已消失。调用数来自完整堆栈，不等同于 Gradle 控制台去重后的警告种类数。

剩余问题唯一类型为 `Configuration.setVisible(boolean)`，Gradle 明确注明 **Gradle 11 移除**。堆栈位置包括 AGP 的 `BasePlugin`、`SourceSetManager`、`VariantDependenciesBuilder`、`TaskManager` 与 UTP 配置。项目构建脚本没有该调用。

截至本次检查，官方 Google Maven 最新稳定 AGP 仍为 9.4.1；9.5 仅发布到 alpha09。上游 issue 560282299 在 alpha06/07 记录过修复，但最新反馈仍报告 alpha09 复现，因此本次保留稳定 AGP，未更换预览工具链、降级 Gradle、修改插件二进制或隐藏警告。后续需要上游发布包含完整修复的稳定 AGP。

Kotlin 官方兼容矩阵对 2.4.20–2.4.21 的完整测试范围为 Gradle 至 9.7.0、AGP 至 9.3.1，也说明允许使用最新版本但可能仍有兼容警告。本项目沿用现有 AGP 9.4.1 / Gradle 9.8.1，并通过下述本地构建和设备回归验证。**未运行尚未采用的 Gradle 10，不能将未发现 Gradle 10 移除项表述为已验证 Gradle 10。**

## 验证

- `testDebugUnitTest`：87 条通过，0 失败、0 错误、0 跳过。
- `lintDebug`：0 问题；新编译器产生的两处源码警告已清理，v1 Rule 弃用提示已消失。
- Debug、Debug 测试 APK、Release（R8/资源压缩）构建通过；Release APK 的 16 KB ZIP 对齐及 4 个 64 位 ELF 库 LOAD 段对齐通过。
- API 37 / 16 KB：65 条通过，0 失败、0 错误、0 跳过。XML、设备 protobuf、汇总 protobuf 均为 65 条，protobuf 中每条状态均为 PASSED；控制台无结果收集异常。
- API 37 真实系统局域网权限流程：1 条通过，0 失败、0 错误、0 跳过；XML 与两份 protobuf 数量均为 1，覆盖拒绝、重试授权和真实 HTTP 登录。
- API 35：65 条通过，0 失败、0 错误、0 跳过；XML 与两份 protobuf 均为 65 条，包含首轮登录步骤超时的 5 条用例。

上述设备矩阵已在共享登录提交函数修复后全部重新执行，未用单项重试替代完整回归。

65 条用例涵盖 16 个测试类，其中账号隔离 13 条、卡片敏感信息生命周期 3 条、待办与风险问卷生命周期 4 条、银行卡操作区 1 条；其余覆盖结果页、文本布局、权限门禁、图标/备份策略及真实 OCR。场景后端离线运行在独立 loopback 端口，不访问正式银行或飞书服务。

常规构建检查（在 `android` 目录）：

```powershell
./gradlew.bat --warning-mode=all :app:testDebugUnitTest :app:lintDebug :app:assembleDebug :app:assembleDebugAndroidTest :app:assembleRelease
```

设备回归使用 `bankScenarioTests=true` 和 AndroidJUnitRunner 的 `annotation=com.aibanking.agent.BankingRegression` 参数，并传入临时 `controlToken` 与专用测试模拟器的 `devicePin`。PowerShell 中应将完整的 `-Pandroid.testInstrumentationRunnerArguments.*=...` 参数放入引号，避免点号被解析为额外任务；不使用逗号拼接多个测试类。

Gradle `dependencyInsight --configuration unified-test-platform-gradle-work-action --dependency com.android.tools.androidtest` 确认引擎、监听器和结果协议四个组件均解析为 1.0.8。

下载仍遇到本机 Java TLS/大文件传输故障，使用临时 loopback 缓存逐字节转发官方 Maven；新 KGP 与编译器大文件另用分段下载并与官方 SHA1 校验。此缓存、控制令牌、日志、测试数据和构建产物均不入库，仓库与 CI 仍使用官方仓库。

## 参考

- [Compose v2 测试迁移指南](https://developer.android.com/develop/ui/compose/testing/migrate-v2)
- [AGP 官方 KGP 升级方式](https://developer.android.com/build/releases/agp-9-0-0-release-notes#upgrade-to-a-higher-kgp-version)
- [Kotlin / Gradle / AGP 兼容矩阵](https://kotlinlang.org/docs/gradle-configure-project.html)
- [测试结果并发收集修复 b/565986136](https://android.googlesource.com/platform/tools/base/+/8fa11269c6fffad7772fb7a1e6f6e9faed3f234c)
- [AGP setVisible 上游问题 560282299](https://issuetracker.google.com/issues/560282299)
- [Gradle setVisible 弃用说明](https://docs.gradle.org/9.8.1/userguide/upgrading_version_9.html#deprecate-visible-property)
