# 飞书只读入口

安言可通过飞书企业自建机器人提供绑定后的私聊查询。它使用官方 Python SDK 的 WebSocket 长连接，无需公网回调；不开放群聊办理，也不执行资金业务。

## 能力边界

| 事项 | 行为 |
| --- | --- |
| 接收消息 | `im.message.receive_v1`，仅处理用户与机器人的私聊文本 |
| 回复 | 固定 Core GET 白名单与原生查询卡片 |
| 身份 | 先在 Android 登录，再用一次性码绑定有效会话 |
| 订阅与写操作 | 返回 App 完成确认或强验证，不用飞书文本代替 |
| 会话结束 | App 退出、会话过期或 Core 重启后重新绑定 |

当前飞书链路不等同 App 的完整咨询规划、自然生成与独立核验链，不能把 Android 的全部能力直接宣传为 IM 可用。

## 准备企业应用

在自己有管理权限的企业创建并发布自建应用，启用机器人和长连接事件。代码使用接收单聊消息的权限 `im:message.p2p_msg:readonly` 与机器人发送消息的权限 `im:message:send_as_bot`，订阅 `im.message.receive_v1`。

权限申请、管理员批准、应用发布与可用范围都应在自己的租户核对。后台菜单会变化，以[接收消息官方说明](https://open.feishu.cn/document/server-docs/im-v1/message/events/receive)和[发送消息官方说明](https://open.feishu.cn/document/server-docs/im-v1/message/create)为准；这些链接说明 API，不证明你的租户已经授权。

将自有配置写入私有 `.env`，不要提交实际值：

```dotenv
BANK_FEISHU_ENABLED=1
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

## 本机启动

先按[本地开发](本地开发.md)安装基础依赖。若只安装了运行依赖，再安装 `requirements-feishu.txt`。**本地面板启动器会关闭飞书监听**，需要单独启动 Core 与 Gateway。

选独立测试窗口，先正常停止本机面板实例，确认 8000 和 8100 空闲。在仓库根目录两个终端分别运行：

```powershell
# 终端一
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

```powershell
# 终端二
.\.venv\Scripts\python.exe -m uvicorn agent_api:app --host 127.0.0.1 --port 8100
```

这条直接启动路径读取仓库 `.env`，数据路径由该配置决定，不复用面板的 `local.env` 副本。先配置独立测试账本、设备与会话路径，避免误用共享数据。同一飞书 App ID 只运行一个监听 Gateway，不使用多 worker 或自动重载。

服务器上可用 `deploy/deploy.sh feishu <服务器地址>` 显式传递应用凭据并启用。启用前停止其他机器上的同应用监听，防止事件被另一个实例消费。

## 绑定与使用

1. Android 登录对应部署的演示账户，在 App 获取一次性绑定码，默认约五分钟有效。
2. 在与机器人的私聊中发送 `/绑定 <绑定码>`，不要把绑定码发到群里或公开 Issue。
3. 查询余额或账单，核对它来自绑定用户；请求资金操作或订阅时按提示回 App。
4. 发送 `/解绑`，验证后续私有查询失效。退出 App 或会话过期后也应失效。

绑定码不是长期口令，不能从别的部署复制后使用。飞书用户标识、会话令牌、App Secret 和原始消息日志均留在私有运行目录。

## 诊断与验收

先分开检查凭据、租户令牌、企业策略、发布范围、长连接和发送权限，不把所有 21001 或权限错误归为同一原因。可运行脱敏诊断：

```powershell
.\.venv\Scripts\python.exe scripts/diagnose_feishu_access.py --help
.\.venv\Scripts\python.exe scripts/feishu_acceptance.py --help
```

`feishu_acceptance.py` 会启动独立真实消息验收监听，只在明确的测试窗口运行，先停止同 App 的其他监听。文本和卡片发送成功不证明账户绑定、跨主体隔离或业务查询全部通过。

完整验收应覆盖：无绑定拒绝、有效绑定、过期或重复码拒绝、本人余额、期间账单、订阅返回 App、资金操作拒绝、解绑、登出及重启失效。历史平台记录仅说明当时指定应用可用，不能推断新租户已发布或旧平台错误已解决。[验证范围](验证记录.md)
