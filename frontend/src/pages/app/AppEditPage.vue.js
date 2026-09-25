import { ref, reactive, onMounted, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useLoginUserStore } from '@/stores/loginUser';
import { getAppVoById, updateApp, updateAppByAdmin } from '@/api/appController';
import { formatTime } from '@/utils/time';
const route = useRoute();
const router = useRouter();
const loginUserStore = useLoginUserStore();
const appInfo = ref({});
const loading = ref(false);
const submitting = ref(false);
const formRef = ref();
const formData = reactive({
    appName: '',
});
const isAdmin = computed(() => loginUserStore.loginUser.userRole === 'admin');
const rules = {
    appName: [
        { required: true, message: '请输入项目名称', trigger: 'blur' },
        { min: 1, max: 50, message: '项目名称长度在1-50个字符', trigger: 'blur' },
    ],
};
const fetchAppInfo = async () => {
    const id = route.params.id;
    if (!id) {
        message.error('项目ID不存在');
        router.push('/');
        return;
    }
    loading.value = true;
    try {
        const res = await getAppVoById({ id: id });
        if (res.data.code === 0 && res.data.data) {
            appInfo.value = res.data.data;
            if (!isAdmin.value && appInfo.value.owner !== loginUserStore.loginUser.id) {
                message.error('您没有权限编辑此项目');
                router.push('/');
                return;
            }
            formData.appName = appInfo.value.appName || '';
        }
        else {
            message.error('获取项目信息失败');
            router.push('/');
        }
    }
    catch (error) {
        console.error('获取项目信息失败：', error);
        message.error('获取项目信息失败');
        router.push('/');
    }
    finally {
        loading.value = false;
    }
};
const handleSubmit = async () => {
    if (!appInfo.value?.id)
        return;
    submitting.value = true;
    try {
        let res;
        if (isAdmin.value) {
            res = await updateAppByAdmin({
                id: appInfo.value.id,
                appName: formData.appName,
            });
        }
        else {
            res = await updateApp({
                id: appInfo.value.id,
                appName: formData.appName,
            });
        }
        if (res.data.code === 0) {
            message.success('修改成功');
            await fetchAppInfo();
        }
        else {
            message.error('修改失败：' + res.data.message);
        }
    }
    catch (error) {
        console.error('修改失败：', error);
        message.error('修改失败');
    }
    finally {
        submitting.value = false;
    }
};
const resetForm = () => {
    if (appInfo.value) {
        formData.appName = appInfo.value.appName || '';
    }
    formRef.value?.clearValidate();
};
const goToChat = () => {
    if (appInfo.value?.id) {
        router.push(`/app/chat/${appInfo.value.id}`);
    }
};
onMounted(() => {
    fetchAppInfo();
});
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "appEditPage",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "page-header" },
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.h1, __VLS_intrinsics.h1)({});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "edit-container" },
});
/** @type {__VLS_StyleScopedClasses['edit-container']} */ ;
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
aCard;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    title: "项目信息",
    loading: (__VLS_ctx.loading),
    ...{ class: "edit-card" },
}));
const __VLS_2 = __VLS_1({
    title: "项目信息",
    loading: (__VLS_ctx.loading),
    ...{ class: "edit-card" },
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
/** @type {__VLS_StyleScopedClasses['edit-card']} */ ;
const { default: __VLS_5 } = __VLS_3.slots;
let __VLS_6;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
aDescriptions;
// @ts-ignore
const __VLS_7 = __VLS_asFunctionalComponent1(__VLS_6, new __VLS_6({
    column: (2),
    bordered: true,
    ...{ style: {} },
}));
const __VLS_8 = __VLS_7({
    column: (2),
    bordered: true,
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_7));
const { default: __VLS_11 } = __VLS_9.slots;
let __VLS_12;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_13 = __VLS_asFunctionalComponent1(__VLS_12, new __VLS_12({
    label: "项目ID",
}));
const __VLS_14 = __VLS_13({
    label: "项目ID",
}, ...__VLS_functionalComponentArgsRest(__VLS_13));
const { default: __VLS_17 } = __VLS_15.slots;
(__VLS_ctx.appInfo.id);
// @ts-ignore
[loading, appInfo,];
var __VLS_15;
let __VLS_18;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_19 = __VLS_asFunctionalComponent1(__VLS_18, new __VLS_18({
    label: "创建者",
}));
const __VLS_20 = __VLS_19({
    label: "创建者",
}, ...__VLS_functionalComponentArgsRest(__VLS_19));
const { default: __VLS_23 } = __VLS_21.slots;
(__VLS_ctx.appInfo.ownerName || (__VLS_ctx.appInfo.owner ? `用户 ${__VLS_ctx.appInfo.owner}` : '未知用户'));
// @ts-ignore
[appInfo, appInfo, appInfo,];
var __VLS_21;
let __VLS_24;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_25 = __VLS_asFunctionalComponent1(__VLS_24, new __VLS_24({
    label: "创建时间",
}));
const __VLS_26 = __VLS_25({
    label: "创建时间",
}, ...__VLS_functionalComponentArgsRest(__VLS_25));
const { default: __VLS_29 } = __VLS_27.slots;
(__VLS_ctx.formatTime(__VLS_ctx.appInfo.createTime));
// @ts-ignore
[appInfo, formatTime,];
var __VLS_27;
let __VLS_30;
/** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
aDescriptionsItem;
// @ts-ignore
const __VLS_31 = __VLS_asFunctionalComponent1(__VLS_30, new __VLS_30({
    label: "更新时间",
}));
const __VLS_32 = __VLS_31({
    label: "更新时间",
}, ...__VLS_functionalComponentArgsRest(__VLS_31));
const { default: __VLS_35 } = __VLS_33.slots;
(__VLS_ctx.formatTime(__VLS_ctx.appInfo.updateTime));
// @ts-ignore
[appInfo, formatTime,];
var __VLS_33;
// @ts-ignore
[];
var __VLS_9;
let __VLS_36;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_37 = __VLS_asFunctionalComponent1(__VLS_36, new __VLS_36({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formData),
    rules: (__VLS_ctx.rules),
    layout: "vertical",
    ref: "formRef",
}));
const __VLS_38 = __VLS_37({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formData),
    rules: (__VLS_ctx.rules),
    layout: "vertical",
    ref: "formRef",
}, ...__VLS_functionalComponentArgsRest(__VLS_37));
let __VLS_41;
const __VLS_42 = ({ finish: {} },
    { onFinish: (__VLS_ctx.handleSubmit) });
var __VLS_43;
const { default: __VLS_45 } = __VLS_39.slots;
let __VLS_46;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_47 = __VLS_asFunctionalComponent1(__VLS_46, new __VLS_46({
    label: "项目名称",
    name: "appName",
}));
const __VLS_48 = __VLS_47({
    label: "项目名称",
    name: "appName",
}, ...__VLS_functionalComponentArgsRest(__VLS_47));
const { default: __VLS_51 } = __VLS_49.slots;
let __VLS_52;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_53 = __VLS_asFunctionalComponent1(__VLS_52, new __VLS_52({
    value: (__VLS_ctx.formData.appName),
    placeholder: "请输入项目名称",
    maxlength: (50),
    showCount: true,
}));
const __VLS_54 = __VLS_53({
    value: (__VLS_ctx.formData.appName),
    placeholder: "请输入项目名称",
    maxlength: (50),
    showCount: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_53));
// @ts-ignore
[formData, formData, rules, handleSubmit,];
var __VLS_49;
let __VLS_57;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_58 = __VLS_asFunctionalComponent1(__VLS_57, new __VLS_57({
    label: "数据库",
    name: "dbName",
}));
const __VLS_59 = __VLS_58({
    label: "数据库",
    name: "dbName",
}, ...__VLS_functionalComponentArgsRest(__VLS_58));
const { default: __VLS_62 } = __VLS_60.slots;
let __VLS_63;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_64 = __VLS_asFunctionalComponent1(__VLS_63, new __VLS_63({
    value: (__VLS_ctx.appInfo?.dbName),
    placeholder: "数据库名称",
    disabled: true,
}));
const __VLS_65 = __VLS_64({
    value: (__VLS_ctx.appInfo?.dbName),
    placeholder: "数据库名称",
    disabled: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_64));
// @ts-ignore
[appInfo,];
var __VLS_60;
let __VLS_68;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_69 = __VLS_asFunctionalComponent1(__VLS_68, new __VLS_68({}));
const __VLS_70 = __VLS_69({}, ...__VLS_functionalComponentArgsRest(__VLS_69));
const { default: __VLS_73 } = __VLS_71.slots;
let __VLS_74;
/** @ts-ignore @type { | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space'] | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space']} */
aSpace;
// @ts-ignore
const __VLS_75 = __VLS_asFunctionalComponent1(__VLS_74, new __VLS_74({}));
const __VLS_76 = __VLS_75({}, ...__VLS_functionalComponentArgsRest(__VLS_75));
const { default: __VLS_79 } = __VLS_77.slots;
let __VLS_80;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_81 = __VLS_asFunctionalComponent1(__VLS_80, new __VLS_80({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.submitting),
}));
const __VLS_82 = __VLS_81({
    type: "primary",
    htmlType: "submit",
    loading: (__VLS_ctx.submitting),
}, ...__VLS_functionalComponentArgsRest(__VLS_81));
const { default: __VLS_85 } = __VLS_83.slots;
// @ts-ignore
[submitting,];
var __VLS_83;
let __VLS_86;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_87 = __VLS_asFunctionalComponent1(__VLS_86, new __VLS_86({
    ...{ 'onClick': {} },
}));
const __VLS_88 = __VLS_87({
    ...{ 'onClick': {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_87));
let __VLS_91;
const __VLS_92 = ({ click: {} },
    { onClick: (__VLS_ctx.resetForm) });
const { default: __VLS_93 } = __VLS_89.slots;
// @ts-ignore
[resetForm,];
var __VLS_89;
var __VLS_90;
let __VLS_94;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_95 = __VLS_asFunctionalComponent1(__VLS_94, new __VLS_94({
    ...{ 'onClick': {} },
    type: "link",
}));
const __VLS_96 = __VLS_95({
    ...{ 'onClick': {} },
    type: "link",
}, ...__VLS_functionalComponentArgsRest(__VLS_95));
let __VLS_99;
const __VLS_100 = ({ click: {} },
    { onClick: (__VLS_ctx.goToChat) });
const { default: __VLS_101 } = __VLS_97.slots;
// @ts-ignore
[goToChat,];
var __VLS_97;
var __VLS_98;
// @ts-ignore
[];
var __VLS_77;
// @ts-ignore
[];
var __VLS_71;
// @ts-ignore
[];
var __VLS_39;
var __VLS_40;
// @ts-ignore
[];
var __VLS_3;
// @ts-ignore
var __VLS_44 = __VLS_43;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
