import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// React + Tailwind v4 (CSS-first, via the official Vite plugin).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { port: 5173, host: true },
})
