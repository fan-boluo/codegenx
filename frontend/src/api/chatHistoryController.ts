import {
  listAllChatHistoryByPageForAdminApiChatHistoryAdminListPageVoPost,
  listAppChatHistoryApiChatHistoryAppAppIdGet,
} from './chatHistory'

export const listAllChatHistoryByPageForAdmin =
  listAllChatHistoryByPageForAdminApiChatHistoryAdminListPageVoPost

export async function listAppChatHistory(
  params: API.listAppChatHistoryParams,
  options?: { [key: string]: any }
) {
  return listAppChatHistoryApiChatHistoryAppAppIdGet(
    {
      app_id: Number(params.appId),
      pageSize: params.pageSize,
      lastCreateTime: params.lastCreateTime,
    },
    options
  )
}
