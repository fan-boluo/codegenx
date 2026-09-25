import { computed, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { ArrowRightOutlined, DeleteOutlined, EditOutlined, MessageOutlined, PlusOutlined, RocketOutlined, TeamOutlined, UserAddOutlined, } from '@ant-design/icons-vue';
import { addApp, addAppMember, getAppVoById, listAppMembers, listMyAppVoByPage, removeAppMember, updateApp, } from '@/api/appController';
import { useLoginUserStore } from '@/stores/loginUser';
import { formatRelativeTime, formatTime } from '@/utils/time';
const router = useRouter();
const loginUserStore = useLoginUserStore();
const loading = ref(false);
const apps = ref([]);
const total = ref(0);
const promptExamples = [
    '生成一个销售数据看板，按区域和时间维度展示营收、利润和 Top 10 产品',
    '做一个 A/B 实验分析报告页，展示实验组对照组的核心指标对比和置信区间',
    '帮我搭一个 API 调用量监控面板，展示 QPS、P99 延迟和错误率趋势',
];
const isLoggedIn = computed(() => Boolean(loginUserStore.loginUser.id));
const hasApps = computed(() => apps.value.length > 0);
const fetchMyApps = async () => {
    if (!isLoggedIn.value) {
        apps.value = [];
        total.value = 0;
        return;
    }
    loading.value = true;
    try {
        const res = await listMyAppVoByPage({
            pageNum: 1,
            pageSize: 12,
            sortField: 'updateTime',
            sortOrder: 'descend',
        });
        if (res.data.code === 0 && res.data.data) {
            apps.value = res.data.data.records ?? [];
            total.value = res.data.data.totalRow ?? 0;
            return;
        }
        message.error(`获取项目列表失败，${res.data.message ?? '请稍后重试'}`);
    }
    catch (error) {
        console.error('获取项目列表失败:', error);
        message.error('获取项目列表失败');
    }
    finally {
        loading.value = false;
    }
};
const createModalVisible = ref(false);
const createForm = ref({ appName: '', dbName: '' });
const isCreating = ref(false);
// 弹窗相关
const editModalOpen = ref(false);
const currentApp = ref({});
const formData = reactive({
    appName: '',
});
const formRef = ref();
const submitting = ref(false);
// 成员管理弹窗
const memberModalOpen = ref(false);
const memberApp = ref({});
const members = ref([]);
const memberLoading = ref(false);
const inviteAccount = ref('');
const addingMember = ref(false);
const isMemberManager = computed(() => {
    // 仅项目属主或管理员可管理成员（与后端权限一致）
    const uid = Number(loginUserStore.loginUser.id || 0);
    return Boolean(memberApp.value?.owner) && Number(memberApp.value.owner) === uid;
});
const openCreateChat = (prompt) => {
    if (!isLoggedIn.value) {
        router.push('/user/login');
        return;
    }
    createForm.value = { appName: '', dbName: '' };
    createModalVisible.value = true;
};
const handleCreateProject = async () => {
    if (!createForm.value.appName.trim()) {
        message.warning('请输入项目名称');
        return;
    }
    if (createForm.value.appName.trim().length > 20) {
        message.warning('项目名称不能超过20个字');
        return;
    }
    isCreating.value = true;
    try {
        const payload = {
            appName: createForm.value.appName.trim(),
        };
        const dbName = createForm.value.dbName.trim();
        if (dbName) {
            payload.dbName = 'prj_' + dbName;
        }
        const createRes = await addApp(payload);
        if (createRes.data.code !== 0 || !createRes.data.data) {
            message.error(`创建项目失败，${createRes.data.message ?? '请稍后重试'}`);
            return;
        }
        const createdAppId = String(createRes.data.data);
        createModalVisible.value = false;
        router.push(`/app/chat/${createdAppId}`);
    }
    catch (error) {
        console.error('创建项目失败:', error);
        message.error('创建项目失败');
    }
    finally {
        isCreating.value = false;
    }
};
const selectPromptExample = (_example) => {
    openCreateChat();
};
const goToChat = (appId) => {
    if (!appId)
        return;
    router.push(`/app/chat/${appId}`);
};
const goToEdit = async (appId) => {
    if (!appId)
        return;
    try {
        const res = await getAppVoById({ id: appId });
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
    if (!formData.appName.trim()) {
        message.warning('请输入项目名称');
        return;
    }
    submitting.value = true;
    try {
        const res = await updateApp({
            id: currentApp.value.id,
            appName: formData.appName.trim(),
        });
        if (res.data.code === 0) {
            message.success('保存成功');
            editModalOpen.value = false;
            fetchMyApps();
        }
        else {
            message.error(`保存失败，${res.data.message ?? '请稍后重试'}`);
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
const handleEditCancel = () => {
    editModalOpen.value = false;
    currentApp.value = {};
    formData.appName = '';
};
// ---------------------- 成员管理 ----------------------
const openMemberModal = async (app) => {
    if (!app?.id)
        return;
    memberApp.value = app;
    memberModalOpen.value = true;
    await fetchMembers();
};
const fetchMembers = async () => {
    if (!memberApp.value?.id)
        return;
    memberLoading.value = true;
    try {
        const res = await listAppMembers({ app_id: Number(memberApp.value.id) });
        if (res.data.code === 0 && res.data.data) {
            members.value = res.data.data ?? [];
            return;
        }
        message.error(`获取成员列表失败，${res.data.message ?? '请稍后重试'}`);
    }
    catch (error) {
        console.error('获取成员列表失败:', error);
        message.error('获取成员列表失败');
    }
    finally {
        memberLoading.value = false;
    }
};
const handleInviteMember = async () => {
    const account = inviteAccount.value.trim();
    if (!account) {
        message.warning('请输入要邀请的用户账号');
        return;
    }
    addingMember.value = true;
    try {
        const res = await addAppMember({
            appId: Number(memberApp.value.id),
            userAccount: account,
        });
        if (res.data.code === 0) {
            message.success('已添加成员');
            inviteAccount.value = '';
            await fetchMembers();
        }
        else {
            message.error(`添加成员失败，${res.data.message ?? '请稍后重试'}`);
        }
    }
    catch (error) {
        console.error('添加成员失败:', error);
        message.error('添加成员失败');
    }
    finally {
        addingMember.value = false;
    }
};
const handleRemoveMember = async (userId) => {
    if (!userId)
        return;
    try {
        const res = await removeAppMember({
            appId: Number(memberApp.value.id),
            userId,
        });
        if (res.data.code === 0) {
            message.success('已移除成员');
            await fetchMembers();
        }
        else {
            message.error(`移除成员失败，${res.data.message ?? '请稍后重试'}`);
        }
    }
    catch (error) {
        console.error('移除成员失败:', error);
        message.error('移除成员失败');
    }
};
watch(() => loginUserStore.loginUser.id, () => {
    fetchMyApps();
}, { immediate: true });
watch(() => loginUserStore.loginUser.id, () => {
    fetchMyApps();
}, { immediate: true });
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['create-guide-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['app-card']} */ ;
/** @type {__VLS_StyleScopedClasses['app-card']} */ ;
/** @type {__VLS_StyleScopedClasses['add-app-card']} */ ;
/** @type {__VLS_StyleScopedClasses['add-app-card']} */ ;
/** @type {__VLS_StyleScopedClasses['add-app-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['example-tag']} */ ;
/** @type {__VLS_StyleScopedClasses['app-card']} */ ;
/** @type {__VLS_StyleScopedClasses['ant-card-body']} */ ;
/** @type {__VLS_StyleScopedClasses['create-guide-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['section-header']} */ ;
/** @type {__VLS_StyleScopedClasses['section-title']} */ ;
/** @type {__VLS_StyleScopedClasses['welcome-card']} */ ;
/** @type {__VLS_StyleScopedClasses['create-guide-card']} */ ;
/** @type {__VLS_StyleScopedClasses['app-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['app-grid']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    id: "homePage",
});
if (!__VLS_ctx.isLoggedIn) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "welcome-section" },
    });
    /** @type {__VLS_StyleScopedClasses['welcome-section']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "welcome-card" },
    });
    /** @type {__VLS_StyleScopedClasses['welcome-card']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "welcome-icon" },
    });
    /** @type {__VLS_StyleScopedClasses['welcome-icon']} */ ;
    let __VLS_0;
    /** @ts-ignore @type { | typeof __VLS_components.RocketOutlined} */
    RocketOutlined;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({}));
    const __VLS_2 = __VLS_1({}, ...__VLS_functionalComponentArgsRest(__VLS_1));
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({
        ...{ class: "section-title" },
    });
    /** @type {__VLS_StyleScopedClasses['section-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "highlight" },
    });
    /** @type {__VLS_StyleScopedClasses['highlight']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "section-description" },
    });
    /** @type {__VLS_StyleScopedClasses['section-description']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "welcome-actions" },
    });
    /** @type {__VLS_StyleScopedClasses['welcome-actions']} */ ;
    let __VLS_5;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_6 = __VLS_asFunctionalComponent1(__VLS_5, new __VLS_5({
        ...{ 'onClick': {} },
        type: "primary",
        size: "large",
        ...{ class: "cta-btn" },
    }));
    const __VLS_7 = __VLS_6({
        ...{ 'onClick': {} },
        type: "primary",
        size: "large",
        ...{ class: "cta-btn" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_6));
    let __VLS_10;
    const __VLS_11 = ({ click: {} },
        { onClick: (...[$event]) => {
                if (!(!__VLS_ctx.isLoggedIn))
                    return;
                __VLS_ctx.router.push('/user/login');
                // @ts-ignore
                [isLoggedIn, router,];
            } });
    /** @type {__VLS_StyleScopedClasses['cta-btn']} */ ;
    const { default: __VLS_12 } = __VLS_8.slots;
    {
        const { icon: __VLS_13 } = __VLS_8.slots;
        let __VLS_14;
        /** @ts-ignore @type { | typeof __VLS_components.ArrowRightOutlined} */
        ArrowRightOutlined;
        // @ts-ignore
        const __VLS_15 = __VLS_asFunctionalComponent1(__VLS_14, new __VLS_14({}));
        const __VLS_16 = __VLS_15({}, ...__VLS_functionalComponentArgsRest(__VLS_15));
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_8;
    var __VLS_9;
}
else if (__VLS_ctx.loading) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "loading-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['loading-panel']} */ ;
    let __VLS_19;
    /** @ts-ignore @type { | typeof __VLS_components.aSpin | typeof __VLS_components.ASpin | typeof __VLS_components['a-spin']} */
    aSpin;
    // @ts-ignore
    const __VLS_20 = __VLS_asFunctionalComponent1(__VLS_19, new __VLS_19({
        size: "large",
    }));
    const __VLS_21 = __VLS_20({
        size: "large",
    }, ...__VLS_functionalComponentArgsRest(__VLS_20));
}
else if (!__VLS_ctx.hasApps) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "create-guide-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['create-guide-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "create-guide-card" },
    });
    /** @type {__VLS_StyleScopedClasses['create-guide-card']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "guide-icon" },
    });
    /** @type {__VLS_StyleScopedClasses['guide-icon']} */ ;
    let __VLS_24;
    /** @ts-ignore @type { | typeof __VLS_components.PlusOutlined} */
    PlusOutlined;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent1(__VLS_24, new __VLS_24({}));
    const __VLS_26 = __VLS_25({}, ...__VLS_functionalComponentArgsRest(__VLS_25));
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({
        ...{ class: "section-title" },
    });
    /** @type {__VLS_StyleScopedClasses['section-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "highlight" },
    });
    /** @type {__VLS_StyleScopedClasses['highlight']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "section-description" },
    });
    /** @type {__VLS_StyleScopedClasses['section-description']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "guide-actions" },
    });
    /** @type {__VLS_StyleScopedClasses['guide-actions']} */ ;
    let __VLS_29;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_30 = __VLS_asFunctionalComponent1(__VLS_29, new __VLS_29({
        ...{ 'onClick': {} },
        type: "primary",
        size: "large",
        ...{ class: "cta-btn" },
    }));
    const __VLS_31 = __VLS_30({
        ...{ 'onClick': {} },
        type: "primary",
        size: "large",
        ...{ class: "cta-btn" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_30));
    let __VLS_34;
    const __VLS_35 = ({ click: {} },
        { onClick: (...[$event]) => {
                if (!!(!__VLS_ctx.isLoggedIn))
                    return;
                if (!!(__VLS_ctx.loading))
                    return;
                if (!(!__VLS_ctx.hasApps))
                    return;
                __VLS_ctx.openCreateChat();
                // @ts-ignore
                [loading, hasApps, openCreateChat,];
            } });
    /** @type {__VLS_StyleScopedClasses['cta-btn']} */ ;
    const { default: __VLS_36 } = __VLS_32.slots;
    {
        const { icon: __VLS_37 } = __VLS_32.slots;
        let __VLS_38;
        /** @ts-ignore @type { | typeof __VLS_components.PlusOutlined} */
        PlusOutlined;
        // @ts-ignore
        const __VLS_39 = __VLS_asFunctionalComponent1(__VLS_38, new __VLS_38({}));
        const __VLS_40 = __VLS_39({}, ...__VLS_functionalComponentArgsRest(__VLS_39));
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_32;
    var __VLS_33;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "example-list" },
    });
    /** @type {__VLS_StyleScopedClasses['example-list']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "example-label" },
    });
    /** @type {__VLS_StyleScopedClasses['example-label']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "example-tags" },
    });
    /** @type {__VLS_StyleScopedClasses['example-tags']} */ ;
    for (const [example] of __VLS_vFor((__VLS_ctx.promptExamples))) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
            ...{ onClick: (...[$event]) => {
                    if (!!(!__VLS_ctx.isLoggedIn))
                        return;
                    if (!!(__VLS_ctx.loading))
                        return;
                    if (!(!__VLS_ctx.hasApps))
                        return;
                    __VLS_ctx.selectPromptExample(example);
                    // @ts-ignore
                    [promptExamples, selectPromptExample,];
                } },
            key: (example),
            ...{ class: "example-tag" },
        });
        /** @type {__VLS_StyleScopedClasses['example-tag']} */ ;
        (example);
        // @ts-ignore
        [];
    }
}
else {
    __VLS_asFunctionalElement1(__VLS_intrinsics.section, __VLS_intrinsics.section)({
        ...{ class: "apps-panel" },
    });
    /** @type {__VLS_StyleScopedClasses['apps-panel']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "section-header" },
    });
    /** @type {__VLS_StyleScopedClasses['section-header']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({});
    __VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({
        ...{ class: "section-title" },
    });
    /** @type {__VLS_StyleScopedClasses['section-title']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
        ...{ class: "section-description" },
    });
    /** @type {__VLS_StyleScopedClasses['section-description']} */ ;
    (__VLS_ctx.total);
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "app-grid" },
    });
    /** @type {__VLS_StyleScopedClasses['app-grid']} */ ;
    for (const [app, idx] of __VLS_vFor((__VLS_ctx.apps))) {
        let __VLS_43;
        /** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
        aCard;
        // @ts-ignore
        const __VLS_44 = __VLS_asFunctionalComponent1(__VLS_43, new __VLS_43({
            key: (app.id),
            ...{ class: "app-card" },
            bordered: (false),
            ...{ style: ({ animationDelay: `${idx * 60}ms` }) },
        }));
        const __VLS_45 = __VLS_44({
            key: (app.id),
            ...{ class: "app-card" },
            bordered: (false),
            ...{ style: ({ animationDelay: `${idx * 60}ms` }) },
        }, ...__VLS_functionalComponentArgsRest(__VLS_44));
        /** @type {__VLS_StyleScopedClasses['app-card']} */ ;
        const { default: __VLS_48 } = __VLS_46.slots;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "app-body" },
        });
        /** @type {__VLS_StyleScopedClasses['app-body']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "app-heading" },
        });
        /** @type {__VLS_StyleScopedClasses['app-heading']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({
            ...{ class: "app-name" },
        });
        /** @type {__VLS_StyleScopedClasses['app-name']} */ ;
        (app.appName || '未命名项目');
        __VLS_asFunctionalElement1(__VLS_intrinsics.p, __VLS_intrinsics.p)({
            ...{ class: "app-prompt" },
        });
        /** @type {__VLS_StyleScopedClasses['app-prompt']} */ ;
        (app.dbName || '未绑定');
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "app-meta" },
        });
        /** @type {__VLS_StyleScopedClasses['app-meta']} */ ;
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.formatTime(app.createTime, 'YYYY-MM-DD') || '未知');
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        (__VLS_ctx.formatRelativeTime(app.updateTime) || '刚刚');
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "app-actions" },
        });
        /** @type {__VLS_StyleScopedClasses['app-actions']} */ ;
        let __VLS_49;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_50 = __VLS_asFunctionalComponent1(__VLS_49, new __VLS_49({
            ...{ 'onClick': {} },
            type: "primary",
            size: "small",
        }));
        const __VLS_51 = __VLS_50({
            ...{ 'onClick': {} },
            type: "primary",
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_50));
        let __VLS_54;
        const __VLS_55 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(!__VLS_ctx.isLoggedIn))
                        return;
                    if (!!(__VLS_ctx.loading))
                        return;
                    if (!!(!__VLS_ctx.hasApps))
                        return;
                    __VLS_ctx.goToChat(app.id);
                    // @ts-ignore
                    [total, apps, formatTime, formatRelativeTime, goToChat,];
                } });
        const { default: __VLS_56 } = __VLS_52.slots;
        {
            const { icon: __VLS_57 } = __VLS_52.slots;
            let __VLS_58;
            /** @ts-ignore @type { | typeof __VLS_components.MessageOutlined} */
            MessageOutlined;
            // @ts-ignore
            const __VLS_59 = __VLS_asFunctionalComponent1(__VLS_58, new __VLS_58({}));
            const __VLS_60 = __VLS_59({}, ...__VLS_functionalComponentArgsRest(__VLS_59));
            // @ts-ignore
            [];
        }
        // @ts-ignore
        [];
        var __VLS_52;
        var __VLS_53;
        let __VLS_63;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_64 = __VLS_asFunctionalComponent1(__VLS_63, new __VLS_63({
            ...{ 'onClick': {} },
            size: "small",
        }));
        const __VLS_65 = __VLS_64({
            ...{ 'onClick': {} },
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_64));
        let __VLS_68;
        const __VLS_69 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(!__VLS_ctx.isLoggedIn))
                        return;
                    if (!!(__VLS_ctx.loading))
                        return;
                    if (!!(!__VLS_ctx.hasApps))
                        return;
                    __VLS_ctx.goToEdit(app.id);
                    // @ts-ignore
                    [goToEdit,];
                } });
        const { default: __VLS_70 } = __VLS_66.slots;
        {
            const { icon: __VLS_71 } = __VLS_66.slots;
            let __VLS_72;
            /** @ts-ignore @type { | typeof __VLS_components.EditOutlined} */
            EditOutlined;
            // @ts-ignore
            const __VLS_73 = __VLS_asFunctionalComponent1(__VLS_72, new __VLS_72({}));
            const __VLS_74 = __VLS_73({}, ...__VLS_functionalComponentArgsRest(__VLS_73));
            // @ts-ignore
            [];
        }
        // @ts-ignore
        [];
        var __VLS_66;
        var __VLS_67;
        let __VLS_77;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_78 = __VLS_asFunctionalComponent1(__VLS_77, new __VLS_77({
            ...{ 'onClick': {} },
            size: "small",
        }));
        const __VLS_79 = __VLS_78({
            ...{ 'onClick': {} },
            size: "small",
        }, ...__VLS_functionalComponentArgsRest(__VLS_78));
        let __VLS_82;
        const __VLS_83 = ({ click: {} },
            { onClick: (...[$event]) => {
                    if (!!(!__VLS_ctx.isLoggedIn))
                        return;
                    if (!!(__VLS_ctx.loading))
                        return;
                    if (!!(!__VLS_ctx.hasApps))
                        return;
                    __VLS_ctx.openMemberModal(app);
                    // @ts-ignore
                    [openMemberModal,];
                } });
        const { default: __VLS_84 } = __VLS_80.slots;
        {
            const { icon: __VLS_85 } = __VLS_80.slots;
            let __VLS_86;
            /** @ts-ignore @type { | typeof __VLS_components.TeamOutlined} */
            TeamOutlined;
            // @ts-ignore
            const __VLS_87 = __VLS_asFunctionalComponent1(__VLS_86, new __VLS_86({}));
            const __VLS_88 = __VLS_87({}, ...__VLS_functionalComponentArgsRest(__VLS_87));
            // @ts-ignore
            [];
        }
        // @ts-ignore
        [];
        var __VLS_80;
        var __VLS_81;
        // @ts-ignore
        [];
        var __VLS_46;
        // @ts-ignore
        [];
    }
    let __VLS_91;
    /** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
    aModal;
    // @ts-ignore
    const __VLS_92 = __VLS_asFunctionalComponent1(__VLS_91, new __VLS_91({
        ...{ 'onCancel': {} },
        open: (__VLS_ctx.editModalOpen),
        title: (`编辑项目 - ${__VLS_ctx.currentApp?.appName}`),
        width: "800px",
        footer: (null),
    }));
    const __VLS_93 = __VLS_92({
        ...{ 'onCancel': {} },
        open: (__VLS_ctx.editModalOpen),
        title: (`编辑项目 - ${__VLS_ctx.currentApp?.appName}`),
        width: "800px",
        footer: (null),
    }, ...__VLS_functionalComponentArgsRest(__VLS_92));
    let __VLS_96;
    const __VLS_97 = ({ cancel: {} },
        { onCancel: (__VLS_ctx.handleEditCancel) });
    const { default: __VLS_98 } = __VLS_94.slots;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "edit-form-container" },
    });
    /** @type {__VLS_StyleScopedClasses['edit-form-container']} */ ;
    let __VLS_99;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions'] | typeof __VLS_components.aDescriptions | typeof __VLS_components.ADescriptions | typeof __VLS_components['a-descriptions']} */
    aDescriptions;
    // @ts-ignore
    const __VLS_100 = __VLS_asFunctionalComponent1(__VLS_99, new __VLS_99({
        column: (2),
        bordered: true,
        ...{ style: {} },
    }));
    const __VLS_101 = __VLS_100({
        column: (2),
        bordered: true,
        ...{ style: {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_100));
    const { default: __VLS_104 } = __VLS_102.slots;
    let __VLS_105;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_106 = __VLS_asFunctionalComponent1(__VLS_105, new __VLS_105({
        label: "项目ID",
    }));
    const __VLS_107 = __VLS_106({
        label: "项目ID",
    }, ...__VLS_functionalComponentArgsRest(__VLS_106));
    const { default: __VLS_110 } = __VLS_108.slots;
    (__VLS_ctx.currentApp?.id);
    // @ts-ignore
    [editModalOpen, currentApp, currentApp, handleEditCancel,];
    var __VLS_108;
    let __VLS_111;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_112 = __VLS_asFunctionalComponent1(__VLS_111, new __VLS_111({
        label: "创建者",
    }));
    const __VLS_113 = __VLS_112({
        label: "创建者",
    }, ...__VLS_functionalComponentArgsRest(__VLS_112));
    const { default: __VLS_116 } = __VLS_114.slots;
    (__VLS_ctx.currentApp?.ownerName || (__VLS_ctx.currentApp?.owner ? `用户 ${__VLS_ctx.currentApp.owner}` : '未知'));
    // @ts-ignore
    [currentApp, currentApp, currentApp,];
    var __VLS_114;
    let __VLS_117;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_118 = __VLS_asFunctionalComponent1(__VLS_117, new __VLS_117({
        label: "创建时间",
    }));
    const __VLS_119 = __VLS_118({
        label: "创建时间",
    }, ...__VLS_functionalComponentArgsRest(__VLS_118));
    const { default: __VLS_122 } = __VLS_120.slots;
    (__VLS_ctx.formatTime(__VLS_ctx.currentApp?.createTime));
    // @ts-ignore
    [formatTime, currentApp,];
    var __VLS_120;
    let __VLS_123;
    /** @ts-ignore @type { | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item'] | typeof __VLS_components.aDescriptionsItem | typeof __VLS_components.ADescriptionsItem | typeof __VLS_components['a-descriptions-item']} */
    aDescriptionsItem;
    // @ts-ignore
    const __VLS_124 = __VLS_asFunctionalComponent1(__VLS_123, new __VLS_123({
        label: "更新时间",
    }));
    const __VLS_125 = __VLS_124({
        label: "更新时间",
    }, ...__VLS_functionalComponentArgsRest(__VLS_124));
    const { default: __VLS_128 } = __VLS_126.slots;
    (__VLS_ctx.formatTime(__VLS_ctx.currentApp?.updateTime));
    // @ts-ignore
    [formatTime, currentApp,];
    var __VLS_126;
    // @ts-ignore
    [];
    var __VLS_102;
    let __VLS_129;
    /** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
    aForm;
    // @ts-ignore
    const __VLS_130 = __VLS_asFunctionalComponent1(__VLS_129, new __VLS_129({
        ...{ 'onFinish': {} },
        model: (__VLS_ctx.formData),
        layout: "vertical",
        ref: "formRef",
    }));
    const __VLS_131 = __VLS_130({
        ...{ 'onFinish': {} },
        model: (__VLS_ctx.formData),
        layout: "vertical",
        ref: "formRef",
    }, ...__VLS_functionalComponentArgsRest(__VLS_130));
    let __VLS_134;
    const __VLS_135 = ({ finish: {} },
        { onFinish: (__VLS_ctx.handleSubmit) });
    var __VLS_136;
    const { default: __VLS_138 } = __VLS_132.slots;
    let __VLS_139;
    /** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
    aFormItem;
    // @ts-ignore
    const __VLS_140 = __VLS_asFunctionalComponent1(__VLS_139, new __VLS_139({
        label: "项目名称",
        name: "appName",
    }));
    const __VLS_141 = __VLS_140({
        label: "项目名称",
        name: "appName",
    }, ...__VLS_functionalComponentArgsRest(__VLS_140));
    const { default: __VLS_144 } = __VLS_142.slots;
    let __VLS_145;
    /** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
    aInput;
    // @ts-ignore
    const __VLS_146 = __VLS_asFunctionalComponent1(__VLS_145, new __VLS_145({
        value: (__VLS_ctx.formData.appName),
        placeholder: "请输入项目名称",
        maxlength: (50),
        showCount: true,
    }));
    const __VLS_147 = __VLS_146({
        value: (__VLS_ctx.formData.appName),
        placeholder: "请输入项目名称",
        maxlength: (50),
        showCount: true,
    }, ...__VLS_functionalComponentArgsRest(__VLS_146));
    // @ts-ignore
    [formData, formData, handleSubmit,];
    var __VLS_142;
    let __VLS_150;
    /** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
    aFormItem;
    // @ts-ignore
    const __VLS_151 = __VLS_asFunctionalComponent1(__VLS_150, new __VLS_150({
        label: "数据库",
        name: "dbName",
    }));
    const __VLS_152 = __VLS_151({
        label: "数据库",
        name: "dbName",
    }, ...__VLS_functionalComponentArgsRest(__VLS_151));
    const { default: __VLS_155 } = __VLS_153.slots;
    let __VLS_156;
    /** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
    aInput;
    // @ts-ignore
    const __VLS_157 = __VLS_asFunctionalComponent1(__VLS_156, new __VLS_156({
        value: (__VLS_ctx.currentApp?.dbName),
        placeholder: "数据库名称",
        disabled: true,
    }));
    const __VLS_158 = __VLS_157({
        value: (__VLS_ctx.currentApp?.dbName),
        placeholder: "数据库名称",
        disabled: true,
    }, ...__VLS_functionalComponentArgsRest(__VLS_157));
    // @ts-ignore
    [currentApp,];
    var __VLS_153;
    let __VLS_161;
    /** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
    aFormItem;
    // @ts-ignore
    const __VLS_162 = __VLS_asFunctionalComponent1(__VLS_161, new __VLS_161({
        ...{ style: {} },
    }));
    const __VLS_163 = __VLS_162({
        ...{ style: {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_162));
    const { default: __VLS_166 } = __VLS_164.slots;
    let __VLS_167;
    /** @ts-ignore @type { | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space'] | typeof __VLS_components.aSpace | typeof __VLS_components.ASpace | typeof __VLS_components['a-space']} */
    aSpace;
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
        loading: (__VLS_ctx.submitting),
    }));
    const __VLS_175 = __VLS_174({
        type: "primary",
        htmlType: "submit",
        loading: (__VLS_ctx.submitting),
    }, ...__VLS_functionalComponentArgsRest(__VLS_174));
    const { default: __VLS_178 } = __VLS_176.slots;
    // @ts-ignore
    [submitting,];
    var __VLS_176;
    let __VLS_179;
    /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
    aButton;
    // @ts-ignore
    const __VLS_180 = __VLS_asFunctionalComponent1(__VLS_179, new __VLS_179({
        ...{ 'onClick': {} },
    }));
    const __VLS_181 = __VLS_180({
        ...{ 'onClick': {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_180));
    let __VLS_184;
    const __VLS_185 = ({ click: {} },
        { onClick: (__VLS_ctx.handleEditCancel) });
    const { default: __VLS_186 } = __VLS_182.slots;
    // @ts-ignore
    [handleEditCancel,];
    var __VLS_182;
    var __VLS_183;
    // @ts-ignore
    [];
    var __VLS_170;
    // @ts-ignore
    [];
    var __VLS_164;
    // @ts-ignore
    [];
    var __VLS_132;
    var __VLS_133;
    // @ts-ignore
    [];
    var __VLS_94;
    var __VLS_95;
    let __VLS_187;
    /** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
    aModal;
    // @ts-ignore
    const __VLS_188 = __VLS_asFunctionalComponent1(__VLS_187, new __VLS_187({
        open: (__VLS_ctx.memberModalOpen),
        title: (`项目成员 - ${__VLS_ctx.memberApp?.appName}`),
        footer: (null),
        width: "560px",
    }));
    const __VLS_189 = __VLS_188({
        open: (__VLS_ctx.memberModalOpen),
        title: (`项目成员 - ${__VLS_ctx.memberApp?.appName}`),
        footer: (null),
        width: "560px",
    }, ...__VLS_functionalComponentArgsRest(__VLS_188));
    const { default: __VLS_192 } = __VLS_190.slots;
    if (__VLS_ctx.isMemberManager) {
        __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
            ...{ class: "member-invite" },
        });
        /** @type {__VLS_StyleScopedClasses['member-invite']} */ ;
        let __VLS_193;
        /** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
        aInput;
        // @ts-ignore
        const __VLS_194 = __VLS_asFunctionalComponent1(__VLS_193, new __VLS_193({
            ...{ 'onPressEnter': {} },
            value: (__VLS_ctx.inviteAccount),
            placeholder: "输入用户账号邀请成员",
        }));
        const __VLS_195 = __VLS_194({
            ...{ 'onPressEnter': {} },
            value: (__VLS_ctx.inviteAccount),
            placeholder: "输入用户账号邀请成员",
        }, ...__VLS_functionalComponentArgsRest(__VLS_194));
        let __VLS_198;
        const __VLS_199 = ({ pressEnter: {} },
            { onPressEnter: (__VLS_ctx.handleInviteMember) });
        var __VLS_196;
        var __VLS_197;
        let __VLS_200;
        /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
        aButton;
        // @ts-ignore
        const __VLS_201 = __VLS_asFunctionalComponent1(__VLS_200, new __VLS_200({
            ...{ 'onClick': {} },
            type: "primary",
            loading: (__VLS_ctx.addingMember),
        }));
        const __VLS_202 = __VLS_201({
            ...{ 'onClick': {} },
            type: "primary",
            loading: (__VLS_ctx.addingMember),
        }, ...__VLS_functionalComponentArgsRest(__VLS_201));
        let __VLS_205;
        const __VLS_206 = ({ click: {} },
            { onClick: (__VLS_ctx.handleInviteMember) });
        const { default: __VLS_207 } = __VLS_203.slots;
        {
            const { icon: __VLS_208 } = __VLS_203.slots;
            let __VLS_209;
            /** @ts-ignore @type { | typeof __VLS_components.UserAddOutlined} */
            UserAddOutlined;
            // @ts-ignore
            const __VLS_210 = __VLS_asFunctionalComponent1(__VLS_209, new __VLS_209({}));
            const __VLS_211 = __VLS_210({}, ...__VLS_functionalComponentArgsRest(__VLS_210));
            // @ts-ignore
            [memberModalOpen, memberApp, isMemberManager, inviteAccount, handleInviteMember, handleInviteMember, addingMember,];
        }
        // @ts-ignore
        [];
        var __VLS_203;
        var __VLS_204;
    }
    let __VLS_214;
    /** @ts-ignore @type { | typeof __VLS_components.aList | typeof __VLS_components.AList | typeof __VLS_components['a-list'] | typeof __VLS_components.aList | typeof __VLS_components.AList | typeof __VLS_components['a-list']} */
    aList;
    // @ts-ignore
    const __VLS_215 = __VLS_asFunctionalComponent1(__VLS_214, new __VLS_214({
        loading: (__VLS_ctx.memberLoading),
        dataSource: (__VLS_ctx.members),
        itemLayout: "horizontal",
    }));
    const __VLS_216 = __VLS_215({
        loading: (__VLS_ctx.memberLoading),
        dataSource: (__VLS_ctx.members),
        itemLayout: "horizontal",
    }, ...__VLS_functionalComponentArgsRest(__VLS_215));
    const { default: __VLS_219 } = __VLS_217.slots;
    {
        const { renderItem: __VLS_220 } = __VLS_217.slots;
        const [{ item }] = __VLS_vSlot(__VLS_220);
        let __VLS_221;
        /** @ts-ignore @type { | typeof __VLS_components.aListItem | typeof __VLS_components.AListItem | typeof __VLS_components['a-list-item'] | typeof __VLS_components.aListItem | typeof __VLS_components.AListItem | typeof __VLS_components['a-list-item']} */
        aListItem;
        // @ts-ignore
        const __VLS_222 = __VLS_asFunctionalComponent1(__VLS_221, new __VLS_221({}));
        const __VLS_223 = __VLS_222({}, ...__VLS_functionalComponentArgsRest(__VLS_222));
        const { default: __VLS_226 } = __VLS_224.slots;
        let __VLS_227;
        /** @ts-ignore @type { | typeof __VLS_components.aListItemMeta | typeof __VLS_components.AListItemMeta | typeof __VLS_components['a-list-item-meta'] | typeof __VLS_components.aListItemMeta | typeof __VLS_components.AListItemMeta | typeof __VLS_components['a-list-item-meta']} */
        aListItemMeta;
        // @ts-ignore
        const __VLS_228 = __VLS_asFunctionalComponent1(__VLS_227, new __VLS_227({}));
        const __VLS_229 = __VLS_228({}, ...__VLS_functionalComponentArgsRest(__VLS_228));
        const { default: __VLS_232 } = __VLS_230.slots;
        {
            const { title: __VLS_233 } = __VLS_230.slots;
            (item.userName || item.userAccount || `用户 ${item.userId}`);
            // @ts-ignore
            [memberLoading, members,];
        }
        {
            const { description: __VLS_234 } = __VLS_230.slots;
            (item.userAccount || '-');
            (__VLS_ctx.formatTime(item.createTime, 'YYYY-MM-DD'));
            // @ts-ignore
            [formatTime,];
        }
        // @ts-ignore
        [];
        var __VLS_230;
        {
            const { actions: __VLS_235 } = __VLS_224.slots;
            if (__VLS_ctx.isMemberManager) {
                let __VLS_236;
                /** @ts-ignore @type { | typeof __VLS_components.aPopconfirm | typeof __VLS_components.APopconfirm | typeof __VLS_components['a-popconfirm'] | typeof __VLS_components.aPopconfirm | typeof __VLS_components.APopconfirm | typeof __VLS_components['a-popconfirm']} */
                aPopconfirm;
                // @ts-ignore
                const __VLS_237 = __VLS_asFunctionalComponent1(__VLS_236, new __VLS_236({
                    ...{ 'onConfirm': {} },
                    title: "确定移除该成员？",
                }));
                const __VLS_238 = __VLS_237({
                    ...{ 'onConfirm': {} },
                    title: "确定移除该成员？",
                }, ...__VLS_functionalComponentArgsRest(__VLS_237));
                let __VLS_241;
                const __VLS_242 = ({ confirm: {} },
                    { onConfirm: (...[$event]) => {
                            if (!!(!__VLS_ctx.isLoggedIn))
                                return;
                            if (!!(__VLS_ctx.loading))
                                return;
                            if (!!(!__VLS_ctx.hasApps))
                                return;
                            if (!(__VLS_ctx.isMemberManager))
                                return;
                            __VLS_ctx.handleRemoveMember(item.userId);
                            // @ts-ignore
                            [isMemberManager, handleRemoveMember,];
                        } });
                const { default: __VLS_243 } = __VLS_239.slots;
                let __VLS_244;
                /** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
                aButton;
                // @ts-ignore
                const __VLS_245 = __VLS_asFunctionalComponent1(__VLS_244, new __VLS_244({
                    size: "small",
                    danger: true,
                }));
                const __VLS_246 = __VLS_245({
                    size: "small",
                    danger: true,
                }, ...__VLS_functionalComponentArgsRest(__VLS_245));
                const { default: __VLS_249 } = __VLS_247.slots;
                {
                    const { icon: __VLS_250 } = __VLS_247.slots;
                    let __VLS_251;
                    /** @ts-ignore @type { | typeof __VLS_components.DeleteOutlined} */
                    DeleteOutlined;
                    // @ts-ignore
                    const __VLS_252 = __VLS_asFunctionalComponent1(__VLS_251, new __VLS_251({}));
                    const __VLS_253 = __VLS_252({}, ...__VLS_functionalComponentArgsRest(__VLS_252));
                    // @ts-ignore
                    [];
                }
                // @ts-ignore
                [];
                var __VLS_247;
                // @ts-ignore
                [];
                var __VLS_239;
                var __VLS_240;
            }
            // @ts-ignore
            [];
        }
        // @ts-ignore
        [];
        var __VLS_224;
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_217;
    // @ts-ignore
    [];
    var __VLS_190;
    let __VLS_256;
    /** @ts-ignore @type { | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card'] | typeof __VLS_components.aCard | typeof __VLS_components.ACard | typeof __VLS_components['a-card']} */
    aCard;
    // @ts-ignore
    const __VLS_257 = __VLS_asFunctionalComponent1(__VLS_256, new __VLS_256({
        ...{ 'onClick': {} },
        ...{ class: "app-card add-app-card" },
        bordered: (false),
    }));
    const __VLS_258 = __VLS_257({
        ...{ 'onClick': {} },
        ...{ class: "app-card add-app-card" },
        bordered: (false),
    }, ...__VLS_functionalComponentArgsRest(__VLS_257));
    let __VLS_261;
    const __VLS_262 = ({ click: {} },
        { onClick: (...[$event]) => {
                if (!!(!__VLS_ctx.isLoggedIn))
                    return;
                if (!!(__VLS_ctx.loading))
                    return;
                if (!!(!__VLS_ctx.hasApps))
                    return;
                __VLS_ctx.openCreateChat();
                // @ts-ignore
                [openCreateChat,];
            } });
    /** @type {__VLS_StyleScopedClasses['app-card']} */ ;
    /** @type {__VLS_StyleScopedClasses['add-app-card']} */ ;
    const { default: __VLS_263 } = __VLS_259.slots;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "add-app-content" },
    });
    /** @type {__VLS_StyleScopedClasses['add-app-content']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "add-app-icon" },
    });
    /** @type {__VLS_StyleScopedClasses['add-app-icon']} */ ;
    let __VLS_264;
    /** @ts-ignore @type { | typeof __VLS_components.PlusOutlined} */
    PlusOutlined;
    // @ts-ignore
    const __VLS_265 = __VLS_asFunctionalComponent1(__VLS_264, new __VLS_264({}));
    const __VLS_266 = __VLS_265({}, ...__VLS_functionalComponentArgsRest(__VLS_265));
    __VLS_asFunctionalElement1(__VLS_intrinsics.h3, __VLS_intrinsics.h3)({
        ...{ class: "add-app-title" },
    });
    /** @type {__VLS_StyleScopedClasses['add-app-title']} */ ;
    // @ts-ignore
    [];
    var __VLS_259;
    var __VLS_260;
}
let __VLS_269;
/** @ts-ignore @type { | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal'] | typeof __VLS_components.aModal | typeof __VLS_components.AModal | typeof __VLS_components['a-modal']} */
aModal;
// @ts-ignore
const __VLS_270 = __VLS_asFunctionalComponent1(__VLS_269, new __VLS_269({
    ...{ 'onOk': {} },
    open: (__VLS_ctx.createModalVisible),
    title: "新建项目",
    confirmLoading: (__VLS_ctx.isCreating),
    okText: "创建项目",
    cancelText: "取消",
    maskClosable: (false),
}));
const __VLS_271 = __VLS_270({
    ...{ 'onOk': {} },
    open: (__VLS_ctx.createModalVisible),
    title: "新建项目",
    confirmLoading: (__VLS_ctx.isCreating),
    okText: "创建项目",
    cancelText: "取消",
    maskClosable: (false),
}, ...__VLS_functionalComponentArgsRest(__VLS_270));
let __VLS_274;
const __VLS_275 = ({ ok: {} },
    { onOk: (__VLS_ctx.handleCreateProject) });
const { default: __VLS_276 } = __VLS_272.slots;
let __VLS_277;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_278 = __VLS_asFunctionalComponent1(__VLS_277, new __VLS_277({
    layout: "vertical",
    ...{ style: {} },
}));
const __VLS_279 = __VLS_278({
    layout: "vertical",
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_278));
const { default: __VLS_282 } = __VLS_280.slots;
let __VLS_283;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_284 = __VLS_asFunctionalComponent1(__VLS_283, new __VLS_283({
    label: "项目名称",
    required: true,
}));
const __VLS_285 = __VLS_284({
    label: "项目名称",
    required: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_284));
const { default: __VLS_288 } = __VLS_286.slots;
let __VLS_289;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_290 = __VLS_asFunctionalComponent1(__VLS_289, new __VLS_289({
    value: (__VLS_ctx.createForm.appName),
    placeholder: "请输入项目名称",
    maxlength: (20),
    showCount: true,
}));
const __VLS_291 = __VLS_290({
    value: (__VLS_ctx.createForm.appName),
    placeholder: "请输入项目名称",
    maxlength: (20),
    showCount: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_290));
// @ts-ignore
[createModalVisible, isCreating, handleCreateProject, createForm,];
var __VLS_286;
let __VLS_294;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_295 = __VLS_asFunctionalComponent1(__VLS_294, new __VLS_294({
    label: "项目库",
}));
const __VLS_296 = __VLS_295({
    label: "项目库",
}, ...__VLS_functionalComponentArgsRest(__VLS_295));
const { default: __VLS_299 } = __VLS_297.slots;
let __VLS_300;
/** @ts-ignore @type { | typeof __VLS_components.aInputGroup | typeof __VLS_components.AInputGroup | typeof __VLS_components['a-input-group'] | typeof __VLS_components.aInputGroup | typeof __VLS_components.AInputGroup | typeof __VLS_components['a-input-group']} */
aInputGroup;
// @ts-ignore
const __VLS_301 = __VLS_asFunctionalComponent1(__VLS_300, new __VLS_300({
    compact: true,
}));
const __VLS_302 = __VLS_301({
    compact: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_301));
const { default: __VLS_305 } = __VLS_303.slots;
let __VLS_306;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_307 = __VLS_asFunctionalComponent1(__VLS_306, new __VLS_306({
    ...{ style: {} },
    value: "prj_",
    disabled: true,
}));
const __VLS_308 = __VLS_307({
    ...{ style: {} },
    value: "prj_",
    disabled: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_307));
let __VLS_311;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_312 = __VLS_asFunctionalComponent1(__VLS_311, new __VLS_311({
    value: (__VLS_ctx.createForm.dbName),
    placeholder: "自定义库名后缀",
    ...{ style: {} },
}));
const __VLS_313 = __VLS_312({
    value: (__VLS_ctx.createForm.dbName),
    placeholder: "自定义库名后缀",
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_312));
// @ts-ignore
[createForm,];
var __VLS_303;
// @ts-ignore
[];
var __VLS_297;
// @ts-ignore
[];
var __VLS_280;
// @ts-ignore
[];
var __VLS_272;
var __VLS_273;
// @ts-ignore
var __VLS_137 = __VLS_136;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
