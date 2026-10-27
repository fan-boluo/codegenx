<template>
  <div class="trace-step-card" :class="'step-' + step.type + ' status-' + step.status">
    <!-- 卡片头部（折叠/展开） -->
    <div class="trace-step-header" @click="collapsed = !collapsed">
      <span class="step-status-icon">
        <LoadingOutlined v-if="step.status === 'running'" spin />
        <CheckCircleOutlined v-else-if="step.status === 'completed'" />
        <CloseCircleOutlined v-else />
      </span>
      <span class="step-type-icon">{{ typeIcon }}</span>
      <span class="step-title">{{ step.title }}</span>
      <span v-if="step.status === 'completed' && step.durationMs != null" class="step-meta">
        {{ step.durationMs }}ms
      </span>
      <span v-if="step.status === 'completed' && step.rowCount != null" class="step-meta">
        {{ step.rowCount }} 行
      </span>
      <span v-if="step.status === 'failed' && step.error" class="step-meta step-error">
        {{ step.error }}
      </span>
      <span class="step-toggle">
        <DownOutlined v-if="!collapsed" />
        <RightOutlined v-else />
      </span>
    </div>

    <!-- 卡片内容 -->
    <div v-show="!collapsed" class="trace-step-body">
      <!-- SQL 代码块 + 结果预览 -->
      <div v-if="step.type === 'sql'" class="step-sql">
        <pre class="sql-code"><code>{{ step.sql || '-- 无 SQL --' }}</code></pre>
        <!-- 执行结果预览（前 N 行） -->
        <div v-if="step.status === 'completed' && step.rowsPreview?.length" class="result-preview">
          <div class="result-preview-title">
            结果预览（前 {{ step.rowsPreview.length }} 行{{ step.rowCount && step.rowCount > step.rowsPreview.length ? ` / 共 ${step.rowCount} 行` : '' }}）
          </div>
          <div class="result-table-wrap">
            <table class="result-table">
              <thead>
                <tr>
                  <th v-for="col in resultColumns" :key="col">{{ col }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in step.rowsPreview" :key="ri">
                  <td v-for="col in resultColumns" :key="col">{{ formatCell(row[col]) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <!-- 图表 -->
      <div v-else-if="step.type === 'chart'" class="step-chart">
        <div ref="chartRef" class="chart-canvas"></div>
        <div v-if="step.status === 'running'" class="chart-loading">
          <a-spin size="small" />
        </div>
        <div v-else-if="!step.chartOption && step.status === 'failed'" class="chart-failed">
          图表生成失败
        </div>
      </div>

      <!-- Markdown 报告 -->
      <div v-else-if="step.type === 'report'" class="step-report">
        <MarkdownRenderer v-if="step.markdown" :content="step.markdown" />
        <div v-else-if="step.status === 'running'" class="report-loading">
          <a-spin size="small" /> 生成报告中...
        </div>
        <div v-else class="report-failed">报告生成失败</div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import {
  LoadingOutlined, CheckCircleOutlined, CloseCircleOutlined,
  DownOutlined, RightOutlined,
} from '@ant-design/icons-vue'
import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import * as echarts from 'echarts'

// 同 AppChatPage.vue 的 AnalysisStep 结构（独立定义避免循环依赖）
interface AnalysisStep {
  id: string
  type: 'sql' | 'chart' | 'report'
  title: string
  status: 'running' | 'completed' | 'failed'
  timestamp: number
  sql?: string
  rowCount?: number
  durationMs?: number
  rowsPreview?: Record<string, any>[]  // SQL 执行结果前 N 行
  chartOption?: any
  chartType?: string
  markdown?: string
  error?: string
}

const props = defineProps<{ step: AnalysisStep }>()

const collapsed = ref(false)

// SQL 结果预览：列名 + 单元格格式化
const resultColumns = computed<string[]>(() => {
  const first = props.step.rowsPreview?.[0]
  return first ? Object.keys(first) : []
})

const formatCell = (val: any): string => {
  if (val === null || val === undefined) return ''
  const s = String(val)
  return s.length > 50 ? s.slice(0, 50) + '…' : s
}

const typeIcon = computed(() => {
  switch (props.step.type) {
    case 'sql': return '🗄️'
    case 'chart': return '📊'
    case 'report': return '📄'
    default: return '•'
  }
})

// ---- ECharts 渲染 ----
const chartRef = ref<HTMLElement | null>(null)
let chartInstance: echarts.ECharts | null = null

const ensureChartInstance = () => {
  if (!chartRef.value) return null
  if (!chartInstance) {
    chartInstance = echarts.init(chartRef.value)
  }
  return chartInstance
}

const renderChart = async () => {
  if (!props.step.chartOption) return
  await nextTick()
  const chart = ensureChartInstance()
  if (chart) {
    chart.setOption(props.step.chartOption, true)
    chart.resize()
  }
}

// 步骤完成时（chartOption 就绪）渲染图表
watch(
  () => [props.step.status, props.step.chartOption] as const,
  () => {
    if (props.step.type === 'chart' && props.step.status === 'completed' && props.step.chartOption) {
      renderChart()
    }
  },
  { deep: true },
)

// 折叠切换时 resize（展开后 DOM 才有尺寸）
watch(collapsed, async (val) => {
  if (!val && props.step.type === 'chart') {
    await nextTick()
    chartInstance?.resize()
  }
})

onBeforeUnmount(() => {
  chartInstance?.dispose()
  chartInstance = null
})
</script>

<style scoped>
.trace-step-card {
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 6px;
  margin-bottom: 8px;
  background: #fff;
  overflow: hidden;
}
.trace-step-card.status-running { border-color: #91caff; background: #f0f5ff; }
.trace-step-card.status-failed { border-color: #ffa39e; background: #fff1f0; }

.trace-step-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  cursor: pointer;
  font-size: 13px;
  user-select: none;
  background: var(--card-header-bg, #fafafa);
}
.trace-step-header:hover { background: var(--card-header-hover, #f0f0f0); }

.step-status-icon { font-size: 12px; color: #8c8c8c; }
.status-completed .step-status-icon { color: #52c41a; }
.status-failed .step-status-icon { color: #ff4d4f; }
.status-running .step-status-icon { color: #1677ff; }

.step-type-icon { font-size: 14px; }
.step-title { flex: 1; font-weight: 500; color: #262626; }
.step-meta { font-size: 12px; color: #8c8c8c; }
.step-meta.step-error { color: #ff4d4f; max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-toggle { color: #bfbfbf; font-size: 12px; }

.trace-step-body { padding: 8px 10px; border-top: 1px solid var(--border-color, #f0f0f0); }

/* SQL */
.step-sql .sql-code {
  margin: 0;
  padding: 8px;
  background: #1e1e1e;
  color: #d4d4d4;
  border-radius: 4px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
  line-height: 1.5;
  overflow-x: auto;
  max-height: 300px;
  white-space: pre;
}

/* SQL 结果预览 */
.result-preview { margin-top: 8px; }
.result-preview-title {
  font-size: 12px;
  color: #595959;
  margin-bottom: 4px;
  font-weight: 500;
}
.result-table-wrap {
  max-height: 220px;
  overflow: auto;
  border: 1px solid #f0f0f0;
  border-radius: 4px;
}
.result-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  font-family: 'Consolas', 'Monaco', monospace;
}
.result-table th, .result-table td {
  padding: 4px 8px;
  text-align: left;
  border-bottom: 1px solid #f5f5f5;
  white-space: nowrap;
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.result-table th {
  background: #fafafa;
  font-weight: 600;
  color: #262626;
  position: sticky;
  top: 0;
  z-index: 1;
}
.result-table tbody tr:hover { background: #fafafa; }

/* Chart */
.step-chart { position: relative; }
.chart-canvas { width: 100%; height: 220px; }
.chart-loading, .chart-failed {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center;
  font-size: 12px; color: #8c8c8c;
  background: rgba(255,255,255,0.8);
}

/* Report */
.step-report { font-size: 13px; line-height: 1.6; }
.step-report :deep(h1), .step-report :deep(h2), .step-report :deep(h3) {
  margin: 10px 0 6px;
  font-weight: 600;
}
.step-report :deep(p) { margin: 4px 0; }
.report-loading, .report-failed {
  text-align: center;
  padding: 16px;
  font-size: 12px;
  color: #8c8c8c;
}
</style>
