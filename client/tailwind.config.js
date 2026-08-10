/** @type {import('tailwindcss').Config} */
// 仅作用于 claudecodeui 原生移植子树(src/agents)。
// 关键隔离手段:
//   1. content 只扫 src/agents/** —— 不为 ops 现有页面生成任何 utility
//   2. corePlugins.preflight = false —— 不注入全局 reset(避免污染 ops 16 套主题)
//      agents 自身的 reset 由 index.css 作用域化到 #agents-root 容器内承担
import typography from '@tailwindcss/typography';

export default {
  darkMode: ['class'],
  content: ['./src/agents/**/*.{js,jsx,ts,tsx}'],
  // 所有 utility 类生成为 `#agents-root .xxx` —— 自动限定在 agents 容器内,
  // 既不污染 ops,又提权压过 ops 自己的样式。
  important: '#agents-root',
  corePlugins: {
    preflight: false,
  },
  theme: {
    extend: {
      colors: {
        // 中性阶走 CSS 变量，不再是静态 hex —— agents 里有 1213 处 `gray-*`
        // 字面色阶（11 档全用到），它们既不吃 ops 的变量也不吃 shadcn 那座桥，
        // 换主题时是唯一纹丝不动的一层。
        //
        // 默认值定义在 workbench.css 的 :root，就是原来这份静态色阶的原值
        // （所以旧 16 套零变化）；门道两套在 mendao-tokens.css 里覆盖。
        //
        // 必须写成 `rgb(var(--x) / <alpha-value>)` 而不是 `var(--x)`：
        // agents 里有 362 处带透明度修饰符的写法（`bg-gray-800/60`），
        // 后一种写法会让它们**静默失效**。
        gray: {
          50:  'rgb(var(--ag-n50)  / <alpha-value>)',
          100: 'rgb(var(--ag-n100) / <alpha-value>)',
          200: 'rgb(var(--ag-n200) / <alpha-value>)',
          300: 'rgb(var(--ag-n300) / <alpha-value>)',
          400: 'rgb(var(--ag-n400) / <alpha-value>)',
          500: 'rgb(var(--ag-n500) / <alpha-value>)',
          600: 'rgb(var(--ag-n600) / <alpha-value>)',
          700: 'rgb(var(--ag-n700) / <alpha-value>)',
          800: 'rgb(var(--ag-n800) / <alpha-value>)',
          900: 'rgb(var(--ag-n900) / <alpha-value>)',
          950: 'rgb(var(--ag-n950) / <alpha-value>)',
        },
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: { DEFAULT: 'hsl(var(--primary))', foreground: 'hsl(var(--primary-foreground))' },
        secondary: { DEFAULT: 'hsl(var(--secondary))', foreground: 'hsl(var(--secondary-foreground))' },
        destructive: { DEFAULT: 'hsl(var(--destructive))', foreground: 'hsl(var(--destructive-foreground))' },
        muted: { DEFAULT: 'hsl(var(--muted))', foreground: 'hsl(var(--muted-foreground))' },
        accent: { DEFAULT: 'hsl(var(--accent))', foreground: 'hsl(var(--accent-foreground))' },
        popover: { DEFAULT: 'hsl(var(--popover))', foreground: 'hsl(var(--popover-foreground))' },
        card: { DEFAULT: 'hsl(var(--card))', foreground: 'hsl(var(--card-foreground))' },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
      spacing: {
        'safe-area-inset-bottom': 'env(safe-area-inset-bottom)',
        'mobile-nav': 'var(--mobile-nav-total)',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '200% 0' },
          '100%': { backgroundPosition: '-200% 0' },
        },
        'dialog-overlay-show': { from: { opacity: '0' }, to: { opacity: '1' } },
        'dialog-content-show': {
          from: { opacity: '0', transform: 'translate(-50%, -48%) scale(0.96)' },
          to: { opacity: '1', transform: 'translate(-50%, -50%) scale(1)' },
        },
      },
      animation: {
        shimmer: 'shimmer 2s linear infinite',
        'dialog-overlay-show': 'dialog-overlay-show 150ms ease-out',
        'dialog-content-show': 'dialog-content-show 150ms ease-out',
      },
    },
  },
  plugins: [typography],
};
