export type RiskLevel = 'GREEN' | 'YELLOW' | 'RED'

export interface UserAccount {
  user_id: string
  user_name: string
  card_no: string
  phone: string
  total_balance: number
  available_balance: number
  locked_amount: number
  status: 'ACTIVE' | 'FROZEN' | 'LOCKED'
  currency?: string
}

export interface TransactionItem {
  tx_id: string
  account_id: string
  merchant: string
  category: string
  amount: number
  time: string
  card_id?: string
  country?: string
  type?: 'EXPENSE' | 'INCOME' | 'TRANSFER'
}

export interface BillAnalysis {
  user_id: string
  date_scope: string
  total_spend: number
  monthly_average: number
  category_distribution: Array<{ category: string; amount: number; percentage: number }>
  anomalies: Array<{ tx_id: string; merchant: string; reason: string; amount: number }>
  insights: string[]
}

export interface WealthProduct {
  product_id: string
  name: string
  category: string
  risk_level: 'R1' | 'R2' | 'R3' | 'R4' | 'R5'
  benchmark: string
  annualized_yield?: number | null
  min_amount: number
  liquidity: string
  term: string
  underlying: string
  target_clients: string
  summary?: string
  latest_nav?: number
  daily_change_pct?: number
  annual_return_pct?: number
}

export interface WealthHolding {
  holding_id: string
  product_id: string
  product_name: string
  amount: number
  current_nav: number
  accumulated_profit: number
  holding_units: number
  purchase_date: string
}

export interface BankCard {
  card_id: string
  pan_ref?: string
  number_masked: string
  last4: string
  type: string
  status: 'ACTIVE' | 'FROZEN' | 'LOST'
  daily_limit: number
  label: string
  expiry?: string
  cvv?: string
  issuer?: string
}

export interface SubscriptionItem {
  subscription_id: string
  merchant: string
  amount: number
  next_charge: string
  status: 'ACTIVE' | 'CANCELLED' | 'PAUSED'
  category?: string
}

export interface ScheduledTask {
  task_id: string
  task_type: string
  user_id: string
  scheduled_time?: string
  status: 'PENDING' | 'WAITING_USER' | 'EXECUTED' | 'FAILED' | 'CANCELLED'
  description?: string
  locked_fund?: number
  payload?: Record<string, any>
  created_at?: string
}

export interface AuditLogEntry {
  audit_id: string
  timestamp: string
  action: string
  user_id: string
  decision: 'ALLOW' | 'BLOCK' | 'REQUIRE_CONFIRMATION' | 'REQUIRE_MFA'
  outcome: 'SUCCESS' | 'FAILURE' | 'PENDING'
  risk_level: RiskLevel
  request_id: string
  hash: string
  prev_hash: string
  details?: Record<string, any>
}

export interface SecurityStatus {
  service_status: 'NORMAL' | 'CIRCUIT_BROKEN' | 'DEGRADED'
  rate_limit_usage: number
  circuit_breaker_open: boolean
  failed_attempts_today: number
  mfa_required_level: RiskLevel
  last_security_event?: string
}

export interface DagNode {
  task_id: string
  action: string
  depends_on: string[]
  risk_level: RiskLevel
  status: 'PENDING' | 'EXECUTING' | 'COMPLETED' | 'FAILED'
  details?: string
}

export interface AgentChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: string
  chart_data?: Array<{ category: string; amount: number }>
  todo_card?: {
    title: string
    locked_fund?: string
    schedule?: string
    status?: string
    task_id?: string
    order_task_ids?: string[]
  }
  plan?: {
    tasks: DagNode[]
  }
  approval_required?: {
    approval_id: string
    auth_token?: string
    risk_level: RiskLevel
    message: string
    request_id: string
    challenge_id?: string
    auth_token_jti?: string
  }
  isStreaming?: boolean
}
