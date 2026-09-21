import React, { useState, useEffect, useCallback } from 'react'
import {
  Box,
  Card,
  Typography,
  Button,
  Chip,
  Slider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
  Tooltip,
  useTheme,
} from '@mui/material'
import {
  Lock,
  Unlock,
  Plus,
  Sliders,
  CheckCircle2,
  Wifi,
  Eye,
  EyeOff,
} from 'lucide-react'
import type { BankCard } from '../../api/types'
import { api } from '../../api/client'

export const CardsView: React.FC = () => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [cards, setCards] = useState<BankCard[]>([])
  const [selectedCard, setSelectedCard] = useState<BankCard | null>(null)
  const [showFullNumber, setShowFullNumber] = useState(false)
  const [newLimit, setNewLimit] = useState<number>(5000)
  const [limitModalOpen, setLimitModalOpen] = useState(false)
  const [applyModalOpen, setApplyModalOpen] = useState(false)
  const [notification, setNotification] = useState<string | null>(null)

  const loadCards = useCallback(async () => {
    const list = await api.getCards()
    setCards(list)
    if (list.length > 0 && !selectedCard) {
      setSelectedCard(list[0])
      setNewLimit(list[0].daily_limit)
    }
  }, [selectedCard])

  useEffect(() => {
    loadCards()
  }, [loadCards])

  const handleToggleFreeze = async (card: BankCard) => {
    if (card.status === 'ACTIVE') {
      await api.freezeCard(card.card_id)
      setNotification(`卡片 ${card.card_id} 已临时冻结，禁止任何线上与线下结算。`)
    } else {
      await api.unfreezeCard(card.card_id)
      setNotification(`卡片 ${card.card_id} 已成功解冻，恢复正常使用。`)
    }
    loadCards()
  }

  const handleSaveLimit = async () => {
    if (!selectedCard) return
    await api.updateCardLimit(selectedCard.card_id, newLimit)
    setLimitModalOpen(false)
    setNotification(`卡片 ${selectedCard.card_id} 每日支付限额已调整为 ¥${newLimit.toLocaleString()}。`)
    loadCards()
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            安全银行卡管家
          </Typography>
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            借贷记分离 · 一键锁卡与风控调额 · 境外支付安全锁
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<Plus size={16} />}
          onClick={() => setApplyModalOpen(true)}
          sx={{ borderRadius: 2.5 }}
        >
          申请新虚拟安全卡
        </Button>
      </Box>

      {notification && (
        <Alert severity="success" onClose={() => setNotification(null)} sx={{ borderRadius: 2.5 }}>
          {notification}
        </Alert>
      )}

      {/* Main Cards Grid */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '5fr 7fr' }, gap: 3 }}>
        {/* Left: 3D Visual Card Display */}
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          {selectedCard && (
            <Card
              elevation={0}
              sx={{
                borderRadius: 4,
                p: 3,
                height: 220,
                position: 'relative',
                overflow: 'hidden',
                background:
                  selectedCard.status === 'FROZEN'
                    ? 'linear-gradient(135deg, #475569 0%, #1E293B 100%)'
                    : 'linear-gradient(135deg, #0B2545 0%, #0369A1 50%, #0284C7 100%)',
                color: '#FFFFFF',
                boxShadow: '0 16px 36px rgba(0, 0, 0, 0.35)',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
              }}
            >
              {/* Top Row: Issuer & Chip */}
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700, letterSpacing: '0.05em' }}>
                  {selectedCard.issuer || '招商银行 CHINA MERCHANTS BANK'}
                </Typography>
                <Chip
                  label={selectedCard.status === 'ACTIVE' ? '正常' : '已冻结'}
                  size="small"
                  sx={{
                    fontWeight: 700,
                    height: 22,
                    fontSize: '0.7rem',
                    backgroundColor: selectedCard.status === 'ACTIVE' ? 'rgba(16, 185, 129, 0.3)' : 'rgba(239, 68, 68, 0.3)',
                    color: selectedCard.status === 'ACTIVE' ? '#5EEAD4' : '#FCA5A5',
                  }}
                />
              </Box>

              {/* EMV Chip & Contactless */}
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
                <Box
                  sx={{
                    width: 44,
                    height: 32,
                    borderRadius: '6px',
                    backgroundColor: '#E2E8F0',
                    background: 'linear-gradient(135deg, #FDE047 0%, #D97706 100%)',
                    border: '1px solid rgba(255, 255, 255, 0.4)',
                  }}
                />
                <Wifi size={20} color="rgba(255, 255, 255, 0.7)" style={{ transform: 'rotate(90deg)' }} />
              </Box>

              {/* Card Number */}
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <Typography
                  variant="h6"
                  sx={{
                    fontFamily: '"JetBrains Mono", monospace',
                    letterSpacing: '0.12em',
                    fontWeight: 600,
                  }}
                >
                  {showFullNumber ? '6222 0200 0002 3082' : selectedCard.number_masked}
                </Typography>
                <Tooltip title={showFullNumber ? '隐藏完整卡号' : '查看完整卡号'}>
                  <Button
                    size="small"
                    onClick={() => setShowFullNumber(!showFullNumber)}
                    sx={{ color: 'rgba(255, 255, 255, 0.8)', minWidth: 'auto', p: 0.5 }}
                  >
                    {showFullNumber ? <EyeOff size={16} /> : <Eye size={16} />}
                  </Button>
                </Tooltip>
              </Box>

              {/* Bottom Row */}
              <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
                <Box>
                  <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.6)', fontSize: '0.65rem' }}>
                    CARD HOLDER
                  </Typography>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                    ZHANG SAN (张三)
                  </Typography>
                </Box>
                <Box sx={{ textAlign: 'right' }}>
                  <Typography variant="caption" sx={{ color: 'rgba(255, 255, 255, 0.6)', fontSize: '0.65rem' }}>
                    EXPIRES
                  </Typography>
                  <Typography variant="subtitle2" sx={{ fontWeight: 600, fontFamily: 'monospace' }}>
                    {selectedCard.expiry || '09/31'}
                  </Typography>
                </Box>
              </Box>
            </Card>
          )}

          {/* Cards Selector Pill List */}
          <Box sx={{ display: 'flex', gap: 1 }}>
            {cards.map((c) => (
              <Chip
                key={c.card_id}
                label={`${c.label} (**** ${c.last4})`}
                onClick={() => {
                  setSelectedCard(c)
                  setNewLimit(c.daily_limit)
                }}
                variant={selectedCard?.card_id === c.card_id ? 'filled' : 'outlined'}
                color={selectedCard?.card_id === c.card_id ? 'primary' : 'default'}
                sx={{ fontWeight: 600, borderRadius: 2 }}
              />
            ))}
          </Box>
        </Box>

        {/* Right: Controls & Parameters */}
        {selectedCard && (
          <Card
            elevation={0}
            sx={{
              borderRadius: 3.5,
              p: 3,
              border: `1px solid ${theme.palette.divider}`,
              backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
              display: 'flex',
              flexDirection: 'column',
              gap: 2.5,
            }}
          >
            <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.05rem' }}>
              卡片安全管理与限额配置
            </Typography>

            {/* Quick Actions */}
            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 1.5 }}>
              <Button
                variant="outlined"
                color={selectedCard.status === 'ACTIVE' ? 'warning' : 'success'}
                startIcon={selectedCard.status === 'ACTIVE' ? <Lock size={16} /> : <Unlock size={16} />}
                onClick={() => handleToggleFreeze(selectedCard)}
                sx={{ borderRadius: 2.5, py: 1 }}
              >
                {selectedCard.status === 'ACTIVE' ? '一键临时冻结卡片' : '一键解除卡片冻结'}
              </Button>

              <Button
                variant="outlined"
                startIcon={<Sliders size={16} />}
                onClick={() => setLimitModalOpen(true)}
                sx={{ borderRadius: 2.5, py: 1 }}
              >
                调整每日支付限额
              </Button>
            </Box>

            {/* Limits display */}
            <Box
              sx={{
                p: 2,
                borderRadius: 2.5,
                backgroundColor: isDark ? 'rgba(0, 0, 0, 0.25)' : 'rgba(244, 247, 251, 0.9)',
                border: `1px solid ${theme.palette.divider}`,
              }}
            >
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                当前日支付累计限额
              </Typography>
              <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#0284C7', my: 0.5 }}>
                ¥{selectedCard.daily_limit.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
              </Typography>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                单笔消费超过此额度将自动转入红色风控并需要二次 MFA 审批。
              </Typography>
            </Box>

            {/* Security Notice */}
            <Alert severity="info" sx={{ borderRadius: 2.5 }}>
              境外无卡支付防护已开启；若发生可疑交易，系统将自动触发短信与 App 预警。
            </Alert>
          </Card>
        )}
      </Box>

      {/* Adjust Limit Modal */}
      <Dialog
        open={limitModalOpen}
        onClose={() => setLimitModalOpen(false)}
        maxWidth="xs"
        fullWidth
        slotProps={{ paper: { sx: { borderRadius: 3, p: 1 } } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            调整每日支付限额
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 2, display: 'flex', flexDirection: 'column', gap: 2 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              当前设定额度：¥{newLimit.toLocaleString()}
            </Typography>
            <Slider
              value={newLimit}
              min={1000}
              max={50000}
              step={1000}
              onChange={(_, val) => setNewLimit(val as number)}
              valueLabelDisplay="auto"
            />
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
              限额设置将通过核心银行即时生效。
            </Typography>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setLimitModalOpen(false)} color="inherit">取消</Button>
          <Button variant="contained" onClick={handleSaveLimit}>保存并生效</Button>
        </DialogActions>
      </Dialog>

      {/* Apply Card Modal */}
      <Dialog
        open={applyModalOpen}
        onClose={() => setApplyModalOpen(false)}
        maxWidth="xs"
        fullWidth
        slotProps={{ paper: { sx: { borderRadius: 3, p: 1 } } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            申领新虚拟卡
          </Typography>
        </DialogTitle>
        <DialogContent>
          <Box sx={{ pt: 1, display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField label="卡片备注名称" size="small" defaultValue="网购专用防盗刷虚拟卡" fullWidth />
            <TextField label="初始日支付限额 (CNY)" size="small" defaultValue="2000" fullWidth />
            <Alert severity="success" icon={<CheckCircle2 size={18} />} sx={{ borderRadius: 2 }}>
              虚拟卡即申即用，独立卡号，与主账户共享余额且享有独立限额隔离。
            </Alert>
          </Box>
        </DialogContent>
        <DialogActions sx={{ p: 2 }}>
          <Button onClick={() => setApplyModalOpen(false)} color="inherit">取消</Button>
          <Button
            variant="contained"
            onClick={() => {
              setApplyModalOpen(false)
              setNotification('新虚拟卡申领成功！已添加至您的安全卡包。')
            }}
          >
            确认申领
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}
