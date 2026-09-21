import type {
  UserAccount,
  TransactionItem,
  BillAnalysis,
  WealthProduct,
  WealthHolding,
  BankCard,
  SubscriptionItem,
  ScheduledTask,
  AuditLogEntry,
  SecurityStatus,
  AgentChatMessage,
} from './types'
import {
  MOCK_USERS,
  MOCK_TRANSACTIONS,
  MOCK_BILL_ANALYSIS,
  MOCK_WEALTH_PRODUCTS,
  MOCK_HOLDINGS,
  MOCK_CARDS,
  MOCK_SUBSCRIPTIONS,
  MOCK_TASKS,
  MOCK_SECURITY_STATUS,
  MOCK_AUDIT_LOGS,
} from './mockData'

class BankingApiClient {
  private currentUserId: string = 'user_zhang'
  private isOnline: boolean | null = null
  private sessionToken: string | null = null

  // Local state cache for sandbox mutations
  private localAccounts = { ...MOCK_USERS }
  private localTransactions = [...MOCK_TRANSACTIONS]
  private localCards = [...MOCK_CARDS]
  private localSubscriptions = [...MOCK_SUBSCRIPTIONS]
  private localTasks = [...MOCK_TASKS]
  private localAuditLogs = [...MOCK_AUDIT_LOGS]

  setUserId(userId: string) {
    this.currentUserId = userId
    this.sessionToken = null
  }

  getUserId(): string {
    return this.currentUserId
  }

  async checkConnectivity(): Promise<boolean> {
    try {
      const controller = new AbortController()
      const timeoutId = setTimeout(() => controller.abort(), 1200)
      const res = await fetch('/api/core/healthz', { signal: controller.signal })
      clearTimeout(timeoutId)
      this.isOnline = res.ok
      return res.ok
    } catch {
      this.isOnline = false
      return false
    }
  }

  getConnectivityStatus(): boolean {
    return this.isOnline ?? false
  }

  private async fetchCore(path: string, options: RequestInit = {}): Promise<any> {
    const headers = new Headers(options.headers || {})
    headers.set('X-User-Id', this.currentUserId)
    headers.set('X-Request-Id', `web_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`)
    if (this.sessionToken) {
      headers.set('X-Session-Token', this.sessionToken)
    }
    if (!headers.has('Content-Type') && options.body) {
      headers.set('Content-Type', 'application/json')
    }

    const res = await fetch(`/api/core/${path.replace(/^\//, '')}`, {
      ...options,
      headers,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err?.detail?.message || err?.message || `HTTP ${res.status}`)
    }
    return res.json()
  }

  private async fetchGateway(path: string, options: RequestInit = {}): Promise<any> {
    const headers = new Headers(options.headers || {})
    headers.set('X-User-Id', this.currentUserId)
    headers.set('X-Request-Id', `gw_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`)
    if (this.sessionToken) {
      headers.set('X-Session-Token', this.sessionToken)
    }
    if (!headers.has('Content-Type') && options.body) {
      headers.set('Content-Type', 'application/json')
    }

    const res = await fetch(`/api/gateway/${path.replace(/^\//, '')}`, {
      ...options,
      headers,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      throw new Error(err?.detail?.message || err?.message || `HTTP ${res.status}`)
    }
    return res.json()
  }

  // 1. Account & Balance
  async getAccount(): Promise<UserAccount> {
    if (this.isOnline) {
      try {
        const data = await this.fetchCore(`api/v1/accounts/${this.currentUserId}/balance`)
        return {
          user_id: this.currentUserId,
          user_name: data.user_name || '用户',
          card_no: '622202001',
          phone: '138****0000',
          total_balance: data.total_balance,
          available_balance: data.available_balance,
          locked_amount: data.locked_amount,
          status: data.status,
          currency: 'CNY',
        }
      } catch (err) {
        console.warn('Fallback to local mock account data:', err)
      }
    }
    return this.localAccounts[this.currentUserId] || this.localAccounts.user_zhang
  }

  // 2. Transactions
  async getTransactions(scope: string = 'ALL'): Promise<TransactionItem[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/accounts/${this.currentUserId}/transactions?date_scope=${scope}`)
        if (res?.data) {
          return res.data
        }
      } catch (err) {
        console.warn('Fallback to local mock transactions:', err)
      }
    }
    return this.localTransactions.filter((tx) => tx.account_id === this.currentUserId)
  }

  // 3. Bill Analysis
  async getBillAnalysis(): Promise<BillAnalysis> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/accounts/${this.currentUserId}/bill-analysis`)
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock bill analysis:', err)
      }
    }
    return MOCK_BILL_ANALYSIS
  }

  // 4. Wealth Products
  async getWealthProducts(): Promise<WealthProduct[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore('api/v1/wealth/products')
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock wealth products:', err)
      }
    }
    return MOCK_WEALTH_PRODUCTS
  }

  async getWealthHoldings(): Promise<WealthHolding[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/wealth/holdings/${this.currentUserId}`)
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock holdings:', err)
      }
    }
    return MOCK_HOLDINGS
  }

  // 5. Cards
  async getCards(): Promise<BankCard[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/cards/${this.currentUserId}`)
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock cards:', err)
      }
    }
    return this.localCards
  }

  async freezeCard(cardId: string): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore('api/v1/cards/freeze', {
          method: 'POST',
          body: JSON.stringify({ user_id: this.currentUserId, card_id: cardId }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localCards = this.localCards.map((c) =>
      c.card_id === cardId ? { ...c, status: 'FROZEN' } : c
    )
    return true
  }

  async unfreezeCard(cardId: string): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore('api/v1/cards/unfreeze', {
          method: 'POST',
          body: JSON.stringify({ user_id: this.currentUserId, card_id: cardId }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localCards = this.localCards.map((c) =>
      c.card_id === cardId ? { ...c, status: 'ACTIVE' } : c
    )
    return true
  }

  async updateCardLimit(cardId: string, dailyLimit: number): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore('api/v1/cards/limit', {
          method: 'POST',
          body: JSON.stringify({ user_id: this.currentUserId, card_id: cardId, daily_limit: dailyLimit }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localCards = this.localCards.map((c) =>
      c.card_id === cardId ? { ...c, daily_limit: dailyLimit } : c
    )
    return true
  }

  // 6. Subscriptions
  async getSubscriptions(): Promise<SubscriptionItem[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/subscriptions/${this.currentUserId}`)
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock subscriptions:', err)
      }
    }
    return this.localSubscriptions
  }

  async cancelSubscription(subId: string): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore('api/v1/subscriptions/cancel', {
          method: 'POST',
          body: JSON.stringify({ user_id: this.currentUserId, subscription_id: subId }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localSubscriptions = this.localSubscriptions.map((s) =>
      s.subscription_id === subId ? { ...s, status: 'CANCELLED' } : s
    )
    return true
  }

  // 7. Tasks
  async getTasks(): Promise<ScheduledTask[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore('api/v1/tasks')
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock tasks:', err)
      }
    }
    return this.localTasks
  }

  async cancelTask(taskId: string): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore(`api/v1/tasks/${taskId}/cancel`, {
          method: 'POST',
          body: JSON.stringify({ reason: '用户在前端界面取消' }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localTasks = this.localTasks.map((t) =>
      t.task_id === taskId ? { ...t, status: 'CANCELLED' } : t
    )
    return true
  }

  async takeoverTask(taskId: string): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchCore(`api/v1/tasks/${taskId}/takeover`, {
          method: 'POST',
          body: JSON.stringify({ reason: '用户请求人工接管' }),
        })
        return true
      } catch (e) {
        console.warn(e)
      }
    }
    this.localTasks = this.localTasks.map((t) =>
      t.task_id === taskId ? { ...t, status: 'WAITING_USER', description: `${t.description} (已转人工专员跟进)` } : t
    )
    return true
  }

  // 8. Security & Audit
  async getSecurityStatus(): Promise<SecurityStatus> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore(`api/v1/security/health`)
        if (res) {
          return {
            service_status: res.status === 'ok' ? 'NORMAL' : 'DEGRADED',
            rate_limit_usage: 5,
            circuit_breaker_open: false,
            failed_attempts_today: 0,
            mfa_required_level: 'YELLOW',
            last_security_event: '实时链路审计监控已连接',
          }
        }
      } catch (err) {
        console.warn('Fallback to local mock security status:', err)
      }
    }
    return MOCK_SECURITY_STATUS
  }

  async getAuditLogs(): Promise<AuditLogEntry[]> {
    if (this.isOnline) {
      try {
        const res = await this.fetchCore('api/v1/audit/logs')
        if (res?.data) return res.data
      } catch (err) {
        console.warn('Fallback to local mock audit logs:', err)
      }
    }
    return this.localAuditLogs
  }

  // 9. Agent Conversational Turn
  async sendAgentMessage(
    prompt: string,
    conversationId?: string,
    onStreamChunk?: (text: string) => void
  ): Promise<Partial<AgentChatMessage>> {
    const text = prompt.trim()

    // Try online gateway if available
    if (this.isOnline) {
      try {
        const res = await this.fetchGateway('api/v1/agent/messages', {
          method: 'POST',
          body: JSON.stringify({
            text,
            conversation_id: conversationId,
          }),
        })

        if (res?.status === 'APPROVAL_REQUIRED' && res.approval) {
          return {
            content: res.approval.message || '此操作涉及资金安全，请核验并确认：',
            approval_required: {
              approval_id: res.approval.approval_id,
              auth_token: res.approval.auth_token,
              risk_level: res.approval.risk_level || 'YELLOW',
              message: res.approval.message,
              request_id: res.request_id || '',
            },
            plan: res.plan,
          }
        }

        return {
          content: res.final_response || res.message || '操作已受理。',
          chart_data: res.chart_data,
          todo_card: res.todo_card,
          plan: res.plan,
        }
      } catch (e) {
        console.warn('Gateway online turn failed, falling back to mock brain:', e)
      }
    }

    // Local Intelligent Simulation (Realistic responses for standard banking queries)
    await new Promise((resolve) => setTimeout(resolve, 600))

    if (text.includes('查余额') || text.includes('余额') || text.includes('多少钱')) {
      const acct = this.localAccounts[this.currentUserId] || this.localAccounts.user_zhang
      const msg = `尊敬的 ${acct.user_name}，您当前的账户总资产为 **¥${acct.total_balance.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}**。
- **可用资金**：¥${acct.available_balance.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
- **已隔离锁定资金**：¥${acct.locked_amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
- **账户卡号**：**** **** **** ${acct.card_no.slice(-4)}
- **状态**：正常履约 (一级结算账户)`
      if (onStreamChunk) onStreamChunk(msg)
      return { content: msg }
    }

    if (text.includes('转账') || text.includes('给我老婆转') || text.includes('转 300') || text.includes('转300')) {
      const isYellow = !text.includes('2000') && !text.includes('大额')
      const targetUser = text.includes('李四') ? '李四' : '王美 (爱人)'
      const amount = text.includes('2000') ? 2000 : 300

      if (isYellow) {
        return {
          content: `已为您完成转账意图理解与合规预检，即将向 **${targetUser}** 汇出款项 **¥${amount.toFixed(2)}**（用途：买菜）。
按安言风控分级，该交易评定为 **黄色安全确认级 (YELLOW)**，请核对信息无误后授权确认。`,
          approval_required: {
            approval_id: `appr_${Date.now()}`,
            auth_token: `token_demo_${Math.random().toString(36).slice(2, 10)}`,
            risk_level: 'YELLOW',
            message: `向 ${targetUser} 转账 ¥${amount.toFixed(2)} 元`,
            request_id: `req_tx_${Date.now()}`,
          },
          plan: {
            tasks: [
              { task_id: 'T1', action: 'RISK_PRECHECK', depends_on: [], risk_level: 'GREEN', status: 'COMPLETED' },
              { task_id: 'T2', action: 'USER_CONFIRMATION', depends_on: ['T1'], risk_level: 'YELLOW', status: 'PENDING' },
              { task_id: 'T3', action: 'EXECUTE_TRANSFER', depends_on: ['T2'], risk_level: 'YELLOW', status: 'PENDING' },
            ],
          },
        }
      } else {
        return {
          content: `转账金额较高 (¥${amount.toFixed(2)})，触发 **红色最高风险管控 (RED)**。核心银行要求进行设备端活体人脸核验与短信一次性凭证（OTP）。`,
          approval_required: {
            approval_id: `appr_red_${Date.now()}`,
            auth_token: `token_red_${Math.random().toString(36).slice(2, 10)}`,
            risk_level: 'RED',
            message: `向 ${targetUser} 大额转账 ¥${amount.toFixed(2)} 元`,
            request_id: `req_red_${Date.now()}`,
            challenge_id: `chal_${Date.now()}`,
          },
          plan: {
            tasks: [
              { task_id: 'T1', action: 'LARGE_AMOUNT_CHECK', depends_on: [], risk_level: 'YELLOW', status: 'COMPLETED' },
              { task_id: 'T2', action: 'MULTI_FACTOR_AUTH', depends_on: ['T1'], risk_level: 'RED', status: 'PENDING' },
              { task_id: 'T3', action: 'CORE_SETTLEMENT', depends_on: ['T2'], risk_level: 'RED', status: 'PENDING' },
            ],
          },
        }
      }
    }

    if (text.includes('账单') || text.includes('分析') || text.includes('消费')) {
      const chartData = [
        { category: '餐饮美食', amount: 403.0 },
        { category: '交通出行', amount: 300.0 },
        { category: '可疑交易', amount: 999.0 },
        { category: '数字订阅', amount: 15.0 },
      ]
      return {
        content: `📊 **为您解析近1个月真实流水事实**：
1. **支出总计**：¥1,717.00，日均约 ¥57.23；
2. **最大占比**：可疑风控交易占 58.2%（**TX1004 境外未知商户 ¥999.00**，发生于凌晨3点，强烈建议核验）；
3. **日常餐饮**：星巴克、轻食及海底捞累计 ¥403.00；
4. **数字订阅**：Apple iCloud+ 保持每月 ¥15.00 扣款。`,
        chart_data: chartData,
      }
    }

    if (text.includes('生日') || text.includes('锁定') || text.includes('锁资') || text.includes('订花')) {
      // Create local task and update lock fund
      const acct = this.localAccounts[this.currentUserId] || this.localAccounts.user_zhang
      acct.locked_amount = 1000.0
      acct.available_balance = acct.total_balance - acct.locked_amount

      return {
        content: `🎉 已为您成功设置 **爱人生日专项关怀计划**：
1. **隔离锁定资金**：¥1,000.00 已从可用余额中划入安全隔离池，绝不会被日常支付透支；
2. **自动定时提醒**：将在生日到期日前一天 (9月25日) 自动推送预定鲜花提醒与定制问候。`,
        todo_card: {
          title: '爱人生日锁资与订花提醒',
          locked_fund: '¥1,000.00 (安全隔离中)',
          schedule: '2026-09-26 09:00:00 (自动唤醒)',
          status: 'WAITING_USER',
          task_id: 'TASK-LK-20260901',
          order_task_ids: ['ORDER-ROSE-01', 'MSG-BLESSING-01'],
        },
      }
    }

    if (text.includes('理财') || text.includes('推荐') || text.includes('稳健')) {
      return {
        content: `根据您的保守稳健型投资习惯，安言智能理财为您优选以下方案：
1. 🥇 **稳健添利 · 日日盈 (R1)**：7日年化 2.05%~2.35%，工作日快速赎回 1 万元内秒级到账，适合日常活期零钱；
2. 🥈 **安逸安享 · 季季丰90天 (R2)**：预期年化 2.75%~3.10%，纯债打底，季季分红，适合存放中短期闲置资金。
您可直接在“智能理财”栏目中点击查看持仓明细与历史业绩折线图。`,
      }
    }

    if (text.includes('订阅') || text.includes('Apple') || text.includes('取消')) {
      return {
        content: `已为您检索到名下活跃订阅：
- **Apple 订阅服务 (iCloud+ 2TB)**：¥15.00/月，下次扣费日 2026-10-03；
- **视频超级 VIP 连续包月**：¥25.00/月，下次扣费日 2026-09-25。
若需退订，您可直接在右侧或下方“订阅托管”专区点击 **一键取消**。`,
      }
    }

    return {
      content: `收到指令：“${text}”。安言智能金融助手已完成语义建模。我可以为您办理：
- 💳 **账户与流水**：查询实时余额、分类账单深度分析；
- 💸 **转账汇款**：家人亲属快捷转账、三级风控智能确认；
- 🎂 **资产保障**：生日关怀资金锁定、日程订花提醒；
- 📈 **稳健理财**：R1-R4 多维度产品对比与持仓托管；
- 🛡️ **卡片与订阅**：一键锁卡调额、无用代扣自动清理。`,
    }
  }

  // 10. Resolve Approval
  async resolveApproval(
    approvalId: string,
    decision: 'approve' | 'reject',
    otp?: string,
    faceAttestation?: string
  ): Promise<boolean> {
    if (this.isOnline) {
      try {
        await this.fetchGateway(`api/v1/agent/approvals/${approvalId}`, {
          method: 'POST',
          body: JSON.stringify({
            decision,
            otp,
            face_attestation: faceAttestation,
          }),
        })
        return true
      } catch (e) {
        console.warn('Online approval failed, using local simulation:', e)
      }
    }

    // Local simulation: deduct balance for transfer if approved
    if (decision === 'approve') {
      const acct = this.localAccounts[this.currentUserId] || this.localAccounts.user_zhang
      const deduct = 300.0
      acct.available_balance = Math.max(0, acct.available_balance - deduct)
      acct.total_balance = acct.available_balance + acct.locked_amount

      // Add transaction record
      this.localTransactions.unshift({
        tx_id: `TX${Date.now().toString().slice(-4)}`,
        account_id: this.currentUserId,
        merchant: '向王美 (爱人) 快捷转账',
        category: '转账汇款',
        amount: -deduct,
        time: new Date().toISOString().replace('T', ' ').slice(0, 19),
        type: 'TRANSFER',
      })

      // Add audit log
      this.localAuditLogs.unshift({
        audit_id: `AUD-${Date.now().toString().slice(-5)}`,
        timestamp: new Date().toISOString().replace('T', ' ').slice(0, 19),
        action: 'TRANSFER_SETTLED',
        user_id: this.currentUserId,
        decision: 'ALLOW',
        outcome: 'SUCCESS',
        risk_level: otp ? 'RED' : 'YELLOW',
        request_id: `gw_res_${Date.now()}`,
        hash: Math.random().toString(16).slice(2, 34),
        prev_hash: this.localAuditLogs[0]?.hash || '00000000000000000000000000000000',
        details: { amount: deduct, approved_by_user: true },
      })
    }

    return true
  }
}

export const api = new BankingApiClient()
