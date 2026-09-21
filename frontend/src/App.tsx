import React, { useState, useEffect } from 'react'
import {
  ThemeProvider,
  CssBaseline,
  Box,
  Container,
  Tabs,
  Tab,
  Typography,
  Chip,
  Snackbar,
  Alert,
  useMediaQuery,
} from '@mui/material'
import {
  LayoutDashboard,
  Receipt,
  TrendingUp,
  CreditCard,
  Repeat,
  ShieldCheck,
  ListTodo,
} from 'lucide-react'
import { createCustomTheme } from './theme'
import { Navbar } from './components/Navbar'
import { AssetBanner } from './components/AssetBanner'
import { AgentChat } from './components/AgentChat/AgentChat'
import { TransactionsView } from './components/views/TransactionsView'
import { WealthView } from './components/views/WealthView'
import { CardsView } from './components/views/CardsView'
import { SubscriptionsView } from './components/views/SubscriptionsView'
import { SecurityAuditView } from './components/views/SecurityAuditView'
import { TasksView } from './components/views/TasksView'
import { api } from './api/client'
import type { UserAccount } from './api/types'

export function App() {
  const prefersDarkMode = useMediaQuery('(prefers-color-scheme: dark)')
  const [isDarkMode, setIsDarkMode] = useState<boolean>(prefersDarkMode)
  const [currentTab, setCurrentTab] = useState<number>(0)
  const [userId, setUserId] = useState<string>('user_zhang')
  const [account, setAccount] = useState<UserAccount | null>(null)
  const [loadingAccount, setLoadingAccount] = useState<boolean>(false)
  const [isOnline, setIsOnline] = useState<boolean>(false)
  const [toastMessage, setToastMessage] = useState<string | null>(null)

  const theme = React.useMemo(() => createCustomTheme(isDarkMode ? 'dark' : 'light'), [isDarkMode])

  const loadAccount = async () => {
    setLoadingAccount(true)
    try {
      const data = await api.getAccount()
      setAccount(data)
    } finally {
      setLoadingAccount(false)
    }
  }

  const checkStatus = async () => {
    const online = await api.checkConnectivity()
    setIsOnline(online)
  }

  useEffect(() => {
    checkStatus()
    loadAccount()
  }, [userId])

  const handleUserChange = (newUserId: string) => {
    setUserId(newUserId)
    api.setUserId(newUserId)
    setToastMessage(`已切换为操作用户：${newUserId === 'user_zhang' ? '张三' : newUserId === 'user_li' ? '李四' : '王美'}`)
  }

  const handleQuickAction = (action: string) => {
    switch (action) {
      case 'transfer':
        setCurrentTab(0)
        break
      case 'bills':
        setCurrentTab(1)
        break
      case 'wealth':
        setCurrentTab(2)
        break
      case 'cards':
        setCurrentTab(3)
        break
      default:
        break
    }
  }

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box
        sx={{
          minHeight: '100vh',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: theme.palette.background.default,
          color: theme.palette.text.primary,
        }}
      >
        {/* Top Navbar */}
        <Navbar
          currentUserId={userId}
          onUserChange={handleUserChange}
          isDarkMode={isDarkMode}
          onToggleTheme={() => setIsDarkMode(!isDarkMode)}
          isOnline={isOnline}
          onRefreshStatus={checkStatus}
        />

        {/* Sub-Navigation Bar / Menu Tabs */}
        <Box
          sx={{
            borderBottom: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDarkMode ? 'rgba(15, 23, 42, 0.7)' : 'rgba(255, 255, 255, 0.7)',
            backdropFilter: 'blur(8px)',
            position: 'sticky',
            top: 68,
            zIndex: 1000,
          }}
        >
          <Container maxWidth="xl">
            <Tabs
              value={currentTab}
              onChange={(_, val) => setCurrentTab(val)}
              variant="scrollable"
              scrollButtons="auto"
              sx={{
                minHeight: 48,
                '& .MuiTab-root': {
                  minHeight: 48,
                  fontSize: '0.85rem',
                  fontWeight: 600,
                  textTransform: 'none',
                  gap: 0.8,
                  px: 2,
                },
              }}
            >
              <Tab icon={<LayoutDashboard size={17} />} iconPosition="start" label="智能交互中心" />
              <Tab icon={<Receipt size={17} />} iconPosition="start" label="流水与账单" />
              <Tab icon={<TrendingUp size={17} />} iconPosition="start" label="智能理财" />
              <Tab icon={<CreditCard size={17} />} iconPosition="start" label="卡片管家" />
              <Tab icon={<Repeat size={17} />} iconPosition="start" label="订阅托管" />
              <Tab icon={<ShieldCheck size={17} />} iconPosition="start" label="安全风控审计" />
              <Tab icon={<ListTodo size={17} />} iconPosition="start" label="任务与关怀" />
            </Tabs>
          </Container>
        </Box>

        {/* Main Content Area */}
        <Container maxWidth="xl" sx={{ flex: 1, py: { xs: 2.5, md: 3.5 } }}>
          {/* Tab 0: Integrated Dashboard */}
          {currentTab === 0 && (
            <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {/* Asset Header Banner */}
              <AssetBanner
                account={account}
                loading={loadingAccount}
                onRefresh={loadAccount}
                onQuickAction={handleQuickAction}
              />

              {/* Main Interaction Area: AI Chat & Side Widgets */}
              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr', lg: '8fr 4fr' },
                  gap: 3,
                  alignItems: 'start',
                }}
              >
                {/* AI Agent Chat Studio */}
                <Box sx={{ minHeight: 600 }}>
                  <AgentChat onTransactionSettled={loadAccount} />
                </Box>

                {/* Right Side Info Panels */}
                <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
                  {/* Risk Control Architecture Overview Card */}
                  <Box
                    sx={{
                      p: 2.5,
                      borderRadius: 3.5,
                      backgroundColor: isDarkMode ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1.5 }}>
                      <ShieldCheck size={18} color="#0284C7" />
                      <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
                        “安言” 核心防护体系
                      </Typography>
                    </Box>
                    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.2 }}>
                      <Box sx={{ p: 1.2, borderRadius: 2, backgroundColor: isDarkMode ? 'rgba(16, 185, 129, 0.1)' : 'rgba(16, 185, 129, 0.05)' }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: '#10B981' }}>
                          🟢 绿区 (只读放行)
                        </Typography>
                        <Typography variant="caption" sx={{ display: 'block', color: theme.palette.text.secondary }}>
                          查询余额、流水解析、理财推荐，免授权秒级响应。
                        </Typography>
                      </Box>
                      <Box sx={{ p: 1.2, borderRadius: 2, backgroundColor: isDarkMode ? 'rgba(245, 158, 11, 0.1)' : 'rgba(245, 158, 11, 0.05)' }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: '#F59E0B' }}>
                          🟡 黄区 (服务端凭据确认)
                        </Typography>
                        <Typography variant="caption" sx={{ display: 'block', color: theme.palette.text.secondary }}>
                          小额亲属转账、关怀锁资，需用户弹窗一键授权。
                        </Typography>
                      </Box>
                      <Box sx={{ p: 1.2, borderRadius: 2, backgroundColor: isDarkMode ? 'rgba(239, 68, 68, 0.1)' : 'rgba(239, 68, 68, 0.05)' }}>
                        <Typography variant="caption" sx={{ fontWeight: 700, color: '#EF4444' }}>
                          🔴 红区 (双因子强制 MFA)
                        </Typography>
                        <Typography variant="caption" sx={{ display: 'block', color: theme.palette.text.secondary }}>
                          大额转账 (≥2000元)、挂失改密，需人脸挑战 + 短信动态码。
                        </Typography>
                      </Box>
                    </Box>
                  </Box>

                  {/* Birthday Care Plan Quick Card */}
                  <Box
                    sx={{
                      p: 2.5,
                      borderRadius: 3.5,
                      backgroundColor: isDarkMode ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                      border: `1px solid ${theme.palette.divider}`,
                    }}
                  >
                    <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 1 }}>
                      🎂 爱人生日锁资专属状态
                    </Typography>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: 'block', mb: 1.5 }}>
                      隔离储备金 ¥1,000.00 专款专用，距离生日还有 6 天。
                    </Typography>
                    <Chip
                      size="small"
                      label="到期自动提醒预定鲜花"
                      color="warning"
                      variant="outlined"
                      sx={{ fontWeight: 600, fontSize: '0.72rem' }}
                    />
                  </Box>
                </Box>
              </Box>
            </Box>
          )}

          {/* Tab 1: Transactions */}
          {currentTab === 1 && <TransactionsView />}

          {/* Tab 2: Wealth */}
          {currentTab === 2 && <WealthView />}

          {/* Tab 3: Cards */}
          {currentTab === 3 && <CardsView />}

          {/* Tab 4: Subscriptions */}
          {currentTab === 4 && <SubscriptionsView />}

          {/* Tab 5: Security Audit */}
          {currentTab === 5 && <SecurityAuditView />}

          {/* Tab 6: Tasks */}
          {currentTab === 6 && <TasksView />}
        </Container>

        {/* Global Footer */}
        <Box
          component="footer"
          sx={{
            py: 2.5,
            px: 3,
            mt: 'auto',
            borderTop: `1px solid ${theme.palette.divider}`,
            backgroundColor: isDarkMode ? 'rgba(11, 19, 43, 0.9)' : 'rgba(244, 247, 251, 0.8)',
            textAlign: 'center',
          }}
        >
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            安言 · 智能银行核心系统 (AI Banking Agent & Core Gateway) · 本演示界面所有账目均为沙箱虚构数据 · 仅供金融科技演示
          </Typography>
        </Box>

        {/* Notification Toast */}
        <Snackbar
          open={Boolean(toastMessage)}
          autoHideDuration={3000}
          onClose={() => setToastMessage(null)}
          anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
        >
          <Alert severity="info" onClose={() => setToastMessage(null)} sx={{ borderRadius: 2 }}>
            {toastMessage}
          </Alert>
        </Snackbar>
      </Box>
    </ThemeProvider>
  )
}

export default App
