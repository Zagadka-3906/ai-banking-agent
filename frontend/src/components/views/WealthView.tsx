import React, { useState, useEffect } from 'react'
import {
  Box,
  Card,
  CardContent,
  Typography,
  Tabs,
  Tab,
  Button,
  Chip,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Alert,
  useTheme,
} from '@mui/material'
import {
  TrendingUp,
  ShieldCheck,
  Award,
  CheckCircle2,
} from 'lucide-react'
import type { WealthProduct, WealthHolding } from '../../api/types'
import { api } from '../../api/client'

const RISK_BADGE_MAP: Record<string, { label: string; color: 'success' | 'info' | 'warning' | 'error' }> = {
  R1: { label: 'R1 低风险', color: 'success' },
  R2: { label: 'R2 中低风险', color: 'info' },
  R3: { label: 'R3 中等风险', color: 'warning' },
  R4: { label: 'R4 中高风险', color: 'error' },
  R5: { label: 'R5 高风险', color: 'error' },
}

export const WealthView: React.FC = () => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [currentTab, setCurrentTab] = useState(0)
  const [products, setProducts] = useState<WealthProduct[]>([])
  const [holdings, setHoldings] = useState<WealthHolding[]>([])
  const [selectedProduct, setSelectedProduct] = useState<WealthProduct | null>(null)
  const [purchaseAmount, setPurchaseAmount] = useState('')
  const [purchaseSuccess, setPurchaseSuccess] = useState(false)
  const [userRiskLevel, setUserRiskLevel] = useState('C2 稳健型')

  useEffect(() => {
    Promise.all([api.getWealthProducts(), api.getWealthHoldings()]).then(
      ([prodList, holdList]) => {
        setProducts(prodList)
        setHoldings(holdList)
      }
    )
  }, [])

  const handleOpenPurchase = (prod: WealthProduct) => {
    setSelectedProduct(prod)
    setPurchaseAmount(prod.min_amount.toString())
    setPurchaseSuccess(false)
  }

  const handleConfirmPurchase = () => {
    if (!selectedProduct) return
    const amt = parseFloat(purchaseAmount)
    if (isNaN(amt) || amt < selectedProduct.min_amount) return

    setPurchaseSuccess(true)
    setTimeout(() => {
      // Add to holdings
      const newHolding: WealthHolding = {
        holding_id: `HLD-${Date.now().toString().slice(-4)}`,
        product_id: selectedProduct.product_id,
        product_name: selectedProduct.name,
        amount: amt,
        current_nav: selectedProduct.latest_nav || 1.0,
        accumulated_profit: 0.0,
        holding_units: amt / (selectedProduct.latest_nav || 1.0),
        purchase_date: new Date().toISOString().slice(0, 10),
      }
      setHoldings((prev) => [newHolding, ...prev])
      setSelectedProduct(null)
    }, 1000)
  }

  const totalHoldingAmount = holdings.reduce((sum, h) => sum + h.amount, 0)
  const totalProfits = holdings.reduce((sum, h) => sum + h.accumulated_profit, 0)

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            安言 · 智能财富管理
          </Typography>
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            R1-R4 完整风险谱系 · 纯债筑底与超额增强 · 适合度合规保障
          </Typography>
        </Box>
        <Chip
          icon={<ShieldCheck size={14} />}
          label={`当前风险评级: ${userRiskLevel}`}
          sx={{
            fontWeight: 600,
            backgroundColor: isDark ? 'rgba(56, 189, 248, 0.15)' : 'rgba(2, 132, 199, 0.1)',
            color: isDark ? '#38BDF8' : '#0284C7',
          }}
        />
      </Box>

      {/* Tabs */}
      <Box sx={{ borderBottom: 1, borderColor: 'divider' }}>
        <Tabs value={currentTab} onChange={(_, val) => setCurrentTab(val)}>
          <Tab label="理财货架与严选" sx={{ fontWeight: 600 }} />
          <Tab label={`我的持仓资产 (${holdings.length})`} sx={{ fontWeight: 600 }} />
          <Tab label="投资者适当性测评" sx={{ fontWeight: 600 }} />
        </Tabs>
      </Box>

      {/* Tab 0: Products Shelf */}
      {currentTab === 0 && (
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: 'repeat(2, 1fr)' }, gap: 2.5 }}>
          {products.map((prod) => {
            const riskInfo = RISK_BADGE_MAP[prod.risk_level] || { label: prod.risk_level, color: 'info' }

            return (
              <Card
                key={prod.product_id}
                elevation={0}
                sx={{
                  borderRadius: 3.5,
                  border: `1px solid ${theme.palette.divider}`,
                  backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                  display: 'flex',
                  flexDirection: 'column',
                  justifyContent: 'space-between',
                  transition: 'transform 0.2s, box-shadow 0.2s',
                  '&:hover': {
                    transform: 'translateY(-2px)',
                    boxShadow: isDark
                      ? '0 12px 30px rgba(0, 0, 0, 0.35)'
                      : '0 12px 30px rgba(11, 37, 69, 0.08)',
                  },
                }}
              >
                <CardContent sx={{ p: 2.5 }}>
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
                    <Box>
                      <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.05rem' }}>
                        {prod.name}
                      </Typography>
                      <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                        编号: {prod.product_id} · {prod.category}
                      </Typography>
                    </Box>
                    <Chip
                      label={riskInfo.label}
                      color={riskInfo.color}
                      size="small"
                      sx={{ fontWeight: 700, fontSize: '0.7rem' }}
                    />
                  </Box>

                  {/* Benchmark Yield Box */}
                  <Box
                    sx={{
                      p: 2,
                      borderRadius: 2.5,
                      backgroundColor: isDark ? 'rgba(0, 0, 0, 0.25)' : 'rgba(244, 247, 251, 0.9)',
                      border: `1px solid ${theme.palette.divider}`,
                      mb: 2,
                    }}
                  >
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      业绩比较基准 / 预期参考
                    </Typography>
                    <Typography
                      variant="h5"
                      sx={{
                        fontWeight: 700,
                        fontFamily: '"Outfit", sans-serif',
                        color: '#0284C7',
                        my: 0.5,
                      }}
                    >
                      {prod.benchmark}
                    </Typography>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: 'block' }}>
                      {prod.liquidity} · 起购金额 ¥{prod.min_amount.toLocaleString()}
                    </Typography>
                  </Box>

                  {/* Underlying & Target */}
                  <Box sx={{ display: 'flex', flexDirection: 'column', gap: 0.8 }}>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      <strong>投资底层：</strong>{prod.underlying}
                    </Typography>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      <strong>适合客群：</strong>{prod.target_clients}
                    </Typography>
                  </Box>
                </CardContent>

                <Box sx={{ p: 2, px: 2.5, pt: 0 }}>
                  <Button
                    fullWidth
                    variant="contained"
                    onClick={() => handleOpenPurchase(prod)}
                    startIcon={<TrendingUp size={16} />}
                    sx={{
                      borderRadius: 2.5,
                      background: 'linear-gradient(135deg, #0284C7 0%, #0B2545 100%)',
                    }}
                  >
                    立即申购 (模拟体验)
                  </Button>
                </Box>
              </Card>
            )
          })}
        </Box>
      )}

      {/* Tab 1: User Holdings */}
      {currentTab === 1 && (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
          {/* Summary Banner */}
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
                  理财总持仓市值
                </Typography>
                <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#0284C7' }}>
                  ¥{totalHoldingAmount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  累计参考收益
                </Typography>
                <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif', color: '#059669' }}>
                  +¥{totalProfits.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                </Typography>
              </Box>
              <Box>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  已配置产品种类
                </Typography>
                <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif' }}>
                  {holdings.length} 款
                </Typography>
              </Box>
            </Box>
          </Card>

          {/* Holdings List */}
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {holdings.map((h) => (
              <Card
                key={h.holding_id}
                elevation={0}
                sx={{
                  borderRadius: 3,
                  p: 2.5,
                  border: `1px solid ${theme.palette.divider}`,
                  backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                }}
              >
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
                  <Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Award size={18} color="#0284C7" />
                      <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                        {h.product_name}
                      </Typography>
                    </Box>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      持仓凭证: {h.holding_id} · 买入日: {h.purchase_date}
                    </Typography>
                  </Box>

                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 3 }}>
                    <Box sx={{ textAlign: 'right' }}>
                      <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>持仓金额</Typography>
                      <Typography variant="subtitle1" sx={{ fontWeight: 700, fontFamily: '"Outfit", sans-serif' }}>
                        ¥{h.amount.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
                      </Typography>
                    </Box>
                    <Box sx={{ textAlign: 'right' }}>
                      <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>累计收益</Typography>
                      <Typography variant="subtitle1" sx={{ fontWeight: 700, color: '#059669', fontFamily: '"Outfit", sans-serif' }}>
                        +¥{h.accumulated_profit.toFixed(2)}
                      </Typography>
                    </Box>
                    <Button size="small" variant="outlined" sx={{ borderRadius: 2 }}>
                      赎回申请
                    </Button>
                  </Box>
                </Box>
              </Card>
            ))}
          </Box>
        </Box>
      )}

      {/* Tab 2: Risk Profile Quiz */}
      {currentTab === 2 && (
        <Card
          elevation={0}
          sx={{
            borderRadius: 3.5,
            p: 3,
            border: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
          }}
        >
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 2 }}>
            <ShieldCheck size={24} color="#0284C7" />
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                投资者风险承受能力评估 (C1-C5)
              </Typography>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                根据监管适当性要求，系统自动按问卷定档，产品推荐绝不逾越您的风险承受边界
              </Typography>
            </Box>
          </Box>

          <Alert severity="info" sx={{ mb: 3, borderRadius: 2 }}>
            您当前的测评定档为：<strong>{userRiskLevel}</strong>（可认购 R1~R2 级别低波动及纯债稳健产品）。如需购买 R3/R4 进取型权益产品，请重测并签署适当性警示。
          </Alert>

          <Box sx={{ display: 'flex', gap: 2 }}>
            <Button
              variant="contained"
              onClick={() => {
                setUserRiskLevel('C3 平衡型')
                alert('已模拟重做风险测评，评级更新为：C3 平衡型 (可购买 R1~R3 理财产品)')
              }}
              sx={{ borderRadius: 2.5 }}
            >
              重新完成风险测评问卷
            </Button>
          </Box>
        </Card>
      )}

      {/* Purchase Modal */}
      <Dialog
        open={Boolean(selectedProduct)}
        onClose={() => setSelectedProduct(null)}
        maxWidth="xs"
        fullWidth
        slotProps={{ paper: { sx: { borderRadius: 3, p: 1 } } }}
      >
        <DialogTitle sx={{ pb: 1 }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>
            理财产品认购确认
          </Typography>
        </DialogTitle>
        <DialogContent>
          {selectedProduct && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
              <Box sx={{ p: 2, borderRadius: 2, backgroundColor: isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.03)' }}>
                <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                  {selectedProduct.name}
                </Typography>
                <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                  基准: {selectedProduct.benchmark} · {selectedProduct.liquidity}
                </Typography>
              </Box>

              {purchaseSuccess ? (
                <Alert severity="success" icon={<CheckCircle2 size={20} />} sx={{ borderRadius: 2 }}>
                  申购成功！份额预计将于 T+1 工作日由核心银行记账确认。
                </Alert>
              ) : (
                <>
                  <TextField
                    label="申购金额 (CNY)"
                    fullWidth
                    size="small"
                    value={purchaseAmount}
                    onChange={(e) => setPurchaseAmount(e.target.value)}
                    helperText={`起购金额: ¥${selectedProduct.min_amount}`}
                  />
                  <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                    点击确认后将通过核心授权通道核验，并在您的可用余额中划扣。
                  </Typography>
                </>
              )}
            </Box>
          )}
        </DialogContent>
        <DialogActions sx={{ p: 2, pt: 0 }}>
          <Button onClick={() => setSelectedProduct(null)} color="inherit">
            关闭
          </Button>
          {!purchaseSuccess && (
            <Button variant="contained" onClick={handleConfirmPurchase}>
              确认申购
            </Button>
          )}
        </DialogActions>
      </Dialog>
    </Box>
  )
}
