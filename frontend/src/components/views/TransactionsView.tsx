import React, { useState, useEffect, useCallback } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  Chip,
  TextField,
  InputAdornment,
  ButtonGroup,
  Button,
  Alert,
  useTheme,
} from '@mui/material'
import {
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  Tooltip as RechartsTooltip,
} from 'recharts'
import {
  Search,
  ArrowDownLeft,
  ArrowUpRight,
  ShieldAlert,
  Sparkles,
  PieChart as PieIcon,
  RefreshCw,
} from 'lucide-react'
import type { TransactionItem, BillAnalysis } from '../../api/types'
import { api } from '../../api/client'

const CATEGORY_COLORS: Record<string, string> = {
  餐饮美食: '#0284C7',
  餐饮: '#0284C7',
  日常出行: '#10B981',
  交通出行: '#10B981',
  可疑风控: '#EF4444',
  可疑交易: '#EF4444',
  数字订阅: '#8B5CF6',
  订阅代扣: '#8B5CF6',
  工资收入: '#059669',
  转账汇款: '#F59E0B',
}

export const TransactionsView: React.FC = () => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [transactions, setTransactions] = useState<TransactionItem[]>([])
  const [billAnalysis, setBillAnalysis] = useState<BillAnalysis | null>(null)
  const [dateScope, setDateScope] = useState('ALL')
  const [searchQuery, setSearchQuery] = useState('')
  const [loading, setLoading] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const [txs, analysis] = await Promise.all([
        api.getTransactions(dateScope),
        api.getBillAnalysis(),
      ])
      setTransactions(txs)
      setBillAnalysis(analysis)
    } finally {
      setLoading(false)
    }
  }, [dateScope])

  useEffect(() => {
    loadData()
  }, [loadData])

  const filteredTransactions = transactions.filter((tx) => {
    const q = searchQuery.toLowerCase()
    return (
      tx.merchant.toLowerCase().includes(q) ||
      tx.category.toLowerCase().includes(q) ||
      tx.tx_id.toLowerCase().includes(q)
    )
  })

  const pieData =
    billAnalysis?.category_distribution.map((item) => ({
      name: item.category,
      value: item.amount,
    })) || []

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header Controls */}
      <Box
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: 2,
        }}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            账户流水与账单智能分析
          </Typography>
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            引用真实流水事实 · 异常风控标记 · 智能消费结构画像
          </Typography>
        </Box>

        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, flexWrap: 'wrap' }}>
          <TextField
            size="small"
            placeholder="搜索商户、分类或单号…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            slotProps={{
              input: {
                startAdornment: (
                  <InputAdornment position="start">
                    <Search size={16} color={theme.palette.text.secondary} />
                  </InputAdornment>
                ),
              },
            }}
            sx={{ width: 220, '& .MuiOutlinedInput-root': { borderRadius: 2 } }}
          />

          <ButtonGroup size="small" variant="outlined" sx={{ borderRadius: 2 }}>
            {[
              { key: 'ALL', label: '全部' },
              { key: 'MONTH', label: '近1月' },
              { key: 'LAST_MONTH', label: '上月' },
              { key: 'YEAR', label: '今年' },
            ].map((scope) => (
              <Button
                key={scope.key}
                variant={dateScope === scope.key ? 'contained' : 'outlined'}
                onClick={() => setDateScope(scope.key)}
                sx={{ fontSize: '0.78rem' }}
              >
                {scope.label}
              </Button>
            ))}
          </ButtonGroup>

          <Button
            size="small"
            variant="outlined"
            onClick={loadData}
            startIcon={<RefreshCw size={14} className={loading ? 'spin-animation' : ''} />}
            sx={{ borderRadius: 2, fontSize: '0.78rem' }}
          >
            刷新
          </Button>
        </Box>
      </Box>

      {/* Bill Analysis Insight Cards */}
      {billAnalysis && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '7fr 5fr' }, gap: 2.5 }}>
          {/* AI Insights & Anomalies */}
          <Card
            elevation={0}
            sx={{
              borderRadius: 3.5,
              border: `1px solid ${theme.palette.divider}`,
              backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
            }}
          >
            <CardContent sx={{ p: 2.5 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
                <Sparkles size={18} color="#0284C7" />
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                  安言 · 真实流水洞察与异常警示
                </Typography>
              </Box>

              {billAnalysis.anomalies.length > 0 && (
                <Alert
                  severity="warning"
                  icon={<ShieldAlert size={20} color="#DC2626" />}
                  sx={{
                    mb: 2,
                    borderRadius: 2.5,
                    border: '1px solid rgba(220, 38, 38, 0.3)',
                    backgroundColor: isDark ? 'rgba(220, 38, 38, 0.12)' : 'rgba(254, 242, 242, 0.9)',
                  }}
                >
                  <Typography variant="subtitle2" sx={{ fontWeight: 700, color: '#DC2626' }}>
                    检测到 1 笔高风险交易：{billAnalysis.anomalies[0].merchant} (¥{billAnalysis.anomalies[0].amount.toFixed(2)})
                  </Typography>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                    {billAnalysis.anomalies[0].reason}。建议前往卡片中心一键冻结或设定境外支付限额。
                  </Typography>
                </Alert>
              )}

              <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.text.secondary, mb: 1, display: 'block' }}>
                核心消费趋势分析：
              </Typography>
              <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
                {billAnalysis.insights.map((insight, idx) => (
                  <Box
                    key={idx}
                    sx={{
                      p: 1.2,
                      px: 1.5,
                      borderRadius: 2,
                      backgroundColor: isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.02)',
                      fontSize: '0.85rem',
                      display: 'flex',
                      alignItems: 'baseline',
                      gap: 1,
                    }}
                  >
                    <span style={{ color: '#0284C7', fontWeight: 700 }}>•</span>
                    <Typography variant="body2" sx={{ fontSize: '0.85rem' }}>
                      {insight}
                    </Typography>
                  </Box>
                ))}
              </Box>
            </CardContent>
          </Card>

          {/* Category Distribution Chart */}
          <Card
            elevation={0}
            sx={{
              borderRadius: 3.5,
              border: `1px solid ${theme.palette.divider}`,
              backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
            }}
          >
            <CardContent sx={{ p: 2.5 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <PieIcon size={18} color="#0284C7" />
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                  支出类目占比画像
                </Typography>
              </Box>

              <Box sx={{ display: 'flex', alignItems: 'center', height: 200 }}>
                <Box sx={{ width: '60%', height: '100%' }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        innerRadius={50}
                        outerRadius={75}
                        paddingAngle={3}
                        dataKey="value"
                      >
                        {pieData.map((entry, index) => (
                          <Cell
                            key={`cell-${index}`}
                            fill={CATEGORY_COLORS[entry.name] || '#0284C7'}
                          />
                        ))}
                      </Pie>
                      <RechartsTooltip
                        formatter={(val: any) => [`¥${Number(val).toFixed(2)}`, '支出']}
                        contentStyle={{
                          backgroundColor: isDark ? '#1E293B' : '#FFFFFF',
                          borderRadius: '8px',
                          border: `1px solid ${theme.palette.divider}`,
                        }}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                </Box>

                <Box sx={{ width: '40%', display: 'flex', flexDirection: 'column', gap: 1 }}>
                  {billAnalysis.category_distribution.map((cat, idx) => (
                    <Box key={idx} sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.8 }}>
                        <Box
                          sx={{
                            width: 10,
                            height: 10,
                            borderRadius: '50%',
                            backgroundColor: CATEGORY_COLORS[cat.category] || '#0284C7',
                          }}
                        />
                        <Typography variant="caption" sx={{ fontWeight: 500 }}>
                          {cat.category}
                        </Typography>
                      </Box>
                      <Typography variant="caption" sx={{ fontWeight: 600, color: theme.palette.text.secondary }}>
                        {cat.percentage}%
                      </Typography>
                    </Box>
                  ))}
                </Box>
              </Box>
            </CardContent>
          </Card>
        </Box>
      )}

      {/* Transactions Table */}
      <TableContainer
        component={Paper}
        elevation={0}
        sx={{
          borderRadius: 3.5,
          border: `1px solid ${theme.palette.divider}`,
          backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
          overflow: 'hidden',
        }}
      >
        <Table>
          <TableHead sx={{ backgroundColor: isDark ? 'rgba(255, 255, 255, 0.03)' : 'rgba(0, 0, 0, 0.02)' }}>
            <TableRow>
              <TableCell sx={{ fontWeight: 600, fontSize: '0.82rem' }}>交易编号</TableCell>
              <TableCell sx={{ fontWeight: 600, fontSize: '0.82rem' }}>交易对手 / 商户</TableCell>
              <TableCell sx={{ fontWeight: 600, fontSize: '0.82rem' }}>支出分类</TableCell>
              <TableCell sx={{ fontWeight: 600, fontSize: '0.82rem' }}>交易时间</TableCell>
              <TableCell sx={{ fontWeight: 600, fontSize: '0.82rem' }}>核验凭据</TableCell>
              <TableCell align="right" sx={{ fontWeight: 600, fontSize: '0.82rem' }}>
                金额 (CNY)
              </TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {filteredTransactions.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} align="center" sx={{ py: 4, color: theme.palette.text.secondary }}>
                  暂无符合条件的交易流水记录
                </TableCell>
              </TableRow>
            ) : (
              filteredTransactions.map((tx) => {
                const isIncome = tx.amount > 0
                const isSuspicious = tx.category === '可疑交易' || tx.amount === -999.0

                return (
                  <TableRow
                    key={tx.tx_id}
                    hover
                    sx={{
                      backgroundColor: isSuspicious
                        ? isDark ? 'rgba(239, 68, 68, 0.08)' : 'rgba(254, 242, 242, 0.6)'
                        : 'inherit',
                    }}
                  >
                    <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.8rem', color: theme.palette.text.secondary }}>
                      {tx.tx_id}
                    </TableCell>
                    <TableCell sx={{ fontWeight: 600, fontSize: '0.88rem' }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        {isIncome ? (
                          <ArrowDownLeft size={16} color="#059669" />
                        ) : (
                          <ArrowUpRight size={16} color={isSuspicious ? '#DC2626' : theme.palette.text.secondary} />
                        )}
                        {tx.merchant}
                      </Box>
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label={tx.category}
                        sx={{
                          height: 22,
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          backgroundColor: isSuspicious
                            ? 'rgba(239, 68, 68, 0.15)'
                            : isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.05)',
                          color: isSuspicious ? '#EF4444' : theme.palette.text.primary,
                        }}
                      />
                    </TableCell>
                    <TableCell sx={{ fontSize: '0.8rem', color: theme.palette.text.secondary }}>
                      {tx.time}
                    </TableCell>
                    <TableCell>
                      <Chip
                        size="small"
                        label="核心已对账"
                        variant="outlined"
                        color="success"
                        sx={{ height: 20, fontSize: '0.68rem' }}
                      />
                    </TableCell>
                    <TableCell
                      align="right"
                      sx={{
                        fontFamily: '"Outfit", sans-serif',
                        fontWeight: 700,
                        fontSize: '0.95rem',
                        color: isIncome ? '#059669' : isSuspicious ? '#DC2626' : theme.palette.text.primary,
                      }}
                    >
                      {isIncome ? `+¥${tx.amount.toFixed(2)}` : `-¥${Math.abs(tx.amount).toFixed(2)}`}
                    </TableCell>
                  </TableRow>
                )
              })
            )}
          </TableBody>
        </Table>
      </TableContainer>
    </Box>
  )
}
