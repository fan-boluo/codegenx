// @ts-ignore
/* eslint-disable */
import request from '@/request'

/** Health Check GET /api/health/ */
export async function healthCheckApiHealthGet(options?: { [key: string]: any }) {
  return request<API.BaseResponseStr_>('/api/health/', {
    method: 'GET',
    ...(options || {}),
  })
}
