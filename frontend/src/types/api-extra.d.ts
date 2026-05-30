declare namespace API {
  type AppVO = {
    id?: number
    userId?: number | string
    appName?: string
    cover?: string
    initPrompt?: string
    priority?: number
    deployKey?: string
    deployedTime?: string
    createTime?: string
    updateTime?: string
    user?: UserVO
  }

  type AppQueryRequest = {
    pageNum?: number
    pageSize?: number
    sortField?: string
    sortOrder?: string
    id?: number
    userId?: number
    appName?: string
    priority?: number
  }

  type AppChatRequest = {
    appId: number
    message: string
    sessionId?: string
    requestId?: string
    stream?: boolean
  }

  type UserSummaryStatsVO = {
    tokenQuota?: number
    usedTokens?: number
    remainingQuota?: number
    totalTokens?: number
    totalRequests?: number
    successRequests?: number
    totalCost?: number
    todayCost?: number
  }

  type UserDailyStatsVO = {
    date?: string
    totalTokens?: number
    totalCost?: number
    requestCount?: number
  }

  type getMyDailyStatsParams = {
    startDate?: string
    endDate?: string
  }
}
