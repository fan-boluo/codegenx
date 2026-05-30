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
      appId: Number(params.appId),
      sessionId: params.sessionId,
    },
    options
  )
}
