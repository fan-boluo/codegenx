declare namespace API {
  type AppVO = {
    id?: number
    userId?: number | string
    appName?: string
    cover?: string
    initPrompt?: string
    codeGenType?: string
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
    codeGenType?: string
    priority?: number
  }

  type ChatHistory = {
    id?: number
    appId?: number
    userId?: number
    message?: string
    messageType?: string
    createTime?: string
  }

  type ChatHistoryQueryRequest = {
    pageNum?: number
    pageSize?: number
    sortField?: string
    sortOrder?: string
    id?: number
    appId?: number
    userId?: number
    messageType?: string
    message?: string
  }

  type AppChatRequest = {
    appId: number
    message: string
    sessionId?: string
    requestId?: string
    stream?: boolean
  }

  type listAppChatHistoryParams = {
    appId: number
    pageSize?: number
    lastCreateTime?: string
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

  type BalanceVO = {
    balance?: number
    totalRecharge?: number
    totalSpending?: number
  }

  type RechargeRecord = {
    id?: number
    amount?: number
    paymentMethod?: string
    status?: string
    createTime?: string
  }

  type BillingRecord = {
    id?: number
    amount?: number
    billingType?: string
    description?: string
    createTime?: string
  }

  type PageRechargeRecord = {
    records?: RechargeRecord[]
    pageNumber?: number
    pageSize?: number
    totalPage?: number
    totalRow?: number
    optimizeCountQuery?: boolean
  }

  type PageBillingRecord = {
    records?: BillingRecord[]
    pageNumber?: number
    pageSize?: number
    totalPage?: number
    totalRow?: number
    optimizeCountQuery?: boolean
  }

  type getMyRechargeRecordsParams = {
    pageNum?: number
    pageSize?: number
  }

  type getMyBillingRecordsParams = {
    pageNum?: number
    pageSize?: number
  }

  type StripeRechargeRequest = {
    amount?: number
  }

  type StripeRechargeResponse = {
    checkoutUrl?: string
  }
}
