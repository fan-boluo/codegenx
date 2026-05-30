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
  params: API.listAppChatHistoryParams,
  options?: { [key: string]: any }
) {
  const { appId, sessionId, ...queryParams } = params
  return request<any>(`/api/chatHistory/app/${appId}`, {
    method: 'GET',
    params: {
      sessionId: sessionId ?? '',
      ...queryParams,
    },
    ...(options || {}),
  })
}
