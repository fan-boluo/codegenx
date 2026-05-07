<template>
  <div id="monitorManagePage">
    <a-row :gutter="16" class="summary-row">
      <a-col v-for="item in summaryCards" :key="item.label" :xs="24" :sm="12" :xl="6">
        <a-card>
          <a-statistic :title="item.label" :value="item.value" :suffix="item.suffix" />
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" class="summary-row">
      <a-col :xs="24" :xl="14">
        <a-card title="状态分布" class="panel-card">
          <div class="tag-list">
            <a-tag v-for="item in overview?.statusBreakdown || []" :key="item.status" :color="statusColor(item.status)">
              {{ item.status }}: {{ item.count }}
            </a-tag>
          </div>
        </a-card>
      </a-col>
      <a-col :xs="24" :xl="10">
        <a-card title="告警规则分布" class="panel-card">
          <div class="tag-list">
            <a-tag v-for="item in overview?.alertBreakdown || []" :key="item.ruleName" color="orange">
              {{ item.ruleName }}: {{ item.count }}
            </a-tag>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <a-row :gutter="16" class="summary-row">
      <a-col :xs="24" :xl="14">
        <a-card title="运行健康" class="panel-card" :loading="healthLoading">
          <div class="tag-list health-summary-tags">
            <a-tag :color="healthColor(monitorHealth?.overallStatus)">
              overall: {{ monitorHealth?.overallStatus || 'unknown' }}
            </a-tag>
            <a-tag v-if="monitorHealth?.checkedAt" color="blue">
              checked: {{ formatTime(monitorHealth?.checkedAt) }}
            </a-tag>
          </div>
          <div class="health-component-list">
            <div v-for="component in monitorHealth?.components || []" :key="component.name" class="health-component-item">
              <div class="health-component-title">
                <span>{{ component.name }}</span>
                <a-tag :color="healthColor(component.status)">{{ component.status }}</a-tag>
              </div>
              <div class="subtle-text">
                {{ component.message || '-' }}
                <span v-if="component.latencyMs"> / {{ component.latencyMs }} ms</span>
                <span v-if="component.consecutiveFailures"> / fail={{ component.consecutiveFailures }}</span>
              </div>
            </div>
          </div>
        </a-card>
      </a-col>
      <a-col :xs="24" :xl="10">
        <a-card title="运维操作" class="panel-card">
          <a-form layout="inline" :model="cleanupForm" @finish="runCleanup">
            <a-form-item label="保留天数">
              <a-input-number v-model:value="cleanupForm.retentionDays" :min="1" :max="3650" />
            </a-form-item>
            <a-form-item label="Dry Run">
              <a-switch v-model:checked="cleanupForm.dryRun" />
            </a-form-item>
            <a-form-item>
              <a-button type="primary" html-type="submit" :loading="cleanupLoading">执行清理</a-button>
            </a-form-item>
          </a-form>
          <div class="cleanup-hint subtle-text">兼容 exporter: /stats/admin/monitor/metrics</div>
          <div v-if="cleanupResult" class="cleanup-result">
            <a-tag :color="cleanupResult.status === 'success' ? 'green' : 'orange'">
              {{ cleanupResult.status }}
            </a-tag>
            <span>
              {{ cleanupResult.dryRun ? '预计清理' : '已清理' }} {{ cleanupResult.deletedRows }} 条，保留 {{ cleanupResult.retentionDays }} 天
            </span>
            <div class="tag-list cleanup-table-tags">
              <a-tag
                v-for="item in cleanupResult.tableResults"
                :key="item.tableName"
                :color="item.status === 'success' ? 'blue' : 'red'"
              >
                {{ item.tableName }}: {{ item.status === 'success' ? item.affectedRows : item.errorMessage }}
              </a-tag>
            </div>
          </div>
        </a-card>
      </a-col>
    </a-row>

    <a-card title="会话检索" class="panel-card">
      <a-form layout="inline" :model="sessionQuery" @finish="doSessionSearch">
        <a-form-item label="会话ID">
          <a-input v-model:value="sessionQuery.sessionId" placeholder="输入 sessionId" />
        </a-form-item>
        <a-form-item label="用户ID">
          <a-input v-model:value="sessionQuery.userId" placeholder="输入 userId" />
        </a-form-item>
        <a-form-item label="状态">
          <a-select v-model:value="sessionQuery.status" allow-clear style="width: 140px" placeholder="全部状态">
            <a-select-option value="running">running</a-select-option>
            <a-select-option value="success">success</a-select-option>
            <a-select-option value="error">error</a-select-option>
            <a-select-option value="stopped">stopped</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item>
          <a-button type="primary" html-type="submit">搜索</a-button>
        </a-form-item>
      </a-form>
      <a-divider />
      <a-table
        row-key="sessionId"
        :loading="sessionLoading"
        :columns="sessionColumns"
        :data-source="sessionPage.records"
        :pagination="sessionPagination"
        :scroll="{ x: 1400 }"
        @change="onSessionTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="statusColor(record.status)">{{ record.status }}</a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'startedAt'">
            <div>{{ formatTime(record.startedAt) }}</div>
            <div class="subtle-text">{{ formatRelativeTime(record.startedAt) }}</div>
          </template>
          <template v-else-if="column.dataIndex === 'avgLlmLatencyMs'">
            {{ formatMs(record.avgLlmLatencyMs) }}
          </template>
          <template v-else-if="column.dataIndex === 'durationMs'">
            {{ formatMs(record.durationMs) }}
          </template>
          <template v-else-if="column.key === 'action'">
            <a-button type="link" @click="openSessionDetail(record.sessionId)">查看详情</a-button>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card title="告警列表" class="panel-card">
      <a-form layout="inline" :model="alertQuery" @finish="doAlertSearch">
        <a-form-item label="规则">
          <a-input v-model:value="alertQuery.ruleName" placeholder="输入 ruleName" />
        </a-form-item>
        <a-form-item label="Session">
          <a-input v-model:value="alertQuery.sessionId" placeholder="输入 sessionId" />
        </a-form-item>
        <a-form-item label="状态">
          <a-select v-model:value="alertQuery.status" allow-clear style="width: 140px" placeholder="全部状态">
            <a-select-option value="open">open</a-select-option>
            <a-select-option value="resolved">resolved</a-select-option>
          </a-select>
        </a-form-item>
        <a-form-item>
          <a-button type="primary" html-type="submit">搜索</a-button>
        </a-form-item>
      </a-form>
      <a-divider />
      <a-table
        row-key="id"
        :loading="alertLoading"
        :columns="alertColumns"
        :data-source="alertPage.records"
        :pagination="alertPagination"
        :scroll="{ x: 1200 }"
        @change="onAlertTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'status'">
            <a-tag :color="record.status === 'open' ? 'red' : 'green'">{{ record.status }}</a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'level'">
            <a-tag :color="record.level === 'ERROR' ? 'red' : 'orange'">{{ record.level }}</a-tag>
          </template>
          <template v-else-if="column.dataIndex === 'triggeredAt'">
            {{ formatTime(record.triggeredAt) }}
          </template>
          <template v-else-if="column.dataIndex === 'resolvedAt'">
            {{ formatTime(record.resolvedAt) }}
          </template>
        </template>
      </a-table>
    </a-card>

    <a-card title="告警配置" class="panel-card">
      <a-descriptions bordered :column="1" size="small">
        <a-descriptions-item label="Redis 实时窗口">
          {{ monitorConfig?.storage.useRedisWindows ? '开启' : '关闭' }}
        </a-descriptions-item>
        <a-descriptions-item label="LLM 最近窗口">
          {{ configSummary('llmAvgLatencyLast5') }}
        </a-descriptions-item>
        <a-descriptions-item label="单次 LLM 超时">
          {{ configSummary('llmSingleTimeout') }}
        </a-descriptions-item>
        <a-descriptions-item label="Token 配额使用率">
          {{ configSummary('tokenQuotaUsage') }}
        </a-descriptions-item>
        <a-descriptions-item label="工具连续失败">
          {{ configSummary('toolConsecutiveFailures') }}
        </a-descriptions-item>
        <a-descriptions-item label="Session 最大轮数">
          {{ configSummary('sessionMaxTurns') }}
        </a-descriptions-item>
      </a-descriptions>
    </a-card>

    <a-drawer v-model:open="sessionDrawerOpen" width="1080" title="Session 详情">
      <template v-if="selectedSessionDetail">
        <a-descriptions bordered :column="2" size="small">
          <a-descriptions-item label="Session ID">{{ selectedSessionDetail.session.sessionId }}</a-descriptions-item>
          <a-descriptions-item label="Trace ID">{{ selectedSessionDetail.session.traceId }}</a-descriptions-item>
          <a-descriptions-item label="状态">
            <a-tag :color="statusColor(selectedSessionDetail.session.status)">
              {{ selectedSessionDetail.session.status }}
            </a-tag>
          </a-descriptions-item>
          <a-descriptions-item label="模型">{{ selectedSessionDetail.session.model || '-' }}</a-descriptions-item>
          <a-descriptions-item label="总轮次">{{ selectedSessionDetail.session.totalTurns }}</a-descriptions-item>
          <a-descriptions-item label="总 Tool 调用">{{ selectedSessionDetail.session.totalToolCalls }}</a-descriptions-item>
          <a-descriptions-item label="平均 LLM 延迟">{{ formatMs(selectedSessionDetail.session.avgLlmLatencyMs) }}</a-descriptions-item>
          <a-descriptions-item label="平均首包延迟">{{ formatMs(selectedSessionDetail.session.avgFirstTokenMs) }}</a-descriptions-item>
          <a-descriptions-item label="总 Prompt Tokens">{{ selectedSessionDetail.session.totalPromptTokens }}</a-descriptions-item>
          <a-descriptions-item label="总 Completion Tokens">{{ selectedSessionDetail.session.totalCompletionTokens }}</a-descriptions-item>
          <a-descriptions-item label="开始时间">{{ formatTime(selectedSessionDetail.session.startedAt) }}</a-descriptions-item>
          <a-descriptions-item label="结束时间">{{ formatTime(selectedSessionDetail.session.endedAt) }}</a-descriptions-item>
        </a-descriptions>

        <a-divider>Turn 延迟折线图</a-divider>
        <div class="chart-card">
          <svg viewBox="0 0 640 200" class="latency-chart" preserveAspectRatio="none">
            <line x1="24" y1="176" x2="616" y2="176" class="axis-line" />
            <line x1="24" y1="24" x2="24" y2="176" class="axis-line" />
            <polyline :points="latencyChartPoints" class="chart-line" fill="none" />
            <circle
              v-for="point in latencyChartDots"
              :key="point.key"
              :cx="point.x"
              :cy="point.y"
              r="4"
              class="chart-dot"
            />
          </svg>
          <div class="chart-empty" v-if="!latencyChartDots.length">暂无 turn 延迟数据</div>
        </div>

        <a-divider>Turn 列表</a-divider>
        <a-table
          row-key="turnId"
          :columns="turnColumns"
          :data-source="selectedSessionDetail.turns"
          :pagination="false"
          size="small"
          :scroll="{ x: 1000 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'status'">
              <a-tag :color="statusColor(record.status)">{{ record.status }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'llmLatencyMs'">
              {{ formatMs(record.llmLatencyMs) }}
            </template>
            <template v-else-if="column.dataIndex === 'startedAt'">
              {{ formatTime(record.startedAt) }}
            </template>
            <template v-else-if="column.key === 'action'">
              <a-button type="link" @click="openTurnDetail(record.turnId)">查看明细</a-button>
            </template>
          </template>
        </a-table>

        <a-divider>Session 告警</a-divider>
        <a-table row-key="id" :columns="sessionAlertColumns" :data-source="selectedSessionDetail.alerts" :pagination="false" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'status'">
              <a-tag :color="record.status === 'open' ? 'red' : 'green'">{{ record.status }}</a-tag>
            </template>
            <template v-else-if="column.dataIndex === 'triggeredAt'">
              {{ formatTime(record.triggeredAt) }}
            </template>
          </template>
        </a-table>
      </template>
    </a-drawer>

    <a-modal v-model:open="turnModalOpen" title="Turn 详情" width="720" :footer="null">
      <template v-if="selectedTurnDetail">
        <a-descriptions bordered :column="2" size="small">
          <a-descriptions-item label="Turn ID">{{ selectedTurnDetail.turnId }}</a-descriptions-item>
          <a-descriptions-item label="状态">{{ selectedTurnDetail.status }}</a-descriptions-item>
          <a-descriptions-item label="LLM 延迟">{{ formatMs(selectedTurnDetail.llmLatencyMs) }}</a-descriptions-item>
          <a-descriptions-item label="首包延迟">{{ formatMs(selectedTurnDetail.firstTokenMs) }}</a-descriptions-item>
          <a-descriptions-item label="Prompt Tokens">{{ selectedTurnDetail.promptTokens }}</a-descriptions-item>
          <a-descriptions-item label="Completion Tokens">{{ selectedTurnDetail.completionTokens }}</a-descriptions-item>
          <a-descriptions-item label="Context Tokens">{{ selectedTurnDetail.contextTokens }}</a-descriptions-item>
          <a-descriptions-item label="Context Usage">{{ selectedTurnDetail.contextTokenUsage }}</a-descriptions-item>
          <a-descriptions-item label="Memory Hits">{{ selectedTurnDetail.memoryHits }}</a-descriptions-item>
          <a-descriptions-item label="Memory Latency">{{ formatMs(selectedTurnDetail.memoryRetrievalMs) }}</a-descriptions-item>
        </a-descriptions>
        <a-divider>Tool 调用明细</a-divider>
        <a-table row-key="name" :columns="toolDetailColumns" :data-source="selectedTurnDetail.toolCallsDetail" :pagination="false" size="small">
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'latencyMs'">
              {{ formatMs(record.latencyMs) }}
            </template>
            <template v-else-if="column.dataIndex === 'status'">
              <a-tag :color="statusColor(record.status)">{{ record.status }}</a-tag>
            </template>
          </template>
        </a-table>
      </template>
    </a-modal>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  cleanupMonitorHistory,
  getMonitorConfig,
  getMonitorHealth,
  getMonitorOverview,
  getMonitorSessionDetail,
  getMonitorTurnDetail,
  listMonitorAlerts,
  listMonitorSessions,
} from '@/api/monitorController'
import { formatRelativeTime, formatTime } from '@/utils/time'
import type {
  MonitorAlertRecordVO,
  MonitorCleanupSummary,
  MonitorConfigResponse,
  MonitorHealthStatus,
  MonitorOverviewStats,
  MonitorPageData,
  MonitorSessionDetail,
  MonitorSessionSummary,
  MonitorTurnSummary,
} from '@/types/monitor'

const sessionColumns = [
  { title: 'Session ID', dataIndex: 'sessionId', width: 220 },
  { title: '状态', dataIndex: 'status', width: 110 },
  { title: '用户', dataIndex: 'userId', width: 120 },
  { title: '轮次', dataIndex: 'totalTurns', width: 90 },
  { title: '平均 LLM 延迟', dataIndex: 'avgLlmLatencyMs', width: 140 },
  { title: 'Tool 调用', dataIndex: 'totalToolCalls', width: 110 },
  { title: 'Memory Hits', dataIndex: 'totalMemoryHits', width: 110 },
  { title: '开始时间', dataIndex: 'startedAt', width: 180 },
  { title: '总时长', dataIndex: 'durationMs', width: 120 },
  { title: '操作', key: 'action', width: 120, fixed: 'right' },
]

const alertColumns = [
  { title: '规则', dataIndex: 'ruleName', width: 180 },
  { title: '等级', dataIndex: 'level', width: 100 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: 'Session', dataIndex: 'sessionId', width: 220 },
  { title: '观测值', dataIndex: 'observedValue', width: 100 },
  { title: '阈值', dataIndex: 'thresholdValue', width: 100 },
  { title: '触发时间', dataIndex: 'triggeredAt', width: 180 },
  { title: '消息', dataIndex: 'message', width: 360 },
]

const turnColumns = [
  { title: 'Turn', dataIndex: 'turnNumber', width: 80 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: 'LLM 延迟', dataIndex: 'llmLatencyMs', width: 120 },
  { title: 'Tool 调用', dataIndex: 'toolCallsCount', width: 100 },
  { title: 'Memory Hits', dataIndex: 'memoryHits', width: 100 },
  { title: '开始时间', dataIndex: 'startedAt', width: 180 },
  { title: '操作', key: 'action', width: 120, fixed: 'right' },
]

const sessionAlertColumns = [
  { title: '规则', dataIndex: 'ruleName', width: 160 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: '观测值', dataIndex: 'observedValue', width: 100 },
  { title: '阈值', dataIndex: 'thresholdValue', width: 100 },
  { title: '触发时间', dataIndex: 'triggeredAt', width: 180 },
]

const toolDetailColumns = [
  { title: 'Tool', dataIndex: 'name', width: 220 },
  { title: '状态', dataIndex: 'status', width: 100 },
  { title: '延迟', dataIndex: 'latencyMs', width: 120 },
  { title: '调用次数', dataIndex: 'callCount', width: 100 },
]

const overview = ref<MonitorOverviewStats>()
const monitorConfig = ref<MonitorConfigResponse>()
const monitorHealth = ref<MonitorHealthStatus>()
const cleanupResult = ref<MonitorCleanupSummary>()
const selectedSessionDetail = ref<MonitorSessionDetail>()
const selectedTurnDetail = ref<MonitorTurnSummary>()
const sessionDrawerOpen = ref(false)
const turnModalOpen = ref(false)
const healthLoading = ref(false)
const cleanupLoading = ref(false)
const sessionLoading = ref(false)
const alertLoading = ref(false)

const sessionPage = ref<MonitorPageData<MonitorSessionSummary>>({
  records: [],
  pageNumber: 1,
  pageSize: 10,
  totalPage: 0,
  totalRow: 0,
})

const alertPage = ref<MonitorPageData<MonitorAlertRecordVO>>({
  records: [],
  pageNumber: 1,
  pageSize: 10,
  totalPage: 0,
  totalRow: 0,
})

const sessionQuery = reactive({
  pageNum: 1,
  pageSize: 10,
  status: undefined as string | undefined,
  appId: '',
  userId: '',
  sessionId: '',
  traceId: '',
})

const alertQuery = reactive({
  pageNum: 1,
  pageSize: 10,
  status: undefined as string | undefined,
  level: undefined as string | undefined,
  ruleName: '',
  sessionId: '',
})

const cleanupForm = reactive({
  retentionDays: 7,
  dryRun: true,
})

const summaryCards = computed(() => [
  { label: 'Session 总数', value: overview.value?.totalSessions ?? 0 },
  { label: '运行中 Session', value: overview.value?.runningSessions ?? 0 },
  { label: 'Open Alerts', value: overview.value?.openAlerts ?? 0 },
  { label: '总 Turn 数', value: overview.value?.totalTurns ?? 0 },
  { label: '平均 Turn 时长', value: overview.value?.avgTurnDurationMs ?? 0, suffix: 'ms' },
  { label: '平均 LLM 延迟', value: overview.value?.avgLlmLatencyMs ?? 0, suffix: 'ms' },
  { label: '总 Tool 调用', value: overview.value?.totalToolCalls ?? 0 },
  { label: '总 Memory Hits', value: overview.value?.totalMemoryHits ?? 0 },
])

const sessionPagination = computed(() => ({
  current: sessionQuery.pageNum,
  pageSize: sessionQuery.pageSize,
  total: sessionPage.value.totalRow,
  showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 条`,
}))

const alertPagination = computed(() => ({
  current: alertQuery.pageNum,
  pageSize: alertQuery.pageSize,
  total: alertPage.value.totalRow,
  showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 条`,
}))

const latencyChartDots = computed(() => {
  const turns = [...(selectedSessionDetail.value?.turns ?? [])].reverse()
  if (!turns.length) {
    return []
  }
  const width = 640
  const height = 200
  const padding = 24
  const maxValue = Math.max(...turns.map((item) => item.llmLatencyMs || 0), 1)
  return turns.map((turn, index) => {
    const usableWidth = width - padding * 2
    const usableHeight = height - padding * 2
    const x = turns.length === 1 ? width / 2 : padding + (index * usableWidth) / (turns.length - 1)
    const y = height - padding - ((turn.llmLatencyMs || 0) / maxValue) * usableHeight
    return {
      key: turn.turnId,
      x,
      y,
    }
  })
})

const latencyChartPoints = computed(() => latencyChartDots.value.map((item) => `${item.x},${item.y}`).join(' '))

const formatMs = (value?: number) => `${Math.round(value ?? 0)} ms`

const healthColor = (status?: string) => {
  switch (status) {
    case 'up':
      return 'green'
    case 'degraded':
      return 'orange'
    case 'down':
      return 'red'
    default:
      return 'default'
  }
}

const statusColor = (status?: string) => {
  switch (status) {
    case 'success':
      return 'green'
    case 'error':
      return 'red'
    case 'running':
      return 'blue'
    case 'stopped':
      return 'default'
    default:
      return 'default'
  }
}

const configSummary = (ruleKey: keyof MonitorConfigResponse['alerts']) => {
  const rule = monitorConfig.value?.alerts?.[ruleKey]
  if (!rule) {
    return '-'
  }
  const threshold =
    rule.thresholdSeconds ?? rule.thresholdRatio ?? rule.thresholdCount ?? rule.thresholdTurns ?? '-'
  const windowSize = rule.windowSize ? `, window=${rule.windowSize}` : ''
  return `${rule.enabled ? '启用' : '关闭'} / level=${rule.level} / threshold=${threshold}${windowSize}`
}

const fetchOverview = async () => {
  const res = await getMonitorOverview()
  if (res.data.code === 0 && res.data.data) {
    overview.value = res.data.data as MonitorOverviewStats
    return
  }
  message.error(`获取监控概览失败：${res.data.message}`)
}

const fetchConfig = async () => {
  const res = await getMonitorConfig()
  if (res.data.code === 0 && res.data.data) {
    monitorConfig.value = res.data.data as MonitorConfigResponse
    return
  }
  message.error(`获取监控配置失败：${res.data.message}`)
}

const fetchHealth = async () => {
  healthLoading.value = true
  try {
    const res = await getMonitorHealth()
    if (res.data.code === 0 && res.data.data) {
      monitorHealth.value = res.data.data as MonitorHealthStatus
      return
    }
    message.error(`获取健康状态失败：${res.data.message}`)
  } finally {
    healthLoading.value = false
  }
}

const fetchSessions = async () => {
  sessionLoading.value = true
  try {
    const res = await listMonitorSessions({ ...sessionQuery })
    if (res.data.code === 0 && res.data.data) {
      sessionPage.value = res.data.data as MonitorPageData<MonitorSessionSummary>
      return
    }
    message.error(`获取会话列表失败：${res.data.message}`)
  } finally {
    sessionLoading.value = false
  }
}

const fetchAlerts = async () => {
  alertLoading.value = true
  try {
    const res = await listMonitorAlerts({ ...alertQuery })
    if (res.data.code === 0 && res.data.data) {
      alertPage.value = res.data.data as MonitorPageData<MonitorAlertRecordVO>
      return
    }
    message.error(`获取告警列表失败：${res.data.message}`)
  } finally {
    alertLoading.value = false
  }
}

const openSessionDetail = async (sessionId: string) => {
  const res = await getMonitorSessionDetail(sessionId)
  if (res.data.code === 0 && res.data.data) {
    selectedSessionDetail.value = res.data.data as MonitorSessionDetail
    sessionDrawerOpen.value = true
    return
  }
  message.error(`获取 Session 详情失败：${res.data.message}`)
}

const openTurnDetail = async (turnId: string) => {
  if (!selectedSessionDetail.value) {
    return
  }
  const res = await getMonitorTurnDetail(selectedSessionDetail.value.session.sessionId, turnId)
  if (res.data.code === 0 && res.data.data) {
    selectedTurnDetail.value = res.data.data as MonitorTurnSummary
    turnModalOpen.value = true
    return
  }
  message.error(`获取 Turn 详情失败：${res.data.message}`)
}

const onSessionTableChange = (page: { current: number; pageSize: number }) => {
  sessionQuery.pageNum = page.current
  sessionQuery.pageSize = page.pageSize
  fetchSessions()
}

const onAlertTableChange = (page: { current: number; pageSize: number }) => {
  alertQuery.pageNum = page.current
  alertQuery.pageSize = page.pageSize
  fetchAlerts()
}

const doSessionSearch = () => {
  sessionQuery.pageNum = 1
  fetchSessions()
}

const doAlertSearch = () => {
  alertQuery.pageNum = 1
  fetchAlerts()
}

const runCleanup = async () => {
  cleanupLoading.value = true
  try {
    const res = await cleanupMonitorHistory({
      retentionDays: cleanupForm.retentionDays,
      dryRun: cleanupForm.dryRun,
    })
    if (res.data.code === 0 && res.data.data) {
      cleanupResult.value = res.data.data as MonitorCleanupSummary
      message.success(cleanupForm.dryRun ? '清理预览完成' : '清理执行完成')
      await Promise.all([fetchOverview(), fetchAlerts(), fetchHealth()])
      return
    }
    message.error(`执行清理失败：${res.data.message}`)
  } finally {
    cleanupLoading.value = false
  }
}

onMounted(async () => {
  await Promise.all([fetchOverview(), fetchConfig(), fetchHealth(), fetchSessions(), fetchAlerts()])
})
</script>

<style scoped>
#monitorManagePage {
  padding: 24px;
  background: #fff;
  margin-top: 16px;
}

.summary-row {
  margin-bottom: 16px;
}

.panel-card {
  margin-bottom: 16px;
}

.tag-list {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.health-summary-tags {
  margin-bottom: 12px;
}

.health-component-list {
  display: grid;
  gap: 12px;
}

.health-component-item {
  padding: 12px;
  border-radius: 10px;
  background: #fafcff;
  border: 1px solid #e6f4ff;
}

.health-component-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-weight: 500;
}

.cleanup-hint {
  margin-top: 12px;
}

.cleanup-result {
  margin-top: 12px;
  display: grid;
  gap: 8px;
}

.cleanup-table-tags {
  margin-top: 4px;
}

.subtle-text {
  color: rgba(0, 0, 0, 0.45);
  font-size: 12px;
}

.chart-card {
  position: relative;
  background: linear-gradient(180deg, #f7fbff 0%, #eef5ff 100%);
  border: 1px solid #d7e7ff;
  border-radius: 12px;
  padding: 12px;
  min-height: 220px;
}

.latency-chart {
  width: 100%;
  height: 220px;
}

.axis-line {
  stroke: #adc6ff;
  stroke-width: 1;
}

.chart-line {
  stroke: #1677ff;
  stroke-width: 3;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.chart-dot {
  fill: #1677ff;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: rgba(0, 0, 0, 0.45);
}
</style>
