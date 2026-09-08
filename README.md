# 🏦 AI Banking Agent (银行 AI 智能体沙箱系统)

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Orchestration-LangGraph-orange.svg)](https://github.com/langchain-ai/langgraph)
[![Backend](https://img.shields.io/badge/Backend-FastAPI-green.svg)](https://fastapi.tiangolo.com/)
[![Frontend](https://img.shields.io/badge/Frontend-Streamlit-red.svg)](https://streamlit.io/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)](LICENSE)

基于 **LangGraph 确定性状态机**、**受限核心银行沙箱 (Mock Core Banking)** 与 **DeepSeek 大模型** 构建的受控银行 AI 智能体系统。

本项目针对银行业务的高安全、严风控需求设计，严格践行 **“决策与执行物理分离”** 与 **“低风险自动、高风险确认、极高风险强验证”** 的权限分级理念，杜绝模型幻觉与越权交易。

---

## 🌟 核心特性

- **三级权限与 Human-in-the-loop 挂起恢复机制**：
  - 🟢 **绿区（自动执行）**：余额查询、今日/近期账单明细查询等纯只读操作，Agent 解析意图后直通执行。
  - 🟡 **黄区（交互确认）**：小额转账（日累计 $\le 1000$ 元）、跨场景专款预留，状态机触发 `interrupt()` 挂起，前端弹出操作确认卡片。
  - 🔴 **红区（强因子鉴权）**：大额转账（日累计 $> 1000$ 元）触发核心风控熔断，前端强制调起活体人脸识别与动态短信验证码模态弹窗，凭单次时效 Token (`x-auth-token`) 结算。
- **账单深度分析与异常排查**：
  - 支持普通查账与深度诊断模式解耦；
  - 自动聚合各类目消费总额，智能识别深夜大额、境外未知商户等可疑盗刷流水，并在前端生成开销分布可视化图表。
- **跨场景事件联动**：
  - 识别复合意图（如“爱人生日锁定活期 1000 元”），联动底层资金池进行额度隔离，并自动生成日程代订备忘录卡片。
- **原生防提示词越狱与防幻觉**：
  - 核心结算接口与大模型物理隔离，无底层下发的安全凭证直接调用 API 一律返回 `403 Forbidden`。

---

## 🏗️ 系统架构设计

系统整体采用 **“感知规划层 $\to$ 安全合规层 $\to$ 确定性执行层”** 的分层流水线设计：

```text
[ 用户自然语言输入 (APP / IM / Web) ]
                  │
                  ▼
         [ 1. Intent Parser ] ── (提取意图/实体/金额/时间范围)
                  │
                  ▼
         [ 2. Risk Checker ] ──► 调用核心风控预检接口 (/risk/precheck)
                  │
       ┌──────────┼──────────────────────────┐
       ▼          ▼                          ▼
   【绿区: 查询】 【黄区: 小额/锁定】         【红区: 超额】
       │          │                          │
       │    interrupt() 挂起状态机       interrupt() 挂起状态机
       │    等待用户卡片点击确认 (Yes)   强验证: 人脸比对 + 动态短信验证码
       │          │                          │
       │          └───────────┬──────────────┘
       │                      ▼ 校验通过下发 Token
       ▼                      ▼
  [ 3. Execution Node ] ──► 携带 x-auth-token 请求核心结算接口 (/transfers/execute)
                  │
                  ▼
  [ 4. Responder Node ] ──► 整合交易凭据 / 流水图表 / 代办卡片输出友好回复
```

---

## 📁 目录结构

```text
mock-banking-core/
├── main.py              # 核心银行系统 (FastAPI 沙箱：包含账户、交易流水、限额风控计数器)
├── agent.py             # 基于 LangGraph 的业务状态机编排与 DeepSeek 节点实现
├── web_app.py           # Streamlit 前端交互系统 (支持卡片挂起、模态弹窗、实时看板)
├── requirements.txt     # 项目 Python 依赖清单
├── .env.example         # 环境变量配置模板文件
├── .gitignore           # Git 版本控制忽略文件
└── README.md            # 项目完整技术文档与运行指南
```

---

## 🚀 快速启动指南

### 1. 克隆代码仓库

```bash
git clone [https://github.com/你的用户名/ai-banking-agent.git](https://github.com/你的用户名/ai-banking-agent.git)
cd ai-banking-agent
```

### 2. 创建并激活虚拟环境

* **Windows (PowerShell)**：
  ```powershell
  python -m venv venv
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
  .\venv\Scripts\Activate.ps1
  ```
* **macOS / Linux**：
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 3. 安装依赖组件

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制环境模板为本地 `.env`，或直接配置系统环境变量：

* **Windows (PowerShell)**:
  ```powershell
  $env:DEEPSEEK_API_KEY="你的DeepSeek_API_Key"
  ```
* **macOS / Linux**:
  ```bash
  export DEEPSEEK_API_KEY="你的DeepSeek_API_Key"
  ```

---

## 🖥️ 运行与交互演示

系统采用前后端解耦运行，请**打开两个终端窗口**分别启动服务：

### 终端 1：启动 Mock 核心银行沙箱 (Port: 8000)

```powershell
python -m uvicorn main:app --reload --port 8000
```
> 启动后可直接访问 Swagger 可视化文档进行接口自测：`http://127.0.0.1:8000/docs`

### 终端 2：启动 Streamlit 智能体交互前端 (Port: 8501)

```powershell
python -m streamlit run web_app.py
```
> 启动后浏览器将自动打开 `http://localhost:8501`。

---

## 🧪 演示场景验证指南

在前端输入框中输入以下指令，可直接检验系统的各项风控与业务能力：

| 测试场景            | 推荐测试提示词                                               | 预期系统表现                                                 |
| :------------------ | :----------------------------------------------------------- | :----------------------------------------------------------- |
| **1. 绿区秒查**     | `帮我查一下现在账户里还有多少钱`                             | 无需确认直接执行，即刻返回总资产、锁定金额与可用余额。       |
| **2. 普通查账**     | `查看今日账单`                                               | 仅拉取当天的交易记录与今日支出总计，不胡乱加戏或误报。       |
| **3. 黄色操作确认** | `给我老婆转账 300 块买菜`                                    | 自动匹配联系人别名，弹出【黄色风险】操作卡片，点击确认后出账并生成流水号。 |
| **4. 红色大额阻断** | `给李四转 2000 元`                                           | 当日累计超 1000 元触发熔断，调出人脸核验与动态验证码（测试码：`888888`）弹窗。 |
| **5. 账单深度排查** | `为什么我上个月花销这么大，有异常交易吗？`                   | 触发深度审计模式，定位深夜境外 999 元可疑流水，生成开销条形图与防盗刷建议。 |
| **6. 跨场景协同**   | `我爱人生日是下周，帮我锁定 1000 元活期，生日前 2 天提醒我订花和蛋糕` | 确认后可用余额扣减 1000 元，侧边栏隔离资金增加，对话区生成日程备忘卡片。 |

---

## 🛡️ 安全合规自评要点

1. **防凭证伪造 (Anti-Forging Token)**：
   - 核心系统仅认签名 Nonce。任何跳过风控前置试算接口、伪造 Header `x-auth-token` 的转账请求将被底层强制阻断并返回 `403 Forbidden`。
2. **状态隔离与幂等性保证**：
   - 每个安全 Token 具备单次有效性（`used: True`），防止网络重放攻击。
3. **资金隔离完整性**：
   - 跨场景锁定的资金计入 `locked_amount`，在计算转账限额与可用资金时执行 `balance - locked_amount` 硬扣除，保障款项专款专用。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 协议开源。