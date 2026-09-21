import React from 'react'
import {
  AppBar,
  Toolbar,
  Typography,
  Box,
  IconButton,
  Tooltip,
  Chip,
  Select,
  MenuItem,
  useTheme,
} from '@mui/material'
import {
  ShieldCheck,
  Sun,
  Moon,
  Wifi,
  WifiOff,
  User,
  Activity,
} from 'lucide-react'

interface NavbarProps {
  currentUserId: string
  onUserChange: (userId: string) => void
  isDarkMode: boolean
  onToggleTheme: () => void
  isOnline: boolean
  onRefreshStatus: () => void
}

export const Navbar: React.FC<NavbarProps> = ({
  currentUserId,
  onUserChange,
  isDarkMode,
  onToggleTheme,
  isOnline,
  onRefreshStatus,
}) => {
  const theme = useTheme()

  return (
    <AppBar
      position="sticky"
      elevation={0}
      sx={{
        backgroundColor: isDarkMode ? 'rgba(11, 19, 43, 0.85)' : 'rgba(255, 255, 255, 0.85)',
        backdropFilter: 'blur(16px)',
        borderBottom: `1px solid ${theme.palette.divider}`,
        color: theme.palette.text.primary,
        zIndex: 1100,
      }}
    >
      <Toolbar sx={{ justifyContent: 'space-between', px: { xs: 2, md: 4 }, minHeight: 68 }}>
        {/* Brand */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: '12px',
              background: 'linear-gradient(135deg, #0284C7 0%, #0B2545 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 4px 12px rgba(2, 132, 199, 0.35)',
            }}
          >
            <ShieldCheck size={24} color="#38BDF8" />
          </Box>
          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
              <Typography
                variant="h6"
                sx={{
                  fontWeight: 700,
                  letterSpacing: '-0.02em',
                  background: isDarkMode
                    ? 'linear-gradient(90deg, #F8FAFC 0%, #7DD3FC 100%)'
                    : 'linear-gradient(90deg, #0B2545 0%, #0284C7 100%)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                }}
              >
                安言 · 智能银行
              </Typography>
              <Chip
                label="AI Core v2.0"
                size="small"
                sx={{
                  height: 20,
                  fontSize: '0.7rem',
                  fontWeight: 600,
                  backgroundColor: isDarkMode ? 'rgba(56, 189, 248, 0.12)' : 'rgba(2, 132, 199, 0.1)',
                  color: isDarkMode ? '#38BDF8' : '#0284C7',
                  border: `1px solid ${isDarkMode ? 'rgba(56, 189, 248, 0.25)' : 'rgba(2, 132, 199, 0.2)'}`,
                }}
              />
            </Box>
            <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: { xs: 'none', sm: 'block' } }}>
              LangGraph 编排 · 三级风控 · Mock Core 唯一记账
            </Typography>
          </Box>
        </Box>

        {/* Status Badges & Controls */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: { xs: 1, md: 2 } }}>
          {/* Connectivity Badge */}
          <Tooltip title={isOnline ? '已成功连接 Core 核心银行及网关服务' : '后端暂未启动，已自动开启安全演示沙箱模式'}>
            <Chip
              icon={isOnline ? <Wifi size={14} /> : <WifiOff size={14} />}
              label={isOnline ? '核心服务在线' : '沙箱演示模式'}
              size="small"
              onClick={onRefreshStatus}
              sx={{
                cursor: 'pointer',
                fontWeight: 600,
                fontSize: '0.75rem',
                backgroundColor: isOnline
                  ? isDarkMode ? 'rgba(45, 212, 191, 0.15)' : 'rgba(13, 148, 136, 0.1)'
                  : isDarkMode ? 'rgba(251, 191, 36, 0.15)' : 'rgba(217, 119, 6, 0.1)',
                color: isOnline
                  ? isDarkMode ? '#2DD4BF' : '#0D9488'
                  : isDarkMode ? '#FBBF24' : '#D97706',
                border: `1px solid ${isOnline ? 'rgba(45, 212, 191, 0.3)' : 'rgba(251, 191, 36, 0.3)'}`,
                '&:hover': { opacity: 0.85 },
              }}
            />
          </Tooltip>

          {/* Risk Level Badge */}
          <Chip
            icon={<Activity size={13} />}
            label="三级风控防护中"
            size="small"
            sx={{
              display: { xs: 'none', md: 'flex' },
              fontSize: '0.75rem',
              backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.04)',
              color: theme.palette.text.secondary,
            }}
          />

          {/* User Selector */}
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
            <User size={16} color={theme.palette.text.secondary} />
            <Select
              size="small"
              value={currentUserId}
              onChange={(e) => onUserChange(e.target.value)}
              sx={{
                fontSize: '0.82rem',
                fontWeight: 600,
                height: 34,
                borderRadius: 2,
                backgroundColor: isDarkMode ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.03)',
                '& .MuiOutlinedInput-notchedOutline': {
                  borderColor: theme.palette.divider,
                },
              }}
            >
              <MenuItem value="user_zhang">张三 (主账户)</MenuItem>
              <MenuItem value="user_li">李四 (联系人)</MenuItem>
              <MenuItem value="user_wife">王美 (爱人)</MenuItem>
            </Select>
          </Box>

          {/* Theme Switcher */}
          <Tooltip title={isDarkMode ? '切换至浅色模式' : '切换至深色模式'}>
            <IconButton
              onClick={onToggleTheme}
              sx={{
                p: 1,
                border: `1px solid ${theme.palette.divider}`,
                borderRadius: 2,
                color: theme.palette.text.primary,
              }}
            >
              {isDarkMode ? <Sun size={18} color="#FBBF24" /> : <Moon size={18} color="#0B2545" />}
            </IconButton>
          </Tooltip>
        </Box>
      </Toolbar>
    </AppBar>
  )
}
