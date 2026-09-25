declare namespace API {
  type AppVO = {
    id?: number
    owner?: number | string
    ownerName?: string | null
    appName?: string
    dbName?: string
    createTime?: string
    updateTime?: string
  }

  type AppQueryRequest = {
    pageNum?: number
    pageSize?: number
    sortField?: string
    sortOrder?: string
    id?: number
    appName?: string
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
