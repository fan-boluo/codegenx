import request from '@/request'

const ok = <T>(data: T, message = 'ok') => ({
  data: {
    code: 0,
    data,
    message,
  },
})

const fail = <T>(message: string) => ({
  data: {
    code: 50000,
    data: undefined as T | undefined,
    message,
  },
})

const emptyRechargePage = (): API.PageRechargeRecord => ({
  records: [],
  pageNumber: 1,
  pageSize: 5,
  totalPage: 0,
  totalRow: 0,
  optimizeCountQuery: true,
})

export async function getMyRechargeRecords(
  params?: API.getMyRechargeRecordsParams,
  options?: { [key: string]: any }
) {
  try {
    return await request('/recharge/my/records', {
      method: 'GET',
      params: {
        ...params,
      },
      ...(options || {}),
    })
  } catch {
    return ok(emptyRechargePage(), '充值记录接口暂未开放，已返回空数据')
  }
}

export async function createStripeRecharge(
  body: API.StripeRechargeRequest,
  options?: { [key: string]: any }
) {
  try {
    return await request('/recharge/stripe/create', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    })
  } catch {
    return fail<API.StripeRechargeResponse>('充值接口暂未开放')
  }
}
