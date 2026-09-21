import React from 'react'
import {
  Box,
  Typography,
  Chip,
  Button,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  useTheme,
} from '@mui/material'
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RechartsTooltip,
  Cell,
} from 'recharts'
import {
  Bot,
  User,
  ShieldAlert,
  Calendar,
  GitFork,
  ArrowRight,
} from 'lucide-react'
import type { AgentChatMessage } from '../../api/types'

interface MessageBubbleProps {
  message: AgentChatMessage
  onOpenApproval?: (data: NonNullable<AgentChatMessage['approval_required']>) => void
}

const BAR_COLORS = ['#0284C7', '#0EA5E9', '#38BDF8', '#7DD3FC', '#F59E0B', '#EF4444']

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  message,
  onOpenApproval,
}) => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'
  const isUser = message.role === 'user'

  // Format simple markdown into paragraphs, bullets and bold
  const renderFormattedText = (raw: string) => {
    const lines = raw.split('\n')
    return lines.map((line, idx) => {
      if (!line.trim()) return <Box key={idx} sx={{ height: 6 }} />

      // List item
      const isBullet = line.trim().startsWith('- ') || /^\d+\.\s/.test(line.trim())

      // Basic bold parsing: **text**
      const parts = line.split(/(\*\*.*?\*\*|`.*?`)/g)
      const renderedParts = parts.map((part, pIdx) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return (
            <strong key={pIdx} style={{ color: isUser ? '#FFFFFF' : isDark ? '#7DD3FC' : '#0B2545' }}>
              {part.slice(2, -2)}
            </strong>
          )
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code
              key={pIdx}
              style={{
                backgroundColor: isDark ? 'rgba(0, 0, 0, 0.3)' : 'rgba(0, 0, 0, 0.06)',
                padding: '2px 6px',
                borderRadius: '4px',
                fontSize: '0.85em',
                fontFamily: 'monospace',
              }}
            >
              {part.slice(1, -1)}
            </code>
          )
        }
        return part
      })

      return (
        <Typography
          key={idx}
          variant="body2"
          sx={{
            lineHeight: 1.6,
            mb: isBullet ? 0.4 : 0.8,
            pl: isBullet ? 1.5 : 0,
            fontSize: '0.9rem',
          }}
        >
          {renderedParts}
        </Typography>
      )
    })
  }

  return (
    <Box
      sx={{
        display: 'flex',
        flexDirection: isUser ? 'row-reverse' : 'row',
        alignItems: 'flex-start',
        gap: 1.5,
        mb: 2.5,
      }}
    >
      {/* Avatar */}
      <Box
        sx={{
          width: 38,
          height: 38,
          borderRadius: '12px',
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: isUser
            ? 'linear-gradient(135deg, #0284C7 0%, #2563EB 100%)'
            : isDark
            ? 'linear-gradient(135deg, #0B2545 0%, #1E293B 100%)'
            : 'linear-gradient(135deg, #0F172A 0%, #1E3A8A 100%)',
          color: '#FFFFFF',
          boxShadow: isUser
            ? '0 4px 10px rgba(2, 132, 199, 0.3)'
            : '0 4px 10px rgba(15, 23, 42, 0.2)',
        }}
      >
        {isUser ? <User size={18} /> : <Bot size={20} color="#38BDF8" />}
      </Box>

      {/* Bubble Container */}
      <Box sx={{ maxWidth: { xs: '88%', sm: '80%', md: '75%' } }}>
        <Box
          sx={{
            p: 2,
            borderRadius: isUser ? '18px 4px 18px 18px' : '4px 18px 18px 18px',
            backgroundColor: isUser
              ? isDark ? '#0284C7' : '#0B2545'
              : isDark ? '#131E3D' : '#FFFFFF',
            color: isUser ? '#FFFFFF' : theme.palette.text.primary,
            border: `1px solid ${
              isUser
                ? 'transparent'
                : isDark ? 'rgba(51, 65, 85, 0.6)' : 'rgba(226, 232, 240, 0.9)'
            }`,
            boxShadow: isDark
              ? '0 4px 16px rgba(0, 0, 0, 0.25)'
              : '0 4px 16px rgba(11, 37, 69, 0.05)',
          }}
        >
          {renderFormattedText(message.content)}

          {/* Render Expense Chart Widget if available */}
          {message.chart_data && message.chart_data.length > 0 && (
            <Box
              sx={{
                mt: 2,
                p: 2,
                borderRadius: 2.5,
                backgroundColor: isDark ? 'rgba(0, 0, 0, 0.25)' : 'rgba(244, 247, 251, 0.9)',
                border: `1px solid ${theme.palette.divider}`,
              }}
            >
              <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.text.secondary, mb: 1, display: 'block' }}>
                📊 开销类目金额分布 (CNY)
              </Typography>
              <Box sx={{ width: '100%', height: 160 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={message.chart_data} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                    <XAxis dataKey="category" tick={{ fontSize: 11, fill: theme.palette.text.secondary }} />
                    <YAxis tick={{ fontSize: 11, fill: theme.palette.text.secondary }} />
                    <RechartsTooltip
                      contentStyle={{
                        backgroundColor: isDark ? '#1E293B' : '#FFFFFF',
                        borderRadius: '8px',
                        border: `1px solid ${theme.palette.divider}`,
                        fontSize: '12px',
                      }}
                      formatter={(val: any) => [`¥${Number(val).toFixed(2)}`, '金额']}
                    />
                    <Bar dataKey="amount" radius={[4, 4, 0, 0]}>
                      {message.chart_data.map((_, index) => (
                        <Cell key={`cell-${index}`} fill={BAR_COLORS[index % BAR_COLORS.length]} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </Box>
            </Box>
          )}

          {/* Render Todo Card Widget (e.g. Birthday fund lock) */}
          {message.todo_card && (
            <Box
              sx={{
                mt: 2,
                p: 2,
                borderRadius: 2.5,
                background: isDark
                  ? 'linear-gradient(135deg, rgba(245, 158, 11, 0.1) 0%, rgba(217, 119, 6, 0.05) 100%)'
                  : 'linear-gradient(135deg, rgba(254, 243, 199, 0.8) 0%, rgba(253, 230, 138, 0.5) 100%)',
                border: '1px solid rgba(245, 158, 11, 0.3)',
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <Calendar size={18} color="#D97706" />
                <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#B45309' }}>
                  📌 {message.todo_card.title}
                </Typography>
              </Box>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1, fontSize: '0.8rem' }}>
                <Box>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>锁定资产：</Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600, color: '#D97706' }}>
                    {message.todo_card.locked_fund || '¥1,000.00'}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>调度周期：</Typography>
                  <Typography variant="body2" sx={{ fontWeight: 500 }}>
                    {message.todo_card.schedule || '自动唤醒'}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>任务编号：</Typography>
                  <Typography variant="body2" sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>
                    {message.todo_card.task_id || 'TASK-LK-01'}
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>任务状态：</Typography>
                  <Chip size="small" label="隔离保护中" color="warning" sx={{ height: 20, fontSize: '0.7rem' }} />
                </Box>
              </Box>
            </Box>
          )}

          {/* Render Execution Plan DAG Widget if present */}
          {message.plan && message.plan.tasks && message.plan.tasks.length > 0 && (
            <Box sx={{ mt: 2 }}>
              <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.text.secondary, display: 'flex', alignItems: 'center', gap: 0.5, mb: 0.8 }}>
                <GitFork size={13} /> LangGraph 任务 DAG 执行计划
              </Typography>
              <TableContainer component={Paper} elevation={0} sx={{ borderRadius: 2, border: `1px solid ${theme.palette.divider}` }}>
                <Table size="small">
                  <TableHead sx={{ backgroundColor: isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)' }}>
                    <TableRow>
                      <TableCell sx={{ fontSize: '0.72rem', py: 0.5 }}>任务节点</TableCell>
                      <TableCell sx={{ fontSize: '0.72rem', py: 0.5 }}>动作类别</TableCell>
                      <TableCell sx={{ fontSize: '0.72rem', py: 0.5 }}>风控级别</TableCell>
                      <TableCell sx={{ fontSize: '0.72rem', py: 0.5 }}>状态</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {message.plan.tasks.map((task) => (
                      <TableRow key={task.task_id}>
                        <TableCell sx={{ fontSize: '0.75rem', fontFamily: 'monospace', py: 0.5 }}>{task.task_id}</TableCell>
                        <TableCell sx={{ fontSize: '0.75rem', py: 0.5 }}>{task.action}</TableCell>
                        <TableCell sx={{ py: 0.5 }}>
                          <Chip
                            size="small"
                            label={task.risk_level}
                            sx={{
                              height: 18,
                              fontSize: '0.65rem',
                              fontWeight: 700,
                              backgroundColor:
                                task.risk_level === 'RED'
                                  ? 'rgba(239, 68, 68, 0.15)'
                                  : task.risk_level === 'YELLOW'
                                  ? 'rgba(245, 158, 11, 0.15)'
                                  : 'rgba(16, 185, 129, 0.15)',
                              color:
                                task.risk_level === 'RED'
                                  ? '#EF4444'
                                  : task.risk_level === 'YELLOW'
                                  ? '#F59E0B'
                                  : '#10B981',
                            }}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.72rem', py: 0.5 }}>{task.status}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </Box>
          )}

          {/* Action Button for Pending Approval */}
          {message.approval_required && onOpenApproval && (
            <Box
              sx={{
                mt: 2,
                p: 1.5,
                borderRadius: 2.5,
                backgroundColor:
                  message.approval_required.risk_level === 'RED'
                    ? 'rgba(239, 68, 68, 0.1)'
                    : 'rgba(245, 158, 11, 0.1)',
                border: `1px dashed ${
                  message.approval_required.risk_level === 'RED' ? '#EF4444' : '#F59E0B'
                }`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                flexWrap: 'wrap',
                gap: 1,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <ShieldAlert
                  size={20}
                  color={message.approval_required.risk_level === 'RED' ? '#EF4444' : '#F59E0B'}
                />
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {message.approval_required.risk_level === 'RED' ? '需要红色双因子认证' : '需要黄色确认授权'}
                  </Typography>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                    点击按钮输入凭证或执行人脸核验
                  </Typography>
                </Box>
              </Box>
              <Button
                size="small"
                variant="contained"
                onClick={() => onOpenApproval(message.approval_required!)}
                endIcon={<ArrowRight size={14} />}
                sx={{
                  backgroundColor:
                    message.approval_required.risk_level === 'RED' ? '#EF4444' : '#F59E0B',
                  '&:hover': {
                    backgroundColor:
                      message.approval_required.risk_level === 'RED' ? '#DC2626' : '#D97706',
                  },
                }}
              >
                立即核验授权
              </Button>
            </Box>
          )}
        </Box>

        {/* Timestamp */}
        <Typography
          variant="caption"
          sx={{
            display: 'block',
            mt: 0.5,
            px: 1,
            color: theme.palette.text.secondary,
            textAlign: isUser ? 'right' : 'left',
            fontSize: '0.7rem',
          }}
        >
          {message.timestamp}
        </Typography>
      </Box>
    </Box>
  )
}
