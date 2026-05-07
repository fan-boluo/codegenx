export interface MonitorPageData<T> {
  records: T[]
  pageNumber: number
  pageSize: number
  totalPage: number
  totalRow: number
  optimizeCountQuery?: boolean
}

export interface MonitorToolCallDetail {
  name: string
  latencyMs: number
  status: string
  callCount: number
}

export interface MonitorSessionSummary {
  sessionId: string
  traceId: string
  appId: string
  userId: string
  model: string
  status: string
  totalTurns: number
  totalPromptTokens: number
  totalCompletionTokens: number
  tokenBudget: number
  avgLlmLatencyMs: number
  avgFirstTokenMs: number
  maxLlmLatencyMs: number
  minLlmLatencyMs: number
  totalToolCalls: number
  totalErrors: number
  recoveryCount: number
  lastRecoveryKind: string
  avgMemoryHits: number
  totalMemoryHits: number
  endReason: string
  startedAt?: string
  endedAt?: string
  durationMs: number
  updatedAt?: string
}

export interface MonitorTurnSummary {
  traceId: string
  sessionId: string
  turnId: string
  turnNumber: number
  status: string
  promptTokens: number
  completionTokens: number
  llmLatencyMs: number
  firstTokenMs: number
  llmRecoveryCount: number
  llmRecoveryKind: string
  toolCallsCount: number
  toolCallsDetail: MonitorToolCallDetail[]
  memoryHits: number
  memoryRetrievalMs: number
  contextTokens: number
  contextTokenUsage: number
  errorCount: number
  startedAt?: string
  endedAt?: string
  durationMs: number
  createdAt?: string
}

export interface MonitorAlertRecordVO {
  id?: number
  ruleName: string
  level: string
  traceId: string
  sessionId: string
  turnId: string
  status: string
  message: string
  observedValue: string
  thresholdValue: string
  triggeredAt?: string
  resolvedAt?: string
  payload: Record<string, unknown>
}

export interface MonitorStatusCount {
  status: string
  count: number
}

export interface MonitorRuleCount {
  ruleName: string
  count: number
}

export interface MonitorOverviewStats {
  totalSessions: number
  runningSessions: number
  successSessions: number
  errorSessions: number
  totalTurns: number
  avgTurnDurationMs: number
  avgLlmLatencyMs: number
  avgFirstTokenMs: number
  avgContextTokens: number
  avgContextTokenUsage: number
  totalToolCalls: number
  totalMemoryHits: number
  openAlerts: number
  statusBreakdown: MonitorStatusCount[]
  alertBreakdown: MonitorRuleCount[]
}

export interface MonitorSessionDetail {
  session: MonitorSessionSummary
  turns: MonitorTurnSummary[]
  alerts: MonitorAlertRecordVO[]
}

export interface MonitorComponentHealth {
  name: string
  status: string
  message: string
  checkedAt?: string
  latencyMs: number
  consecutiveFailures: number
  lastSuccessAt?: string
  lastErrorAt?: string
  metadata: Record<string, unknown>
}

export interface MonitorHealthStatus {
  enabled: boolean
  overallStatus: string
  degraded: boolean
  checkedAt?: string
  components: MonitorComponentHealth[]
}

export interface MonitorCleanupTableResult {
  tableName: string
  status: string
  affectedRows: number
  cutoffAt?: string
  errorMessage: string
}

export interface MonitorCleanupSummary {
  retentionDays: number
  dryRun: boolean
  status: string
  deletedRows: number
  executedAt?: string
  tableResults: MonitorCleanupTableResult[]
}

export interface MonitorStorageConfig {
  enabled: boolean
  persistSpans: boolean
  persistSessionMetrics: boolean
  persistTurnMetrics: boolean
  useRedisWindows: boolean
}

export interface MonitorThresholdRuleConfig {
  enabled: boolean
  level: string
  thresholdSeconds?: number
  thresholdRatio?: number
  thresholdCount?: number
  thresholdTurns?: number
  windowSize?: number
}

export interface MonitorConfigResponse {
  enabled: boolean
  storage: MonitorStorageConfig
  alerts: {
    llmAvgLatencyLast5: MonitorThresholdRuleConfig
    llmSingleTimeout: MonitorThresholdRuleConfig
    tokenQuotaUsage: MonitorThresholdRuleConfig
    toolConsecutiveFailures: MonitorThresholdRuleConfig
    sessionMaxTurns: MonitorThresholdRuleConfig
  }
}
