import request from '@/request';
export async function getMonitorOverview(options) {
    return request('/api/stats/admin/monitor/overview', {
        method: 'GET',
        ...(options || {}),
    });
}
export async function listMonitorSessions(params, options) {
    return request('/api/stats/admin/monitor/sessions', {
        method: 'GET',
        params: {
            ...(params || {}),
        },
        ...(options || {}),
    });
}
export async function getMonitorSessionDetail(sessionId, options) {
    return request(`/api/stats/admin/monitor/sessions/${sessionId}`, {
        method: 'GET',
        ...(options || {}),
    });
}
export async function getMonitorTurnDetail(sessionId, turnId, options) {
    return request(`/api/stats/admin/monitor/sessions/${sessionId}/turns/${turnId}`, {
        method: 'GET',
        ...(options || {}),
    });
}
export async function listMonitorAlerts(params, options) {
    return request('/api/stats/admin/monitor/alerts', {
        method: 'GET',
        params: {
            ...(params || {}),
        },
        ...(options || {}),
    });
}
export async function getMonitorConfig(options) {
    return request('/api/stats/admin/monitor/config', {
        method: 'GET',
        ...(options || {}),
    });
}
export async function cleanupMonitorHistory(params, options) {
    return request('/api/stats/admin/monitor/cleanup', {
        method: 'POST',
        params: {
            ...(params || {}),
        },
        ...(options || {}),
    });
}
