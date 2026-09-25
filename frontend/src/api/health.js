// @ts-ignore
/* eslint-disable */
import request from '@/request';
/** Health Check GET /api/health/ */
export async function healthCheckApiHealthGet(options) {
    return request('/api/health/', {
        method: 'GET',
        ...(options || {}),
    });
}
