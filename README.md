# AI Banking Agent（银行 AI 智能体安全沙箱）

这是一个可离线运行的银行智能体参赛基线：自然语言先被解析为白名单意图和可追踪任务 DAG，再由独立的 Mock Core Banking 服务完成余额与流水变更。模型只能提出建议，不能直接调用任意工具或修改账户。

> 项目是教学/比赛 Mock，不是生产银行系统。账户数据在进程内存中，生产化必须替换为数据库事务、真实身份认证、KMS 密钥和合规审查。

## 1. 赛题映射

| 要求 | 实现 | 权限 |
| --- | --- | --- |
| 智能转账 | 人名/别名/手机号、日累计限额、幂等、防重放、预约/AA Mock | ≤1000 元黄区；超出红区 |
| 账单分析 | 今日/本月/上月/年度/全部过滤、分类统计、深夜/境外异常、流水引用 | 绿区 |
| 理财操作 | 产品查询、风险评估、推荐/对比、申购/赎回 Mock 结算 | 查询/评估绿区；写操作红区 |
| 卡片管理 | 查询、虚拟卡申请、额度、冻结/解冻、挂失/解挂 | 申请黄区；安全操作红区 |
| 密码安全 | 演示凭证版本轮换 | 红区 MFA；不接收或保存明文密码 |
| 订阅代扣 | 订阅列表、商户匹配、一键取消 | 查询绿区；取消黄区 |
| 跨场景联动 | 生日锁资、提醒任务、鲜花/蛋糕订单草稿、解锁补偿、人工接管 | 黄区（订单到期需再次确认） |
| 安全 | HMAC 短时效一次性 nonce、服务端确认/MFA、审计哈希链、熔断、注入防御 | Core API 强制 |
| 代码沙箱 | 只读 AST 表达式执行器，时间/大小/结果限制 | 禁止写账 |

## 2. 目录

```text
main.py       FastAPI Mock Core：唯一记账边界与策略网关
agent.py      LangGraph 编排、离线解析、HITL 中断/恢复
planner.py    意图白名单、槽位校验、风险映射、DAG、事实分析
security.py   HMAC token、MFA、审计、熔断、注入检测
scheduler.py  线程安全任务存储（定时/补偿/人工接管）
sandbox.py    受限只读分析表达式沙箱
web_app.py    Streamlit 对话与确认/MFA 卡片
tests/        Core API 安全回归契约
docs/         架构、安全自评和演示脚本
```

## 3. 一步一步运行

### 3.1 创建环境

Python 3.10–3.12（推荐 3.12）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
# 开发/测试环境再安装：
python -m pip install -r requirements-dev.txt
```

### 3.2 配置安全参数

复制 .env.example 为 .env。至少设置：

```dotenv
BANK_AUTH_SECRET=请替换为随机长字符串
BANK_DEMO_MODE=1
BANK_MFA_TEST_OTP=888888
BANK_REQUIRE_CLIENT_AUTH=0
BANK_API_BASE=http://127.0.0.1:8000/api/v1
```

共享环境设置 BANK_DEMO_MODE=0、BANK_REQUIRE_CLIENT_AUTH=1 并接入真实 MFA；固定测试码只允许本机沙箱。即使本地演示关闭严格模式，所有写操作和确认/MFA 仍必须携带 `X-User-Id`。

Compose 默认已打开 `BANK_REQUIRE_CLIENT_AUTH=1`，且不再内置固定签名密钥；启动共享环境前必须显式设置 `BANK_AUTH_SECRET`。

### 3.3 启动核心与前端

终端一：

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

检查 /healthz 和 /docs。终端二：

```powershell
python -m streamlit run web_app.py
```

如果出现 `No module named uvicorn`，说明当前解释器没有安装运行依赖。请使用同一个虚拟环境的解释器补装，避免把包装到别的 Python：

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

本项目要求 Python 3.10–3.12；如果版本显示 3.13/3.14，使用 Python 3.12 重新创建 `.venv` 后再安装依赖。

如果启动 Streamlit 时看到 `Importing the numpy C-extensions failed`，并且错误中出现类似
`_multiarray_umath.cp314-win_amd64.pyd`、`Python 3.12` 或 `platform win32`，说明虚拟环境里混入了其他 Python 版本/架构的二进制包。不要在这个环境里继续覆盖安装，先保留旧环境并重建：

```powershell
Set-Location 'D:\3.Study\bank-agent\mock-banking-core'
if (Test-Path -LiteralPath '.venv') {
    Rename-Item -LiteralPath '.venv' -NewName '.venv-backup-numpy'
}
# 官方 64 位 Python 3.12；如果 py 启动器没有该版本，请先安装它
py -3.12-64 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python.exe -m pip install --no-cache-dir --only-binary=:all: -r requirements.txt
.\.venv\Scripts\python.exe -c "import sys,struct,numpy,pandas,uvicorn; print(sys.version); print(struct.calcsize('P')*8); print(numpy.__version__); print(pandas.__version__); print(uvicorn.__version__)"
```

最后一条命令应显示 `64`，且 NumPy wheel 标签应为 `cp312-cp312-win_amd64`。如果 `py -3.12-64` 不存在，请安装官方 64 位 Python 3.12，不要用 Python 3.14 创建本项目环境。回归测试建议在离线模式运行，避免 `.env` 中的在线模型改变测试输入：

```powershell
$env:DEEPSEEK_API_KEY = ''
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

黄色确认卡会先调用 /api/v1/auth/confirm；红色卡会调用 /api/v1/auth/mfa/verify，取得一次性 mfa_proof 后才恢复 LangGraph。浏览器按钮和布尔字段不是授权边界。

## 4. 推荐验收顺序

1. 绿区：查余额；查看今日账单；分析上月账单有没有异常。
2. 黄区：给我老婆转账 300 元买菜，先取消再重新确认，核对余额和流水号。
3. 红区：给李四转 2000 元，故意不完成 MFA，确认核心拒绝且余额不变，再完成本地测试验证。
4. 跨场景：我爱人生日下周，锁定 1000 元并提醒订花和蛋糕，查看 locked_amount、任务 DAG 和任务中心；到期订单草稿需再次确认。
5. 扩展：做风险评估、比较 MMF001 与 BOND002；申购 MMF001 1000 元；查看我的卡片；取消 Apple 订阅；申请虚拟卡；修改登录密码。

## 5. Core 安全调用顺序

写操作必须先预检，不能直接调用结算接口：

```text
POST /api/v1/risk/precheck
  -> POST /api/v1/auth/confirm
  -> POST /api/v1/transfers/execute
```

红区在确认后还必须调用 /api/v1/auth/mfa/verify，并将返回 proof 放入 X-MFA-Proof。锁资使用 risk/precheck/lock → auth/confirm → accounts/lock_fund；解锁使用对应 risk/precheck/unlock 流程。所有请求建议带 X-User-Id、X-Request-Id 和 Idempotency-Key。

理财风险评估可通过 `POST /api/v1/wealth/risk-assessment`（Agent 已接入），产品对比使用 `GET /api/v1/wealth/compare?product_ids=MMF001,BOND002`；密码修改和解挂分别走红区 `PASSWORD_CHANGE`、`CARD_UNREPORT_LOST`，仍只轮换演示凭证版本，不保存明文密码。

## 6. 测试、质量与部署

```powershell
python -m pytest -q
python -m compileall -q .
docker compose up --build -d
python scripts/smoke_test.py http://127.0.0.1:8000
```

Smoke check 通过后访问 `http://127.0.0.1:8501`；结束演示时执行 `docker compose down`。

测试覆盖金额边界、NaN/Inf、1000 元累计阈值、伪造/篡改/重放 token、MFA 绕过、负锁资、冻结账户、幂等和对象级授权；CI 见 .github/workflows/ci.yml。

生产替换清单：PostgreSQL/Redis 事务与锁、OIDC/设备绑定、短信/生物供应商、TLS/WAF/限流、KMS 密钥轮换、消息队列调度、OpenTelemetry、SAST/DAST/SBOM 和数据留存策略。

## 7. 参赛交付物

- 200 字以内作品简介：docs/作品简介.md
- 架构图与算法：docs/architecture.md
- 安全设计与风险清单：docs/security-self-assessment.md
- 赛题逐项验收表：docs/requirements-audit.md
- 5 分钟演示脚本：docs/demo-script.md
- 10 分钟答辩提纲：docs/答辩提纲.md
- 分步实施与验收指南：docs/implementation-guide.md
- 源码、部署说明、测试：本 README、Dockerfile、docker-compose.yml、tests/

所有账户、交易、产品和联系人均为虚构；项目不接入真实银行账户，不收集真实生物特征或验证码。参赛前请进行依赖许可证、数据保护、威胁建模和人工安全评审。
