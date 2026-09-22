# Android 独立 APK

默认 Android 构建是自包含的 Mock 银行沙箱，不需要电脑运行 Python、Core、Gateway 或 Docker。首次安装后即可使用 `user_zhang` / `Bank@2026` 登录；所有账户、产品和交易都是虚构演示数据。

## 构建与启动

```powershell
.\start-emulator.bat
# Gradle 依赖已缓存时可关闭构建联网
.\start-emulator.bat -Offline
```

脚本始终从当前源码重新构建 `android/app/build/outputs/apk/debug/app-debug.apk`，覆盖安装并打开应用。默认构建写入 `BuildConfig.STANDALONE_MODE=true`，第 3 步会明确输出“不需要电脑后端”。

需要联调原有 Android/IM 服务端时使用：

```powershell
.\start-emulator.bat -NetworkMode
# 或只构建联网变体
cd android
.\gradlew.bat :app:assembleDebug -PbankStandalone=false
```

## APK 内包含的能力

- 本地登录会话和应用私有 Mock 账本；余额、流水、银行卡、联系人、订阅、持仓、风险档案和任务跨重启保存。
- 确定性中文意图路由，支持余额/账单、转账、预约、AA、理财、卡片、订阅和生日跨场景安排；信息不完整的写操作只追问，不猜测执行。
- Green 查询、Yellow 明确确认、Red OTP + BiometricPrompt + Android Keystore P-256 签名；审批句柄绑定用户、动作、金额和有效期。
- Prompt 注入拒绝、三次可疑/验证失败熔断、消息幂等、链式本地审计和只读账单统计。

Repository 继续依赖与 Retrofit 相同的 `AgentApi`、`SessionApi`、`CardsApi` 等接口。独立构建由 `StandaloneBank` 实现这些接口；联网构建仍使用 Retrofit，因此两种模式复用同一套 Compose 页面、结果卡和审批交互。

## 数据与边界

本地状态位于应用私有 SharedPreferences，清除应用数据或卸载会重置沙箱。设备私钥位于 Android Keystore，不写入账本。独立版不调用在线大模型、不发送真实款项、不访问真实银行，也不提供公网 IM webhook 或服务端推送；IM provider 接入必须使用联网模式。

本地哈希审计和应用沙箱适合断网演示，不能等同服务器事务数据库、HSM、WORM 审计或 root 设备上的防篡改能力。

## 验收记录

2026-09-22 在 `bankagent` API 35 模拟器上关闭宿主机 8000/8100 端口后完成：35 个 Android JVM 测试、6 个核心聊天 UI 测试、6 个银行卡 UI 测试、联系人与理财详情 UI 测试，以及使用系统 PIN、BiometricPrompt 和真实 Keystore 签名的红区转账测试。联网变体同时通过构建与 Android Lint。
