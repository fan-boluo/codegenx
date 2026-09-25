import { computed, h, ref } from 'vue';
import { useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
import { useLoginUserStore } from '@/stores/loginUser.ts';
import { userLogout } from '@/api/userController.ts';
import { clearLocalAuth } from '@/request';
import { useIdleTimeout } from '@/composables/useIdleTimeout';
import { LogoutOutlined, HomeOutlined, AppstoreOutlined, UserOutlined, DashboardOutlined, } from '@ant-design/icons-vue';
const loginUserStore = useLoginUserStore();
const router = useRouter();
const selectedKeys = ref([]);
const originItems = [
    {
        key: '/',
        icon: () => h(HomeOutlined),
        label: '首页',
        title: '首页',
    },
    {
        key: '/admin/appManage',
        icon: () => h(AppstoreOutlined),
        label: '项目管理',
        title: '项目管理',
    },
    {
        key: '/admin/userManage',
        icon: () => h(UserOutlined),
        label: '用户管理',
        title: '用户管理',
    },
    {
        key: '/admin/monitor',
        icon: () => h(DashboardOutlined),
        label: '监控中心',
        title: '监控中心',
    },
];
const filterMenus = (items) => {
    if (!items)
        return [];
    return items.filter((item) => {
        if (!item)
            return false;
        const key = 'key' in item ? item.key : '';
        if (key.startsWith('/admin')) {
            return loginUserStore.loginUser.userRole === 'admin';
        }
        return true;
    });
};
const menuItems = computed(() => filterMenus(originItems));
const handleMenuClick = (e) => {
    const key = e.key;
    selectedKeys.value = [key];
    if (key.startsWith('/')) {
        router.push(key);
    }
};
const performIdleLogout = () => {
    if (!loginUserStore.loginUser.id)
        return;
    // 空闲超时登出也通知后端 revoke token
    userLogout().catch(() => { });
    clearLocalAuth();
    loginUserStore.setLoginUser({ userName: '未登录' });
    message.warning('长时间未操作，已自动退出登录');
    router.push('/user/login');
};
useIdleTimeout(performIdleLogout);
const doLogout = async () => {
    const res = await userLogout();
    if (res.data.code === 0) {
        clearLocalAuth();
        loginUserStore.setLoginUser({ userName: '未登录' });
        message.success('退出登录成功');
        await router.push('/user/login');
    }
    else {
        message.error('退出登录失败，' + res.data.message);
    }
};
const __VLS_ctx = {
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "global-header" },
});
/** @type {__VLS_StyleScopedClasses['global-header']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "header-left" },
});
/** @type {__VLS_StyleScopedClasses['header-left']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.a, __VLS_intrinsics.a)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.$router.push('/');
            // @ts-ignore
            [$router,];
        } },
    ...{ class: "header-logo" },
    href: "/",
});
/** @type {__VLS_StyleScopedClasses['header-logo']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
    ...{ class: "logo-text" },
});
/** @type {__VLS_StyleScopedClasses['logo-text']} */ ;
if (__VLS_ctx.loginUserStore.loginUser.id && __VLS_ctx.menuItems.length) {
    let __VLS_0;
    /** @ts-ignore @type { | typeof __VLS_components.aMenu | typeof __VLS_components.AMenu | typeof __VLS_components['a-menu']} */
    aMenu;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
        ...{ 'onClick': {} },
        selectedKeys: (__VLS_ctx.selectedKeys),
        mode: "horizontal",
        items: (__VLS_ctx.menuItems),
        ...{ class: "header-menu" },
    }));
    const __VLS_2 = __VLS_1({
        ...{ 'onClick': {} },
        selectedKeys: (__VLS_ctx.selectedKeys),
        mode: "horizontal",
        items: (__VLS_ctx.menuItems),
        ...{ class: "header-menu" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    let __VLS_5;
    const __VLS_6 = ({ click: {} },
        { onClick: (__VLS_ctx.handleMenuClick) });
    /** @type {__VLS_StyleScopedClasses['header-menu']} */ ;
    var __VLS_3;
    var __VLS_4;
}
if (__VLS_ctx.loginUserStore.loginUser.id) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "header-right" },
    });
    /** @type {__VLS_StyleScopedClasses['header-right']} */ ;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "user-info" },
    });
    /** @type {__VLS_StyleScopedClasses['user-info']} */ ;
    let __VLS_7;
    /** @ts-ignore @type { | typeof __VLS_components.aDropdown | typeof __VLS_components.ADropdown | typeof __VLS_components['a-dropdown'] | typeof __VLS_components.aDropdown | typeof __VLS_components.ADropdown | typeof __VLS_components['a-dropdown']} */
    aDropdown;
    // @ts-ignore
    const __VLS_8 = __VLS_asFunctionalComponent1(__VLS_7, new __VLS_7({}));
    const __VLS_9 = __VLS_8({}, ...__VLS_functionalComponentArgsRest(__VLS_8));
    const { default: __VLS_12 } = __VLS_10.slots;
    __VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
        ...{ class: "user-dropdown-trigger" },
    });
    /** @type {__VLS_StyleScopedClasses['user-dropdown-trigger']} */ ;
    let __VLS_13;
    /** @ts-ignore @type { | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar'] | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar']} */
    aAvatar;
    // @ts-ignore
    const __VLS_14 = __VLS_asFunctionalComponent1(__VLS_13, new __VLS_13({
        src: (__VLS_ctx.loginUserStore.loginUser.userAvatar || undefined),
        size: (28),
    }));
    const __VLS_15 = __VLS_14({
        src: (__VLS_ctx.loginUserStore.loginUser.userAvatar || undefined),
        size: (28),
    }, ...__VLS_functionalComponentArgsRest(__VLS_14));
    const { default: __VLS_18 } = __VLS_16.slots;
    (__VLS_ctx.loginUserStore.loginUser.userName?.charAt(0) || 'U');
    // @ts-ignore
    [loginUserStore, loginUserStore, loginUserStore, loginUserStore, menuItems, menuItems, selectedKeys, handleMenuClick,];
    var __VLS_16;
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "user-name" },
    });
    /** @type {__VLS_StyleScopedClasses['user-name']} */ ;
    (__VLS_ctx.loginUserStore.loginUser.userName);
    {
        const { overlay: __VLS_19 } = __VLS_10.slots;
        let __VLS_20;
        /** @ts-ignore @type { | typeof __VLS_components.aMenu | typeof __VLS_components.AMenu | typeof __VLS_components['a-menu'] | typeof __VLS_components.aMenu | typeof __VLS_components.AMenu | typeof __VLS_components['a-menu']} */
        aMenu;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent1(__VLS_20, new __VLS_20({
            ...{ 'onClick': {} },
        }));
        const __VLS_22 = __VLS_21({
            ...{ 'onClick': {} },
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
        let __VLS_25;
        const __VLS_26 = ({ click: {} },
            { onClick: (__VLS_ctx.doLogout) });
        const { default: __VLS_27 } = __VLS_23.slots;
        let __VLS_28;
        /** @ts-ignore @type { | typeof __VLS_components.aMenuItem | typeof __VLS_components.AMenuItem | typeof __VLS_components['a-menu-item'] | typeof __VLS_components.aMenuItem | typeof __VLS_components.AMenuItem | typeof __VLS_components['a-menu-item']} */
        aMenuItem;
        // @ts-ignore
        const __VLS_29 = __VLS_asFunctionalComponent1(__VLS_28, new __VLS_28({
            key: "logout",
        }));
        const __VLS_30 = __VLS_29({
            key: "logout",
        }, ...__VLS_functionalComponentArgsRest(__VLS_29));
        const { default: __VLS_33 } = __VLS_31.slots;
        let __VLS_34;
        /** @ts-ignore @type { | typeof __VLS_components.LogoutOutlined} */
        LogoutOutlined;
        // @ts-ignore
        const __VLS_35 = __VLS_asFunctionalComponent1(__VLS_34, new __VLS_34({}));
        const __VLS_36 = __VLS_35({}, ...__VLS_functionalComponentArgsRest(__VLS_35));
        __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({});
        // @ts-ignore
        [loginUserStore, doLogout,];
        var __VLS_31;
        // @ts-ignore
        [];
        var __VLS_23;
        var __VLS_24;
        // @ts-ignore
        [];
    }
    // @ts-ignore
    [];
    var __VLS_10;
}
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
