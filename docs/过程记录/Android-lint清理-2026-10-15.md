# Android lint 清理（2026-10-15）

基线为 `e849f66` 的 `lintDebug`：19 条 Warning、1 条 Information、0 条 Error。保留既有界面、业务流程和设计素材，未添加全局忽略项或 lint baseline。

## 逐项处理

| 类型 | 原数量 | 处理 |
| --- | ---: | --- |
| `DataExtractionRules` | 1 | 保留 `allowBackup=false`，显式关闭旧系统 fullBackupContent，为新系统云备份和设备迁移分别排除全部 9 个存储域，避免迁移复制本机会话和设备登记数据 |
| `UnusedAttribute` | 1 | 保留预测性返回开关；用 `tools:targetApi=33` 及注释声明该 Manifest 配置有意仅在支持的系统生效，不提高最低 SDK |
| `RedundantLabel` | 1 | Activity 继承应用名称，移除相同的重复 label |
| `GradleDependency` | 4 | AndroidX Test 同步为 Runner 1.7.0、Espresso 3.7.0、ext-JUnit 1.3.0；仅更新测试依赖 |
| `ModifierParameter` | 1 | `BankScreenHeading` 的 modifier 前移到首个可选参数，唯一位置调用改用命名参数 |
| `ObsoleteSdkInt`、`MonochromeLauncherIcon` | 2 | 将启动器三层合入 `mipmap-anydpi/ic_launcher.xml`，移除 v26/v33 重复变体，保留现有图形与配色 |
| `UnusedResources` | 9 | 确认全仓无静态或动态引用后，移除旧字符串，保留当前 app_name；不修改可见文案 |
| `AutoboxingStateCreation`（Information） | 1 | 理财图表选中索引改用 `mutableIntStateOf` |

## 策略与兼容性依据

Android 12 起，部分设备的设备间迁移不完全受 `allowBackup=false` 限制，因此增加显式规则。两种通道均排除 `root/file/database/sharedpref/external/device_root/device_file/device_database/device_sharedpref` 的 `.` 路径；不能仅排除根域，因为框架还会分别遍历文件、数据库和偏好设置域。[Android 备份说明](https://developer.android.com/identity/data/autobackup)、[Android 12 BackupAgent 源码](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-12.0.0_r1/core/java/android/app/backup/BackupAgent.java)

本项目最低 API 为 26；Android 8 的自适应图标解析器忽略未知子标签，旧系统仍读取原前景与背景，Android 13+ 读取单色层。官方图标示例也将三层放在同一文件中。[官方图标说明](https://developer.android.com/develop/ui/compose/system/icon_design_adaptive)、[Android 8 图标解析源码](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-8.0.0_r1/graphics/java/android/graphics/drawable/AdaptiveIconDrawable.java)

测试依赖采用同批稳定发布；已核对 Maven 元数据和 AAR，最低 API 21，未引入额外 compileSdk 门槛，兼容当前 minSdk 26／compileSdk 35。生产依赖、AGP、Gradle 与 Kotlin 版本保持原值。[AndroidX Test 发布记录](https://developer.android.com/jetpack/androidx/releases/test)

新增 `BackupPolicyTest` 从已安装应用的 Manifest 读取实际规则引用，并检查两个通道完整排除存储域。它验证打包策略，不宣称覆盖各厂商的迁移实现。

## 联网刷新后出现的版本提示

本次下载测试依赖时刷新了版本元数据，lint 额外报告了 6 个生产／构建依赖的最新版提示，在 3 个构建变体中重复为 18 条。它们与基线的 19 条告警不同；保留提示，不添加忽略项。已检查官方 AAR 的 `aar-metadata.properties` 与发布说明，直接替换会超出现有 compileSdk 35／AGP 8.7.3／Gradle 8.9 的兼容范围：

| 建议版本 | 核查结果与处理 |
| --- | --- |
| AGP 9.4.1 | 要求 Gradle 9.6.0，且涉及 AGP 9 内置 Kotlin 迁移，保留当前配套版本 |
| Core KTX 1.19.1 | AAR 要求 compileSdk 37、AGP 9.1.0 |
| Lifecycle runtime KTX 2.11.0 | 本体门槛为 compileSdk 34、AGP 8.1.1；项目与下项共用版本，保留统一版本以便一起迁移 |
| Lifecycle ViewModel Compose 2.11.0 | AAR 要求 compileSdk 37、AGP 9.1.0；发布说明对 Compose 另要求 AGP 9.2.0，后续迁移按更高门槛验证 |
| Activity Compose 1.13.0 | AAR 要求 compileSdk 36、AGP 8.9.1 |
| Compose BOM 2026.09.00 | 对应 UI 1.12.1 的 AAR 要求 compileSdk 37、AGP 9.1.0 |

因此本次只修复原检查范围的问题；生产依赖最新版需要单独进行 SDK／构建工具链迁移和回归，不能将版本提示当成已完成升级。[AGP 9.4 兼容性](https://developer.android.com/build/releases/agp-9-4-0-release-notes)、[AGP 内置 Kotlin 迁移](https://developer.android.com/build/migrate-to-built-in-kotlin)、[Lifecycle 2.11 发布说明](https://developer.android.com/jetpack/androidx/releases/lifecycle#2.11.0)

核查原始包和元数据下载自 Google Maven；本地证据保存在被 Git 忽略的 `build/lint-review-20261015/dependency-audit/`，不把依赖二进制和运行日志入库。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 与原 lint XML 对比 | 原 19 条 Warning 均已消除，原 1 条 Information 已消除 |
| 默认 `lintDebug`（联网，包含测试源码） | 0 Error、0 Information；18 条均为上述 6 个依赖的版本提示，无其他警告 |
| `testDebugUnitTest` | 78 项通过，0 失败、0 跳过 |
| Android 15 `scenario` 模拟器回归 | 52 项通过，0 失败、0 跳过；包含备份策略、图标、布局、理财、结果页和账号回调隔离 |
| 最终备份声明回归 | 增加旧系统 `fullBackupContent=false` 后复跑 `BackupPolicyTest` 2 项，全部通过 |
| `assembleRelease` | 通过；用 AAPT 检查最终 APK，确认两个备份开关均为 false、新规则引用存在、预测性返回仍为 true |

测试使用本机离线 Core／Gateway 和专用模拟器 PIN；没有连接真实银行或飞书。资源目录合并曾触发旧构建缓存缺失启动图标的问题，清理构建产物并禁用构建缓存后重建通过。未将缓存、日志、凭据或 APK 加入提交。
