import React, { useState, useRef, useEffect } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  TextField,
  IconButton,
  Button,
  Chip,
  CircularProgress,
  Tooltip,
  useTheme,
} from '@mui/material'
import {
  Send,
  RotateCcw,
  Bot,
  ArrowUpRight,
} from 'lucide-react'
import type { AgentChatMessage } from '../../api/types'
import { api } from '../../api/client'
import { MessageBubble } from './MessageBubble'
import { ApprovalModal } from './ApprovalModal'

interface AgentChatProps {
  onTransactionSettled?: () => void
}

const DEFAULT_PROMPTS = [
  '查余额',
  '给我老婆转账 300 元买菜',
  '给李四转 2000 元 (触发红色MFA)',
  '分析上月账单有没有异常',
  '我爱人生日下周，锁定 1000 元并提醒订花',
  '推荐稳健理财产品',
  '查看订阅并取消 Apple',
]

export const AgentChat: React.FC<AgentChatProps> = ({ onTransactionSettled }) => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'
  const chatBottomRef = useRef<HTMLDivElement>(null)

  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [messages, setMessages] = useState<AgentChatMessage[]>([
    {
      id: 'm-welcome',
      role: 'assistant',
      content:
        '您好！我是您的 **安言 AI 金融助理**。\n您可以随时对我说：\n- *“查余额”* 或 *“分析上月账单”*\n- *“给我老婆转账 300 元买菜”*\n- *“我爱人生日下周，锁定 1000 元并提醒订花”*\n- *“推荐稳健理财”* 或 *“管理名下卡片与订阅”*\n\n所有交易严格遵循绿、黄、红三级风控与 Mock 核心银行唯一记账。',
      timestamp: '刚刚',
    },
  ])

  // Approval modal state
  const [activeApproval, setActiveApproval] = useState<NonNullable<AgentChatMessage['approval_required']> | null>(null)

  const scrollToBottom = () => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading])

  const handleSend = async (textToSend?: string) => {
    const text = (textToSend || input).trim()
    if (!text || loading) return

    const userMsg: AgentChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
    }

    setMessages((prev) => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const response = await api.sendAgentMessage(text)
      const assistantMsg: AgentChatMessage = {
        id: `a-${Date.now()}`,
        role: 'assistant',
        content: response.content || '已为您处理完毕。',
        timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        chart_data: response.chart_data,
        todo_card: response.todo_card,
        plan: response.plan,
        approval_required: response.approval_required,
      }

      setMessages((prev) => [...prev, assistantMsg])

      // If approval is required, auto open modal for great UX
      if (response.approval_required) {
        setActiveApproval(response.approval_required)
      }

      // If action settled immediately (like birthday lock)
      if (response.todo_card && onTransactionSettled) {
        onTransactionSettled()
      }
    } catch (e: any) {
      setMessages((prev) => [
        ...prev,
        {
          id: `err-${Date.now()}`,
          role: 'assistant',
          content: `⚠️ 处理异常：${e?.message || '未能连接到银行渠道网关'}`,
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        },
      ])
    } finally {
      setLoading(false)
    }
  }

  const handleConfirmApproval = async (
    decision: 'approve' | 'reject',
    otp?: string,
    faceAttestation?: string
  ) => {
    if (!activeApproval) return

    await api.resolveApproval(activeApproval.approval_id, decision, otp, faceAttestation)

    if (decision === 'approve') {
      setMessages((prev) => [
        ...prev,
        {
          id: `res-${Date.now()}`,
          role: 'assistant',
          content: `✅ **核验授权成功！**\n核心银行已记录该笔交易，流水哈希链已持久化。已向对方账户完成划转。`,
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        },
      ])
      if (onTransactionSettled) {
        onTransactionSettled()
      }
    } else {
      setMessages((prev) => [
        ...prev,
        {
          id: `res-${Date.now()}`,
          role: 'assistant',
          content: `❌ 操作已由用户手动取消，核心账户未发生任何资金变动。`,
          timestamp: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        },
      ])
    }
  }

  const handleReset = () => {
    setMessages([
      {
        id: 'm-welcome',
        role: 'assistant',
        content:
          '会话已重置。您可以重新输入任意自然语言金融业务需求。',
        timestamp: '刚刚',
      },
    ])
  }

  return (
    <Card
      elevation={0}
      sx={{
        borderRadius: 4,
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 560,
        backgroundColor: isDark ? 'rgba(19, 30, 61, 0.7)' : '#FFFFFF',
        border: `1px solid ${theme.palette.divider}`,
        overflow: 'hidden',
      }}
    >
      {/* Header */}
      <Box
        sx={{
          p: 2,
          px: 2.5,
          borderBottom: `1px solid ${theme.palette.divider}`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: isDark ? 'rgba(11, 19, 43, 0.5)' : 'rgba(244, 247, 251, 0.6)',
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.2 }}>
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: '10px',
              backgroundColor: isDark ? 'rgba(56, 189, 248, 0.15)' : 'rgba(2, 132, 199, 0.1)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Bot size={18} color="#0284C7" />
          </Box>
          <Box>
            <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: '0.95rem' }}>
              安言 AI 交互中心
            </Typography>
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
              自然语言意图理解 · 实时策略防范
            </Typography>
          </Box>
        </Box>
        <Tooltip title="重置对话">
          <IconButton size="small" onClick={handleReset}>
            <RotateCcw size={16} />
          </IconButton>
        </Tooltip>
      </Box>

      {/* Message History List */}
      <CardContent
        sx={{
          flex: 1,
          overflowY: 'auto',
          p: { xs: 1.5, sm: 2.5 },
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            onOpenApproval={(appr) => setActiveApproval(appr)}
          />
        ))}

        {loading && (
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, my: 1.5, pl: 1 }}>
            <CircularProgress size={18} thickness={5} sx={{ color: '#0284C7' }} />
            <Typography variant="body2" sx={{ color: theme.palette.text.secondary, fontStyle: 'italic', fontSize: '0.85rem' }}>
              安言正在意图解析与风控预检中…
            </Typography>
          </Box>
        )}

        <div ref={chatBottomRef} />
      </CardContent>

      {/* Prompt Suggestions */}
      <Box
        sx={{
          px: 2,
          py: 1,
          borderTop: `1px solid ${theme.palette.divider}`,
          backgroundColor: isDark ? 'rgba(0, 0, 0, 0.15)' : 'rgba(0, 0, 0, 0.01)',
          display: 'flex',
          gap: 0.8,
          overflowX: 'auto',
          whiteSpace: 'nowrap',
          '&::-webkit-scrollbar': { height: 4 },
          '&::-webkit-scrollbar-thumb': { backgroundColor: 'rgba(100, 100, 100, 0.2)', borderRadius: 2 },
        }}
      >
        {DEFAULT_PROMPTS.map((p, idx) => (
          <Chip
            key={idx}
            label={p}
            size="small"
            onClick={() => handleSend(p)}
            icon={<ArrowUpRight size={12} />}
            sx={{
              fontSize: '0.75rem',
              fontWeight: 500,
              cursor: 'pointer',
              backgroundColor: isDark ? 'rgba(56, 189, 248, 0.08)' : 'rgba(2, 132, 199, 0.06)',
              color: isDark ? '#7DD3FC' : '#0B2545',
              border: `1px solid ${isDark ? 'rgba(56, 189, 248, 0.2)' : 'rgba(2, 132, 199, 0.15)'}`,
              '&:hover': {
                backgroundColor: isDark ? 'rgba(56, 189, 248, 0.18)' : 'rgba(2, 132, 199, 0.12)',
              },
            }}
          />
        ))}
      </Box>

      {/* Input Bar */}
      <Box
        sx={{
          p: 1.5,
          px: 2,
          borderTop: `1px solid ${theme.palette.divider}`,
          display: 'flex',
          alignItems: 'center',
          gap: 1,
          backgroundColor: isDark ? 'rgba(11, 19, 43, 0.4)' : '#FFFFFF',
        }}
      >
        <TextField
          fullWidth
          size="small"
          placeholder="请输入银行业务指令（如：转账 300 给老婆、查账单、锁资）…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          disabled={loading}
          sx={{
            '& .MuiOutlinedInput-root': {
              borderRadius: 3,
              backgroundColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.02)',
            },
          }}
        />
        <Button
          variant="contained"
          disabled={loading || !input.trim()}
          onClick={() => handleSend()}
          sx={{
            minWidth: 44,
            height: 40,
            borderRadius: 2.5,
            px: 2,
            background: 'linear-gradient(135deg, #0284C7 0%, #0B2545 100%)',
          }}
        >
          <Send size={18} />
        </Button>
      </Box>

      {/* Risk Approval Modal */}
      <ApprovalModal
        open={Boolean(activeApproval)}
        onClose={() => setActiveApproval(null)}
        approvalData={activeApproval}
        onConfirm={handleConfirmApproval}
      />
    </Card>
  )
}
