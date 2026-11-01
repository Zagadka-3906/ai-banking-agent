# 安言：安全可控的银行 AI 智能体

通过文字或语音查询余额、分析账单、办理模拟转账、管理卡片、理财和订阅。Android 提供对话和确认界面，Agent Gateway 理解需求、读取证据并核验回答，Mock Core 独立执行授权、风控、设备签名和幂等提交。全部账户与交易均为虚构数据。

GitHub 仓库：https://github.com/Zagadka-3906/ai-banking-agent

## 参赛材料

| 类别 | 文档或文件 |
| --- | --- |
| 作品名称与简介 | [作品简介](docs/作品简介.md)，正文 171 字，参赛者自行提交报名系统 |
| 技术文档 | [系统架构](docs/技术文档.md)、[核心算法](docs/核心算法说明.md)、[安全设计](docs/安全设计文档.md) |
| 答辩材料 | [10 页答辩讲稿](docs/答辩提纲.md)、[真实演示说明](docs/演示视频.md) |
| 作品信息 | 本仓库源码、[部署说明](deploy/README.md)、[测试用例](docs/测试用例.md)、[验证记录](docs/验证记录.md) |
| 安全自评 | [权限分级与已知风险](docs/安全自评报告.md) |

本地 `submission/` 按上述五类放置交付文件，包含可编辑 PPTX、演示 MP4、源码 ZIP 和公网演示 APK。二进制交付物不进入 Git；可用 `scripts/package_contest_submission.py` 重新打包，交付包的 manifest 记录源码提交及文件哈希。它是参赛材料包，不替代稳定候选发布门槛。

## 功能与权限

| 功能 | 授权要求 |
| --- | --- |
| 本人余额、流水、持仓、卡片查询 | 登录会话，服务端核验主体与对象 |
| 小额转账、未执行安排撤销、订阅查询与取消 | 核对对象与金额后确认 |
| 大额转账、理财申赎、冻结挂失、调额、开通渠道 | 用户确认、一次性演示验证码、登记设备签名 |

核心依据实际金额、累计支付及业务类型判定等级。模型回复和客户端标记不能替代核心授权或成功回执。已结算转账不能直接撤销。

联系人删除前弹窗核对姓名，确认后同步服务器，成功或只读核对后更新列表。个人转账成功后自动保存双方，保留去重和删除状态。客户端与后台使用纯色和中性背景。

后台支持创建账户、编辑关联数据、保存初始状态，以及恢复到创建／首次保存时的状态。保存前备份；本地后台免令牌、绑定回环地址，公网后台需要令牌。管理权限仅用于演示环境。

## 快速开始（Windows）

**环境要求：**

- Python 3.10–3.12
- JDK 17 或 21
- Android SDK API 37（Build Tools 36.0.0）与 Android Emulator，并建立名为 `bankagent` 的 AVD

首次可运行 `android/tools/install-android-toolchain.ps1` 安装构建工具链。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env` 只在本机使用，不入库。可选填写：

- `DEEPSEEK_API_KEY`：启用对话理解、只读咨询规划、自然回答、独立核验及“猜你想问”。未配置或调用失败时使用规则与已验证结果，复杂理解和解释能力会下降。
- `BANK_LLM_MODEL`：默认 `deepseek-flash`；本机 Agent、核验、模拟用户与评分器均使用 Flash。`BANK_MODEL_THINKING` 选择 `disabled/enabled`，空值跟随服务商默认；当前候选为 `disabled`，与 12 秒单次期限一同评测。
  初始理解等待 4 秒且模型池仅有当前调用时，最多追加一次相同输入请求，共用原 12 秒期限；两次真实调用均计量，运行中未采用的调用保留占位。评测记录推理策略及 SDK 完成计量证明，晚到输出不参与改答或评分。
- `BANK_ANSWER_PLANNING`：默认 `1`，启用有依据的自然回答和独立核验；`0` 保留固定结果，咨询取证与规划仍可运行。`BANK_ANSWER_MODE=shadow` 生成核验但不替换可见回复；`BANK_REVIEW_MODEL` 可配置同一模型服务上的核验模型。详见 [Agent 架构](docs/Agent架构.md)。
- `DASHSCOPE_API_KEY`：没有端侧识别时，经百炼转写语音。

建议把 `BANK_AUTH_SECRET` 换成随机长密钥。全部变量见 [`.env.example`](.env.example)。

**一键启动**会启动 Core、Gateway 和本地管理面板，构建并安装 Debug APK，再打开模拟器：

```powershell
.\start-emulator.bat
```

只启动电脑后端和管理面板，可双击根目录的 `start-local-panel.bat`，或运行：

```powershell
.\.venv\Scripts\python.exe scripts\serve_local_panel.py start
```

浏览器打开 `http://127.0.0.1:9100`，无需登录令牌，直接进入后台。进入“账户管理”即可新增账户、编辑账户和关联业务数据、重新保存初始状态，或将单个账户恢复到创建／首次保存时的状态。保存前自动备份，保存后自动重载本机银行服务；修改或重置后 App 需重新登录。该面板只管理电脑上的数据，与云服务器分开；服务器面板仍需令牌登录。

首次启动会将根目录 `.env` 复制为独立的 `local.env`，并保留签名密钥；若原先配置了本地账本，则复制其文件，同时复制已有设备登记、通过 SQLite 备份导入会话，原文件保留。管理数据保存在 `%LOCALAPPDATA%\bank-agent\local-panel\<仓库标识>`，不进入 Git。已有账户在首次启用时保存初始状态；新账户在创建时保存。本机默认关闭飞书事件接收，避免与云端重复接收。

后续模型等配置请修改该目录的 `local.env`，再在面板中重启对应服务。不要随意更换账本签名密钥或管理数据路径。查看状态、停止这套本机服务：

```powershell
.\.venv\Scripts\python.exe scripts\serve_local_panel.py status
.\.venv\Scripts\python.exe scripts\serve_local_panel.py stop
```

启动器只管理自己创建的进程。若端口被原先手动启动的服务占用，会提示占用并退出；请先正常结束原服务，再重新启动面板。Core、Gateway、面板和内部控制均只监听 `127.0.0.1`；面板限制本机 Host，写操作保留操作确认头。内部进程控制的随机令牌由启动器自动管理，无需用户输入。电脑关闭后重新运行启动入口即可恢复。

仅调试原始服务时，也可分别运行（此方式不提供本地面板的服务管理和数据导入）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m uvicorn agent_api:app --host 127.0.0.1 --port 8100
```

健康检查：`http://127.0.0.1:8000/healthz`、`http://127.0.0.1:8100/healthz`。模拟器通过 `10.0.2.2` 访问电脑。

**演示账户**（仅限虚构数据）：

| 用户 | 登录口令 |
| --- | --- |
| `user_zhang`（张三）、`user_li`（李四）、`user_wife` | `Bank@2026` |

红区操作还需要：

- 模拟器设置屏幕锁，应用会登记设备公钥。
- 一次性演示验证码，普通 App 在红区审批时自动获取并填入；隔离回归包关闭自动填码，供测试分别验证正确码和错误码。

飞书接入步骤见[飞书接入说明](docs/飞书IM接入.md)。


## 验证

业务版本 `cb37631` 的结果：后端 3101 项通过、1 项历史跳过；Android JVM 115 项通过，联系人与主题设备专项 24 项通过。收尾新增演示录制用例单独记录，不混入历史统计。详见[测试用例](docs/测试用例.md)与[验证记录](docs/验证记录.md)。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe scripts/export_banking_rules.py --check
```

## 源码结构

```text
bank_agent/       会话、取证、审批与回答核验
bank_planning/    规划、表达式计算与来源
bank_core/        模拟银行核心、账本和业务路由
bank_security/    授权、签名与安全组件
android/          Android 源码、JVM 与设备测试
deploy/           Linux 部署、Nginx 和管理面板
scripts/          本地启动、评测、验证与打包工具
tests/            后端测试用例
docs/             五类参赛文档和技术附录
submission/       本机生成的最终交付物，不入库
```

`.venv`、`.env`、Android 本机配置和现有账户／设备／会话数据保留供本机运行，不进入交付包。历史试验工作区和构建缓存已清理，未合并版本与未提交内容另存私有历史备份，详见[整理说明](docs/整理说明.md)。

## 已知边界

本项目不接入真实银行、短信、商户履约或客服审核。公网演示使用私有 CA，安装包需要与服务器证书和地址匹配。规则模式录制不证明联网大模型效果，端侧语音及完整真机矩阵仍需单独验收。风险清单见[安全自评](docs/安全自评报告.md)。

## 许可证

[MIT](LICENSE)。
