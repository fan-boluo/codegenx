import request from '@/request';
const ok = (data, message = 'ok') => ({
    data: {
        code: 0,
        data,
        message,
    },
});
const emptySummaryStats = () => ({
    tokenQuota: 0,
    usedTokens: 0,
    remainingQuota: 0,
    totalTokens: 0,
    totalRequests: 0,
    successRequests: 0,
    totalCost: 0,
    todayCost: 0,
});
export async function getMySummaryStats(options) {
    try {
        return await request('/stats/my/summary', {
            method: 'GET',
            ...(options || {}),
        });
    }
    catch {
        return ok(emptySummaryStats(), '统计接口暂未开放，已返回默认值');
    }
}
export async function getMyDailyStats(params, options) {
    try {
        return await request('/stats/my/daily', {
            method: 'GET',
            params: {
                ...params,
            },
            ...(options || {}),
        });
    }
    catch {
        return ok([], '每日统计接口暂未开放，已返回空数据');
    }
}
