# 真机 / 模拟器验收手册

本仓库的 Android 端只靠编译证明不了什么：Compose 界面树、流式对话、Keystore 注册、审批链路、推送到达，都必须在一台真实设备或模拟器上跑一遍。本文记录这套验收环境的**按需**拉起步骤与通过标准。

模拟器不常驻：它是 GUI 进程，放在后台运行在本机实测会被回收（两次都走了优雅关闭，日志尾部为 `Stream removed (CANCELLED)`）。所以约定是**需要验证时再启动**，冷启动约 20 秒。

## 1. 先起后端

设备上的 App 连的是宿主机，模拟器里对应 `10.0.2.2`。

```powershell
# 核心：真实登录 + 严格会话模式（App 的写入都带会话凭证）
$env:BANK_REQUIRE_SESSION = '1'
uvicorn main:app --port 8000

# 渠道网关：App 的对话与审批走这里
$env:BANK_CONVERSATION_DB = '.tmp-conversations.sqlite3'
uvicorn agent_api:app --port 8100
```

只有要验推送时才需要 `BANK_FCM_CREDENTIALS`（服务账号私钥，仓库外）；没有它时 App 仍能登录使用，只是 `/api/v1/security/health` 会报 `push.configured=false`，派发审计为 `NOT_SENT`。

## 2. 启动模拟器

前置工具链由 `android/tools/install-android-toolchain.ps1` 装好，`JAVA_HOME` / `ANDROID_HOME` / `ANDROID_SDK_ROOT` 写在用户级环境变量里。注意 `platform-tools` 进了 PATH 而 **`emulator` 没有**，所以模拟器要用完整路径：

```powershell
& "$env:ANDROID_HOME\emulator\emulator.exe" -avd bankagent   # 保留既有 App 与密钥
# 追加 -wipe-data 可得干净环境；会同时清掉 App 与设备 PIN

# 等它起来
adb wait-for-device
while (((adb shell getprop sys.boot_completed) -join '').Trim() -ne '1') { Start-Sleep 5 }
```

`-wipe-data` 之后必须重新安装 APK 并重新登录；设备密钥是新的，服务端会注册出一台新设备（设备编号由公钥派生：`DEV` + `sha256(SPKI)` 的前 24 位十六进制大写，见 `security.py` 的 `enroll_device`）。

## 3. 解锁，并按需设置设备凭据

**必须先解锁**：未解锁时应用的 Activity 不可见，`adb shell am start` 会报 `Activity class ... does not exist`，monkey 也会说找不到可运行的活动——现象像"应用没装"，实际是锁屏。

```powershell
adb shell input keyevent KEYCODE_WAKEUP
adb shell input swipe 540 1800 540 500     # 上滑出 PIN 键盘
adb shell input text 1234
adb shell input keyevent 66                # 回车提交
adb shell dumpsys window | Select-String isKeyguardShowing   # 应为 false
```

红区要过 `BiometricPrompt`，需要设备已设置凭据：

```powershell
adb shell locksettings set-pin 1234
```

`android/tools/satisfy-biometric-prompt.ps1` 用来应答 BiometricPrompt（它在确认应用真正获得焦点后才输入 PIN，避免把 PIN 打进空处）：

```powershell
pwsh -File android\tools\satisfy-biometric-prompt.ps1 -Pin 1234 -TimeoutSeconds 180
```

## 4. 安装并放行通知

```powershell
adb install -r -t android\app\build\outputs\apk\debug\app-debug.apk
# 应用启动时会申请通知权限，系统弹窗会盖住登录页
adb shell pm grant com.aibanking.agent android.permission.POST_NOTIFICATIONS
adb shell am start -n com.aibanking.agent/.MainActivity
```

APK 里是否带 Firebase 配置取决于构建时 `android/app/google-services.json` 是否存在：有则推送开启，无则照常构建运行、推送静默关闭（构建日志会打印 `google-services.json not found: building without Firebase push.`）。

## 5. 手动点测（日常自测）

只想自己点着看效果时，起完 §1–§4 的进程与设备，直接操作模拟器窗口即可。本节记录的都是实测结论。

### 中文怎么输入（这一步最容易卡住）

`adb shell input text` 传中文不是"打不出来"，而是**命令直接失败**：

```text
adb shell input text '查一下余额'
java.lang.NullPointerException: Attempt to get length of null array
    at com.android.server.input.InputShellCommand.sendText(InputShellCommand.java:390)
```

这个镜像上也没有 `cmd clipboard`（返回 `No shell command implementation.`），所以别指望用 adb 把中文塞进去。可行做法：

1. **零安装，日常推荐**：模拟器里的 Gboard 自带 `中文 (繁體) (拼音)`（zh_TW）且该子类型**已启用**。点一下聊天输入框 → 按屏幕键盘上的地球键切到中文 → 用拼音打。它输出的是**繁體字**，而 Agent 实测能懂：

   | 你打的（繁體） | 实测结果 |
   | --- | --- |
   | `查一下餘額` | `BALANCE_QUERY` / 绿区，直接回余额 |
   | `給李四轉 2000 元` | `APPROVAL_REQUIRED` / 红区，弹审批 |

   确定性关键词表是简体的，繁體靠模型兜底识别——上面两条就是这么跑通的。
2. **要打简体**：装第三方 IME [ADBKeyboard](https://github.com/senzhk/ADBKeyBoard)，之后用
   `adb shell am broadcast -a ADB_INPUT_TEXT --es msg '查一下余额'` 发任意 Unicode。这属于引入第三方组件，装之前先确认接受。
3. **不想手打**：直接用 §6 的插桩测试，它本来就负责"输入真实中文并点击"。

### 三条链路各点什么

| 想验的 | 输入 | 预期 |
| --- | --- | --- |
| 绿区只读 | `查一下餘額` | 直接回余额，无确认卡 |
| 黄区确认 | `給李四轉 300 元` | 出现确认卡 → 点确认 → 回执带流水号 |
| 红区强验证 | `給李四轉 2000 元` | 要求短信码（演示码 `888888`）+ 活体验证，验证前拒绝结算 |

红区的 BiometricPrompt 需要设备已设凭据（§3）。可以手动在系统弹窗里输 `1234`，也可以另开一个终端：

```powershell
pwsh -File android\tools\satisfy-biometric-prompt.ps1 -Pin 1234 -TimeoutSeconds 180
```

### 推送怎么手动触发

应用不在前台才显出推送的意义：

```powershell
adb shell input keyevent KEYCODE_HOME           # 切后台
python scripts\fcm_delivery_e2e.py              # 制造一条"任务到期"，服务端真实派发
adb shell dumpsys notification --noredact | Select-String 'aibanking|android.title'
```

通知出现后点它，应用会被拉回前台（`mCurrentFocus` 变成 `MainActivity`），会话不丢。

### 两个会让你以为"应用坏了"的坑

* **跑过插桩测试后应用会从桌面消失**：`connectedAndroidTest` 结束时会把被测 APK 与测试 APK 一起卸载（Gradle 的标准行为）。重新执行 §4 的 `adb install` 即可。日志特征是
  `Package [com.aibanking.agent] reported as REPLACED, but missing application info. Assuming REMOVED.`
* **核心一重启，已登录的会话就失效**：会话保存在核心进程内存里。当前版本会在启动时向核心确认一次，失效就诚实回到登录页；如果停在对话页而操作报"登录状态已失效"，重新登录即可。

## 6. 驱动方式 A：插桩 UI 测试（首选）

应用识别的是中文指令，而 `adb shell input text` 只支持 ASCII，所以**真正的链路验收只能用插桩测试**。它们用 Compose 语义定位控件、输入真实文本、执行真实点击。

```powershell
cd android
.\gradlew.bat --no-daemon connectedDebugAndroidTest `
  "-Pandroid.testInstrumentationRunnerArguments.class=com.aibanking.agent.LoginAndChatUiTest"

.\gradlew.bat --no-daemon connectedDebugAndroidTest `
  "-Pandroid.testInstrumentationRunnerArguments.class=com.aibanking.agent.RedTierUiTest"
```

约束（同样写在 `LoginAndChatUiTest.kt` 顶部，以那里为准）：

* 栈必须跑在宿主机的 `10.0.2.2:8000`（核心）与 `10.0.2.2:8100`（网关）；
* **每个测试类之间要重启核心**：日累计风控是跨请求累计的，同一进程里连着跑会让后一个类落在红区；
* 红区那条要先设好 PIN，并另开一个终端跑 `satisfy-biometric-prompt.ps1`；
* 模拟器必须处于解锁状态。

## 7. 驱动方式 B：adb 直驱（回退手段）

只适合快速确认界面能起来，**不要用它做链路验收**。登录页会把演示账号预填成 `user_zhang`，口令是 `Bank@2026`：

```powershell
adb shell input tap 503 1310    # 登录口令输入框
adb shell input text 'Bank@2026'
adb shell input keyevent 111    # 收起键盘
adb shell input tap 540 1494    # 登录按钮
adb shell input tap 993 212     # 已登录后右上角「退出」
```

这些坐标绑定 1080x2400 / 420dpi 的布局，**改一次界面就失效**。每一步都应先 `adb exec-out uiautomator dump /dev/tty` 核对文本与 bounds 再点，不要盲点——本轮就遇到过点击被吞、输入框没清空的情况。

## 8. 后端探针

```powershell
python scripts/smoke_test.py http://127.0.0.1:8000
python scripts/agent_gateway_e2e.py          # 三档授权、审批句柄、审计连续性
python scripts/fcm_delivery_e2e.py           # 真实推送投递（需要 FCM 凭据 + 已注册设备）
```

`fcm_delivery_e2e.py` 会先断言"服务端能发"与"设备有令牌"两件事同时成立，再走完审批、调度、派发，并要求审计为 `SENT` 且 `delivered >= 1`——`NOT_SENT` 判为失败而不是跳过。

## 9. 什么算通过

可核对的具体证据，而不是"看起来没报错"：

| 项 | 证据 |
| --- | --- |
| 设备注册 | `GET /api/v1/devices` 出现 `ECDSA-P256-SHA256`、`status=ACTIVE` 的设备；审计 `DEVICE_ENROLLED` |
| 推送令牌 | 审计 `PUSH_TOKEN_REGISTERED`（记录的是令牌指纹，不是令牌本身）；`push.devices_with_tokens >= 1` |
| 红区链路 | `GET /api/v1/security/audit/trace/{request_id}` 单链：`*_PRECHECK → FACE_CHALLENGE_ISSUED → MFA_VERIFY → AUTH_TOKEN_CONSUME → …` |
| 调度与通知 | 同一 `request_id` 下 `TASK_RUN` + `PUSH_DISPATCHED`；派发 `outcome=SENT` |
| 通知到达 | `adb shell dumpsys notification --noredact` 中有 `pkg=com.aibanking.agent` 的通知，标题/正文与 `notifications.py` 的文案一致；应用在前台和后台都应收到 |
| 通知点击 | 点通知后 `mCurrentFocus` 变为 `com.aibanking.agent/MainActivity`，且回到对话界面（会话未丢） |
| 设备库落盘 | 配了 `BANK_DEVICE_STORE_PATH` 时，重启核心后 `devices.enrolled` 与 `devices_with_tokens` 不变，无需重新登录 |

## 10. 已知限制

* 模拟器不常驻，按需启动（原因见开头）。
* 设备注册表默认只在内存里：不配 `BANK_DEVICE_STORE_PATH`，核心一重启设备与推送令牌就都没了，推送会静默失效直到用户重新登录。本仓库已支持落盘，见 `.env.example`。
* 日累计风控是累计的，同一核心进程里反复做敏感操作会升级风险档位；验收时按测试类切分并重启核心。
* 当前仍是虚构数据的演示 Mock（账本/审批/设备库可经环境变量选配落盘），未做生产化改造（数据库、OIDC、真实 MFA、TLS/WAF、KMS 等），见 `docs/security-self-assessment.md`。
