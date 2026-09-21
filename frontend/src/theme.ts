import { createTheme, type ThemeOptions } from '@mui/material/styles'

const getDesignTokens = (mode: 'light' | 'dark'): ThemeOptions => ({
  palette: {
    mode,
    ...(mode === 'light'
      ? {
          primary: {
            main: '#0B2545',
            light: '#134074',
            dark: '#05162B',
            contrastText: '#FFFFFF',
          },
          secondary: {
            main: '#0077B6',
            light: '#48CAE4',
            dark: '#023E8A',
            contrastText: '#FFFFFF',
          },
          background: {
            default: '#F4F7FB',
            paper: '#FFFFFF',
          },
          text: {
            primary: '#0F172A',
            secondary: '#475569',
          },
          divider: 'rgba(226, 232, 240, 0.9)',
          success: {
            main: '#0D9488',
            light: '#14B8A6',
            dark: '#0F766E',
          },
          warning: {
            main: '#D97706',
            light: '#F59E0B',
            dark: '#B45309',
          },
          error: {
            main: '#DC2626',
            light: '#EF4444',
            dark: '#B91C1C',
          },
        }
      : {
          primary: {
            main: '#38BDF8',
            light: '#7DD3FC',
            dark: '#0284C7',
            contrastText: '#082F49',
          },
          secondary: {
            main: '#60A5FA',
            light: '#93C5FD',
            dark: '#2563EB',
            contrastText: '#0B1E38',
          },
          background: {
            default: '#0B132B',
            paper: '#131E3D',
          },
          text: {
            primary: '#F8FAFC',
            secondary: '#94A3B8',
          },
          divider: 'rgba(51, 65, 85, 0.7)',
          success: {
            main: '#2DD4BF',
            light: '#5EEAD4',
            dark: '#0D9488',
          },
          warning: {
            main: '#FBBF24',
            light: '#FCD34D',
            dark: '#D97706',
          },
          error: {
            main: '#F87171',
            light: '#FCA5A5',
            dark: '#DC2626',
          },
        }),
  },
  typography: {
    fontFamily: '"Outfit", "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    h1: { fontWeight: 700, letterSpacing: '-0.02em' },
    h2: { fontWeight: 700, letterSpacing: '-0.02em' },
    h3: { fontWeight: 600, letterSpacing: '-0.01em' },
    h4: { fontWeight: 600, letterSpacing: '-0.01em' },
    h5: { fontWeight: 600 },
    h6: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 600, letterSpacing: '0.01em' },
  },
  shape: {
    borderRadius: 12,
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          borderRadius: 10,
          padding: '8px 18px',
          boxShadow: 'none',
          '&:hover': {
            boxShadow: '0 4px 12px rgba(0, 0, 0, 0.1)',
          },
        },
        contained: {
          '&.MuiButton-containedPrimary': {
            background: mode === 'light'
              ? 'linear-gradient(135deg, #0B2545 0%, #134074 100%)'
              : 'linear-gradient(135deg, #0284C7 0%, #0369A1 100%)',
            '&:hover': {
              background: mode === 'light'
                ? 'linear-gradient(135deg, #05162B 0%, #0B2545 100%)'
                : 'linear-gradient(135deg, #0369A1 0%, #075985 100%)',
            },
          },
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: 16,
          border: mode === 'light' ? '1px solid rgba(226, 232, 240, 0.8)' : '1px solid rgba(51, 65, 85, 0.5)',
          boxShadow: mode === 'light' ? '0 4px 20px -2px rgba(11, 37, 69, 0.05)' : '0 4px 20px -2px rgba(0, 0, 0, 0.3)',
          backgroundImage: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: {
          fontWeight: 500,
          borderRadius: 8,
        },
      },
    },
  },
})

export const createCustomTheme = (mode: 'light' | 'dark') => createTheme(getDesignTokens(mode))
