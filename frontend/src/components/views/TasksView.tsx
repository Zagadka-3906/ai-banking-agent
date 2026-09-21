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
  ListTodo,
  Lock,
  XCircle,
  UserCheck,
  RefreshCw,
  Clock,
} from 'lucide-react'
import type { ScheduledTask } from '../../api/types'
import { api } from '../../api/client'

export const TasksView: React.FC = () => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [tasks, setTasks] = useState<ScheduledTask[]>([])
  const [notification, setNotification] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadTasks = async () => {
    setLoading(true)
    try {
      const list = await api.getTasks()
      setTasks(list)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadTasks()
  }, [])

  const handleCancel = async (taskId: string) => {
    await api.cancelTask(taskId)
    setNotification(`任务 ${taskId} 已取消，相关隔离锁资已自动解除并原路返还至可用余额。`)
    loadTasks()
  }

  const handleTakeover = async (taskId: string) => {
    await api.takeoverTask(taskId)
    setNotification(`任务 ${taskId} 已成功转接人工专属客户经理跟进。`)
    loadTasks()
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {/* Header */}
      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 2 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 700 }}>
            任务调度与定时关怀中心
          </Typography>
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            爱人生日专属锁资 · 定时自动汇款 · 人工无缝接管
          </Typography>
        </Box>
        <Button
          variant="outlined"
          size="small"
          onClick={loadTasks}
          startIcon={<RefreshCw size={14} className={loading ? 'spin-animation' : ''} />}
          sx={{ borderRadius: 2 }}
        >
          刷新任务列表
        </Button>
      </Box>

      {notification && (
        <Alert severity="success" onClose={() => setNotification(null)} sx={{ borderRadius: 2.5 }}>
          {notification}
        </Alert>
      )}

      {/* Task Cards Grid */}
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {tasks.map((task) => {
          const isCancelled = task.status === 'CANCELLED'
          const isWaiting = task.status === 'WAITING_USER'

          return (
            <Card
              key={task.task_id}
              elevation={0}
              sx={{
                borderRadius: 3.5,
                border: `1px solid ${theme.palette.divider}`,
                backgroundColor: isDark ? 'rgba(19, 30, 61, 0.6)' : '#FFFFFF',
                opacity: isCancelled ? 0.6 : 1,
              }}
            >
              <CardContent sx={{ p: 2.5 }}>
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5, flexWrap: 'wrap', gap: 1 }}>
                  <Box>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <ListTodo size={18} color="#0284C7" />
                      <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                        {task.task_type === 'BIRTHDAY_FUND_LOCK' ? '爱人生日锁资与日程订花' : '定时计划转账'}
                      </Typography>
                    </Box>
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      编号: {task.task_id} · 创建于 {task.created_at}
                    </Typography>
                  </Box>

                  <Chip
                    label={
                      isCancelled
                        ? '已取消'
                        : isWaiting
                        ? '等待用户/到期触发'
                        : '待执行'
                    }
                    color={isCancelled ? 'default' : isWaiting ? 'warning' : 'info'}
                    size="small"
                    sx={{ fontWeight: 600, height: 22, fontSize: '0.72rem' }}
                  />
                </Box>

                <Typography variant="body2" sx={{ color: theme.palette.text.primary, mb: 1.5 }}>
                  {task.description}
                </Typography>

                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 3, my: 1 }}>
                  {task.locked_fund && (
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                      <Lock size={14} color="#F59E0B" />
                      <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                        隔离资金：<strong>¥{task.locked_fund.toFixed(2)}</strong>
                      </Typography>
                    </Box>
                  )}
                  <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.5 }}>
                    <Clock size={14} color={theme.palette.text.secondary} />
                    <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                      计划触发时间：{task.scheduled_time || '到期自动唤醒'}
                    </Typography>
                  </Box>
                </Box>

                {/* Actions */}
                {!isCancelled && (
                  <Box sx={{ display: 'flex', justifyContent: 'flex-end', gap: 1.5, pt: 1 }}>
                    <Button
                      size="small"
                      variant="outlined"
                      color="error"
                      startIcon={<XCircle size={14} />}
                      onClick={() => handleCancel(task.task_id)}
                      sx={{ borderRadius: 2 }}
                    >
                      取消任务并解冻
                    </Button>
                    <Button
                      size="small"
                      variant="outlined"
                      startIcon={<UserCheck size={14} />}
                      onClick={() => handleTakeover(task.task_id)}
                      sx={{ borderRadius: 2 }}
                    >
                      申请人工接管
                    </Button>
                  </Box>
                )}
              </CardContent>
            </Card>
          )
        })}
      </Box>
    </Box>
  )
}
