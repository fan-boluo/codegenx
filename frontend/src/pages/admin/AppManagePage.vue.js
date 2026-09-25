import { computed, onMounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { listAppVoByPageByAdmin, deleteAppByAdmin, updateAppByAdmin, getAppVoById, } from '@/api/appController';
import { formatTime } from '@/utils/time';
const router = useRouter();
const columns = [
    { title: 'ID', dataIndex: 'id', width: 80, fixed: 'left' },
    { title: '项目名称', dataIndex: 'appName', width: 150 },
    { title: '项目库', dataIndex: 'dbName', width: 150 },
    { title: '创建者', dataIndex: 'owner', width: 120 },
    { title: '创建时间', dataIndex: 'createTime', width: 160 },
    { title: '操作', key: 'action', width: 220, fixed: 'right' },
];
const data = ref([]);
const total = ref(0);
const searchParams = reactive({
    pageNum: 1,
    pageSize: 10,
});
// 弹窗相关
const editModalOpen = ref(false);
const currentApp = ref({});
const formData = reactive({
    appName: '',
});
const formRef = ref();
const submitting = ref(false);
const fetchData = async () => {
    try {
        const res = await listAppVoByPageByAdmin({ ...searchParams });
        if (res.data.data) {
            data.value = res.data.data.records ?? [];
            total.value = res.data.data.totalRow ?? 0;
        }
        else {
            message.error('获取数据失败，' + res.data.message);
        }
    }
    catch (error) {
        console.error('获取数据失败：', error);
        message.error('获取数据失败');
    }
};
onMounted(() => {
    fetchData();
});
const pagination = computed(() => ({
    current: searchParams.pageNum ?? 1,
    pageSize: searchParams.pageSize ?? 10,
    total: total.value,
    showSizeChanger: true,
    showTotal: (total) => `共 ${total} 条`,
}));
const doTableChange = (page) => {
    searchParams.pageNum = page.current;
    searchParams.pageSize = page.pageSize;
    fetchData();
};
const doSearch = () => {
    searchParams.pageNum = 1;
    fetchData();
};
const editApp = async (app) => {
    try {
        const res = await getAppVoById({ id: app.id });
        console.log(res);
        if (res.data.code === 0 && res.data.data) {
            currentApp.value = res.data.data;
            formData.appName = res.data.data.appName || '';
            editModalOpen.value = true;
        }
        else {
            message.error('获取项目信息失败');
        }
    }
    catch (error) {
        console.error('获取项目信息失败: ', error);
        message.error('获取项目信息失败');
    }
};
const handleSubmit = async () => {
    if (!currentApp.value?.id)
        return;
    submitting.value = true;
    try {
        const res = await updateAppByAdmin({
            id: currentApp.value.id,
            appName: formData.appName,
        });
        if (res.data.code === 0) {
            message.success('修改成功');
            editModalOpen.value = false;
            await fetchData();
        }
        else {
            message.error('修改失败: ' + res.data.message);
        }
    }
    catch (error) {
        console.error('修改失败: ', error);
        message.error('修改失败');
    }
    finally {
        submitting.value = false;
    }
};
const handleEditOk = async () => {
    await handleSubmit();
};
const handleEditCancel = () => {
    editModalOpen.value = false;
    currentApp.value = {};
    formData.appName = '';
};
const rules = {
    appName: [
        { required: true, message: '请输入项目名称', trigger: 'blur' },
        { min: 1, max: 50, message: '项目名称长度在1-50个字符', trigger: 'blur' },
    ],
};
const deleteApp = async (id) => {
    if (!id)
        return;
    try {
        const res = await deleteAppByAdmin({ id });
        if (res.data.code === 0) {
            message.success('删除成功');
            fetchData();
        }
        else {
            message.error('删除失败：' + res.data.message);
        }
    }
    catch (error) {
        console.error('删除失败：', error);
        message.error('删除失败');
    }
};
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['edit-form-container']} */ ;
/** @type {__VLS_StyleScopedClasses['edit-form-container']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "appManagePage",
});
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.searchParams),
}));
const __VLS_2 = __VLS_1({
    ...{ 'onFinish': {} },
    layout: "inline",
    model: (__VLS_ctx.searchParams),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
let __VLS_5;
const __VLS_6 = ({ finish: {} },
    { onFinish: (__VLS_ctx.doSearch) });
const { default: __VLS_7 } = __VLS_3.slots;
let __VLS_8;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_9 = __VLS_asFunctionalComponent1(__VLS_8, new __VLS_8({
    label: "项目名称",
}));
const __VLS_10 = __VLS_9({
    label: "项目名称",
}, ...__VLS_functionalComponentArgsRest(__VLS_9));
const { default: __VLS_13 } = __VLS_11.slots;
let __VLS_14;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_15 = __VLS_asFunctionalComponent1(__VLS_14, new __VLS_14({
    value: (__VLS_ctx.searchParams.appName),
    placeholder: "输入项目名称",
}));
const __VLS_16 = __VLS_15({
    value: (__VLS_ctx.searchParams.appName),
    placeholder: "输入项目名称",
}, ...__VLS_functionalComponentArgsRest(__VLS_15));
// @ts-ignore
[searchParams, searchParams, doSearch,];
var __VLS_11;
let __VLS_19;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_20 = __VLS_asFunctionalComponent1(__VLS_19, new __VLS_19({}));
const __VLS_21 = __VLS_20({}, ...__VLS_functionalComponentArgsRest(__VLS_20));
const { default: __VLS_24 } = __VLS_22.slots;
let __VLS_25;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_26 = __VLS_asFunctionalComponent1(__VLS_25, new __VLS_25({
    type: "primary",
    htmlType: "submit",
}));
const __VLS_27 = __VLS_26({
    type: "primary",
    htmlType: "submit",
}, ...__VLS_functionalComponentArgsRest(__VLS_26));
const { default: __VLS_30 } = __VLS_28.slots;
// @ts-ignore
[];
var __VLS_28;
// @ts-ignore
[];
var __VLS_22;
// @ts-ignore
[];
var __VLS_3;
var __VLS_4;
let __VLS_31;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_32 = __VLS_asFunctionalComponent1(__VLS_31, new __VLS_31({}));
const __VLS_33 = __VLS_32({}, ...__VLS_functionalComponentArgsRest(__VLS_32));
let __VLS_36;
/** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
aTable;
// @ts-ignore
const __VLS_37 = __VLS_asFunctionalComponent1(__VLS_36, new __VLS_36({
    ...{ 'onChange': {} },
    columns: (__VLS_ctx.columns),
    dataSource: (__VLS_ctx.data),
    pagination: (__VLS_ctx.pagination),
    scroll: ({ x: 1200 }),
}));
const __VLS_38 = __VLS_37({
    ...{ 'onChange': {} },
    columns: (__VLS_ctx.columns),
    dataSource: (__VLS_ctx.data),
    pagination: (__VLS_ctx.pagination),
    scroll: ({ x: 1200 }),
}, ...__VLS_functionalComponentArgsRest(__VLS_37));
let __VLS_41;
const __VLS_42 = ({ change: {} },
    { onChange: (__VLS_ctx.doTableChange) });
const { default: __VLS_43 } = __VLS_39.slots;
{
    const { bodyCell: __VLS_44 } = __VLS_39.slots;
    const [{ column, record }] = __VLS_vSlot(__VLS_44);
    if (column.dataIndex === 'id' || column.dataIndex === 'appName') {
        (record[column.dataIndex]);
    }
    else if (column.dataIndex === 'createTime') {
        (__VLS_ctx.formatTime(record.createTime));
    }
    else if (column.dataIndex === 'owner') {
        (record.ownerName || (record.owner ? `用户 ${record.owner}` : '未知用户'));
    }
    else if (column.key === 'action') {
        let __VLS_45;
        /** @ts-ignore @type { | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space'] | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space']} */
        aSpace;
        // @ts-ignore
        const __VLS_46 = __VLS_asFunctionalComponent1(__VLS_45, new __VLS_45({}));
        const __VLS_47 = __VLS_46({}, ...__VLS_functionalComponentArgsRest(__VLS_46));
        const { default: __VLS_50 } = __VLS_48.slots;
        let __VLS_51;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_52 = __VLS_asFunctionalComponent1(__VLS_51, new __VLS_51({
            ...{ 'onClick': {} },
            type: "primary",
            size: "small",
        }));
        const __VLS_53 = __VLS_52({
            ...{ 'onClick': {} },
            type: "primary",
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_52));
        let __VLS_56;
        const __VLS_57 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(column.dataIndex === 'id' || column.dataIndex === 'appName'))
                        return;
                    if (!!(column.dataIndex === 'createTime'))
                        return;
                    if (!!(column.dataIndex === 'owner'))
                        return;
                    if (!(column.key === 'action'))
                        return;
                    __VLS_ctx.editApp(record);
                    // @ts-ignore
                    [columns, data, pagination, doTableChange, formatTime, editApp,];
                } });
        const { default: __VLS_58 } = __VLS_54.slots;
        // @ts-ignore
        [];
        var __VLS_54;
        var __VLS_55;
        let __VLS_59;
        /** @ts-ignore @type { | typeof __VLS_components.aPopconfirm | typeof __VLS_components.APopconfirm | typeof __VLS_components['a-popconfirm'] | typeof __VLS_components.aPopconfirm | typeof __VLS_components.APopconfirm | typeof __VLS_components['a-popconfirm']} */
        aPopconfirm;
        // @ts-ignore
        const __VLS_60 = __VLS_asFunctionalComponent1(__VLS_59, new __VLS_59({
            ...{ 'onConfirm': {} },
            title: "确定要删除这个项目吗？",
        }));
        const __VLS_61 = __VLS_60({
            ...{ 'onConfirm': {} },
            title: "确定要删除这个项目吗？",
        }, ...__VLS_functionalComponentArgsRest(__VLS_60));
        let __VLS_64;
        const __VLS_65 = ({ confirm: {} },
            { onConfirm: (...[$event]) => {
                    if (!!(column.dataIndex === 'id' || column.dataIndex === 'appName'))
                        return;
                    if (!!(column.dataIndex === 'createTime'))
                        return;
                    if (!!(column.dataIndex === 'owner'))
                        return;
                    if (!(column.key === 'action'))
                        return;
                    __VLS_ctx.deleteApp(record.id);
                    // @ts-ignore
                    [deleteApp,];
                } });
        const { default: __VLS_66 } = __VLS_62.slots;
        let __VLS_67;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_68 = __VLS_asFunctionalComponent1(__VLS_67, new __VLS_67({
            danger: true,
            size: "small",
        }));
        const __VLS_69 = __VLS_68({
            danger: true,
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_68));
        const { default: __VLS_72 } = __VLS_70.slots;
        // @ts-ignore
        [];
        var __VLS_70;
        // @ts-ignore
        [];
        var __VLS_62;
        var __VLS_63;
        // @ts-ignore
        [];
        var __VLS_48;
    }
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_39;
var __VLS_40;
let __VLS_73;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_74 = __VLS_asFunctionalComponent1(__VLS_73, new __VLS_73({
    ...{ 'onOk': {} },
    ...{ 'onCancel': {} },
    open: (__VLS_ctx.editModalOpen),
    title: (`编辑项目 - ${__VLS_ctx.currentApp?.appName}`),
    width: "800px",
    footer: (null),
}));
const __VLS_75 = __VLS_74({
    ...{ 'onOk': {} },
    ...{ 'onCancel': {} },
    open: (__VLS_ctx.editModalOpen),
    title: (`编辑项目 - ${__VLS_ctx.currentApp?.appName}`),
    width: "800px",
    footer: (null),
}, ...__VLS_functionalComponentArgsRest(__VLS_74));
let __VLS_78;
const __VLS_79 = ({ ok: {} },
    { onOk: (__VLS_ctx.handleEditOk) });
const __VLS_80 = ({ cancel: {} },
    { onCancel: (__VLS_ctx.handleEditCancel) });
const { default: __VLS_81 } = __VLS_76.slots;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "edit-form-container" },
});
/** @type {__VLS_StyleScopedClasses['edit-form-container']} */ ;
let __VLS_82;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
aDescriptions;
// @ts-ignore
const __VLS_83 = __VLS_asFunctionalComponent1(__VLS_82, new __VLS_82({
    column: (2),
    bordered: true,
    ...{ style: {} },
}));
const __VLS_84 = __VLS_83({
    column: (2),
    bordered: true,
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_83));
const { default: __VLS_87 } = __VLS_85.slots;
let __VLS_88;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_89 = __VLS_asFunctionalComponent1(__VLS_88, new __VLS_88({
    label: "项目ID",
}));
const __VLS_90 = __VLS_89({
    label: "项目ID",
}, ...__VLS_functionalComponentArgsRest(__VLS_89));
const { default: __VLS_93 } = __VLS_91.slots;
(__VLS_ctx.currentApp?.id);
// @ts-ignore
[editModalOpen, currentApp, currentApp, handleEditOk, handleEditCancel,];
var __VLS_91;
let __VLS_94;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_95 = __VLS_asFunctionalComponent1(__VLS_94, new __VLS_94({
    label: "创建者",
}));
const __VLS_96 = __VLS_95({
    label: "创建者",
}, ...__VLS_functionalComponentArgsRest(__VLS_95));
const { default: __VLS_99 } = __VLS_97.slots;
(__VLS_ctx.currentApp?.ownerName || (__VLS_ctx.currentApp?.owner ? `用户 ${__VLS_ctx.currentApp.owner}` : '未知用户'));
// @ts-ignore
[currentApp, currentApp, currentApp,];
var __VLS_97;
let __VLS_100;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_101 = __VLS_asFunctionalComponent1(__VLS_100, new __VLS_100({
    label: "创建时间",
}));
const __VLS_102 = __VLS_101({
    label: "创建时间",
}, ...__VLS_functionalComponentArgsRest(__VLS_101));
const { default: __VLS_105 } = __VLS_103.slots;
(__VLS_ctx.formatTime(__VLS_ctx.currentApp?.createTime));
// @ts-ignore
[formatTime, currentApp,];
var __VLS_103;
let __VLS_106;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_107 = __VLS_asFunctionalComponent1(__VLS_106, new __VLS_106({
    label: "更新时间",
}));
const __VLS_108 = __VLS_107({
    label: "更新时间",
}, ...__VLS_functionalComponentArgsRest(__VLS_107));
const { default: __VLS_111 } = __VLS_109.slots;
(__VLS_ctx.formatTime(__VLS_ctx.currentApp?.updateTime));
// @ts-ignore
[formatTime, currentApp,];
var __VLS_109;
// @ts-ignore
[];
var __VLS_85;
let __VLS_112;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_113 = __VLS_asFunctionalComponent1(__VLS_112, new __VLS_112({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formData),
    rules: (__VLS_ctx.rules),
    layout: "vertical",
    ref: "formRef",
}));
const __VLS_114 = __VLS_113({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formData),
    rules: (__VLS_ctx.rules),
    layout: "vertical",
    ref: "formRef",
}, ...__VLS_functionalComponentArgsRest(__VLS_113));
let __VLS_117;
const __VLS_118 = ({ finish: {} },
    { onFinish: (__VLS_ctx.handleSubmit) });
var __VLS_119;
const { default: __VLS_121 } = __VLS_115.slots;
let __VLS_122;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_123 = __VLS_asFunctionalComponent1(__VLS_122, new __VLS_122({
    label: "项目名称",
    name: "appName",
}));
const __VLS_124 = __VLS_123({
    label: "项目名称",
    name: "appName",
}, ...__VLS_functionalComponentArgsRest(__VLS_123));
const { default: __VLS_127 } = __VLS_125.slots;
let __VLS_128;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_129 = __VLS_asFunctionalComponent1(__VLS_128, new __VLS_128({
    value: (__VLS_ctx.formData.appName),
    placeholder: "请输入项目名称",
    maxlength: (50),
    showCount: true,
}));
const __VLS_130 = __VLS_129({
    value: (__VLS_ctx.formData.appName),
    placeholder: "请输入项目名称",
    maxlength: (50),
    showCount: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_129));
// @ts-ignore
[formData, formData, rules, handleSubmit,];
var __VLS_125;
let __VLS_133;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_134 = __VLS_asFunctionalComponent1(__VLS_133, new __VLS_133({
    label: "数据库",
    name: "dbName",
}));
const __VLS_135 = __VLS_134({
    label: "数据库",
    name: "dbName",
}, ...__VLS_functionalComponentArgsRest(__VLS_134));
const { default: __VLS_138 } = __VLS_136.slots;
let __VLS_139;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_140 = __VLS_asFunctionalComponent1(__VLS_139, new __VLS_139({
    value: (__VLS_ctx.currentApp?.dbName),
    placeholder: "数据库名称",
    disabled: true,
}));
const __VLS_141 = __VLS_140({
    value: (__VLS_ctx.currentApp?.dbName),
    placeholder: "数据库名称",
    disabled: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_140));
// @ts-ignore
[currentApp,];
var __VLS_136;
let __VLS_144;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_145 = __VLS_asFunctionalComponent1(__VLS_144, new __VLS_144({
    ...{ style: {} },
}));
const __VLS_146 = __VLS_145({
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_145));
const { default: __VLS_149 } = __VLS_147.slots;
let __VLS_150;
/** @ts-ignore @type { | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space'] | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space']} */
aSpace;
// @ts-ignore
const __VLS_151 = __VLS_asFunctionalComponent1(__VLS_150, new __VLS_150({}));
const __VLS_152 = __VLS_151({}, ...__VLS_functionalComponentArgsRest(__VLS_151));
const { default: __VLS_155 } = __VLS_153.slots;
let __VLS_156;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_157 = __VLS_asFunctionalComponent1(__VLS_156, new __VLS_156({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.submitting),
}));
const __VLS_158 = __VLS_157({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.submitting),
}, ...__VLS_functionalComponentArgsRest(__VLS_157));
const { default: __VLS_161 } = __VLS_159.slots;
// @ts-ignore
[submitting,];
var __VLS_159;
let __VLS_162;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_163 = __VLS_asFunctionalComponent1(__VLS_162, new __VLS_162({
    ...{ 'onClick': {} },
}));
const __VLS_164 = __VLS_163({
    ...{ 'onClick': {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_163));
let __VLS_167;
const __VLS_168 = ({ click: {} },
    { onClick: (__VLS_ctx.handleEditCancel) });
const { default: __VLS_169 } = __VLS_165.slots;
// @ts-ignore
[handleEditCancel,];
var __VLS_165;
var __VLS_166;
// @ts-ignore
[];
var __VLS_153;
// @ts-ignore
[];
var __VLS_147;
// @ts-ignore
[];
var __VLS_115;
var __VLS_116;
// @ts-ignore
[];
var __VLS_76;
var __VLS_77;
// @ts-ignore
var __VLS_120 = __VLS_119;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
