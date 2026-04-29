import request from '@/request'

const ok = <T>(data: T, message = 'ok') => ({
  data: {
    code: 0,
    data,
    message,
  },
})

const emptyBalance = (): API.BalanceVO => ({
  balance: 0,
  totalRecharge: 0,
  totalSpending: 0,
})

const emptyBillingPage = (): API.PageBillingRecord => ({
  records: [],
  pageNumber: 1,
  pageSize: 5,
  totalPage: 0,
  totalRow: 0,
  optimizeCountQuery: true,
})

export async function getMyBalance(options?: { [key: string]: any }) {
  try {
    return await request('/balance/my', {
      method: 'GET',
      ...(options || {}),
    })
  } catch {
    return ok(emptyBalance(), '余额接口暂未开放，已返回默认值')
  }
}

export async function getMyBillingRecords(
  params?: API.getMyBillingRecordsParams,
  options?: { [key: string]: any }
) {
  try {
    return await request('/balance/my/billing-records', {
      method: 'GET',
      params: {
        ...params,
      },
      ...(options || {}),
    })
  } catch {
    return ok(emptyBillingPage(), '账单接口暂未开放，已返回空数据')
  }
}
