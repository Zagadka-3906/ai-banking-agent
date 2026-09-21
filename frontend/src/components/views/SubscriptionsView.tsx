import React, { useState, useEffect } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Chip,
  Alert,
  useTheme,
} from '@mui/material'
import {
  RefreshCw,
  XCircle,
  Sparkles,
  Calendar,
} from 'lucide-react'
import type { SubscriptionItem } from '../../api/types'
import { api } from '../../api/client'

export const SubscriptionsView: React.FC = () => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [subscriptions, setSubscriptions] = useState<SubscriptionItem[]>([])
  const [notification, setNotification] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadSubscriptions = async () => {
    setLoading(true)
    try {
      const list = await api.getSubscriptions()
      setSubscriptions(list)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSubscriptions()
  }, [])

  const handleCancel = async (sub: SubscriptionItem) => {
    await api.cancelSubscription(sub.subscription_id)
    setNotification(`已成功为您取消订阅：${sub.merchant}，后续将不再产生代扣划款。`)
    loadSubscriptions()
  }

  const activeSubs = subscriptions.filter((s) => s.status === 'ACTIVE')
  const totalMonthly = activeSubs.reduce((sum, s) => sum + s.amount, 0)
  const totalAnnual = totalMonthly * 12

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            订阅托管与自动扣费清理
          </Typography>
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            智能识别隐形连续包月 · 到期前提醒 · 一键安全解约
          </Typography>
        </Box>
        <Button
          variant="outlined"
          size="small"
          onClick={loadSubscriptions}
          startIcon={<RefreshCw size={14} className={loading ? 'spin-animation' : ''} />}
          sx={{ borderRadius: 2 }}
        >
          重新扫描账单
        </Button>
      </Box>

      {notification && (
        <Alert severity="success" onClose={() => setNotification(null)} sx={{ borderRadius: 2.5 }}>
          {notification}
        </Alert>
      )}

      {/* Summary Card */}
      <Card
        elevation={0}
        sx={{
          borderRadius: 3.5,
          p: 2.5,
          border: `1px solid ${theme.palette.divider}`,
          backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
        }}
      >
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', sm: 'repeat(3, 1fr)' }, gap: 2 }}>
          <Box>
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
              活跃订阅总数
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#0284C7' }}>
              {activeSubs.length} 项
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
              月度固定支出预估
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#F59E0B' }}>
              ¥{totalMonthly.toFixed(2)} / 月
            </Typography>
          </Box>
          <Box>
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
              年度累计支出预估
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif' }}>
              ¥{totalAnnual.toFixed(2)} / 年
            </Typography>
          </Box>
        </Box>
      </Card>

      {/* Subscriptions List */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)' }, gap: 2.5 }}>
        {subscriptions.map((sub) => {
          const isCancelled = sub.status === 'CANCELLED'

          return (
            <Card
              key={sub.subscription_id}
              elevation={0}
              sx={{
                borderRadius: 3.5,
                border: `1px solid ${theme.palette.divider}`,
                backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                opacity: isCancelled ? 0.6 : 1,
              }}
            >
              <CardContent sx={{ p: 2.5 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
                  <Box>
                    <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.05rem' }}>
                      {sub.merchant}
                    </Typography>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      代扣编号: {sub.subscription_id} · {sub.category || '数字生活'}
                    </Typography>
                  </Box>
                  <Chip
                    label={isCancelled ? '已取消代扣' : '持续扣费中'}
                    size="small"
                    color={isCancelled ? 'default' : 'success'}
                    sx={{ height: 22, fontSize: '0.7rem', fontWeight: 600 }}
                  />
                </Box>

                <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, my: 1 }}>
                  <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#0284C7' }}>
                    ¥{sub.amount.toFixed(2)}
                  </Typography>
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                    / 月 (周期性扣款)
                  </Typography>
                </Box>

                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, my: 1.5 }}>
                  <Calendar size={14} color={theme.palette.text.secondary} />
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                    下期扣款预定时间: <strong>{sub.next_charge}</strong>
                  </Typography>
                </Box>

                <Box sx={{ pt: 1, display: 'flex', justifyContent: 'flex-end' }}>
                  {!isCancelled ? (
                    <Button
                      size="small"
                      color="error"
                      variant="outlined"
                      startIcon={<XCircle size={14} />}
                      onClick={() => handleCancel(sub)}
                      sx={{ borderRadius: 2 }}
                    >
                      一键取消订阅
                    </Button>
                  ) : (
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      该代扣已终止，不再续费
                    </Typography>
                  )}
                </Box>
              </CardContent>
            </Card>
          )
        })}
      </Box>

      {/* Tip Banner */}
      <Alert
        severity="info"
        icon={<Sparkles size={18} />}
        sx={{ borderRadius: 2.5 }}
      >
        您也可以直接在 AI 对话框中对安言说：<strong>“帮我取消 Apple 订阅”</strong>，系统将自动匹配商户并代为发起取消流程。
      </Alert>
    </Box>
  )
}
