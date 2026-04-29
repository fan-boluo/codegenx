// @ts-ignore
/* eslint-disable */
import request from '@/request'

/** List All Chat History By Page For Admin POST /api/chatHistory/admin/list/page/vo */
export async function listAllChatHistoryByPageForAdminApiChatHistoryAdminListPageVoPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/chatHistory/admin/list/page/vo', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** List App Chat History GET /api/chatHistory/app/${param0} */
export async function listAppChatHistoryApiChatHistoryAppAppIdGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listAppChatHistoryApiChatHistoryAppAppIdGetParams,
  options?: { [key: string]: any }
) {
  const { app_id: param0, ...queryParams } = params
  return request<any>(`/api/chatHistory/app/${param0}`, {
    method: 'GET',
    params: {
      // pageSize has a default value: 10
      pageSize: '10',
      ...queryParams,
    },
    ...(options || {}),
  })
}
