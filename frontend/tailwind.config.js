/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        valence: {
          black: '#070B14',
          surface: '#0D1424',
          card: '#131C31',
          border: '#1E2C4A',
          hover: '#18243E',
          blue: {
            50: '#EEF6FF',
            100: '#DBECFE',
            200: '#BDDCFE',
            300: '#93C5FD',
            400: '#60A5FA',
            500: '#3B82F6',
            600: '#2563EB',
            700: '#1D4ED8',
            800: '#1E40AF',
            900: '#172554',
          },
          indigo: {
            400: '#818CF8',
            500: '#6366F1',
            600: '#4F46E5',
          },
          violet: {
            400: '#A78BFA',
            500: '#8B5CF6',
            600: '#7C3AED',
          },
          light: {
            bg: '#F8FAFC',
            card: '#FFFFFF',
            border: '#E2E8F0',
            hover: '#F1F5F9',
            muted: '#64748B',
          }
        },
        valens: {
          black: '#070B14',
          surface: '#0D1424',
          card: '#131C31',
          border: '#1E2C4A',
          hover: '#18243E',
        }
      },
      backgroundImage: {
        'valence-grad': 'linear-gradient(135deg, #3B82F6 0%, #6366F1 50%, #8B5CF6 100%)',
        'valence-grad-subtle': 'linear-gradient(135deg, rgba(59, 130, 246, 0.15) 0%, rgba(99, 102, 241, 0.1) 50%, rgba(139, 92, 246, 0.05) 100%)',
      },
      boxShadow: {
        'glow-valence': '0 0 25px -4px rgba(99, 102, 241, 0.35)',
        'glow-blue': '0 0 20px -5px rgba(37, 99, 235, 0.35)',
        'glow-blue-lg': '0 0 30px -5px rgba(59, 130, 246, 0.45)',
        'subtle': '0 4px 20px -2px rgba(0, 0, 0, 0.25)',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['JetBrains Mono', 'Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
