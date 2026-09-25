import { computed, onMounted, reactive, ref } from 'vue';
import { deleteUser, listUserVoByPage } from '@/api/userController.ts';
import { message } from 'ant-design-vue';
import dayjs from 'dayjs';
const columns = [
    { title: 'ID', dataIndex: 'id' },
    { title: '账号', dataIndex: 'userAccount' },
    { title: '用户名', dataIndex: 'userName' },
    { title: '简介', dataIndex: 'userProfile' },
    { title: '用户角色', dataIndex: 'userRole' },
    { title: '创建时间', dataIndex: 'createTime' },
    { title: '操作', key: 'action' },
];
const data = ref([]);
const total = ref(0);
const searchParams = reactive({
    pageNum: 1,
    pageSize: 10,
});
const fetchData = async () => {
    const res = await listUserVoByPage({ ...searchParams });
    if (res.data.data) {
        data.value = res.data.data.records ?? [];
        total.value = res.data.data.totalRow ?? 0;
    }
    else {
        message.error('获取数据失败，' + res.data.message);
    }
};
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
const doDelete = async (id) => {
    if (!id)
        return;
    // 后端 DeleteRequest 只接收 id 字段，传 user_id 会报参数错误
    const res = await deleteUser({ id });
    if (res.data.code === 0) {
        message.success('删除成功');
        fetchData();
    }
    else {
        message.error('删除失败');
    }
};
onMounted(() => {
    fetchData();
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "userManagePage",
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
    label: "账号",
}));
const __VLS_10 = __VLS_9({
    label: "账号",
}, ...__VLS_functionalComponentArgsRest(__VLS_9));
const { default: __VLS_13 } = __VLS_11.slots;
let __VLS_14;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_15 = __VLS_asFunctionalComponent1(__VLS_14, new __VLS_14({
    value: (__VLS_ctx.searchParams.userAccount),
    placeholder: "输入账号",
}));
const __VLS_16 = __VLS_15({
    value: (__VLS_ctx.searchParams.userAccount),
    placeholder: "输入账号",
}, ...__VLS_functionalComponentArgsRest(__VLS_15));
// @ts-ignore
[searchParams, searchParams, doSearch,];
var __VLS_11;
let __VLS_19;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_20 = __VLS_asFunctionalComponent1(__VLS_19, new __VLS_19({
    label: "用户名",
}));
const __VLS_21 = __VLS_20({
    label: "用户名",
}, ...__VLS_functionalComponentArgsRest(__VLS_20));
const { default: __VLS_24 } = __VLS_22.slots;
let __VLS_25;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_26 = __VLS_asFunctionalComponent1(__VLS_25, new __VLS_25({
    value: (__VLS_ctx.searchParams.userName),
    placeholder: "输入用户名",
}));
const __VLS_27 = __VLS_26({
    value: (__VLS_ctx.searchParams.userName),
    placeholder: "输入用户名",
}, ...__VLS_functionalComponentArgsRest(__VLS_26));
// @ts-ignore
[searchParams,];
var __VLS_22;
let __VLS_30;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_31 = __VLS_asFunctionalComponent1(__VLS_30, new __VLS_30({}));
const __VLS_32 = __VLS_31({}, ...__VLS_functionalComponentArgsRest(__VLS_31));
const { default: __VLS_35 } = __VLS_33.slots;
let __VLS_36;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_37 = __VLS_asFunctionalComponent1(__VLS_36, new __VLS_36({
    type: "primary",
    htmlType: "submit",
}));
const __VLS_38 = __VLS_37({
    type: "primary",
    htmlType: "submit",
}, ...__VLS_functionalComponentArgsRest(__VLS_37));
const { default: __VLS_41 } = __VLS_39.slots;
// @ts-ignore
[];
var __VLS_39;
// @ts-ignore
[];
var __VLS_33;
// @ts-ignore
[];
var __VLS_3;
var __VLS_4;
let __VLS_42;
/** @ts-ignore @type { | typeof __VLS_components.aDivider | typeof __VLS_components.ADivider | typeof __VLS_components['a-divider']} */
aDivider;
// @ts-ignore
const __VLS_43 = __VLS_asFunctionalComponent1(__VLS_42, new __VLS_42({}));
const __VLS_44 = __VLS_43({}, ...__VLS_functionalComponentArgsRest(__VLS_43));
let __VLS_47;
/** @ts-ignore @type { | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table'] | typeof __VLS_components.aTable | typeof __VLS_components.ATable | typeof __VLS_components['a-table']} */
aTable;
// @ts-ignore
const __VLS_48 = __VLS_asFunctionalComponent1(__VLS_47, new __VLS_47({
    ...{ 'onChange': {} },
    columns: (__VLS_ctx.columns),
    dataSource: (__VLS_ctx.data),
    pagination: (__VLS_ctx.pagination),
}));
const __VLS_49 = __VLS_48({
    ...{ 'onChange': {} },
    columns: (__VLS_ctx.columns),
    dataSource: (__VLS_ctx.data),
    pagination: (__VLS_ctx.pagination),
}, ...__VLS_functionalComponentArgsRest(__VLS_48));
let __VLS_52;
const __VLS_53 = ({ change: {} },
    { onChange: (__VLS_ctx.doTableChange) });
const { default: __VLS_54 } = __VLS_50.slots;
{
    const { bodyCell: __VLS_55 } = __VLS_50.slots;
    const [{ column, record }] = __VLS_vSlot(__VLS_55);
    if (column.dataIndex === 'id' ||
        column.dataIndex === 'userAccount' ||
        column.dataIndex === 'userName') {
        (record[column.dataIndex]);
    }
    else if (column.dataIndex === 'userRole') {
        if (record.userRole === 'admin') {
            let __VLS_56;
            /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
            aTag;
            // @ts-ignore
            const __VLS_57 = __VLS_asFunctionalComponent1(__VLS_56, new __VLS_56({
                color: "blue",
            }));
            const __VLS_58 = __VLS_57({
                color: "blue",
            }, ...__VLS_functionalComponentArgsRest(__VLS_57));
            const { default: __VLS_61 } = __VLS_59.slots;
            // @ts-ignore
            [columns, data, pagination, doTableChange,];
            var __VLS_59;
        }
        else {
            let __VLS_62;
            /** @ts-ignore @type { | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag'] | typeof __VLS_components.aTag | typeof __VLS_components.ATag | typeof __VLS_components['a-tag']} */
            aTag;
            // @ts-ignore
            const __VLS_63 = __VLS_asFunctionalComponent1(__VLS_62, new __VLS_62({
                color: "default",
            }));
            const __VLS_64 = __VLS_63({
                color: "default",
            }, ...__VLS_functionalComponentArgsRest(__VLS_63));
            const { default: __VLS_67 } = __VLS_65.slots;
            // @ts-ignore
            [];
            var __VLS_65;
        }
    }
    else if (column.dataIndex === 'createTime') {
        (__VLS_ctx.dayjs(record.createTime).format('YYYY-MM-DD HH:mm:ss'));
    }
    else if (column.key === 'action') {
        let __VLS_68;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_69 = __VLS_asFunctionalComponent1(__VLS_68, new __VLS_68({
            ...{ 'onClick': {} },
            danger: true,
        }));
        const __VLS_70 = __VLS_69({
            ...{ 'onClick': {} },
            danger: true,
        }, ...__VLS_functionalComponentArgsRest(__VLS_69));
        let __VLS_73;
        const __VLS_74 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(column.dataIndex === 'id' ||
                        column.dataIndex === 'userAccount' ||
                        column.dataIndex === 'userName'))
                        return;
                    if (!!(column.dataIndex === 'userRole'))
                        return;
                    if (!!(column.dataIndex === 'createTime'))
                        return;
                    if (!(column.key === 'action'))
                        return;
                    __VLS_ctx.doDelete(record.id);
                    // @ts-ignore
                    [dayjs, doDelete,];
                } });
        const { default: __VLS_75 } = __VLS_71.slots;
        // @ts-ignore
        [];
        var __VLS_71;
        var __VLS_72;
    }
    // @ts-ignore
    [];
}
// @ts-ignore
[];
var __VLS_50;
var __VLS_51;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
