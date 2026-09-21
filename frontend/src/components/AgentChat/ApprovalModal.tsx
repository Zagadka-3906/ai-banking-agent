import React, { useState } from 'react'
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  Typography,
  Box,
  TextField,
  Chip,
  Alert,
  LinearProgress,
  useTheme,
} from '@mui/material'
import {
  ShieldAlert,
  KeyRound,
  CheckCircle2,
  ScanFace,
  Lock,
  XCircle,
} from 'lucide-react'
import type { RiskLevel } from '../../api/types'

interface ApprovalModalProps {
  open: boolean
  onClose: () => void
  approvalData: {
    approval_id: string
    auth_token?: string
    risk_level: RiskLevel
    message: string
    request_id: string
  } | null
  onConfirm: (decision: 'approve' | 'reject', otp?: string, faceAttestation?: string) => Promise<void>
}

export const ApprovalModal: React.FC<ApprovalModalProps> = ({
  open,
  onClose,
  approvalData,
  onConfirm,
}) => {
  const theme = useTheme()
  const isDark = theme.palette.mode === 'dark'

  const [otp, setOtp] = useState('')
  const [isFaceScanned, setIsFaceScanned] = useState(false)
  const [scanning, setScanning] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState('')

  if (!approvalData) return null

  const isRed = approvalData.risk_level === 'RED'

  const handleSimulateFaceScan = () => {
    setScanning(true)
    setErrorMsg('')
    setTimeout(() => {
      setScanning(false)
      setIsFaceScanned(true)
    }, 1200)
  }

  const handleSubmitApprove = async () => {
    if (isRed) {
      if (!isFaceScanned) {
        setErrorMsg('请先完成设备端活体人脸核验')
        return
      }
      if (!otp.trim()) {
        setErrorMsg('请输入短信验证码 (本地演示码: 123456)')
        return
      }
    }

    try {
      setSubmitting(true)
      setErrorMsg('')
      const faceAtt = isRed ? `attestation_mock_${Date.now()}` : undefined
      await onConfirm('approve', otp || undefined, faceAtt)
      onClose()
    } catch (e: any) {
      setErrorMsg(e?.message || '确认失败，核心银行拒绝该请求')
    } finally {
      setSubmitting(false)
    }
  }

  const handleReject = async () => {
    try {
      setSubmitting(true)
      await onConfirm('reject')
      onClose()
    } catch (e: any) {
      setErrorMsg(e?.message || '操作取消失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onClose={submitting ? undefined : onClose}
      maxWidth="sm"
      fullWidth
      slotProps={{
        paper: {
          sx: {
            borderRadius: 4,
            p: 1,
            backgroundColor: isDark ? '#131E3D' : '#FFFFFF',
            backgroundImage: 'none',
            border: `1px solid ${isRed ? 'rgba(239, 68, 68, 0.4)' : 'rgba(245, 158, 11, 0.4)'}`,
            boxShadow: isRed
              ? '0 20px 45px rgba(239, 68, 68, 0.25)'
              : '0 20px 45px rgba(245, 158, 11, 0.2)',
          },
        },
      }}
    >
      <DialogTitle sx={{ pb: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5 }}>
            <Box
              sx={{
                width: 42,
                height: 42,
                borderRadius: '12px',
                backgroundColor: isRed ? 'rgba(239, 68, 68, 0.15)' : 'rgba(245, 158, 11, 0.15)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <ShieldAlert size={24} color={isRed ? '#EF4444' : '#F59E0B'} />
            </Box>
            <Box>
              <Typography variant="h6" sx={{ fontWeight: 700, fontSize: '1.1rem' }}>
                {isRed ? '红色高风险 · 双因子认证 (MFA)' : '黄色风险 · 业务执行授权'}
              </Typography>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
                核心银行审计防护 · 凭证由 Core 权威验签
              </Typography>
            </Box>
          </Box>
          <Chip
            label={isRed ? 'RED ZONE' : 'YELLOW ZONE'}
            size="small"
            sx={{
              fontWeight: 700,
              backgroundColor: isRed ? 'rgba(239, 68, 68, 0.2)' : 'rgba(245, 158, 11, 0.2)',
              color: isRed ? '#EF4444' : '#F59E0B',
            }}
          />
        </Box>
      </DialogTitle>

      <DialogContent sx={{ pt: 2 }}>
        {/* Detail Box */}
        <Box
          sx={{
            p: 2.5,
            borderRadius: 3,
            backgroundColor: isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.02)',
            border: `1px solid ${theme.palette.divider}`,
            mb: 2.5,
          }}
        >
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: 'block', mb: 0.5 }}>
            操作意图与详情：
          </Typography>
          <Typography variant="body1" sx={{ fontWeight: 600, color: theme.palette.text.primary }}>
            {approvalData.message}
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, mt: 1.5, flexWrap: 'wrap' }}>
            <Chip
              size="small"
              label={`审批编号: ${approvalData.approval_id}`}
              sx={{ fontSize: '0.7rem', height: 22 }}
            />
            {approvalData.auth_token && (
              <Chip
                size="small"
                icon={<KeyRound size={12} />}
                label="HMAC 授权凭证有效"
                color="success"
                variant="outlined"
                sx={{ fontSize: '0.7rem', height: 22 }}
              />
            )}
          </Box>
        </Box>

        {errorMsg && (
          <Alert severity="error" sx={{ mb: 2, borderRadius: 2 }}>
            {errorMsg}
          </Alert>
        )}

        {/* If RED Risk, require MFA: Face Attestation + OTP */}
        {isRed && (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            {/* Step 1: Face Scan */}
            <Box
              sx={{
                p: 2,
                borderRadius: 2.5,
                border: `1px solid ${isFaceScanned ? 'rgba(16, 185, 129, 0.5)' : theme.palette.divider}`,
                backgroundColor: isFaceScanned
                  ? isDark ? 'rgba(16, 185, 129, 0.08)' : 'rgba(16, 185, 129, 0.05)'
                  : 'transparent',
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                  <ScanFace size={18} color={isFaceScanned ? '#10B981' : theme.palette.text.secondary} />
                  <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                    1. 客户端设备活体人脸核验
                  </Typography>
                </Box>
                {isFaceScanned && (
                  <Chip
                    icon={<CheckCircle2 size={13} />}
                    label="已生成签名票据"
                    size="small"
                    color="success"
                    sx={{ height: 22, fontSize: '0.72rem' }}
                  />
                )}
              </Box>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: 'block', mb: 1.5 }}>
                通过手机 Keystore 硬件私钥对服务端下发的 Challenge 进行活体签名（防重放与注入）。
              </Typography>

              {scanning && <LinearProgress sx={{ my: 1, borderRadius: 1 }} />}

              {!isFaceScanned ? (
                <Button
                  variant="outlined"
                  size="small"
                  disabled={scanning}
                  onClick={handleSimulateFaceScan}
                  startIcon={<ScanFace size={16} />}
                  sx={{ borderRadius: 2 }}
                >
                  {scanning ? '正在核验人脸活体特征…' : '模拟执行活体人脸识别'}
                </Button>
              ) : (
                <Typography variant="caption" sx={{ color: '#10B981', fontWeight: 500 }}>
                  ✓ 人脸生物特征与预存档案一致，已获取抗重放签发 Token。
                </Typography>
              )}
            </Box>

            {/* Step 2: SMS OTP */}
            <Box
              sx={{
                p: 2,
                borderRadius: 2.5,
                border: `1px solid ${theme.palette.divider}`,
              }}
            >
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
                <Lock size={18} color={theme.palette.text.secondary} />
                <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
                  2. 核心服务端短信一次性验证码 (OTP)
                </Typography>
              </Box>
              <Typography variant="caption" sx={{ color: theme.palette.text.secondary, display: 'block', mb: 1.5 }}>
                本地沙箱演示验证码请直接输入: <strong>123456</strong>
              </Typography>
              <TextField
                fullWidth
                size="small"
                placeholder="请输入 6 位短信验证码"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                sx={{
                  '& .MuiOutlinedInput-root': { borderRadius: 2 },
                }}
              />
            </Box>
          </Box>
        )}

        {!isRed && (
          <Typography variant="caption" sx={{ color: theme.palette.text.secondary }}>
            点击“确认执行”后，将直接调用核心银行 <code>/api/v1/auth/confirm</code> 进行幂等结账与流水写入。
          </Typography>
        )}
      </DialogContent>

      <DialogActions sx={{ p: 2.5, pt: 1, gap: 1.5 }}>
        <Button
          variant="outlined"
          color="inherit"
          onClick={handleReject}
          disabled={submitting}
          startIcon={<XCircle size={16} />}
          sx={{ borderRadius: 2, px: 2 }}
        >
          取消操作
        </Button>
        <Button
          variant="contained"
          color={isRed ? 'error' : 'primary'}
          onClick={handleSubmitApprove}
          disabled={submitting}
          startIcon={submitting ? undefined : <CheckCircle2 size={16} />}
          sx={{
            borderRadius: 2,
            px: 3,
            background: isRed
              ? 'linear-gradient(135deg, #EF4444 0%, #DC2626 100%)'
              : 'linear-gradient(135deg, #0284C7 0%, #0369A1 100%)',
          }}
        >
          {submitting ? '正在安全记账…' : '核实无误 · 确认执行'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}
