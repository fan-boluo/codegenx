import { onMounted, onUnmounted, ref, computed, nextTick } from 'vue';
import { getMySummaryStats, getMyDailyStats } from '@/api/statsController';
import { message } from 'ant-design-vue';
import * as echarts from 'echarts';
const loading = ref(false);
const chartLoading = ref(false);
const quotaInfo = ref({});
const summaryStats = ref({});
const dailyStats = ref([]);
const chartRef = ref();
let chartInstance = null;
// 计算配额使用百分比
const usagePercent = computed(() => {
    if (!quotaInfo.value.tokenQuota ||
        quotaInfo.value.tokenQuota === -1 ||
        !quotaInfo.value.usedTokens) {
        return 0;
    }
    return Math.round((quotaInfo.value.usedTokens / quotaInfo.value.tokenQuota) * 100);
});
// 加载综合统计数据
const loadSummaryStats = async () => {
    loading.value = true;
    try {
        const res = await getMySummaryStats();
        if (res.data.code === 0 && res.data.data) {
            summaryStats.value = res.data.data;
            quotaInfo.value = {
                tokenQuota: res.data.data.tokenQuota,
                usedTokens: res.data.data.usedTokens,
                remainingQuota: res.data.data.remainingQuota,
            };
        }
        else {
            message.error('获取统计数据失败：' + res.data.message);
        }
    }
    catch (error) {
        message.error('获取统计数据失败');
    }
    finally {
        loading.value = false;
    }
};
// 加载每日统计数据
const loadDailyStats = async () => {
    chartLoading.value = true;
    try {
        const endDate = new Date();
        const startDate = new Date();
        startDate.setDate(startDate.getDate() - 6);
        const res = await getMyDailyStats({
            startDate: startDate.toISOString().split('T')[0],
            endDate: endDate.toISOString().split('T')[0],
        });
        if (res.data.code === 0 && res.data.data) {
            dailyStats.value = res.data.data;
            // 等待 loading 状态更新后再渲染图表
            chartLoading.value = false;
            await nextTick();
            renderChart();
        }
        else {
            message.error('获取每日统计失败：' + res.data.message);
            chartLoading.value = false;
        }
    }
    catch (error) {
        message.error('获取每日统计失败');
        chartLoading.value = false;
    }
};
// 渲染图表
const renderChart = async () => {
    if (!chartRef.value || dailyStats.value.length === 0) {
        return;
    }
    await nextTick();
    // 如果已存在图表实例，先销毁
    if (chartInstance) {
        chartInstance.dispose();
    }
    // 初始化图表
    chartInstance = echarts.init(chartRef.value);
    // 提取并转换数据为数字类型
    const dates = dailyStats.value.map((item) => item.date);
    const tokens = dailyStats.value.map((item) => Number(item.totalTokens) || 0);
    const costs = dailyStats.value.map((item) => Number(item.totalCost) || 0);
    const requests = dailyStats.value.map((item) => Number(item.requestCount) || 0);
    const option = {
        tooltip: {
            trigger: 'axis',
            axisPointer: {
                type: 'cross',
            },
            formatter: (params) => {
                let result = `${params[0].axisValue}<br/>`;
                params.forEach((param) => {
                    const value = param.seriesName === '费用（元）' ? `¥${param.value.toFixed(2)}` : param.value;
                    result += `${param.marker}${param.seriesName}: ${value}<br/>`;
                });
                return result;
            },
        },
        legend: {
            data: ['Token消耗', '费用（元）', '请求次数'],
        },
        grid: {
            left: '3%',
            right: '4%',
            bottom: '3%',
            containLabel: true,
        },
        xAxis: {
            type: 'category',
            boundaryGap: false,
            data: dates,
        },
        yAxis: [
            {
                type: 'value',
                name: 'Tokens / 请求次数',
                position: 'left',
            },
            {
                type: 'value',
                name: '费用（元）',
                position: 'right',
                axisLabel: {
                    formatter: '¥{value}',
                },
            },
        ],
        series: [
            {
                name: 'Token消耗',
                type: 'line',
                data: tokens,
                smooth: true,
                itemStyle: {
                    color: '#1890ff',
                },
                lineStyle: {
                    color: '#1890ff',
                },
            },
            {
                name: '费用（元）',
                type: 'line',
                yAxisIndex: 1,
                data: costs,
                smooth: true,
                itemStyle: {
                    color: '#faad14',
                },
                lineStyle: {
                    color: '#faad14',
                },
            },
            {
                name: '请求次数',
                type: 'line',
                data: requests,
                smooth: true,
                itemStyle: {
                    color: '#52c41a',
                },
                lineStyle: {
                    color: '#52c41a',
                },
            },
        ],
    };
    chartInstance.setOption(option);
};
// 响应式调整
const handleResize = () => {
    if (chartInstance) {
        chartInstance.resize();
    }
};
// 组件挂载时添加监听
onMounted(() => {
    loadSummaryStats();
    loadDailyStats();
    window.addEventListener('resize', handleResize);
});
// 组件卸载时清理
onUnmounted(() => {
    window.removeEventListener('resize', handleResize);
    if (chartInstance) {
        chartInstance.dispose();
        chartInstance = null;
    }
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "profilePage",
});
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row'] | typeof __VLS_components.aRow | typeof __VLS_components.ARow | typeof __VLS_components['a-row']} */
aRow;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    gutter: (16),
}));
const __VLS_2 = __VLS_1({
    gutter: (16),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
const { default: __VLS_5 } = __VLS_3.slots;
let __VLS_6;
/** @ts-ignore @type { | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col'] | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col']} */
aCol;
// @ts-ignore
const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
    span: (8),
}));
const __VLS_8 = __VLS_7({
    span: (8),
}, ...__VLS_functionalComponentArgsRest(__VLS_7));
const { default: __VLS_11 } = __VLS_9.slots;
let __VLS_12;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_13 = __VLS_asFunctionalComponent1(__VLS_12, new __VLS_12({
    title: "配额信息",
    loading: (__VLS_ctx.loading),
}));
const __VLS_14 = __VLS_13({
    title: "配额信息",
    loading: (__VLS_ctx.loading),
}, ...__VLS_functionalComponentArgsRest(__VLS_13));
const { default: __VLS_17 } = __VLS_15.slots;
let __VLS_18;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_19 = __VLS_asFunctionalComponent1(__VLS_18, new __VLS_18({
    title: "Token配额",
    value: (__VLS_ctx.quotaInfo.tokenQuota === -1 ? '无限制' : __VLS_ctx.quotaInfo.tokenQuota),
    valueStyle: ({ color: '#3f8600' }),
}));
const __VLS_20 = __VLS_19({
    title: "Token配额",
    value: (__VLS_ctx.quotaInfo.tokenQuota === -1 ? '无限制' : __VLS_ctx.quotaInfo.tokenQuota),
    valueStyle: ({ color: '#3f8600' }),
}, ...__VLS_functionalComponentArgsRest(__VLS_19));
let __VLS_23;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_24 = __VLS_asFunctionalComponent1(__VLS_23, new __VLS_23({}));
const __VLS_25 = __VLS_24({}, ...__VLS_functionalComponentArgsRest(__VLS_24));
let __VLS_28;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_29 = __VLS_asFunctionalComponent1(__VLS_28, new __VLS_28({
    title: "已使用",
    value: (__VLS_ctx.quotaInfo.usedTokens || 0),
    suffix: "Tokens",
}));
const __VLS_30 = __VLS_29({
    title: "已使用",
    value: (__VLS_ctx.quotaInfo.usedTokens || 0),
    suffix: "Tokens",
}, ...__VLS_functionalComponentArgsRest(__VLS_29));
let __VLS_33;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_34 = __VLS_asFunctionalComponent1(__VLS_33, new __VLS_33({}));
const __VLS_35 = __VLS_34({}, ...__VLS_functionalComponentArgsRest(__VLS_34));
let __VLS_38;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_39 = __VLS_asFunctionalComponent1(__VLS_38, new __VLS_38({
    title: "剩余配额",
    value: (__VLS_ctx.quotaInfo.remainingQuota === -1 ? '无限制' : __VLS_ctx.quotaInfo.remainingQuota),
    valueStyle: ({ color: '#cf1322' }),
}));
const __VLS_40 = __VLS_39({
    title: "剩余配额",
    value: (__VLS_ctx.quotaInfo.remainingQuota === -1 ? '无限制' : __VLS_ctx.quotaInfo.remainingQuota),
    valueStyle: ({ color: '#cf1322' }),
}, ...__VLS_functionalComponentArgsRest(__VLS_39));
if (__VLS_ctx.quotaInfo.tokenQuota !== -1) {
    let __VLS_43;
    /** @ts-ignore @type { | typeof __VLS_components.aProgress | typeof __VLS_components.AProgress | typeof __VLS_components['a-progress']} */
    aProgress;
    // @ts-ignore
    const __VLS_44 = __VLS_asFunctionalComponent1(__VLS_43, new __VLS_43({
        percent: (__VLS_ctx.usagePercent),
        status: (__VLS_ctx.usagePercent > 90 ? 'exception' : 'normal'),
        ...{ style: {} },
    }));
    const __VLS_45 = __VLS_44({
        percent: (__VLS_ctx.usagePercent),
        status: (__VLS_ctx.usagePercent > 90 ? 'exception' : 'normal'),
        ...{ style: {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_44));
}
// @ts-ignore
[loading, quotaInfo, quotaInfo, quotaInfo, quotaInfo, quotaInfo, quotaInfo, usagePercent, usagePercent,];
var __VLS_15;
// @ts-ignore
[];
var __VLS_9;
let __VLS_48;
/** @ts-ignore @type { | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col'] | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col']} */
aCol;
// @ts-ignore
const __VLS_49 = __VLS_asFunctionalComponent1(__VLS_48, new __VLS_48({
    span: (8),
}));
const __VLS_50 = __VLS_49({
    span: (8),
}, ...__VLS_functionalComponentArgsRest(__VLS_49));
const { default: __VLS_53 } = __VLS_51.slots;
let __VLS_54;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_55 = __VLS_asFunctionalComponent1(__VLS_54, new __VLS_54({
    title: "Token消耗",
    loading: (__VLS_ctx.loading),
}));
const __VLS_56 = __VLS_55({
    title: "Token消耗",
    loading: (__VLS_ctx.loading),
}, ...__VLS_functionalComponentArgsRest(__VLS_55));
const { default: __VLS_59 } = __VLS_57.slots;
let __VLS_60;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_61 = __VLS_asFunctionalComponent1(__VLS_60, new __VLS_60({
    title: "累计消耗",
    value: (__VLS_ctx.summaryStats.totalTokens || 0),
    suffix: "Tokens",
    valueStyle: ({ color: '#1890ff' }),
}));
const __VLS_62 = __VLS_61({
    title: "累计消耗",
    value: (__VLS_ctx.summaryStats.totalTokens || 0),
    suffix: "Tokens",
    valueStyle: ({ color: '#1890ff' }),
}, ...__VLS_functionalComponentArgsRest(__VLS_61));
let __VLS_65;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_66 = __VLS_asFunctionalComponent1(__VLS_65, new __VLS_65({}));
const __VLS_67 = __VLS_66({}, ...__VLS_functionalComponentArgsRest(__VLS_66));
let __VLS_70;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_71 = __VLS_asFunctionalComponent1(__VLS_70, new __VLS_70({
    title: "总请求数",
    value: (__VLS_ctx.summaryStats.totalRequests || 0),
}));
const __VLS_72 = __VLS_71({
    title: "总请求数",
    value: (__VLS_ctx.summaryStats.totalRequests || 0),
}, ...__VLS_functionalComponentArgsRest(__VLS_71));
let __VLS_75;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_76 = __VLS_asFunctionalComponent1(__VLS_75, new __VLS_75({}));
const __VLS_77 = __VLS_76({}, ...__VLS_functionalComponentArgsRest(__VLS_76));
let __VLS_80;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_81 = __VLS_asFunctionalComponent1(__VLS_80, new __VLS_80({
    title: "成功请求数",
    value: (__VLS_ctx.summaryStats.successRequests || 0),
    valueStyle: ({ color: '#52c41a' }),
}));
const __VLS_82 = __VLS_81({
    title: "成功请求数",
    value: (__VLS_ctx.summaryStats.successRequests || 0),
    valueStyle: ({ color: '#52c41a' }),
}, ...__VLS_functionalComponentArgsRest(__VLS_81));
// @ts-ignore
[loading, summaryStats, summaryStats, summaryStats,];
var __VLS_57;
// @ts-ignore
[];
var __VLS_51;
let __VLS_85;
/** @ts-ignore @type { | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col'] | typeof __VLS_components.aCol | typeof __VLS_components.ACol | typeof __VLS_components['a-col']} */
aCol;
// @ts-ignore
const __VLS_86 = __VLS_asFunctionalComponent1(__VLS_85, new __VLS_85({
    span: (8),
}));
const __VLS_87 = __VLS_86({
    span: (8),
}, ...__VLS_functionalComponentArgsRest(__VLS_86));
const { default: __VLS_90 } = __VLS_88.slots;
let __VLS_91;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_92 = __VLS_asFunctionalComponent1(__VLS_91, new __VLS_91({
    title: "费用统计",
    loading: (__VLS_ctx.loading),
}));
const __VLS_93 = __VLS_92({
    title: "费用统计",
    loading: (__VLS_ctx.loading),
}, ...__VLS_functionalComponentArgsRest(__VLS_92));
const { default: __VLS_96 } = __VLS_94.slots;
let __VLS_97;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_98 = __VLS_asFunctionalComponent1(__VLS_97, new __VLS_97({
    title: "累计消费",
    value: (__VLS_ctx.summaryStats.totalCost || 0),
    prefix: "¥",
    precision: (2),
    valueStyle: ({ color: '#faad14' }),
}));
const __VLS_99 = __VLS_98({
    title: "累计消费",
    value: (__VLS_ctx.summaryStats.totalCost || 0),
    prefix: "¥",
    precision: (2),
    valueStyle: ({ color: '#faad14' }),
}, ...__VLS_functionalComponentArgsRest(__VLS_98));
let __VLS_102;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_103 = __VLS_asFunctionalComponent1(__VLS_102, new __VLS_102({}));
const __VLS_104 = __VLS_103({}, ...__VLS_functionalComponentArgsRest(__VLS_103));
let __VLS_107;
/** @ts-ignore @type { | typeof __VLS_components.aStatistic | typeof __VLS_components.AStatistic | typeof __VLS_components['a-statistic']} */
aStatistic;
// @ts-ignore
const __VLS_108 = __VLS_asFunctionalComponent1(__VLS_107, new __VLS_107({
    title: "今日消费",
    value: (__VLS_ctx.summaryStats.todayCost || 0),
    prefix: "¥",
    precision: (2),
}));
const __VLS_109 = __VLS_108({
    title: "今日消费",
    value: (__VLS_ctx.summaryStats.todayCost || 0),
    prefix: "¥",
    precision: (2),
}, ...__VLS_functionalComponentArgsRest(__VLS_108));
// @ts-ignore
[loading, summaryStats, summaryStats,];
var __VLS_94;
// @ts-ignore
[];
var __VLS_88;
// @ts-ignore
[];
var __VLS_3;
let __VLS_112;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_113 = __VLS_asFunctionalComponent1(__VLS_112, new __VLS_112({
    title: "每日消耗趋势（最近7天）",
    ...{ style: {} },
    loading: (__VLS_ctx.chartLoading),
}));
const __VLS_114 = __VLS_113({
    title: "每日消耗趋势（最近7天）",
    ...{ style: {} },
    loading: (__VLS_ctx.chartLoading),
}, ...__VLS_functionalComponentArgsRest(__VLS_113));
const { default: __VLS_117 } = __VLS_115.slots;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ref: "chartRef",
    ...{ style: {} },
});
// @ts-ignore
[chartLoading,];
var __VLS_115;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
