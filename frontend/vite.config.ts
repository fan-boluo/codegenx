import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    // 只保留这两个，monaco 插件全部删除
    vue(),
    vueDevTools(),
  ],
  build: {
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) {
            return undefined
          }

          if (id.includes('monaco-editor')) {
            return 'monaco-editor'
          }

          if (id.includes('echarts')) {
            return 'echarts'
          }

          if (
            id.includes('ant-design-vue') ||
            id.includes('@ant-design') ||
            id.includes('@ctrl/tinycolor')
          ) {
            return 'ant-design-vue'
          }

          if (id.includes('markdown-it') || id.includes('highlight.js')) {
            return 'markdown'
          }

          if (id.includes('vue') || id.includes('pinia') || id.includes('vue-router')) {
            return 'vue-vendor'
          }

          return 'vendor'
        },
      },
    },
  },
  resolve: {
    // src/ 下存在 vue-tsc 误提交的 .js 编译产物（与 .ts 同名），默认顺序 .js 优先会吞掉 .ts 源码改动，这里强制 .ts 优先
    extensions: ['.ts', '.mts', '.mjs', '.js', '.jsx', '.tsx', '.json'],
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
})
