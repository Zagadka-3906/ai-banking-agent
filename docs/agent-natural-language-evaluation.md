# Android Agent 自然语言验收

> 当前仓库交付范围为 Android 原生客户端与签名 IM webhook；本文的对话语义验收适用于两条渠道共用的 Agent 编排，UI 结论仅适用于 Android。

截至 **2026-09-22（北京时间，UTC+08:00）**，最终离线与在线验收分别通过 **57/57 个案例、142/142 轮**。这两份 accepted 证据均由先前完整运行中保留的 **41 个完整案例**与最新 closing 复测的 **16 个完整案例**合并，属于“全量运行＋定向复验”的归档结果，不是两次各 57 案的单次连续运行。最终在线所选案例记录模型调用 **113 次成功、0 次失败**；后端全量 **598 项通过**，Android JVM **26 项通过**，回复卡 UI **3 项通过**。

基线为 **12/48 个案例、55/111 轮**通过。历史两次完整在线运行 `after-live`、`after-live-final` 均通过旧清单的 **56/56 案例、140/140 轮**，分别耗时 **315.196 秒**、**312.649 秒**；随后语义审阅发现并修复了订阅取消后的措辞、境外消费解释和重复追问问题，强化四案预期并新增 W09 产品比较续接。旧成绩不能覆盖这些新增要求，最终结论以 `after-offline-accepted`、`after-live-accepted` 为准。最新 closing 覆盖 S/X 全部 13 案以及 B05、T11、W09，共 **16 案例、33 轮**，两种模式均通过。

## 范围与证据

用例清单为 `tests/data/natural_conversation_cases.json`，覆盖智能转账、账单分析、理财操作、卡片管理、订阅代扣与跨场景生日安排，包括省略、指代、纠正、取消、缺少参数和审批后的继续操作。

`NaturalConversationUiTest.kt` 通过实际 Android 输入框逐段输入文本，并点击审批按钮；`scripts/scenario_backend.py` 只观察网关和隔离核心，不代替用户发送对话。场景服务使用独立端口 18800/18810、独立数据和控制令牌，不复用日常 8000/8100 服务。

证据包含每案例原始 `before`、每轮输入或点击动作、服务端终态与解析、Android 保存的回复及结果卡数据、每轮 `after`、模型调用观测，以及案例末尾或失败截图。保存的 `ChatMessage` 证明消息数据已进入界面状态；排版、遮挡、滚动和实际像素仍需结合截图检查，不能仅凭 JSON 宣称视觉验收通过。

## 执行记录

执行设备为独立 Pixel 6 配置的 API 35、Google APIs、x86_64 AVD；配置保留在 `build/agent-scenarios/emulator.avd/config.ini`。场景应用包名为 `com.aibanking.agent.scenariotest`，版本 `1.0.0`（versionCode 1），通过 `scenario` 变体安装。以下时间统一列为北京时间（UTC+08:00），评估报告原始时间为 UTC。

| 运行 | 模式 | 案例 / 轮次通过数 | instrumentation 耗时 | 时间与版本证据 |
| --- | --- | --- | --- | --- |
| `baseline-offline` | offline | 12/48；55/111 | 359.998 秒 | 2026-09-21 23:26:11 重评；旧运行未生成源码指纹元数据，不能事后补造 |
| `after-offline-final` | offline | 54/56；138/140 | 253.608 秒 | 2026-09-21 23:39:45 评估；剩余两轮观测错误仍保留原报告 |
| `after-offline-recheck` | offline | 4/4；11/11 | 25.478 秒 | 2026-09-21 23:40:43 评估；T04、T12、C06、X04 完整案例重跑 |
| `after-offline-verified` | offline 聚合 | 56/56；140/140 | 聚合结果，无单次运行耗时 | 2026-09-21 23:41:14 评估；逐案例来源见 provenance |
| `after-live`（已完成的前轮） | live | 56/56；140/140 | 315.196 秒 | 核心 2026-09-21 23:43:32、网关 23:43:34 启动；23:50:44 评估；两个 run.json 源码指纹一致 |
| `after-live-final`（第二次完整运行） | live | 56/56；140/140 | 312.649 秒 | 核心 2026-09-21 23:51:58、网关 23:52:00 启动；23:58:25 评估 |
| `after-offline-closing` | offline | 16/16；33/33 | 67.514 秒 | 2026-09-22 核心 00:05:02、网关 00:05:03 启动；00:07:12 评估 |
| `after-live-closing` | live | 16/16；33/33 | 83.931 秒 | 2026-09-22 核心 00:07:15、网关 00:07:17 启动；00:14:13 评估 |
| `after-offline-accepted` | offline 聚合 | 57/57；142/142 | 聚合结果，无单次运行耗时 | 2026-09-22 00:14:13 评估；41 个保留案例＋16 个 closing 案例 |
| `after-live-accepted` | live 聚合 | 57/57；142/142 | 聚合结果，无单次运行耗时 | 2026-09-22 00:14:14 评估；41 个保留案例＋16 个 closing 案例 |

前轮在线运行的 [core-run.json](../build/agent-scenarios/after-live/core-run.json) 与 [gateway-run.json](../build/agent-scenarios/after-live/gateway-run.json) 记录七个实际源码 SHA-256。该轮 `agent.py` 为 `f51730d3509fefcb865b57855e26ad6c3973b2512cb99034966853171a2beae2`，`conversation.py` 为 `e013ab9c3593dd4ee79c11efb4af820e41f7d0985393637dc913598d5ae4f7b2`；其余五个指纹以链接中的完整元数据为准。这里记录工作区文件指纹，不声称所有修改已对应一个提交。

第二次完整在线运行的 [核心元数据](../build/agent-scenarios/after-live-final/core-run.json) 与 [网关元数据](../build/agent-scenarios/after-live-final/gateway-run.json) 同样一致，其 `agent.py` 指纹为 `f7b0c782e04e6265b314141106eaf35b9bc35791a3b808f6c36d264f94061b77`，用于区分新增上下文修复后的版本。后续语义修复不能追溯套用该指纹。

最终 closing 的离线 [核心元数据](../build/agent-scenarios/after-offline-closing/core-run.json)、[网关元数据](../build/agent-scenarios/after-offline-closing/gateway-run.json) 与在线 [核心元数据](../build/agent-scenarios/after-live-closing/core-run.json)、[网关元数据](../build/agent-scenarios/after-live-closing/gateway-run.json) 的七个源码指纹一致。其中 `agent.py` 为 `c90ef949b10c8ba4b5a053d5ec55bf9b6caad10b0e46b78c23dbe450708cfc06`，记录最终语义修复版本；运行模式分别明确标记为 offline、live。

| 证据集 | 用例文件 SHA-256 | 观测文件 SHA-256 |
| --- | --- | --- |
| `baseline-offline` | `33214430fd02af723745787ac0e50fbf13eadf08fc52e93587a616c74dde8b6e` | `2bfe2b5d04cea6824262e3642809e5b52bcdc926938dcd7ee8b334ac79a98ab1` |
| `after-offline-verified` | `c42a1d0ddbf1a31b4269d4e70db7159c6a682873aa1a6df53fed8a1bfd7d164a` | `fb89534583f3190bab75340063e6d3eb0f1bd7a6178878d79412fdba7eda3ca3` |
| `after-live` | `f52d8520bc8dda956f321e23748468f00f442c3878b166f8ffd07ef8685c4ba6` | `02eda152cf6165ab1b27e3aa1ad5ae2c5982a696655b1243e1b49b52be7628fa` |
| `after-live-final` | `f52d8520bc8dda956f321e23748468f00f442c3878b166f8ffd07ef8685c4ba6` | `69c77f46dcbae4e4412fac84a2474cde9294578a807ffdae2c15c9124036f61c` |
| `after-offline-accepted` | `a9ffaa3fd0def59ae2a370265424c310a6e370870e98945a72e9f73a0fea8880` | `7cf109f941e4568834de043b3291dfd185561dca227af2b12f59defdc1085c79` |
| `after-live-accepted` | `a9ffaa3fd0def59ae2a370265424c310a6e370870e98945a72e9f73a0fea8880` | `a1d01eccc547d4701ca96d8c769ac0406018d66e78e1c47493e9cf8b5a1a46b7` |

这些哈希已与评估报告中的 `sources` 及磁盘文件重新核对。最终 provenance 文件 SHA-256：离线为 `10d612e79afeeb7cd7cb33c4e08ed5143162c525a029723f5913802335ebe5bc`，在线为 `893511d655286be6ae373bd99b0d831ef24b2ad36721847c8b40023a473028c1`。

[最终独立核验元数据](../build/agent-scenarios/final-verification.json) 汇总来源对象深比较、文件哈希、模型统计、测试结果、三个 APK 哈希及 Git HEAD/dirty 标记；并确认两种 closing 的七个源码指纹与本轮最终源码一致。

### 模型调用实测

| 证据集 | 调用成功 | 调用失败 | 累计模型耗时 | 统计覆盖 |
| --- | --- | --- | --- | --- |
| `baseline-offline` | 0 | 0 | 已录制调用为 0 | 45 个案例有空调用数组；W02、W07、W08 缺少完整调用观测，不能把缺录当成完整统计 |
| `after-offline-verified` | 0 | 0 | 0 | 56 个案例均有观测，均无模型调用 |
| `after-live` | 111 | 0 | 76.706 秒 | 56 个案例均至少调用一次；每案例只取最后完整累计数组一次 |
| `after-live-final` | 111 | 0 | 79.252 秒 | 第二次完整运行的 56 个案例；同样按案例末轮累计数组去重计数 |
| `after-live-closing` | 29 | 0 | 22.983 秒 | 16 个专项案例均有调用；是最终在线聚合的一部分，不再次叠加计入总数 |
| `after-offline-accepted` | 0 | 0 | 0 | 最终选定的 57 个案例均有空调用数组，无缺录 |
| `after-live-accepted` | **113** | **0** | **83.376 秒** | 最终选定的 57 个案例均至少调用一次；仅取每案例最后完整累计数组一次，无缺录 |

离线与在线结果应分别列出。在线模式下模型调用失败而本地规则成功，属于降级完成，不能写成模型成功。模型调用数组是案例内累计记录，统计时不能逐轮重复相加。

这里的模型“成功”仅表示 `invoke` 正常返回；后续参数校验与本地约束仍可能修正或拒绝其解析。最终 113 次成功调用不是“模型独立正确率 100%”，142 轮通过是包含规则、校验、核心和 Android 界面的完整流程结果。累计模型耗时也不是整段对话或应用响应耗时。被 closing 替换的旧案例调用未重复计入最终统计。

### 已完成的构建与代码回归

| 验证 | 已有实际结果 | 证据与限制 |
| --- | --- | --- |
| 普通 Android debug 构建与检查 | `BUILD SUCCESSFUL in 19s` | [构建日志](../build/agent-scenarios/final-android-build.log)；包含 `assembleDebug`、`testDebugUnitTest`、`lintDebug` |
| Android JVM 测试 | 26/26 通过；0 失败、0 错误、0 跳过 | `ReplyMarkdownParserTest` 22 项、`CardNumberParserTest` 4 项；对应 `android/app/build/test-results/testDebugUnitTest/TEST-*.xml` |
| `lintDebug` | 0 Error、21 Warning、1 Information | [Lint 报告](../android/app/build/reports/lint-results-debug.html)；警告没有被描述为已全部消除 |
| 最终后端全量 pytest | **598 passed，1 warning，28.69 秒** | [最终日志](../build/agent-scenarios/final-backend-tests.log)；唯一警告是既有 Starlette 对 anyio `BlockingPortal` 别名的 DeprecationWarning |
| 普通回复卡 Android UI 复测 | 3/3 通过，5.317 秒 | [复测日志](../build/agent-scenarios/final-reply-ui-tests-recheck.log)；不代替各银行场景的完整业务验收 |
| 场景测试宿主依赖修复后构建 | `BUILD SUCCESSFUL in 4s` | [构建日志](../build/agent-scenarios/closing-scenario-build.log)；首次场景变体缺少 `ui-test-manifest`，导致 3 项宿主 Activity 错误，已仅为 scenario 补依赖并重建，属于测试工具链修复 |

上述各类验证分别保留日志。场景构建与 closing 真实界面复验覆盖展示修复后的 APK；后端最终日志覆盖新增的语义、缺参提示及比较续接回归。JVM、UI 与后端数字是不同测试集合，不合并为一个未经定义的通过率。

## 在独立模拟器复现

以下 PowerShell 示例从仓库根目录执行，要求 `.venv`、JDK 17、Android SDK 35 和 `adb` 已可用。先在 Android Studio Device Manager 新建仅用于本次验收的 API 35 AVD，设置屏幕锁 PIN 为 `1234`，启动并解锁。这个 PIN 仅是独立测试设备的固定测试凭据；红色审批会实际打开系统凭据界面并输入它。通知权限由测试中的 `GrantPermissionRule` 授予，不需要通过关闭通知弹窗来跳过测试。

### 1. 固定设备、模式和证据目录

先用 `adb devices` 确认独立 AVD 的序列号。以下 `emulator-5556` 是示例，必须替换为实际独立设备；后续所有 `adb` 命令都显式使用 `-s`，避免安装或操作日常设备。

```powershell
$scenarioSerial = 'emulator-5556'
$scenarioMode = 'offline'  # 在线验收另开一轮，改为 live
$scenarioRoot = (Get-Location).Path
$scenarioPython = Join-Path $scenarioRoot '.venv\Scripts\python.exe'
$scenarioRunner = Join-Path $scenarioRoot 'scripts\scenario_backend.py'
$scenarioRunName = 'after-' + $scenarioMode + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
$scenarioRunDir = Join-Path $scenarioRoot ('build\agent-scenarios\' + $scenarioRunName)
New-Item -ItemType Directory -Path $scenarioRunDir | Out-Null
Copy-Item -LiteralPath 'tests\data\natural_conversation_cases.json' -Destination (Join-Path $scenarioRunDir 'cases.json')
adb -s $scenarioSerial emu avd name
adb -s $scenarioSerial shell getprop ro.build.version.sdk
```

确认返回的 AVD 名称正确，API 为 `35`。基线、修改后、offline、live 和每次重跑必须使用不同的 `run-dir`，并在目录内保留当轮 `cases.json`，防止之后扩充用例改变原始分母。若复现冻结的旧后端，两个场景进程都应传入同一个 `--source-dir` 源码快照目录，并记录其版本；日常服务目录和数据无需变更。

### 2. 启动隔离后端并建立端口反向映射

确认 18800/18810 未被其它轮次占用。若已占用，先检查进程归属，不结束日常 8000/8100 服务。控制令牌每轮随机生成，保存在当前进程环境中供子进程继承；`scenario_backend.py` 只从 `BANK_SCENARIO_CONTROL_TOKEN` 读取它。不要将令牌、模型密钥或 `.env` 内容写入源码、公开日志或验收报告。

```powershell
if (Get-NetTCPConnection -LocalPort 18800,18810 -State Listen -ErrorAction SilentlyContinue) {
    throw '18800/18810 已被占用；请先确认是否为上一轮独立场景进程。'
}
$env:BANK_SCENARIO_CONTROL_TOKEN = & $scenarioPython -c 'import secrets; print(secrets.token_urlsafe(32))'
$scenarioCoreArgs = @('-u', ('"{0}"' -f $scenarioRunner), 'core', '--run-dir', ('"{0}"' -f $scenarioRunDir), '--mode', $scenarioMode)
$scenarioGatewayArgs = @('-u', ('"{0}"' -f $scenarioRunner), 'gateway', '--run-dir', ('"{0}"' -f $scenarioRunDir), '--mode', $scenarioMode)
$scenarioCore = Start-Process -FilePath $scenarioPython -ArgumentList $scenarioCoreArgs `
    -WorkingDirectory $scenarioRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $scenarioRunDir 'core.stdout.log') `
    -RedirectStandardError (Join-Path $scenarioRunDir 'core.stderr.log')
$scenarioGateway = Start-Process -FilePath $scenarioPython -ArgumentList $scenarioGatewayArgs `
    -WorkingDirectory $scenarioRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $scenarioRunDir 'gateway.stdout.log') `
    -RedirectStandardError (Join-Path $scenarioRunDir 'gateway.stderr.log')
adb -s $scenarioSerial reverse tcp:18800 tcp:18800
adb -s $scenarioSerial reverse tcp:18810 tcp:18810
adb -s $scenarioSerial reverse --list
```

等待两个场景进程启动后，分别检查 `http://127.0.0.1:18800/healthz` 与 `http://127.0.0.1:18810/healthz`。场景应用的业务 URL 和测试的 `__scenario` 控制端点都访问设备端 `127.0.0.1`，均依赖上述反向映射；该配置不使用 `10.0.2.2`。

`offline` 会在场景子进程内关闭模型调用；`live` 使用环境已有的模型配置。仅将模式设为 `live` 不构成模型成功证据，必须核对录制的调用结果。两种模式都只启动 18800/18810，不能同时运行两轮占用相同端口。

### 3. 构建并手动安装两个场景 APK

```powershell
Push-Location android
.\gradlew.bat -PbankScenarioTests=true assembleScenario assembleScenarioAndroidTest
Pop-Location
adb -s $scenarioSerial install -r android\app\build\outputs\apk\scenario\app-scenario.apk
adb -s $scenarioSerial install -r android\app\build\outputs\apk\androidTest\scenario\app-scenario-androidTest.apk
adb -s $scenarioSerial shell pm list instrumentation
```

每条构建和安装命令都应成功后才继续。场景应用包名为 `com.aibanking.agent.scenariotest`，测试包名为 `com.aibanking.agent.scenariotest.test`。只安装这两个 APK；不要清除或覆盖 `com.aibanking.agent` 的日常应用数据。此流程不运行 `connectedDebugAndroidTest`，以免选择普通 debug 变体或其它已连接设备。

### 4. 推送固定用例并运行真实界面输入

```powershell
adb -s $scenarioSerial push (Join-Path $scenarioRunDir 'cases.json') /data/local/tmp/bank-scenario-cases.json
adb -s $scenarioSerial shell am instrument -w -r `
    -e class com.aibanking.agent.NaturalConversationUiTest `
    -e caseFile /data/local/tmp/bank-scenario-cases.json `
    -e controlToken $env:BANK_SCENARIO_CONTROL_TOKEN `
    com.aibanking.agent.scenariotest.test/androidx.test.runner.AndroidJUnitRunner
```

测试从同一临时环境变量取得控制令牌，并通过 instrumentation 参数交给测试进程；不要保存包含展开后令牌的命令行或进程参数截图。测试会逐案例重置隔离数据、登录，经过实际输入框发送文本，按用例点击确认、拒绝及系统凭据验证。服务端记录器只观察这些操作。

小规模试跑可增加 `-e caseIds T01,B01`，但评估时也必须使用对应的独立清单子集，并标记为试跑。每次 instrumentation 开始时会清理场景应用内上一轮观测文件与案例截图，因此必须先拉取上一轮证据再开始下一轮。Android 测试进程结束或显示执行成功，不等于业务用例全部通过；每轮仍需交给评估器判断。

### 5. 拉取证据、评估并结束本轮

```powershell
adb -s $scenarioSerial pull /sdcard/Android/data/com.aibanking.agent.scenariotest/files/natural-conversations $scenarioRunDir
& $scenarioPython scripts\evaluate_agent_scenarios.py `
    --cases (Join-Path $scenarioRunDir 'cases.json') `
    --observations (Join-Path $scenarioRunDir 'natural-conversations\observations.json') `
    --output-dir (Join-Path $scenarioRunDir 'evaluated')
$scenarioEvaluationExitCode = $LASTEXITCODE
```

保留退出码、两份评估报告、原始观测、用例副本和截图。runner 还会自动生成 `core-run.json` 与 `gateway-run.json`，记录各自的 `mode`、带时区的 `started_at`、实际 `source_root`，以及 `agent.py`、`agent_api.py`、`conversation.py`、`main.py`、`planner.py`、`security.py`、`bank_tools.py` 七个文件的 `source_sha256`；这些元数据应随证据保存，用于核对实际运行的模式与代码版本。

中途异常也应拉取已有观测并评估，不能删除失败轮或用上一轮截图替代。Windows 的 `.venv\Scripts\python.exe` 可能只是启动包装器，真正监听端口的是它启动的 Python 子进程；`Start-Process -PassThru` 返回的 `$scenarioCore`、`$scenarioGateway` 可用于诊断，不能单凭它们完成清理。证据收齐后，按 18800/18810 查询实际监听 PID，通过 `Get-CimInstance` 核对其命令行同时包含本轮绝对脚本路径与证据目录，再仅停止匹配的监听进程；任一项不匹配就报错停止清理：

```powershell
$scenarioRunnerAbsolute = (Resolve-Path -LiteralPath $scenarioRunner).Path
$scenarioRunAbsolute = (Resolve-Path -LiteralPath $scenarioRunDir).Path
$scenarioRunnerPattern = '(^|[\s"])' + [regex]::Escape($scenarioRunnerAbsolute) + '(?=$|[\s"])'
$scenarioRunPattern = '(^|[\s"])' + [regex]::Escape($scenarioRunAbsolute) + '(?=$|[\s"])'
foreach ($scenarioPort in @(18800, 18810)) {
    $scenarioListenerPids = @(Get-NetTCPConnection -LocalPort $scenarioPort -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($scenarioListenerPid in $scenarioListenerPids) {
        $scenarioListener = Get-CimInstance Win32_Process -Filter "ProcessId = $scenarioListenerPid" -ErrorAction Stop
        $scenarioCommandLine = [string]$scenarioListener.CommandLine
        if (-not $scenarioListener -or
            $scenarioCommandLine -notmatch $scenarioRunnerPattern -or
            $scenarioCommandLine -notmatch $scenarioRunPattern) {
            throw "端口 $scenarioPort 的监听进程 $scenarioListenerPid 与本轮脚本或目录不匹配，未终止该进程。"
        }
        Stop-Process -Id $scenarioListenerPid -ErrorAction Stop
    }
}
adb -s $scenarioSerial reverse --remove tcp:18800
adb -s $scenarioSerial reverse --remove tcp:18810
Remove-Item Env:BANK_SCENARIO_CONTROL_TOKEN
```

分享证据前另建脱敏副本，隐藏会话凭据、设备凭据、模型密钥、控制令牌及不必要的个人信息；原始审计材料保留在受控本地目录，不改写实际回复或观测来获得通过。模型调用统计采用每案例最后一个完整的累计 `model_calls`，必要时参考失败轮的 `server_records.model_calls`；每条调用仅计一次，成功、失败和无调用分别报告。

## 评估命令与输出

```powershell
.venv\Scripts\python.exe scripts\evaluate_agent_scenarios.py `
  --cases tests\data\natural_conversation_cases.json `
  --observations build\agent-scenarios\RUN\observations.json `
  --output-dir build\agent-scenarios\RUN\evaluation
```

输出 `evaluation.json`（机器可读、完整检查与证据）和 `evaluation.md`（中文逐轮实际/预期对照）。退出码 0 表示清单全部通过；1 表示至少一项业务、覆盖或观测失败；2 表示输入格式或文件读写错误。只运行部分案例时，须另存对应的清单子集并明确标记为试跑；完整清单中未录制的案例始终计入失败分母。

| 预期字段 | 判定方式 |
| --- | --- |
| `approval` | 终态及非空审批 ID 一致；待审批状态自动要求账务状态未变 |
| `parsed` | 对象递归部分匹配；数组长度和顺序必须一致；布尔值不等于数值 0/1 |
| `state` | 按同样规则递归部分匹配当前 `after`；卡片、订阅、持仓、任务等被要求的字段缺失时失败 |
| `reply_any` / `reply_all` | Android 回复文本及结果卡数据包含任一 / 全部指定片段；隐藏计划元数据不参与匹配 |
| `no_claim` | 服务端回复及 Android 回复/结果卡均不得含指定片段 |
| `unchanged` | 首轮对照原始 `before`；之后对照上一轮 `after`；比较全部已观测账务字段 |
| `balance_delta` / `locked_delta` | 相邻轮的余额 / 锁定金额增量，必须有有限数值证据；同时报告原始 `before` 至当前的累计变化 |
| `new_approval` | 必须有上一轮审批 ID，且本轮产生不同的新审批 ID |
| `time_contains` | 解析出的预约 / 事件时间统一为 Asia/Shanghai（UTC+08:00）后匹配 |

不支持的预期字段不能忽略。初始化失败、UI 异常、遗漏轮次、重复录制、服务器输入或回复与界面不一致、缺少状态快照均不能判为通过。审批确认轮允许沿用原请求 ID；普通输入轮核对界面 trace ID，所有轮检查新消息 ID 和服务端事件。Android 会把理财产品清单、测评结论移到结果卡，评估器依据明确的 `displayText` 规则核对这部分投影，并允许 Gson 省略 JSON 对象的 null 成员。

服务端输入对照单独采用与 `sanitize_text` 一致的 NFKC 规范化及首尾空白去除，因此中文逗号规范化为 ASCII 逗号不会构成录制错误。原始界面输入、轮次与复制的预期仍严格匹配，真实文字、收款人、金额不同不能通过规范化掩盖。

## 最终结果

最终逐轮实际、预期、检查项和状态变化见 [离线 accepted 报告](../build/agent-scenarios/after-offline-accepted/evaluated/evaluation.md) 与 [在线 accepted 报告](../build/agent-scenarios/after-live-accepted/evaluated/evaluation.md)。历史 [基线报告](../build/agent-scenarios/baseline-offline/evaluated/evaluation.md)、[56 案离线聚合报告](../build/agent-scenarios/after-offline-verified/evaluated/evaluation.md)、[第一次完整在线报告](../build/agent-scenarios/after-live/evaluated/evaluation.md)、[第二次完整在线报告](../build/agent-scenarios/after-live-final/evaluated/evaluation.md) 均保留。下表每格依次为“通过案例/总案例；通过轮次/总轮次”。

| 方向 | 基线 offline | 最终 offline accepted | 最终 live accepted |
| --- | --- | --- | --- |
| 智能转账 | 2/14；11/30 | 20/20；54/54 | 20/20；54/54 |
| 账单分析 | 0/6；5/13 | 6/6；13/13 | 6/6；13/13 |
| 理财操作 | 2/8；9/22 | 9/9；24/24 | 9/9；24/24 |
| 卡片管理 | 2/8；11/20 | 9/9；23/23 | 9/9；23/23 |
| 订阅代扣 | 3/6；10/13 | 7/7；15/15 | 7/7；15/15 |
| 跨场景联动 | 3/6；9/13 | 6/6；13/13 | 6/6；13/13 |
| 合计 | **12/48；55/111** | **57/57；142/142** | **57/57；142/142** |

基线案例通过率为 25.00%，轮次通过率为 49.55%；最终两种模式 accepted 的案例和轮次通过率均为 100%，业务失败、观测错误及缺录均为 0。基线只有 48 例、111 轮，之后增加到 56 例、140 轮，closing 再增加到 57 例、142 轮，并强化核心状态和语义断言、校准部分文字预期，因此不是严格不变题集上的单一提升百分比。

### 最终 accepted 的完整案例来源

最终 [离线 provenance](../build/agent-scenarios/after-offline-accepted/provenance.json) 选取 `after-offline-verified` 的 41 个完整案例和 `after-offline-closing` 的 16 个完整案例；[在线 provenance](../build/agent-scenarios/after-live-accepted/provenance.json) 选取 `after-live-final` 的 41 个完整案例和 `after-live-closing` 的 16 个完整案例。两者的 closing 范围完全一致：S01–S07、X01–X06、B05、T11、W09。

聚合脚本 `build/agent-scenarios/aggregate_final_evidence.py` 按完整案例保留原始对象并校验所有 `expected` 与最终清单一致。本报告再次核对了两份聚合共 114 个案例对象：均与 provenance 指向的源对象完全一致，每轮预期与清单深比较相等，轮数相等，来源 SHA-256 匹配。没有修改原始回复、核心状态或单轮预期来制造通过；旧失败记录仍保存在其原始目录。

最新离线 closing 的观测 SHA-256 为 `f9f6002f21a1474e7a9518e8922ae3e95ddada994ab7497f6b28e28ca896ea4f`，在线为 `527efd75c597dcdf29c2ec259d65646927d3604b86e7e55ca992c2c7a9c86689`。两份专项报告分别为 [离线 16 案复验](../build/agent-scenarios/after-offline-closing/evaluated/evaluation.md)、[在线 16 案复验](../build/agent-scenarios/after-live-closing/evaluated/evaluation.md)。保留的 41 案是此前相同预期下的实测证据，并未宣称这些案例都在 closing 源码版本上重新逐一运行。

### 离线聚合的来源

`after-offline-final` 是一次完整 56 例运行，其原报告保留 **54/56、138/140**：C06 第 4 轮找不到审批 OTP 节点，X04 第 3 轮请求身份与界面 trace 对照失败，均未当作通过。随后真实重跑 T04、T12、C06、X04 四个完整案例，得到 **4/4、11/11**；不是只替换某句回复或某项检查。

[provenance.json](../build/agent-scenarios/after-offline-verified/provenance.json) 明确记录：52 例来自 `after-offline-final`，T04、T12、C06、X04 来自 `after-offline-recheck`。已逐案例核对聚合观测与对应原始对象完全一致，未改写其中的输入、回复、预期或核心状态。两个来源观测 SHA-256 分别为 `4e4f3f01de276b47d2556b19531ff09897604acdaacbba021a69a720ae9182b2` 和 `400bdc7dc2c4c04cfcf03b62758efdf86974a2e9348fc0e7f751891478857d9e`。

截图保留在各来源的 `natural-conversations` 目录：离线全量目录 73 张、四例复测目录 4 张，聚合报告按 provenance 回溯对应截图；该数量不是独立通过案例数。前轮 `after-live/natural-conversations` 有 56 张案例截图。

### 保留失败与测试环境差异

基线最终统计保留 21 个业务失败案例、15 个观测错误案例；按轮次为 34 个不符合预期、13 个观测错误、9 个未录制，均在分母内。具体包含真实上下文/审批问题，也包含 SocketTimeout、初始化失败、发送按钮禁用、审批节点缺失、请求身份不一致和 T12 预期校准差异，不能把这些全部归为模型解析错误。

较早的 `after-offline` 尝试也仍保留：[该次报告](../build/agent-scenarios/after-offline/evaluated/evaluation.md) 为 38/56 例、107/140 轮通过，错误证据含访问 `10.0.2.2:18800/18810` 超时及后续初始化失败。随后将场景业务与控制端点统一为 `127.0.0.1`，通过独立设备的双端口 `adb reverse` 重跑；这是运行环境与观测稳定性改进，不把旧失败删掉或改判成功。

## 缺陷、修复与回归证据

以下记录已处理的代码问题和对应回归断言。最终模拟器是否通过、完整后端测试数量及在线模型表现仍以最终执行记录为准，不能由这张表推定成功率。

| 案例 / 轮次 | 真实输入及实际行为 | 期望行为 | 根因与修复位置 | 回归证据 |
| --- | --- | --- | --- | --- |
| T01–T05、T10、T13–T14 | 补充金额、手机号、备注或日期时，可能丢失上下文，或把号码中的数字当金额 | 缺少必要参数时澄清；补充后只使用明示金额，保留收款人、备注和预约时间 | `agent.py` 的本地解析、金额/时间提取、缺参续接；`planner.py` 的参数校验 | `tests/test_natural_transfers.py`：手机号与金额分离、模糊金额、备注保留、预约时间 |
| T06–T09、X06 | “改成20元”“换成王美”“先别办了”等不能安全替换或撤回已有审批 | 修正产生新审批并使旧审批失效；取消后不扣款、不恢复旧操作 | `conversation.py` 待审批对话处理；`agent.py` 的指代与纠正解析 | `tests/test_natural_transfers.py`：审批替换、取消、重复送达不重复扣款及核心取消失败路径 |
| B01、B02、B04–B06 | “那上个月呢”“今年整体呢”“再看一遍”可能退回未知意图或扩大日期范围；单问境外消费可能被误当争议处置 | 沿用账单业务并仅切换明确日期；询问消费本身不冻结卡片、不创建争议 | `agent.py` 的账单上下文续接、日期范围与争议触发规则 | `tests/test_natural_bills_subscriptions_events.py`：月份/年度切换、异常追问与境外消费只读 |
| B03 | “餐饮花了多少”仍展示全部账单总额 | 日期范围不变，仅统计餐饮；明细、图表、事实和文字一致；没有该类记录时明确暂无 | `_execute_green` 的账单分支先按日期再按 `slots.category` 筛选；对应 responder 增加类别与空态 | 同文件：餐饮 200 元不混入全月 3555 元；分析/明细两个路径；空分类与上月分类 |
| 账单退款 | 负金额退款被当成无效金额或混入支出 | 支出、退款与净支出分别依据真实流水呈现 | `agent.py` 的账单金额验证、汇总与回复 | 同文件：本月流水含退款仍可查询，支出 140、退款 10、净支出 130 |
| W02、W05–W08、C02–C09 | 产品/卡片编号中的数字、产品指代、缺少金额或卡片对象影响后续操作 | 编号不作为金额；指代保持正确产品；校验真实持仓和卡片，并遵守原审批要求 | `agent.py` 的产品与卡片解析、风险与执行分支；核心继续裁定实际结果 | `tests/test_natural_wealth_cards.py`：申购赎回同一持仓、风险不匹配、超持仓赎回、多卡澄清与调额 |
| S01–S03、S05–S06 | “乱扣费”“把它取消掉”“Apple那个”“下次什么时候续费”缺少稳定上下文 | 查询真实订阅；取消对象不明确时追问；商户片段可补齐，取消需审批 | `agent.py` 的订阅识别、商户澄清与续接 | `tests/test_natural_bills_subscriptions_events.py`：多轮查询、取消及刷新状态 |
| S04、S07 | 续费提醒缺少真实执行入口；重复“同样的提醒再设置一次”需保持商户和提前天数 | 只为指定商户创建真实提醒任务；相同提醒不新增任务，不取消订阅或扣费 | `main.py` 的 reminders 端点；`planner.py` 保留受限提醒参数；`agent.py` 执行与回执、指代续接 | 同文件：真实任务日期与商户、端点幂等、多轮重复后相同任务 ID 且总数为 1 |
| X01–X05 | 生日、对象或预算缺失时猜测；提醒和订单草稿可能被误说成已采购 | 分别澄清缺参；按实际日期建立提醒；锁款与订单草稿不等于鲜花蛋糕已购买 | `planner.py` 的生日必填参数；`agent.py` 的日期/预算续接、锁定回执与采购状态追问 | 同文件：缺日期/金额/对象、提前两天、下周日期、裸数字不能当日期、未采购真实回复 |
| 在线解析防护 | 模型输出可能改写明确金额、日期、类别、问卷答案或额外增加操作 | 以用户明示事实和当前上下文为边界，模型不能替用户补授权参数 | `agent.py` 的 `_model_parse` 结果约束 | `tests/test_model_parse_guards.py`：日期/分类保留、缺参不猜、未请求的提醒与产品不执行 |
| C03、S02、S03 的新增边界 | 桩模型补充回归发现：只回复 CARD-001、Apple那个、视频会员时，可能丢失正在澄清的操作意图 | 纯对象回复继承待澄清操作；明确“查询Apple订阅”仍切换为新查询 | 已补上下文边界修复，4 条针对性回归通过 | 第二次完整在线 `after-live-final` 已通过 56/56 案例、140/140 轮，包含这三个案例 |
| S05 取消后的语义 | Apple 已取消，查询回复却仍称“下次扣款”，并建议再取消 Apple | 已取消项目显示“原计划扣款”与已取消状态；停用建议只引用 ACTIVE 商户 | 已修订订阅结果渲染，保留原计划日期但不暗示还将扣费 | 2 条新增真实网关测试及原回归通过；两种模式 closing 的 S05 全链路通过，截图语义复验通过 |
| B05 境外消费解释 | “那笔境外消费怎么回事”原先只返回全部账单汇总，未解释具体记录 | 按真实境外记录说明商户、日期、金额、关联卡片及命中的风险规则；无记录或多候选时明确说明，保持只读 | 已补真实记录筛选与解释，不能虚构交易、自动冻结或发起争议 | 6 条真实网关测试通过；强化 B05 预期后两种模式 closing 均通过 |
| X02、T11 重复追问 | 已提供生日日期或 AA 金额后，回复仍重复要求这些已有参数 | 仅根据 `missing_fields` 追问真正缺失的信息，不丢弃已提供日期/金额 | 已将缺参提示按缺失字段生成 | 15 条真实网关回归通过；强化预期后两种模式 closing 均通过 |
| W09 新增比较续接 | “比较CJ01002”只给一个产品；随后“AY90204”需要补齐第二产品而不是丢失比较上下文 | 保留第一个产品，等待明确第二编号后比较两款真实产品 | 已补单产品比较续接与回归 | W09 两轮在两种模式 closing 均通过，截图可见两款真实产品；属于新增覆盖，旧 56 案通过不能代替其结果 |

### 预期校准与观测协议修正

以下属于验收预期或观测工具的校准，不计为产品功能修复。原始观测不得修改；若旧录制中的 `expected` 与校准后的清单不同，应保留差异并在报告中解释，不能直接把旧轮次改成通过。

| 项目 | 校准内容 | 判定边界 |
| --- | --- | --- |
| T12，3 人 AA 300 元 | 本用例另外两位参与人各支付 100 元，发起人余额增量应为 `+200`；原先的 `0` 预期错误 | 使用核心余额及 AA 请求事实核对；旧基线录制的 `expected=0` 与校准清单的差异继续保留 |
| W03，取消风险测评 | “取消测评”和“退出测评”表达相同的停止意图，回复预期允许对应同义表述 | 仍须退出测评上下文，后续余额查询能正常执行；不能只靠出现“取消”二字通过 |
| W06，申购结果 | “申购已由核心确认”是成功结果的有效表述，不应仅因缺少原先预设的“已申购/成功/完成”关键词失败 | 同时核对审批完成、实际余额减少和对应持仓，不用同义词放宽核心状态要求 |
| 输入 NFKC 规范化 | 网关把全角标点规范化为半角，是既有输入清洗行为；评估器只规范化 `recording.server_input` 对照 | 原始输入与预期精确比对；新增中文标点通过、真实不同收款人失败测试，不能消除真实业务差异 |

## 视觉与交互复核

人工先检查前轮 `after-live` 的 T03-2、B03-2、W01-2、C03-2、S04-2、X03-2，发现提醒已创建但进度仍为 0/1、生日审批显示原始 ISO UTC 两项问题。修复后，实际打开以下六张 `after-offline-closing` 截图复验，均无裸 Markdown 星号、横向溢出或文字重叠。另检查 `after-live-closing` 的 B05-1、S05-3、W09-2，结果与离线一致。长内容仍需滚动，不能把单张截图外的控件描述成同时可见。

| 页面或场景 | 截图证据 | 已观察结果 | 发现问题与修复状态 | 复验结论 |
| --- | --- | --- | --- | --- |
| 续费提醒 S04-2 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/S04-2.png) | 创建进度显示 1/1，真实提醒仍为等待执行 | 已修正“创建完成”与“提醒已触发”的状态区分 | 原 0/1 显示问题复验通过 |
| 重复提醒 S07-2 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/S07-2.png) | 创建进度 1/1，重复设置不新增任务 | 任务事实与计划显示一致 | 复验通过 |
| 生日审批 X03-2 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/X03-2.png) | 日期显示北京时间 09:00，确认按钮可见 | 原始 UTC 表达已本地化；取消按钮在当前视口下方，需滚动 | 时间可读性复验通过；不声称两按钮同时可见 |
| 取消后查询 S05-3 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/S05-3.png) | Apple 标为已取消，日期称原计划；停用建议仅包含 ACTIVE 视频会员 | 不再暗示 Apple 仍将扣款或需再次取消 | 离线与在线截图均复验通过 |
| 境外消费解释 B05-1 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/B05-1.png) | 特定 2000 元交易的商户、日期、金额、卡片与规则原因完整 | 明确仅凭记录不能确认盗刷，保持只读 | 离线与在线截图均复验通过 |
| 比较续接 W09-2 | [截图](../build/agent-scenarios/after-offline-closing/natural-conversations/W09-2.png) | 两款产品可见，无重叠或横向溢出 | 第二款下半部分超出当前视口，需要滚动阅读 | 离线与在线截图均复验通过；不声称全部内容一屏可见 |

## 验收边界与遗留事项

账务快照覆盖观测端点实际输出的余额、锁定额、交易、订阅、卡片、预约转账、AA 请求，以及新版观测器增加的持仓和任务。旧基线缺少持仓或任务字段时不能据此宣称这些存储未变；增强用例若要求这些字段，评估器会将缺失判为失败。未纳入端点的其它存储仍需独立核心测试验证。生日场景的花和蛋糕必须区分提醒、订单草稿与真实采购，不能将安排提醒写成已购买。

最终 57 例是固定自然语言矩阵，不代表任意表达或所有银行数据都已验证。两次旧 56 案自动检查通过后仍经语义审阅发现真实问题，因此最终结果采用强化预期及新增用例。设备覆盖仅为单一 API 35 模拟器，未覆盖真实手机、多机型或系统大字体。输入虽经过实际控件，但每次按 3 个字符注入，不等于真实拼音输入法的组合输入与候选选择。

生日场景实际完成的是资金预留、提醒任务和待确认订单草稿，没有向鲜花或蛋糕商户真实采购、付款。本轮隔离验收未操作日常 AVD，也未重启日常 8000/8100 服务；这些既有后端需要重启才能加载本轮源码修复。收尾只终止核对归属后的 18800/18810 场景进程和独立验收 AVD，不据此推断日常 AVD 当前是否在线。

原始观测、截图、逐轮报告和运行元数据保存在被 Git 忽略的本地 `build/agent-scenarios` 目录，不属于自动随源码发布的材料。分享报告时应同时提供对应证据和脱敏副本，否则这些相对链接在其它机器上可能不可用。

最终结论：在上述覆盖范围内，离线与在线 accepted 均通过 57/57 案例、142/142 轮；在线选定证据有 113 次成功模型调用、0 次失败。598 项后端测试、26 项 JVM 测试和 3 项回复卡 UI 测试通过，Lint 为 0 Error、21 Warning、1 Information。语义审阅和截图复验所发现的问题已修复并有 closing 证据；合并方式、历史失败、预期校准及环境边界均完整保留。
