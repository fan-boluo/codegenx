import { computed, onMounted, reactive, ref } from 'vue';
import { message } from 'ant-design-vue';
import { cleanupMonitorHistory, getMonitorConfig, getMonitorOverview, getMonitorSessionDetail, getMonitorTurnDetail, listMonitorAlerts, listMonitorSessions, } from '@/api/monitorController';
import { formatRelativeTime, formatTime } from '@/utils/time';
const sessionColumns = [
    { title: 'Session ID', dataIndex: 'sessionId', width: 220 },
    { title: '状态', dataIndex: 'status', width: 110 },
    { title: '用户', dataIndex: 'userId', width: 120 },
    { title: '轮次', dataIndex: 'totalTurns', width: 90 },
    { title: '平均 LLM 延迟', dataIndex: 'avgLlmLatencyMs', width: 140 },
    { title: 'Tool 调用', dataIndex: 'totalToolCalls', width: 110 },
    { title: 'Memory Hits', dataIndex: 'totalMemoryHits', width: 110 },
    { title: '开始时间', dataIndex: 'startedAt', width: 180 },
    { title: '总时长', dataIndex: 'durationMs', width: 120 },
    { title: '操作', key: 'action', width: 120, fixed: 'right' },
];
const alertColumns = [
    { title: '规则', dataIndex: 'ruleName', width: 180 },
    { title: '等级', dataIndex: 'level', width: 100 },
    { title: '状态', dataIndex: 'status', width: 100 },
    { title: 'Session', dataIndex: 'sessionId', width: 220 },
    { title: '观测值', dataIndex: 'observedValue', width: 100 },
    { title: '阈值', dataIndex: 'thresholdValue', width: 100 },
    { title: '触发时间', dataIndex: 'triggeredAt', width: 180 },
    { title: '消息', dataIndex: 'message', width: 360 },
];
const turnColumns = [
    { title: 'Turn', dataIndex: 'turnNumber', width: 80 },
    { title: '状态', dataIndex: 'status', width: 100 },
    { title: 'LLM 延迟', dataIndex: 'llmLatencyMs', width: 120 },
    { title: 'Tool 调用', dataIndex: 'toolCallsCount', width: 100 },
    { title: 'Memory Hits', dataIndex: 'memoryHits', width: 100 },
    { title: '开始时间', dataIndex: 'startedAt', width: 180 },
    { title: '操作', key: 'action', width: 120, fixed: 'right' },
];
const sessionAlertColumns = [
    { title: '规则', dataIndex: 'ruleName', width: 160 },
    { title: '状态', dataIndex: 'status', width: 100 },
    { title: '观测值', dataIndex: 'observedValue', width: 100 },
    { title: '阈值', dataIndex: 'thresholdValue', width: 100 },
    { title: '触发时间', dataIndex: 'triggeredAt', width: 180 },
];
const toolDetailColumns = [
    { title: 'Tool', dataIndex: 'name', width: 220 },
    { title: '状态', dataIndex: 'status', width: 100 },
    { title: '延迟', dataIndex: 'latencyMs', width: 120 },
    { title: '调用次数', dataIndex: 'callCount', width: 100 },
];
const overview = ref();
const monitorConfig = ref();
const cleanupResult = ref();
const selectedSessionDetail = ref();
const selectedTurnDetail = ref();
const sessionDrawerOpen = ref(false);
const turnModalOpen = ref(false);
const cleanupLoading = ref(false);
const sessionLoading = ref(false);
const alertLoading = ref(false);
const sessionPage = ref({
    records: [],
    pageNumber: 1,
    pageSize: 10,
    totalPage: 0,
    totalRow: 0,
});
const alertPage = ref({
    records: [],
    pageNumber: 1,
    pageSize: 10,
    totalPage: 0,
    totalRow: 0,
});
const sessionQuery = reactive({
    pageNum: 1,
    pageSize: 10,
    status: undefined,
    appId: '',
    userId: '',
    sessionId: '',
    traceId: '',
});
const alertQuery = reactive({
    pageNum: 1,
    pageSize: 10,
    status: undefined,
    level: undefined,
    ruleName: '',
    sessionId: '',
});
const cleanupForm = reactive({
    retentionDays: 7,
    dryRun: true,
});
const summaryCards = computed(() => {
    const totalSessions = overview.value?.totalSessions ?? 0;
    const totalTurns = overview.value?.totalTurns ?? 0;
    const avgTurns = totalSessions > 0 ? (totalTurns / totalSessions).toFixed(1) : '0';
    return [
        { label: 'Session 总数', value: totalSessions },
        { label: '运行中 Session', value: overview.value?.runningSessions ?? 0 },
        { label: '平均 Turn 数', value: avgTurns },
        { label: '平均 Turn 时长', value: overview.value?.avgTurnDurationMs ?? 0, suffix: 'ms' },
        { label: 'Token 使用总数', value: overview.value?.totalTokens ?? 0 },
        { label: '平均 LLM 耗时', value: overview.value?.avgLlmLatencyMs ?? 0, suffix: 'ms' },
        { label: 'Tool 调用总数', value: overview.value?.totalToolCalls ?? 0 },
        { label: 'Memory Hits 总数', value: overview.value?.totalMemoryHits ?? 0 },
    ];
});
const sessionPagination = computed(() => ({
    current: sessionQuery.pageNum,
    pageSize: sessionQuery.pageSize,
    total: sessionPage.value.totalRow,
    showSizeChanger: true,
    showTotal: (total) => `共 ${total} 条`,
}));
const alertPagination = computed(() => ({
    current: alertQuery.pageNum,
    pageSize: alertQuery.pageSize,
    total: alertPage.value.totalRow,
    showSizeChanger: true,
    showTotal: (total) => `共 ${total} 条`,
}));
const latencyChartDots = computed(() => {
    const turns = [...(selectedSessionDetail.value?.turns ?? [])].reverse();
    if (!turns.length) {
        return [];
    }
    const width = 640;
    const height = 200;
    const padding = 24;
    const maxValue = Math.max(...turns.map((item) => item.llmLatencyMs || 0), 1);
    return turns.map((turn, index) => {
        const usableWidth = width - padding * 2;
        const usableHeight = height - padding * 2;
        const x = turns.length === 1 ? width / 2 : padding + (index * usableWidth) / (turns.length - 1);
        const y = height - padding - ((turn.llmLatencyMs || 0) / maxValue) * usableHeight;
        return {
            key: turn.turnId,
            x,
            y,
        };
    });
});
const latencyChartPoints = computed(() => latencyChartDots.value.map((item) => `${item.x},${item.y}`).join(' '));
const formatMs = (value) => `${Math.round(value ?? 0)} ms`;
const statusColor = (status) => {
    switch (status) {
        case 'success':
            return 'green';
        case 'error':
            return 'red';
        case 'running':
            return 'blue';
        case 'stopped':
            return 'default';
        default:
            return 'default';
    }
};
const configSummary = (ruleKey) => {
    const rule = monitorConfig.value?.alerts?.[ruleKey];
    if (!rule) {
        return '-';
    }
    const threshold = rule.thresholdSeconds ?? rule.thresholdRatio ?? rule.thresholdCount ?? rule.thresholdTurns ?? '-';
    const windowSize = rule.windowSize ? `, window=${rule.windowSize}` : '';
    return `${rule.enabled ? '启用' : '关闭'} / level=${rule.level} / threshold=${threshold}${windowSize}`;
};
const fetchOverview = async () => {
    const res = await getMonitorOverview();
    if (res.data.code === 0 && res.data.data) {
        overview.value = res.data.data;
        return;
    }
    message.error(`获取监控概览失败：${res.data.message}`);
};
const fetchConfig = async () => {
    const res = await getMonitorConfig();
    if (res.data.code === 0 && res.data.data) {
        monitorConfig.value = res.data.data;
        return;
    }
    message.error(`获取监控配置失败：${res.data.message}`);
};
const fetchSessions = async () => {
    sessionLoading.value = true;
    try {
        const res = await listMonitorSessions({ ...sessionQuery });
        if (res.data.code === 0 && res.data.data) {
            sessionPage.value = res.data.data;
            return;
        }
        message.error(`获取会话列表失败：${res.data.message}`);
    }
    finally {
        sessionLoading.value = false;
    }
};
const fetchAlerts = async () => {
    alertLoading.value = true;
    try {
        const res = await listMonitorAlerts({ ...alertQuery });
        if (res.data.code === 0 && res.data.data) {
            alertPage.value = res.data.data;
            return;
        }
        message.error(`获取告警列表失败：${res.data.message}`);
    }
    finally {
        alertLoading.value = false;
    }
};
const openSessionDetail = async (sessionId) => {
    const res = await getMonitorSessionDetail(sessionId);
    if (res.data.code === 0 && res.data.data) {
        selectedSessionDetail.value = res.data.data;
        sessionDrawerOpen.value = true;
        return;
    }
    message.error(`获取 Session 详情失败：${res.data.message}`);
};
const openTurnDetail = async (turnId) => {
    if (!selectedSessionDetail.value) {
        return;
    }
    const res = await getMonitorTurnDetail(selectedSessionDetail.value.session.sessionId, turnId);
    if (res.data.code === 0 && res.data.data) {
        selectedTurnDetail.value = res.data.data;
        turnModalOpen.value = true;
        return;
    }
    message.error(`获取 Turn 详情失败：${res.data.message}`);
};
const onSessionTableChange = (page) => {
    sessionQuery.pageNum = page.current;
    sessionQuery.pageSize = page.pageSize;
    fetchSessions();
};
const onAlertTableChange = (page) => {
    alertQuery.pageNum = page.current;
    alertQuery.pageSize = page.pageSize;
    fetchAlerts();
};
const doSessionSearch = () => {
    sessionQuery.pageNum = 1;
    fetchSessions();
};
const doAlertSearch = () => {
    alertQuery.pageNum = 1;
    fetchAlerts();
};
const runCleanup = async () => {
    cleanupLoading.value = true;
    try {
        const res = await cleanupMonitorHistory({
            retentionDays: cleanupForm.retentionDays,
            dryRun: cleanupForm.dryRun,
        });
        if (res.data.code === 0 && res.data.data) {
            cleanupResult.value = res.data.data;
            message.success(cleanupForm.dryRun ? '清理预览完成' : '清理执行完成');
            await Promise.all([fetchOverview(), fetchAlerts()]);
            return;
        }
        message.error(`执行清理失败：${res.data.message}`);
    }
    finally {
        cleanupLoading.value = false;
    }
};
onMounted(async () => {
    await Promise.all([fetchOverview(), fetchConfig(), fetchSessions(), fetchAlerts()]);
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "monitorManagePage",
});
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row'] | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row']} */
aRow;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    gutter: (16),
    ...{ class: "summary-row" },
}));
const __VLS_2 = __VLS_1({
    gutter: (16),
    ...{ class: "summary-row" },
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
/** @type {__VLS_StyleScopedClasses['summary-row']} */ ;
const { default: __VLS_5 } = __VLS_3.slots;
for (const [item] of __VLS_vFor((__VLS_ctx.summaryCards))) {
    let __VLS_6;
    /** @ts-ignore @type { | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col'] | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col']} */
    aCol;
    // @ts-ignore
    const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
        key: (item.label),
        xs: (24),
        sm: (12),
        xl: (6),
    }));
    const __VLS_8 = __VLS_7({
        key: (item.label),
        xs: (24),
        sm: (12),
        xl: (6),
    }, ...__VLS_functionalComponentArgsRest(__VLS_7));
    const { default: __VLS_11 } = __VLS_9.slots;
    let __VLS_12;
    /** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
    aCard;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent1(__VLS_12, new __VLS_12({}));
    const __VLS_14 = __VLS_13({}, ...__VLS_functionalComponentArgsRest(__VLS_13));
    const { default: __VLS_17 } = __VLS_15.slots;
    let __VLS_18;
    /** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
    aStatistic;
    // @ts-ignore
    const __VLS_19 = __VLS_asFunctionalComponent1(__VLS_18, new __VLS_18({
        title: (item.label),
        value: (item.value),
        suffix: (item.suffix),
    }));
    const __VLS_20 = __VLS_19({
        title: (item.label),
        value: (item.value),
        suffix: (item.suffix),
    }, ...__VLS_functionalComponentArgsRest(__VLS_19));
    // @ts-ignore
    [summaryCards,];
    var __VLS_15;
    // @ts-ignore
    [];
    var __VLS_9;
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_3;
let __VLS_23;
/** @ts-ignore @type { | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row'] | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row']} */
aRow;
// @ts-ignore
const __VLS_24 = __VLS_asFunctionalComponent1(__VLS_23, new __VLS_23({
    gutter: (16),
    ...{ class: "summary-row" },
}));
const __VLS_25 = __VLS_24({
    gutter: (16),
    ...{ class: "summary-row" },
}, ...__VLS_functionalComponentArgsRest(__VLS_24));
/** @type {__VLS_StyleScopedClasses['summary-row']} */ ;
const { default: __VLS_28 } = __VLS_26.slots;
let __VLS_29;
/** @ts-ignore @type { | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col'] | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col']} */
aCol;
// @ts-ignore
const __VLS_30 = __VLS_asFunctionalComponent1(__VLS_29, new __VLS_29({
    span: (24),
}));
const __VLS_31 = __VLS_30({
    span: (24),
}, ...__VLS_functionalComponentArgsRest(__VLS_30));
const { default: __VLS_34 } = __VLS_32.slots;
let __VLS_35;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_36 = __VLS_asFunctionalComponent1(__VLS_35, new __VLS_35({
    title: "运维操作",
    ...{ class: "panel-card" },
}));
const __VLS_37 = __VLS_36({
    title: "运维操作",
    ...{ class: "panel-card" },
}, ...__VLS_functionalComponentArgsRest(__VLS_36));
/** @type {__VLS_StyleScopedClasses['panel-card']} */ ;
const { default: __VLS_40 } = __VLS_38.slots;
let __VLS_41;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_42 = __VLS_asFunctionalComponent1(__VLS_41, new __VLS_41({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.cleanupForm),
}));
const __VLS_43 = __VLS_42({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.cleanupForm),
}, ...__VLS_functionalComponentArgsRest(__VLS_42));
let __VLS_46;
const __VLS_47 = ({ finish: {} },
    { onFinish: (__VLS_ctx.runCleanup) });
const { default: __VLS_48 } = __VLS_44.slots;
let __VLS_49;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_50 = __VLS_asFunctionalComponent1(__VLS_49, new __VLS_49({
    label: "保留天数",
}));
const __VLS_51 = __VLS_50({
    label: "保留天数",
}, ...__VLS_functionalComponentArgsRest(__VLS_50));
const { default: __VLS_54 } = __VLS_52.slots;
let __VLS_55;
/** @ts-ignore @type { | typeof __VLS_components.aInputNumber | typeof __VLS_components.AInputNumber | typeof __VLS_components['a-input-number']} */
aInputNumber;
// @ts-ignore
const __VLS_56 = __VLS_asFunctionalComponent1(__VLS_55, new __VLS_55({
    value: (__VLS_ctx.cleanupForm.retentionDays),
    min: (1),
    max: (3650),
}));
const __VLS_57 = __VLS_56({
    value: (__VLS_ctx.cleanupForm.retentionDays),
    min: (1),
    max: (3650),
}, ...__VLS_functionalComponentArgsRest(__VLS_56));
// @ts-ignore
[cleanupForm, cleanupForm, runCleanup,];
var __VLS_52;
let __VLS_60;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_61 = __VLS_asFunctionalComponent1(__VLS_60, new __VLS_60({
    label: "Dry Run",
}));
const __VLS_62 = __VLS_61({
    label: "Dry Run",
}, ...__VLS_functionalComponentArgsRest(__VLS_61));
const { default: __VLS_65 } = __VLS_63.slots;
let __VLS_66;
/** @ts-ignore @type { | typeof __VLS_components.aSwitch | typeof __VLS_components.ASwitch | typeof __VLS_components['a-switch']} */
aSwitch;
// @ts-ignore
const __VLS_67 = __VLS_asFunctionalComponent1(__VLS_66, new __VLS_66({
    checked: (__VLS_ctx.cleanupForm.dryRun),
}));
const __VLS_68 = __VLS_67({
    checked: (__VLS_ctx.cleanupForm.dryRun),
}, ...__VLS_functionalComponentArgsRest(__VLS_67));
// @ts-ignore
[cleanupForm,];
var __VLS_63;
let __VLS_71;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_72 = __VLS_asFunctionalComponent1(__VLS_71, new __VLS_71({}));
const __VLS_73 = __VLS_72({}, ...__VLS_functionalComponentArgsRest(__VLS_72));
const { default: __VLS_76 } = __VLS_74.slots;
let __VLS_77;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_78 = __VLS_asFunctionalComponent1(__VLS_77, new __VLS_77({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.cleanupLoading),
}));
const __VLS_79 = __VLS_78({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.cleanupLoading),
}, ...__VLS_functionalComponentArgsRest(__VLS_78));
const { default: __VLS_82 } = __VLS_80.slots;
// @ts-ignore
[cleanupLoading,];
var __VLS_80;
// @ts-ignore
[];
var __VLS_74;
// @ts-ignore
[];
var __VLS_44;
var __VLS_45;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "cleanup-hint subtle-text" },
});
/** @type {__VLS_StyleScopedClasses['cleanup-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['subtle-text']} */ ;
if (__VLS_ctx.cleanupResult) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "cleanup-result" },
    });
    /** @type {__VLS_StyleScopedClasses['cleanup-result']} */ ;
    let __VLS_83;
    /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
    aTag;
    // @ts-ignore
    const __VLS_84 = __VLS_asFunctionalComponent1(__VLS_83, new __VLS_83({
        color: (__VLS_ctx.cleanupResult.status === 'success' ? 'green' : 'orange'),
    }));
    const __VLS_85 = __VLS_84({
        color: (__VLS_ctx.cleanupResult.status === 'success' ? 'green' : 'orange'),
    }, ...__VLS_functionalComponentArgsRest(__VLS_84));
    const { default: __VLS_88 } = __VLS_86.slots;
    (__VLS_ctx.cleanupResult.status);
    // @ts-ignore
    [cleanupResult, cleanupResult, cleanupResult,];
    var __VLS_86;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
    (__VLS_ctx.cleanupResult.dryRun ? '预计清理' : '已清理');
    (__VLS_ctx.cleanupResult.deletedRows);
    (__VLS_ctx.cleanupResult.retentionDays);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "tag-list cleanup-table-tags" },
    });
    /** @type {__VLS_StyleScopedClasses['tag-list']} */ ;
    /** @type {__VLS_StyleScopedClasses['cleanup-table-tags']} */ ;
    for (const [item] of __VLS_vFor((__VLS_ctx.cleanupResult.tableResults))) {
        let __VLS_89;
        /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
        aTag;
        // @ts-ignore
        const __VLS_90 = __VLS_asFunctionalComponent1(__VLS_89, new __VLS_89({
            key: (item.tableName),
            color: (item.status === 'success' ? 'blue' : 'red'),
        }));
        const __VLS_91 = __VLS_90({
            key: (item.tableName),
            color: (item.status === 'success' ? 'blue' : 'red'),
        }, ...__VLS_functionalComponentArgsRest(__VLS_90));
        const { default: __VLS_94 } = __VLS_92.slots;
        (item.tableName);
        (item.status === 'success' ? item.affectedRows : item.errorMessage);
        // @ts-ignore
        [cleanupResult, cleanupResult, cleanupResult, cleanupResult,];
        var __VLS_92;
        // @ts-ignore
        [];
    }
}
// @ts-ignore
[];
var __VLS_38;
// @ts-ignore
[];
var __VLS_32;
// @ts-ignore
[];
var __VLS_26;
let __VLS_95;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_96 = __VLS_asFunctionalComponent1(__VLS_95, new __VLS_95({
    title: "会话检索",
    ...{ class: "panel-card" },
}));
const __VLS_97 = __VLS_96({
    title: "会话检索",
    ...{ class: "panel-card" },
}, ...__VLS_functionalComponentArgsRest(__VLS_96));
/** @type {__VLS_StyleScopedClasses['panel-card']} */ ;
const { default: __VLS_100 } = __VLS_98.slots;
let __VLS_101;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_102 = __VLS_asFunctionalComponent1(__VLS_101, new __VLS_101({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.sessionQuery),
}));
const __VLS_103 = __VLS_102({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.sessionQuery),
}, ...__VLS_functionalComponentArgsRest(__VLS_102));
let __VLS_106;
const __VLS_107 = ({ finish: {} },
    { onFinish: (__VLS_ctx.doSessionSearch) });
const { default: __VLS_108 } = __VLS_104.slots;
let __VLS_109;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_110 = __VLS_asFunctionalComponent1(__VLS_109, new __VLS_109({
    label: "会话ID",
}));
const __VLS_111 = __VLS_110({
    label: "会话ID",
}, ...__VLS_functionalComponentArgsRest(__VLS_110));
const { default: __VLS_114 } = __VLS_112.slots;
let __VLS_115;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_116 = __VLS_asFunctionalComponent1(__VLS_115, new __VLS_115({
    value: (__VLS_ctx.sessionQuery.sessionId),
    placeholder: "输入 sessionId",
}));
const __VLS_117 = __VLS_116({
    value: (__VLS_ctx.sessionQuery.sessionId),
    placeholder: "输入 sessionId",
}, ...__VLS_functionalComponentArgsRest(__VLS_116));
// @ts-ignore
[sessionQuery, sessionQuery, doSessionSearch,];
var __VLS_112;
let __VLS_120;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_121 = __VLS_asFunctionalComponent1(__VLS_120, new __VLS_120({
    label: "用户ID",
}));
const __VLS_122 = __VLS_121({
    label: "用户ID",
}, ...__VLS_functionalComponentArgsRest(__VLS_121));
const { default: __VLS_125 } = __VLS_123.slots;
let __VLS_126;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_127 = __VLS_asFunctionalComponent1(__VLS_126, new __VLS_126({
    value: (__VLS_ctx.sessionQuery.userId),
    placeholder: "输入 userId",
}));
const __VLS_128 = __VLS_127({
    value: (__VLS_ctx.sessionQuery.userId),
    placeholder: "输入 userId",
}, ...__VLS_functionalComponentArgsRest(__VLS_127));
// @ts-ignore
[sessionQuery,];
var __VLS_123;
let __VLS_131;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_132 = __VLS_asFunctionalComponent1(__VLS_131, new __VLS_131({
    label: "状态",
}));
const __VLS_133 = __VLS_132({
    label: "状态",
}, ...__VLS_functionalComponentArgsRest(__VLS_132));
const { default: __VLS_136 } = __VLS_134.slots;
let __VLS_137;
/** @ts-ignore @type { | typeof __VLS_components.aSelect | typeof __VLS_components.ASelect | typeof __VLS_components['a-select'] | typeof __VLS_components.aSelect | typeof __VLS_components.ASelect | typeof __VLS_components['a-select']} */
aSelect;
// @ts-ignore
const __VLS_138 = __VLS_asFunctionalComponent1(__VLS_137, new __VLS_137({
    value: (__VLS_ctx.sessionQuery.status),
    allowClear: true,
    ...{ style: {} },
    placeholder: "全部状态",
}));
const __VLS_139 = __VLS_138({
    value: (__VLS_ctx.sessionQuery.status),
    allowClear: true,
    ...{ style: {} },
    placeholder: "全部状态",
}, ...__VLS_functionalComponentArgsRest(__VLS_138));
const { default: __VLS_142 } = __VLS_140.slots;
let __VLS_143;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_144 = __VLS_asFunctionalComponent1(__VLS_143, new __VLS_143({
    value: "running",
}));
const __VLS_145 = __VLS_144({
    value: "running",
}, ...__VLS_functionalComponentArgsRest(__VLS_144));
const { default: __VLS_148 } = __VLS_146.slots;
// @ts-ignore
[sessionQuery,];
var __VLS_146;
let __VLS_149;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_150 = __VLS_asFunctionalComponent1(__VLS_149, new __VLS_149({
    value: "success",
}));
const __VLS_151 = __VLS_150({
    value: "success",
}, ...__VLS_functionalComponentArgsRest(__VLS_150));
const { default: __VLS_154 } = __VLS_152.slots;
// @ts-ignore
[];
var __VLS_152;
let __VLS_155;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_156 = __VLS_asFunctionalComponent1(__VLS_155, new __VLS_155({
    value: "error",
}));
const __VLS_157 = __VLS_156({
    value: "error",
}, ...__VLS_functionalComponentArgsRest(__VLS_156));
const { default: __VLS_160 } = __VLS_158.slots;
// @ts-ignore
[];
var __VLS_158;
let __VLS_161;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_162 = __VLS_asFunctionalComponent1(__VLS_161, new __VLS_161({
    value: "stopped",
}));
const __VLS_163 = __VLS_162({
    value: "stopped",
}, ...__VLS_functionalComponentArgsRest(__VLS_162));
const { default: __VLS_166 } = __VLS_164.slots;
// @ts-ignore
[];
var __VLS_164;
// @ts-ignore
[];
var __VLS_140;
// @ts-ignore
[];
var __VLS_134;
let __VLS_167;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_168 = __VLS_asFunctionalComponent1(__VLS_167, new __VLS_167({}));
const __VLS_169 = __VLS_168({}, ...__VLS_functionalComponentArgsRest(__VLS_168));
const { default: __VLS_172 } = __VLS_170.slots;
let __VLS_173;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_174 = __VLS_asFunctionalComponent1(__VLS_173, new __VLS_173({
    type: "primary",
    htmlType: "submit",
}));
const __VLS_175 = __VLS_174({
    type: "primary",
    htmlType: "submit",
}, ...__VLS_functionalComponentArgsRest(__VLS_174));
const { default: __VLS_178 } = __VLS_176.slots;
// @ts-ignore
[];
var __VLS_176;
// @ts-ignore
[];
var __VLS_170;
// @ts-ignore
[];
var __VLS_104;
var __VLS_105;
let __VLS_179;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_180 = __VLS_asFunctionalComponent1(__VLS_179, new __VLS_179({}));
const __VLS_181 = __VLS_180({}, ...__VLS_functionalComponentArgsRest(__VLS_180));
let __VLS_184;
/** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
aTable;
// @ts-ignore
const __VLS_185 = __VLS_asFunctionalComponent1(__VLS_184, new __VLS_184({
    ...{ 'onChange': {} },
    rowKey: "sessionId",
    loading: (__VLS_ctx.sessionLoading),
    columns: (__VLS_ctx.sessionColumns),
    dataSource: (__VLS_ctx.sessionPage.records),
    pagination: (__VLS_ctx.sessionPagination),
    scroll: ({ x: 1400 }),
}));
const __VLS_186 = __VLS_185({
    ...{ 'onChange': {} },
    rowKey: "sessionId",
    loading: (__VLS_ctx.sessionLoading),
    columns: (__VLS_ctx.sessionColumns),
    dataSource: (__VLS_ctx.sessionPage.records),
    pagination: (__VLS_ctx.sessionPagination),
    scroll: ({ x: 1400 }),
}, ...__VLS_functionalComponentArgsRest(__VLS_185));
let __VLS_189;
const __VLS_190 = ({ change: {} },
    { onChange: (__VLS_ctx.onSessionTableChange) });
const { default: __VLS_191 } = __VLS_187.slots;
{
    const { bodyCell: __VLS_192 } = __VLS_187.slots;
    const [{ column, record }] = __VLS_vSlot(__VLS_192);
    if (column.dataIndex === 'status') {
        let __VLS_193;
        /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
        aTag;
        // @ts-ignore
        const __VLS_194 = __VLS_asFunctionalComponent1(__VLS_193, new __VLS_193({
            color: (__VLS_ctx.statusColor(record.status)),
        }));
        const __VLS_195 = __VLS_194({
            color: (__VLS_ctx.statusColor(record.status)),
        }, ...__VLS_functionalComponentArgsRest(__VLS_194));
        const { default: __VLS_198 } = __VLS_196.slots;
        (record.status);
        // @ts-ignore
        [sessionLoading, sessionColumns, sessionPage, sessionPagination, onSessionTableChange, statusColor,];
        var __VLS_196;
    }
    else if (column.dataIndex === 'startedAt') {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
        (__VLS_ctx.formatTime(record.startedAt));
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "subtle-text" },
        });
        /** @type {__VLS_StyleScopedClasses['subtle-text']} */ ;
        (__VLS_ctx.formatRelativeTime(record.startedAt));
    }
    else if (column.dataIndex === 'avgLlmLatencyMs') {
        (__VLS_ctx.formatMs(record.avgLlmLatencyMs));
    }
    else if (column.dataIndex === 'durationMs') {
        (__VLS_ctx.formatMs(record.durationMs));
    }
    else if (column.key === 'action') {
        let __VLS_199;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_200 = __VLS_asFunctionalComponent1(__VLS_199, new __VLS_199({
            ...{ 'onClick': {} },
            type: "link",
        }));
        const __VLS_201 = __VLS_200({
            ...{ 'onClick': {} },
            type: "link",
        }, ...__VLS_functionalComponentArgsRest(__VLS_200));
        let __VLS_204;
        const __VLS_205 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(column.dataIndex === 'status'))
                        return;
                    if (!!(column.dataIndex === 'startedAt'))
                        return;
                    if (!!(column.dataIndex === 'avgLlmLatencyMs'))
                        return;
                    if (!!(column.dataIndex === 'durationMs'))
                        return;
                    if (!(column.key === 'action'))
                        return;
                    __VLS_ctx.openSessionDetail(record.sessionId);
                    // @ts-ignore
                    [formatTime, formatRelativeTime, formatMs, formatMs, openSessionDetail,];
                } });
        const { default: __VLS_206 } = __VLS_202.slots;
        // @ts-ignore
        [];
        var __VLS_202;
        var __VLS_203;
    }
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_187;
var __VLS_188;
// @ts-ignore
[];
var __VLS_98;
let __VLS_207;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_208 = __VLS_asFunctionalComponent1(__VLS_207, new __VLS_207({
    title: "告警列表",
    ...{ class: "panel-card" },
}));
const __VLS_209 = __VLS_208({
    title: "告警列表",
    ...{ class: "panel-card" },
}, ...__VLS_functionalComponentArgsRest(__VLS_208));
/** @type {__VLS_StyleScopedClasses['panel-card']} */ ;
const { default: __VLS_212 } = __VLS_210.slots;
let __VLS_213;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_214 = __VLS_asFunctionalComponent1(__VLS_213, new __VLS_213({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.alertQuery),
}));
const __VLS_215 = __VLS_214({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.alertQuery),
}, ...__VLS_functionalComponentArgsRest(__VLS_214));
let __VLS_218;
const __VLS_219 = ({ finish: {} },
    { onFinish: (__VLS_ctx.doAlertSearch) });
const { default: __VLS_220 } = __VLS_216.slots;
let __VLS_221;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_222 = __VLS_asFunctionalComponent1(__VLS_221, new __VLS_221({
    label: "规则",
}));
const __VLS_223 = __VLS_222({
    label: "规则",
}, ...__VLS_functionalComponentArgsRest(__VLS_222));
const { default: __VLS_226 } = __VLS_224.slots;
let __VLS_227;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_228 = __VLS_asFunctionalComponent1(__VLS_227, new __VLS_227({
    value: (__VLS_ctx.alertQuery.ruleName),
    placeholder: "输入 ruleName",
}));
const __VLS_229 = __VLS_228({
    value: (__VLS_ctx.alertQuery.ruleName),
    placeholder: "输入 ruleName",
}, ...__VLS_functionalComponentArgsRest(__VLS_228));
// @ts-ignore
[alertQuery, alertQuery, doAlertSearch,];
var __VLS_224;
let __VLS_232;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_233 = __VLS_asFunctionalComponent1(__VLS_232, new __VLS_232({
    label: "Session",
}));
const __VLS_234 = __VLS_233({
    label: "Session",
}, ...__VLS_functionalComponentArgsRest(__VLS_233));
const { default: __VLS_237 } = __VLS_235.slots;
let __VLS_238;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_239 = __VLS_asFunctionalComponent1(__VLS_238, new __VLS_238({
    value: (__VLS_ctx.alertQuery.sessionId),
    placeholder: "输入 sessionId",
}));
const __VLS_240 = __VLS_239({
    value: (__VLS_ctx.alertQuery.sessionId),
    placeholder: "输入 sessionId",
}, ...__VLS_functionalComponentArgsRest(__VLS_239));
// @ts-ignore
[alertQuery,];
var __VLS_235;
let __VLS_243;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_244 = __VLS_asFunctionalComponent1(__VLS_243, new __VLS_243({
    label: "状态",
}));
const __VLS_245 = __VLS_244({
    label: "状态",
}, ...__VLS_functionalComponentArgsRest(__VLS_244));
const { default: __VLS_248 } = __VLS_246.slots;
let __VLS_249;
/** @ts-ignore @type { | typeof __VLS_components.aSelect | typeof __VLS_components.ASelect | typeof __VLS_components['a-select'] | typeof __VLS_components.aSelect | typeof __VLS_components.ASelect | typeof __VLS_components['a-select']} */
aSelect;
// @ts-ignore
const __VLS_250 = __VLS_asFunctionalComponent1(__VLS_249, new __VLS_249({
    value: (__VLS_ctx.alertQuery.status),
    allowClear: true,
    ...{ style: {} },
    placeholder: "全部状态",
}));
const __VLS_251 = __VLS_250({
    value: (__VLS_ctx.alertQuery.status),
    allowClear: true,
    ...{ style: {} },
    placeholder: "全部状态",
}, ...__VLS_functionalComponentArgsRest(__VLS_250));
const { default: __VLS_254 } = __VLS_252.slots;
let __VLS_255;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_256 = __VLS_asFunctionalComponent1(__VLS_255, new __VLS_255({
    value: "open",
}));
const __VLS_257 = __VLS_256({
    value: "open",
}, ...__VLS_functionalComponentArgsRest(__VLS_256));
const { default: __VLS_260 } = __VLS_258.slots;
// @ts-ignore
[alertQuery,];
var __VLS_258;
let __VLS_261;
/** @ts-ignore @type { | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option'] | typeof __VLS_components.aSelectOption | typeof __VLS_components.ASelectOption | typeof __VLS_components['a-select-option']} */
aSelectOption;
// @ts-ignore
const __VLS_262 = __VLS_asFunctionalComponent1(__VLS_261, new __VLS_261({
    value: "resolved",
}));
const __VLS_263 = __VLS_262({
    value: "resolved",
}, ...__VLS_functionalComponentArgsRest(__VLS_262));
const { default: __VLS_266 } = __VLS_264.slots;
// @ts-ignore
[];
var __VLS_264;
// @ts-ignore
[];
var __VLS_252;
// @ts-ignore
[];
var __VLS_246;
let __VLS_267;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_268 = __VLS_asFunctionalComponent1(__VLS_267, new __VLS_267({}));
const __VLS_269 = __VLS_268({}, ...__VLS_functionalComponentArgsRest(__VLS_268));
const { default: __VLS_272 } = __VLS_270.slots;
let __VLS_273;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_274 = __VLS_asFunctionalComponent1(__VLS_273, new __VLS_273({
    type: "primary",
    htmlType: "submit",
}));
const __VLS_275 = __VLS_274({
    type: "primary",
    htmlType: "submit",
}, ...__VLS_functionalComponentArgsRest(__VLS_274));
const { default: __VLS_278 } = __VLS_276.slots;
// @ts-ignore
[];
var __VLS_276;
// @ts-ignore
[];
var __VLS_270;
// @ts-ignore
[];
var __VLS_216;
var __VLS_217;
let __VLS_279;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_280 = __VLS_asFunctionalComponent1(__VLS_279, new __VLS_279({}));
const __VLS_281 = __VLS_280({}, ...__VLS_functionalComponentArgsRest(__VLS_280));
let __VLS_284;
/** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
aTable;
// @ts-ignore
const __VLS_285 = __VLS_asFunctionalComponent1(__VLS_284, new __VLS_284({
    ...{ 'onChange': {} },
    rowKey: "id",
    loading: (__VLS_ctx.alertLoading),
    columns: (__VLS_ctx.alertColumns),
    dataSource: (__VLS_ctx.alertPage.records),
    pagination: (__VLS_ctx.alertPagination),
    scroll: ({ x: 1200 }),
}));
const __VLS_286 = __VLS_285({
    ...{ 'onChange': {} },
    rowKey: "id",
    loading: (__VLS_ctx.alertLoading),
    columns: (__VLS_ctx.alertColumns),
    dataSource: (__VLS_ctx.alertPage.records),
    pagination: (__VLS_ctx.alertPagination),
    scroll: ({ x: 1200 }),
}, ...__VLS_functionalComponentArgsRest(__VLS_285));
let __VLS_289;
const __VLS_290 = ({ change: {} },
    { onChange: (__VLS_ctx.onAlertTableChange) });
const { default: __VLS_291 } = __VLS_287.slots;
{
    const { bodyCell: __VLS_292 } = __VLS_287.slots;
    const [{ column, record }] = __VLS_vSlot(__VLS_292);
    if (column.dataIndex === 'status') {
        let __VLS_293;
        /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
        aTag;
        // @ts-ignore
        const __VLS_294 = __VLS_asFunctionalComponent1(__VLS_293, new __VLS_293({
            color: (record.status === 'open' ? 'red' : 'green'),
        }));
        const __VLS_295 = __VLS_294({
            color: (record.status === 'open' ? 'red' : 'green'),
        }, ...__VLS_functionalComponentArgsRest(__VLS_294));
        const { default: __VLS_298 } = __VLS_296.slots;
        (record.status);
        // @ts-ignore
        [alertLoading, alertColumns, alertPage, alertPagination, onAlertTableChange,];
        var __VLS_296;
    }
    else if (column.dataIndex === 'level') {
        let __VLS_299;
        /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
        aTag;
        // @ts-ignore
        const __VLS_300 = __VLS_asFunctionalComponent1(__VLS_299, new __VLS_299({
            color: (record.level === 'ERROR' ? 'red' : 'orange'),
        }));
        const __VLS_301 = __VLS_300({
            color: (record.level === 'ERROR' ? 'red' : 'orange'),
        }, ...__VLS_functionalComponentArgsRest(__VLS_300));
        const { default: __VLS_304 } = __VLS_302.slots;
        (record.level);
        // @ts-ignore
        [];
        var __VLS_302;
    }
    else if (column.dataIndex === 'triggeredAt') {
        (__VLS_ctx.formatTime(record.triggeredAt));
    }
    else if (column.dataIndex === 'resolvedAt') {
        (__VLS_ctx.formatTime(record.resolvedAt));
    }
    // @ts-ignore
    [formatTime, formatTime,];
}
// @ts-ignore
[];
var __VLS_287;
var __VLS_288;
// @ts-ignore
[];
var __VLS_210;
let __VLS_305;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_306 = __VLS_asFunctionalComponent1(__VLS_305, new __VLS_305({
    title: "告警配置",
    ...{ class: "panel-card" },
}));
const __VLS_307 = __VLS_306({
    title: "告警配置",
    ...{ class: "panel-card" },
}, ...__VLS_functionalComponentArgsRest(__VLS_306));
/** @type {__VLS_StyleScopedClasses['panel-card']} */ ;
const { default: __VLS_310 } = __VLS_308.slots;
let __VLS_311;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
aDescriptions;
// @ts-ignore
const __VLS_312 = __VLS_asFunctionalComponent1(__VLS_311, new __VLS_311({
    bordered: true,
    column: (1),
    size: "small",
}));
const __VLS_313 = __VLS_312({
    bordered: true,
    column: (1),
    size: "small",
}, ...__VLS_functionalComponentArgsRest(__VLS_312));
const { default: __VLS_316 } = __VLS_314.slots;
let __VLS_317;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_318 = __VLS_asFunctionalComponent1(__VLS_317, new __VLS_317({
    label: "Redis 实时窗口",
}));
const __VLS_319 = __VLS_318({
    label: "Redis 实时窗口",
}, ...__VLS_functionalComponentArgsRest(__VLS_318));
const { default: __VLS_322 } = __VLS_320.slots;
(__VLS_ctx.monitorConfig?.storage.useRedisWindows ? '开启' : '关闭');
// @ts-ignore
[monitorConfig,];
var __VLS_320;
let __VLS_323;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_324 = __VLS_asFunctionalComponent1(__VLS_323, new __VLS_323({
    label: "LLM 最近窗口",
}));
const __VLS_325 = __VLS_324({
    label: "LLM 最近窗口",
}, ...__VLS_functionalComponentArgsRest(__VLS_324));
const { default: __VLS_328 } = __VLS_326.slots;
(__VLS_ctx.configSummary('llmAvgLatencyLast5'));
// @ts-ignore
[configSummary,];
var __VLS_326;
let __VLS_329;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_330 = __VLS_asFunctionalComponent1(__VLS_329, new __VLS_329({
    label: "单次 LLM 超时",
}));
const __VLS_331 = __VLS_330({
    label: "单次 LLM 超时",
}, ...__VLS_functionalComponentArgsRest(__VLS_330));
const { default: __VLS_334 } = __VLS_332.slots;
(__VLS_ctx.configSummary('llmSingleTimeout'));
// @ts-ignore
[configSummary,];
var __VLS_332;
let __VLS_335;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_336 = __VLS_asFunctionalComponent1(__VLS_335, new __VLS_335({
    label: "Token 配额使用率",
}));
const __VLS_337 = __VLS_336({
    label: "Token 配额使用率",
}, ...__VLS_functionalComponentArgsRest(__VLS_336));
const { default: __VLS_340 } = __VLS_338.slots;
(__VLS_ctx.configSummary('tokenQuotaUsage'));
// @ts-ignore
[configSummary,];
var __VLS_338;
let __VLS_341;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_342 = __VLS_asFunctionalComponent1(__VLS_341, new __VLS_341({
    label: "工具连续失败",
}));
const __VLS_343 = __VLS_342({
    label: "工具连续失败",
}, ...__VLS_functionalComponentArgsRest(__VLS_342));
const { default: __VLS_346 } = __VLS_344.slots;
(__VLS_ctx.configSummary('toolConsecutiveFailures'));
// @ts-ignore
[configSummary,];
var __VLS_344;
let __VLS_347;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_348 = __VLS_asFunctionalComponent1(__VLS_347, new __VLS_347({
    label: "Session 最大轮数",
}));
const __VLS_349 = __VLS_348({
    label: "Session 最大轮数",
}, ...__VLS_functionalComponentArgsRest(__VLS_348));
const { default: __VLS_352 } = __VLS_350.slots;
(__VLS_ctx.configSummary('sessionMaxTurns'));
// @ts-ignore
[configSummary,];
var __VLS_350;
// @ts-ignore
[];
var __VLS_314;
// @ts-ignore
[];
var __VLS_308;
let __VLS_353;
/** @ts-ignore @type { | typeof __VLS_components.aDrawer | typeof __VLS_components.ADrawer | typeof __VLS_components['a-drawer'] | typeof __VLS_components.aDrawer | typeof __VLS_components.ADrawer | typeof __VLS_components['a-drawer']} */
aDrawer;
// @ts-ignore
const __VLS_354 = __VLS_asFunctionalComponent1(__VLS_353, new __VLS_353({
    open: (__VLS_ctx.sessionDrawerOpen),
    width: "1080",
    title: "Session 详情",
}));
const __VLS_355 = __VLS_354({
    open: (__VLS_ctx.sessionDrawerOpen),
    width: "1080",
    title: "Session 详情",
}, ...__VLS_functionalComponentArgsRest(__VLS_354));
const { default: __VLS_358 } = __VLS_356.slots;
if (__VLS_ctx.selectedSessionDetail) {
    let __VLS_359;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
    aDescriptions;
    // @ts-ignore
    const __VLS_360 = __VLS_asFunctionalComponent1(__VLS_359, new __VLS_359({
        bordered: true,
        column: (2),
        size: "small",
    }));
    const __VLS_361 = __VLS_360({
        bordered: true,
        column: (2),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_360));
    const { default: __VLS_364 } = __VLS_362.slots;
    let __VLS_365;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_366 = __VLS_asFunctionalComponent1(__VLS_365, new __VLS_365({
        label: "Session ID",
    }));
    const __VLS_367 = __VLS_366({
        label: "Session ID",
    }, ...__VLS_functionalComponentArgsRest(__VLS_366));
    const { default: __VLS_370 } = __VLS_368.slots;
    (__VLS_ctx.selectedSessionDetail.session.sessionId);
    // @ts-ignore
    [sessionDrawerOpen, selectedSessionDetail, selectedSessionDetail,];
    var __VLS_368;
    let __VLS_371;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_372 = __VLS_asFunctionalComponent1(__VLS_371, new __VLS_371({
        label: "Trace ID",
    }));
    const __VLS_373 = __VLS_372({
        label: "Trace ID",
    }, ...__VLS_functionalComponentArgsRest(__VLS_372));
    const { default: __VLS_376 } = __VLS_374.slots;
    (__VLS_ctx.selectedSessionDetail.session.traceId);
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_374;
    let __VLS_377;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_378 = __VLS_asFunctionalComponent1(__VLS_377, new __VLS_377({
        label: "状态",
    }));
    const __VLS_379 = __VLS_378({
        label: "状态",
    }, ...__VLS_functionalComponentArgsRest(__VLS_378));
    const { default: __VLS_382 } = __VLS_380.slots;
    let __VLS_383;
    /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
    aTag;
    // @ts-ignore
    const __VLS_384 = __VLS_asFunctionalComponent1(__VLS_383, new __VLS_383({
        color: (__VLS_ctx.statusColor(__VLS_ctx.selectedSessionDetail.session.status)),
    }));
    const __VLS_385 = __VLS_384({
        color: (__VLS_ctx.statusColor(__VLS_ctx.selectedSessionDetail.session.status)),
    }, ...__VLS_functionalComponentArgsRest(__VLS_384));
    const { default: __VLS_388 } = __VLS_386.slots;
    (__VLS_ctx.selectedSessionDetail.session.status);
    // @ts-ignore
    [statusColor, selectedSessionDetail, selectedSessionDetail,];
    var __VLS_386;
    // @ts-ignore
    [];
    var __VLS_380;
    let __VLS_389;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_390 = __VLS_asFunctionalComponent1(__VLS_389, new __VLS_389({
        label: "模型",
    }));
    const __VLS_391 = __VLS_390({
        label: "模型",
    }, ...__VLS_functionalComponentArgsRest(__VLS_390));
    const { default: __VLS_394 } = __VLS_392.slots;
    (__VLS_ctx.selectedSessionDetail.session.model || '-');
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_392;
    let __VLS_395;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_396 = __VLS_asFunctionalComponent1(__VLS_395, new __VLS_395({
        label: "总轮次",
    }));
    const __VLS_397 = __VLS_396({
        label: "总轮次",
    }, ...__VLS_functionalComponentArgsRest(__VLS_396));
    const { default: __VLS_400 } = __VLS_398.slots;
    (__VLS_ctx.selectedSessionDetail.session.totalTurns);
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_398;
    let __VLS_401;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_402 = __VLS_asFunctionalComponent1(__VLS_401, new __VLS_401({
        label: "总 Tool 调用",
    }));
    const __VLS_403 = __VLS_402({
        label: "总 Tool 调用",
    }, ...__VLS_functionalComponentArgsRest(__VLS_402));
    const { default: __VLS_406 } = __VLS_404.slots;
    (__VLS_ctx.selectedSessionDetail.session.totalToolCalls);
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_404;
    let __VLS_407;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_408 = __VLS_asFunctionalComponent1(__VLS_407, new __VLS_407({
        label: "平均 LLM 延迟",
    }));
    const __VLS_409 = __VLS_408({
        label: "平均 LLM 延迟",
    }, ...__VLS_functionalComponentArgsRest(__VLS_408));
    const { default: __VLS_412 } = __VLS_410.slots;
    (__VLS_ctx.formatMs(__VLS_ctx.selectedSessionDetail.session.avgLlmLatencyMs));
    // @ts-ignore
    [formatMs, selectedSessionDetail,];
    var __VLS_410;
    let __VLS_413;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_414 = __VLS_asFunctionalComponent1(__VLS_413, new __VLS_413({
        label: "平均首包延迟",
    }));
    const __VLS_415 = __VLS_414({
        label: "平均首包延迟",
    }, ...__VLS_functionalComponentArgsRest(__VLS_414));
    const { default: __VLS_418 } = __VLS_416.slots;
    (__VLS_ctx.formatMs(__VLS_ctx.selectedSessionDetail.session.avgFirstTokenMs));
    // @ts-ignore
    [formatMs, selectedSessionDetail,];
    var __VLS_416;
    let __VLS_419;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_420 = __VLS_asFunctionalComponent1(__VLS_419, new __VLS_419({
        label: "总 Prompt Tokens",
    }));
    const __VLS_421 = __VLS_420({
        label: "总 Prompt Tokens",
    }, ...__VLS_functionalComponentArgsRest(__VLS_420));
    const { default: __VLS_424 } = __VLS_422.slots;
    (__VLS_ctx.selectedSessionDetail.session.totalPromptTokens);
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_422;
    let __VLS_425;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_426 = __VLS_asFunctionalComponent1(__VLS_425, new __VLS_425({
        label: "总 Completion Tokens",
    }));
    const __VLS_427 = __VLS_426({
        label: "总 Completion Tokens",
    }, ...__VLS_functionalComponentArgsRest(__VLS_426));
    const { default: __VLS_430 } = __VLS_428.slots;
    (__VLS_ctx.selectedSessionDetail.session.totalCompletionTokens);
    // @ts-ignore
    [selectedSessionDetail,];
    var __VLS_428;
    let __VLS_431;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_432 = __VLS_asFunctionalComponent1(__VLS_431, new __VLS_431({
        label: "开始时间",
    }));
    const __VLS_433 = __VLS_432({
        label: "开始时间",
    }, ...__VLS_functionalComponentArgsRest(__VLS_432));
    const { default: __VLS_436 } = __VLS_434.slots;
    (__VLS_ctx.formatTime(__VLS_ctx.selectedSessionDetail.session.startedAt));
    // @ts-ignore
    [formatTime, selectedSessionDetail,];
    var __VLS_434;
    let __VLS_437;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_438 = __VLS_asFunctionalComponent1(__VLS_437, new __VLS_437({
        label: "结束时间",
    }));
    const __VLS_439 = __VLS_438({
        label: "结束时间",
    }, ...__VLS_functionalComponentArgsRest(__VLS_438));
    const { default: __VLS_442 } = __VLS_440.slots;
    (__VLS_ctx.formatTime(__VLS_ctx.selectedSessionDetail.session.endedAt));
    // @ts-ignore
    [formatTime, selectedSessionDetail,];
    var __VLS_440;
    // @ts-ignore
    [];
    var __VLS_362;
    let __VLS_443;
    /** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider'] | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
    aDivider;
    // @ts-ignore
    const __VLS_444 = __VLS_asFunctionalComponent1(__VLS_443, new __VLS_443({}));
    const __VLS_445 = __VLS_444({}, ...__VLS_functionalComponentArgsRest(__VLS_444));
    const { default: __VLS_448 } = __VLS_446.slots;
    // @ts-ignore
    [];
    var __VLS_446;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "chart-card" },
    });
    /** @type {__VLS_StyleScopedClasses['chart-card']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.svg, __VLS_intrinsics.svg)({
        viewBox: "0 0 640 200",
        ...{ class: "latency-chart" },
        preserveAspectRatio: "none",
    });
    /** @type {__VLS_StyleScopedClasses['latency-chart']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.line)({
        x1: "24",
        y1: "176",
        x2: "616",
        y2: "176",
        ...{ class: "axis-line" },
    });
    /** @type {__VLS_StyleScopedClasses['axis-line']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.line)({
        x1: "24",
        y1: "24",
        x2: "24",
        y2: "176",
        ...{ class: "axis-line" },
    });
    /** @type {__VLS_StyleScopedClasses['axis-line']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.polyline)({
        points: (__VLS_ctx.latencyChartPoints),
        ...{ class: "chart-line" },
        fill: "none",
    });
    /** @type {__VLS_StyleScopedClasses['chart-line']} */ ;
    for (const [point] of __VLS_vFor((__VLS_ctx.latencyChartDots))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.circle)({
            key: (point.key),
            cx: (point.x),
            cy: (point.y),
            r: "4",
            ...{ class: "chart-dot" },
        });
        /** @type {__VLS_StyleScopedClasses['chart-dot']} */ ;
        // @ts-ignore
        [latencyChartPoints, latencyChartDots,];
    }
    if (!__VLS_ctx.latencyChartDots.length) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "chart-empty" },
        });
        /** @type {__VLS_StyleScopedClasses['chart-empty']} */ ;
    }
    let __VLS_449;
    /** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider'] | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
    aDivider;
    // @ts-ignore
    const __VLS_450 = __VLS_asFunctionalComponent1(__VLS_449, new __VLS_449({}));
    const __VLS_451 = __VLS_450({}, ...__VLS_functionalComponentArgsRest(__VLS_450));
    const { default: __VLS_454 } = __VLS_452.slots;
    // @ts-ignore
    [latencyChartDots,];
    var __VLS_452;
    let __VLS_455;
    /** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
    aTable;
    // @ts-ignore
    const __VLS_456 = __VLS_asFunctionalComponent1(__VLS_455, new __VLS_455({
        rowKey: "turnId",
        columns: (__VLS_ctx.turnColumns),
        dataSource: (__VLS_ctx.selectedSessionDetail.turns),
        pagination: (false),
        size: "small",
        scroll: ({ x: 1000 }),
    }));
    const __VLS_457 = __VLS_456({
        rowKey: "turnId",
        columns: (__VLS_ctx.turnColumns),
        dataSource: (__VLS_ctx.selectedSessionDetail.turns),
        pagination: (false),
        size: "small",
        scroll: ({ x: 1000 }),
    }, ...__VLS_functionalComponentArgsRest(__VLS_456));
    const { default: __VLS_460 } = __VLS_458.slots;
    {
        const { bodyCell: __VLS_461 } = __VLS_458.slots;
        const [{ column, record }] = __VLS_vSlot(__VLS_461);
        if (column.dataIndex === 'status') {
            let __VLS_462;
            /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
            aTag;
            // @ts-ignore
            const __VLS_463 = __VLS_asFunctionalComponent1(__VLS_462, new __VLS_462({
                color: (__VLS_ctx.statusColor(record.status)),
            }));
            const __VLS_464 = __VLS_463({
                color: (__VLS_ctx.statusColor(record.status)),
            }, ...__VLS_functionalComponentArgsRest(__VLS_463));
            const { default: __VLS_467 } = __VLS_465.slots;
            (record.status);
            // @ts-ignore
            [statusColor, selectedSessionDetail, turnColumns,];
            var __VLS_465;
        }
        else if (column.dataIndex === 'llmLatencyMs') {
            (__VLS_ctx.formatMs(record.llmLatencyMs));
        }
        else if (column.dataIndex === 'startedAt') {
            (__VLS_ctx.formatTime(record.startedAt));
        }
        else if (column.key === 'action') {
            let __VLS_468;
            /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
            aButton;
            // @ts-ignore
            const __VLS_469 = __VLS_asFunctionalComponent1(__VLS_468, new __VLS_468({
                ...{ 'onClick': {} },
                type: "link",
            }));
            const __VLS_470 = __VLS_469({
                ...{ 'onClick': {} },
                type: "link",
            }, ...__VLS_functionalComponentArgsRest(__VLS_469));
            let __VLS_473;
            const __VLS_474 = ({ click: {} },
                { onClick: (...[$event]) => {
                        if (!(__VLS_ctx.selectedSessionDetail))
                            return;
                        if (!!(column.dataIndex === 'status'))
                            return;
                        if (!!(column.dataIndex === 'llmLatencyMs'))
                            return;
                        if (!!(column.dataIndex === 'startedAt'))
                            return;
                        if (!(column.key === 'action'))
                            return;
                        __VLS_ctx.openTurnDetail(record.turnId);
                        // @ts-ignore
                        [formatTime, formatMs, openTurnDetail,];
                    } });
            const { default: __VLS_475 } = __VLS_471.slots;
            // @ts-ignore
            [];
            var __VLS_471;
            var __VLS_472;
        }
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_458;
    let __VLS_476;
    /** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider'] | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
    aDivider;
    // @ts-ignore
    const __VLS_477 = __VLS_asFunctionalComponent1(__VLS_476, new __VLS_476({}));
    const __VLS_478 = __VLS_477({}, ...__VLS_functionalComponentArgsRest(__VLS_477));
    const { default: __VLS_481 } = __VLS_479.slots;
    // @ts-ignore
    [];
    var __VLS_479;
    let __VLS_482;
    /** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
    aTable;
    // @ts-ignore
    const __VLS_483 = __VLS_asFunctionalComponent1(__VLS_482, new __VLS_482({
        rowKey: "id",
        columns: (__VLS_ctx.sessionAlertColumns),
        dataSource: (__VLS_ctx.selectedSessionDetail.alerts),
        pagination: (false),
        size: "small",
    }));
    const __VLS_484 = __VLS_483({
        rowKey: "id",
        columns: (__VLS_ctx.sessionAlertColumns),
        dataSource: (__VLS_ctx.selectedSessionDetail.alerts),
        pagination: (false),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_483));
    const { default: __VLS_487 } = __VLS_485.slots;
    {
        const { bodyCell: __VLS_488 } = __VLS_485.slots;
        const [{ column, record }] = __VLS_vSlot(__VLS_488);
        if (column.dataIndex === 'status') {
            let __VLS_489;
            /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
            aTag;
            // @ts-ignore
            const __VLS_490 = __VLS_asFunctionalComponent1(__VLS_489, new __VLS_489({
                color: (record.status === 'open' ? 'red' : 'green'),
            }));
            const __VLS_491 = __VLS_490({
                color: (record.status === 'open' ? 'red' : 'green'),
            }, ...__VLS_functionalComponentArgsRest(__VLS_490));
            const { default: __VLS_494 } = __VLS_492.slots;
            (record.status);
            // @ts-ignore
            [selectedSessionDetail, sessionAlertColumns,];
            var __VLS_492;
        }
        else if (column.dataIndex === 'triggeredAt') {
            (__VLS_ctx.formatTime(record.triggeredAt));
        }
        // @ts-ignore
        [formatTime,];
    }
    // @ts-ignore
    [];
    var __VLS_485;
}
// @ts-ignore
[];
var __VLS_356;
let __VLS_495;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_496 = __VLS_asFunctionalComponent1(__VLS_495, new __VLS_495({
    open: (__VLS_ctx.turnModalOpen),
    title: "Turn 详情",
    width: "720",
    footer: (null),
}));
const __VLS_497 = __VLS_496({
    open: (__VLS_ctx.turnModalOpen),
    title: "Turn 详情",
    width: "720",
    footer: (null),
}, ...__VLS_functionalComponentArgsRest(__VLS_496));
const { default: __VLS_500 } = __VLS_498.slots;
if (__VLS_ctx.selectedTurnDetail) {
    let __VLS_501;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
    aDescriptions;
    // @ts-ignore
    const __VLS_502 = __VLS_asFunctionalComponent1(__VLS_501, new __VLS_501({
        bordered: true,
        column: (2),
        size: "small",
    }));
    const __VLS_503 = __VLS_502({
        bordered: true,
        column: (2),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_502));
    const { default: __VLS_506 } = __VLS_504.slots;
    let __VLS_507;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_508 = __VLS_asFunctionalComponent1(__VLS_507, new __VLS_507({
        label: "Turn ID",
    }));
    const __VLS_509 = __VLS_508({
        label: "Turn ID",
    }, ...__VLS_functionalComponentArgsRest(__VLS_508));
    const { default: __VLS_512 } = __VLS_510.slots;
    (__VLS_ctx.selectedTurnDetail.turnId);
    // @ts-ignore
    [turnModalOpen, selectedTurnDetail, selectedTurnDetail,];
    var __VLS_510;
    let __VLS_513;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_514 = __VLS_asFunctionalComponent1(__VLS_513, new __VLS_513({
        label: "状态",
    }));
    const __VLS_515 = __VLS_514({
        label: "状态",
    }, ...__VLS_functionalComponentArgsRest(__VLS_514));
    const { default: __VLS_518 } = __VLS_516.slots;
    (__VLS_ctx.selectedTurnDetail.status);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_516;
    let __VLS_519;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_520 = __VLS_asFunctionalComponent1(__VLS_519, new __VLS_519({
        label: "LLM 延迟",
    }));
    const __VLS_521 = __VLS_520({
        label: "LLM 延迟",
    }, ...__VLS_functionalComponentArgsRest(__VLS_520));
    const { default: __VLS_524 } = __VLS_522.slots;
    (__VLS_ctx.formatMs(__VLS_ctx.selectedTurnDetail.llmLatencyMs));
    // @ts-ignore
    [formatMs, selectedTurnDetail,];
    var __VLS_522;
    let __VLS_525;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_526 = __VLS_asFunctionalComponent1(__VLS_525, new __VLS_525({
        label: "首包延迟",
    }));
    const __VLS_527 = __VLS_526({
        label: "首包延迟",
    }, ...__VLS_functionalComponentArgsRest(__VLS_526));
    const { default: __VLS_530 } = __VLS_528.slots;
    (__VLS_ctx.formatMs(__VLS_ctx.selectedTurnDetail.firstTokenMs));
    // @ts-ignore
    [formatMs, selectedTurnDetail,];
    var __VLS_528;
    let __VLS_531;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_532 = __VLS_asFunctionalComponent1(__VLS_531, new __VLS_531({
        label: "Prompt Tokens",
    }));
    const __VLS_533 = __VLS_532({
        label: "Prompt Tokens",
    }, ...__VLS_functionalComponentArgsRest(__VLS_532));
    const { default: __VLS_536 } = __VLS_534.slots;
    (__VLS_ctx.selectedTurnDetail.promptTokens);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_534;
    let __VLS_537;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_538 = __VLS_asFunctionalComponent1(__VLS_537, new __VLS_537({
        label: "Completion Tokens",
    }));
    const __VLS_539 = __VLS_538({
        label: "Completion Tokens",
    }, ...__VLS_functionalComponentArgsRest(__VLS_538));
    const { default: __VLS_542 } = __VLS_540.slots;
    (__VLS_ctx.selectedTurnDetail.completionTokens);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_540;
    let __VLS_543;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_544 = __VLS_asFunctionalComponent1(__VLS_543, new __VLS_543({
        label: "Context Tokens",
    }));
    const __VLS_545 = __VLS_544({
        label: "Context Tokens",
    }, ...__VLS_functionalComponentArgsRest(__VLS_544));
    const { default: __VLS_548 } = __VLS_546.slots;
    (__VLS_ctx.selectedTurnDetail.contextTokens);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_546;
    let __VLS_549;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_550 = __VLS_asFunctionalComponent1(__VLS_549, new __VLS_549({
        label: "Context Usage",
    }));
    const __VLS_551 = __VLS_550({
        label: "Context Usage",
    }, ...__VLS_functionalComponentArgsRest(__VLS_550));
    const { default: __VLS_554 } = __VLS_552.slots;
    (__VLS_ctx.selectedTurnDetail.contextTokenUsage);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_552;
    let __VLS_555;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_556 = __VLS_asFunctionalComponent1(__VLS_555, new __VLS_555({
        label: "Memory Hits",
    }));
    const __VLS_557 = __VLS_556({
        label: "Memory Hits",
    }, ...__VLS_functionalComponentArgsRest(__VLS_556));
    const { default: __VLS_560 } = __VLS_558.slots;
    (__VLS_ctx.selectedTurnDetail.memoryHits);
    // @ts-ignore
    [selectedTurnDetail,];
    var __VLS_558;
    let __VLS_561;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_562 = __VLS_asFunctionalComponent1(__VLS_561, new __VLS_561({
        label: "Memory Latency",
    }));
    const __VLS_563 = __VLS_562({
        label: "Memory Latency",
    }, ...__VLS_functionalComponentArgsRest(__VLS_562));
    const { default: __VLS_566 } = __VLS_564.slots;
    (__VLS_ctx.formatMs(__VLS_ctx.selectedTurnDetail.memoryRetrievalMs));
    // @ts-ignore
    [formatMs, selectedTurnDetail,];
    var __VLS_564;
    // @ts-ignore
    [];
    var __VLS_504;
    let __VLS_567;
    /** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider'] | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
    aDivider;
    // @ts-ignore
    const __VLS_568 = __VLS_asFunctionalComponent1(__VLS_567, new __VLS_567({}));
    const __VLS_569 = __VLS_568({}, ...__VLS_functionalComponentArgsRest(__VLS_568));
    const { default: __VLS_572 } = __VLS_570.slots;
    // @ts-ignore
    [];
    var __VLS_570;
    let __VLS_573;
    /** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
    aTable;
    // @ts-ignore
    const __VLS_574 = __VLS_asFunctionalComponent1(__VLS_573, new __VLS_573({
        rowKey: "name",
        columns: (__VLS_ctx.toolDetailColumns),
        dataSource: (__VLS_ctx.selectedTurnDetail.toolCallsDetail),
        pagination: (false),
        size: "small",
    }));
    const __VLS_575 = __VLS_574({
        rowKey: "name",
        columns: (__VLS_ctx.toolDetailColumns),
        dataSource: (__VLS_ctx.selectedTurnDetail.toolCallsDetail),
        pagination: (false),
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_574));
    const { default: __VLS_578 } = __VLS_576.slots;
    {
        const { bodyCell: __VLS_579 } = __VLS_576.slots;
        const [{ column, record }] = __VLS_vSlot(__VLS_579);
        if (column.dataIndex === 'latencyMs') {
            (__VLS_ctx.formatMs(record.latencyMs));
        }
        else if (column.dataIndex === 'status') {
            let __VLS_580;
            /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
            aTag;
            // @ts-ignore
            const __VLS_581 = __VLS_asFunctionalComponent1(__VLS_580, new __VLS_580({
                color: (__VLS_ctx.statusColor(record.status)),
            }));
            const __VLS_582 = __VLS_581({
                color: (__VLS_ctx.statusColor(record.status)),
            }, ...__VLS_functionalComponentArgsRest(__VLS_581));
            const { default: __VLS_585 } = __VLS_583.slots;
            (record.status);
            // @ts-ignore
            [statusColor, formatMs, selectedTurnDetail, toolDetailColumns,];
            var __VLS_583;
        }
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_576;
}
// @ts-ignore
[];
var __VLS_498;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
