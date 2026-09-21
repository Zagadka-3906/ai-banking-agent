import React from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Button,
  Chip,
  Tooltip,
  useTheme,
} from '@mui/material'
import {
  Wallet,
  Lock,
  ArrowRightLeft,
  PieChart,
  TrendingUp,
  CreditCard,
  RefreshCw,
  Sparkles,
} from 'lucide-react'
import type { UserAccount } from '../api/types'

interface AssetBannerProps {
  account: UserAccount | null
  loading: boolean
  onRefresh: () => void
  onQuickAction: (action: string) => void
}

export const AssetBanner: React.FC<AssetBannerProps> = ({
  account,
  loading,
  onRefresh,
  onQuickAction,
}) => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const total = account?.total_balance ?? 15000.0
  const available = account?.available_balance ?? 14000.0
  const locked = account?.locked_amount ?? 1000.0

  return (
    <Card
      elevation={0}
      sx={{
        borderRadius: 4,
        overflow: 'hidden',
        position: 'relative',
        background: isDark
          ? 'linear-gradient(135deg, #0F172A 0%, #1E293B 50%, #0F2347 100%)'
          : 'linear-gradient(135deg, #0B2545 0%, #134074 60%, #0077B6 100%)',
        color: '#FFFFFF',
        boxShadow: isDark
          ? '0 12px 36px rgba(0, 0, 0, 0.45)'
          : '0 12px 36px rgba(11, 37, 69, 0.25)',
        border: `1px solid ${isDark ? 'rgba(56, 189, 248, 0.2)' : 'rgba(255, 255, 255, 0.15)'}`,
      }}
    >
      {/* Background Decorative Rings */}
      <Box
        sx={{
          position: 'absolute',
          right: -40,
          top: -40,
          width: 220,
          height: 220,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(56, 189, 248, 0.15) 0%, rgba(2, 132, 199, 0.02) 70%)',
          pointerEvents: 'none',
        }}
      />
      <Box
        sx={{
          position: 'absolute',
          right: 80,
          bottom: -50,
          width: 180,
          height: 180,
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(45, 212, 191, 0.12) 0%, rgba(13, 148, 136, 0.01) 70%)',
          pointerEvents: 'none',
        }}
      />

      <CardContent sx={{ p: { xs: 2.5, md: 3.5 } }}>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '7fr 5fr' }, gap: 3, alignItems: 'center' }}>
          {/* Main Balance Info */}
          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                <Wallet size={18} color="#7DD3FC" />
                <Typography variant="body2" sx={{ color: 'rgba(255, 255, 255, 0.8)', fontWeight: 500 }}>
                  核心账户资产总览
                </Typography>
                <Chip
                  label={account?.status === 'ACTIVE' ? '正常履约' : '受限'}
                  size="small"
                  sx={{
                    height: 20,
                    fontSize: '0.68rem',
                    fontWeight: 600,
                    backgroundColor: 'rgba(45, 212, 191, 0.25)',
                    color: '#5EEAD4',
                    border: '1px solid rgba(45, 212, 191, 0.4)',
                  }}
                />
              </Box>
              <Tooltip title="刷新实时账户余额">
                <Button
                  size="small"
                  onClick={onRefresh}
                  disabled={loading}
                  startIcon={<RefreshCw size={14} className={loading ? 'spin-animation' : ''} />}
                  sx={{
                    color: 'rgba(255, 255, 255, 0.8)',
                    minWidth: 'auto',
                    px: 1,
                    fontSize: '0.75rem',
                    '&:hover': { color: '#FFFFFF', backgroundColor: 'rgba(255, 255, 255, 0.1)' },
                  }}
                >
                  刷新
                </Button>
              </Tooltip>
            </Box>

            {/* Total Balance Amount */}
            <Box sx={{ display: 'flex', alignItems: 'baseline', gap: 1, my: 1 }}>
              <Typography variant="h5" sx={{ fontWeight: 500, color: 'rgba(255, 255, 255, 0.85)' }}>
                ¥
              </Typography>
              <Typography
                variant="h3"
                sx={{
                  fontWeight: 700,
                  fontFamily: '"Outfit", sans-serif',
                  letterSpacing: '-0.03em',
                  color: '#FFFFFF',
                  textShadow: '0 2px 10px rgba(0,0,0,0.2)',
                }}
              >
                {total.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </Typography>
              <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.65)', ml: 0.5 }}>
                CNY 总额
              </Typography>
            </Box>

            {/* Sub balances: Available vs Locked */}
            <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: { xs: 2, sm: 4 }, mt: 2 }}>
              <Box>
                <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.7)', display: 'block' }}>
                  可用资金 (可支配)
                </Typography>
                <Typography variant="subtitle1" sx={{ fontWeight: 600, color: '#38BDF8', fontFamily: '"Outfit", sans-serif' }}>
                  ¥{available.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                </Typography>
              </Box>

              <Box>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                  <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.7)' }}>
                    已隔离锁定资金
                  </Typography>
                  <Tooltip title="包括爱人生日专项关怀锁资、预约扣款锁定等，不可被日常支付透支">
                    <Lock size={12} color="#FBBF24" />
                  </Tooltip>
                </Box>
                <Typography variant="subtitle1" sx={{ fontWeight: 600, color: '#FBBF24', fontFamily: '"Outfit", sans-serif' }}>
                  ¥{locked.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                </Typography>
              </Box>

              <Box>
                <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.7)', display: 'block' }}>
                  户名 / 主卡号
                </Typography>
                <Typography variant="subtitle2" sx={{ fontWeight: 500, color: '#E2E8F0' }}>
                  {account?.user_name || '张三'} · **** 3082
                </Typography>
              </Box>
            </Box>
          </Box>

          {/* Quick Shortcuts Pill Buttons */}
          <Box>
            <Box
              sx={{
                p: 2,
                borderRadius: 3,
                backgroundColor: 'rgba(0, 0, 0, 0.22)',
                border: '1px solid rgba(255, 255, 255, 0.1)',
                backdropFilter: 'blur(8px)',
              }}
            >
              <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.8)', fontWeight: 600, mb: 1.5, display: 'flex', alignItems: 'center', gap: 0.5 }}>
                <Sparkles size={14} color="#38BDF8" /> 智能银行快捷入口
              </Typography>
              <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1 }}>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<ArrowRightLeft size={14} />}
                  onClick={() => onQuickAction('transfer')}
                  sx={{
                    color: '#FFFFFF',
                    borderColor: 'rgba(255, 255, 255, 0.25)',
                    justifyContent: 'flex-start',
                    fontSize: '0.8rem',
                    py: 0.8,
                    '&:hover': { borderColor: '#38BDF8', backgroundColor: 'rgba(56, 189, 248, 0.1)' },
                  }}
                >
                  语音/文字转账
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<PieChart size={14} />}
                  onClick={() => onQuickAction('bills')}
                  sx={{
                    color: '#FFFFFF',
                    borderColor: 'rgba(255, 255, 255, 0.25)',
                    justifyContent: 'flex-start',
                    fontSize: '0.8rem',
                    py: 0.8,
                    '&:hover': { borderColor: '#38BDF8', backgroundColor: 'rgba(56, 189, 248, 0.1)' },
                  }}
                >
                  账单消费分析
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<TrendingUp size={14} />}
                  onClick={() => onQuickAction('wealth')}
                  sx={{
                    color: '#FFFFFF',
                    borderColor: 'rgba(255, 255, 255, 0.25)',
                    justifyContent: 'flex-start',
                    fontSize: '0.8rem',
                    py: 0.8,
                    '&:hover': { borderColor: '#38BDF8', backgroundColor: 'rgba(56, 189, 248, 0.1)' },
                  }}
                >
                  稳健理财优选
                </Button>
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<CreditCard size={14} />}
                  onClick={() => onQuickAction('cards')}
                  sx={{
                    color: '#FFFFFF',
                    borderColor: 'rgba(255, 255, 255, 0.25)',
                    justifyContent: 'flex-start',
                    fontSize: '0.8rem',
                    py: 0.8,
                    '&:hover': { borderColor: '#38BDF8', backgroundColor: 'rgba(56, 189, 248, 0.1)' },
                  }}
                >
                  安全卡片管理
                </Button>
              </Box>
            </Box>
          </Box>
        </Box>
      </CardContent>
    </Card>
  )
}
