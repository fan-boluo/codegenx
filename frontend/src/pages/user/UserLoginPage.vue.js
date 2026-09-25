import { reactive } from 'vue';
import { userLogin } from '@/api/userController.ts';
import { useLoginUserStore } from '@/stores/loginUser.ts';
import { useRoute, useRouter } from 'vue-router';
import { message } from 'ant-design-vue';
const formState = reactive({
    userAccount: '',
    userPassword: '',
});
const router = useRouter();
const route = useRoute();
const loginUserStore = useLoginUserStore();
const handleSubmit = async (values) => {
    const res = await userLogin(values);
    console.log(res);
    if (res.data.code === 0 && res.data.data) {
        const token = res.data.data;
        localStorage.setItem('token', token);
        await loginUserStore.fetchLoginUser();
        message.success('登录成功');
        const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/';
        router.replace(redirect);
    }
    else {
        message.error('登录失败，' + res.data.message);
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
    id: "userLoginPage",
});
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "login-card" },
});
/** @type {__VLS_StyleScopedClasses['login-card']} */ ;
__VLS_asFunctionalElement1(__VLS_intrinsics.h2, __VLS_intrinsics.h2)({
    ...{ class: "title" },
});
/** @type {__VLS_StyleScopedClasses['title']} */ ;
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form'] | typeof __VLS_components.aForm | typeof __VLS_components.AForm | typeof __VLS_components['a-form']} */
aForm;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formState),
    name: "basic",
    autocomplete: "off",
}));
const __VLS_2 = __VLS_1({
    ...{ 'onFinish': {} },
    model: (__VLS_ctx.formState),
    name: "basic",
    autocomplete: "off",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
let __VLS_5;
const __VLS_6 = ({ finish: {} },
    { onFinish: (__VLS_ctx.handleSubmit) });
const { default: __VLS_7 } = __VLS_3.slots;
let __VLS_8;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_9 = __VLS_asFunctionalComponent1(__VLS_8, new __VLS_8({
    name: "userAccount",
    rules: ([{ required: true, message: '请输入账号' }]),
}));
const __VLS_10 = __VLS_9({
    name: "userAccount",
    rules: ([{ required: true, message: '请输入账号' }]),
}, ...__VLS_functionalComponentArgsRest(__VLS_9));
const { default: __VLS_13 } = __VLS_11.slots;
let __VLS_14;
/** @ts-ignore @type { | typeof __VLS_components.aInput | typeof __VLS_components.AInput | typeof __VLS_components['a-input']} */
aInput;
// @ts-ignore
const __VLS_15 = __VLS_asFunctionalComponent1(__VLS_14, new __VLS_14({
    value: (__VLS_ctx.formState.userAccount),
    placeholder: "请输入账号",
    size: "large",
}));
const __VLS_16 = __VLS_15({
    value: (__VLS_ctx.formState.userAccount),
    placeholder: "请输入账号",
    size: "large",
}, ...__VLS_functionalComponentArgsRest(__VLS_15));
// @ts-ignore
[formState, formState, handleSubmit,];
var __VLS_11;
let __VLS_19;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_20 = __VLS_asFunctionalComponent1(__VLS_19, new __VLS_19({
    name: "userPassword",
    rules: ([
        { required: true, message: '请输入密码' },
        { min: 8, message: '密码长度不能小于 8 位' },
    ]),
}));
const __VLS_21 = __VLS_20({
    name: "userPassword",
    rules: ([
        { required: true, message: '请输入密码' },
        { min: 8, message: '密码长度不能小于 8 位' },
    ]),
}, ...__VLS_functionalComponentArgsRest(__VLS_20));
const { default: __VLS_24 } = __VLS_22.slots;
let __VLS_25;
/** @ts-ignore @type { | typeof __VLS_components.aInputPassword | typeof __VLS_components.AInputPassword | typeof __VLS_components['a-input-password']} */
aInputPassword;
// @ts-ignore
const __VLS_26 = __VLS_asFunctionalComponent1(__VLS_25, new __VLS_25({
    value: (__VLS_ctx.formState.userPassword),
    placeholder: "请输入密码",
    size: "large",
}));
const __VLS_27 = __VLS_26({
    value: (__VLS_ctx.formState.userPassword),
    placeholder: "请输入密码",
    size: "large",
}, ...__VLS_functionalComponentArgsRest(__VLS_26));
// @ts-ignore
[formState,];
var __VLS_22;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "tips" },
});
/** @type {__VLS_StyleScopedClasses['tips']} */ ;
let __VLS_30;
/** @ts-ignore @type { | typeof __VLS_components.RouterLink | typeof __VLS_components.RouterLink} */
RouterLink;
// @ts-ignore
const __VLS_31 = __VLS_asFunctionalComponent1(__VLS_30, new __VLS_30({
    to: "/user/register",
}));
const __VLS_32 = __VLS_31({
    to: "/user/register",
}, ...__VLS_functionalComponentArgsRest(__VLS_31));
const { default: __VLS_35 } = __VLS_33.slots;
// @ts-ignore
[];
var __VLS_33;
let __VLS_36;
/** @ts-ignore @type { | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item'] | typeof __VLS_components.aFormItem | typeof __VLS_components.AFormItem | typeof __VLS_components['a-form-item']} */
aFormItem;
// @ts-ignore
const __VLS_37 = __VLS_asFunctionalComponent1(__VLS_36, new __VLS_36({}));
const __VLS_38 = __VLS_37({}, ...__VLS_functionalComponentArgsRest(__VLS_37));
const { default: __VLS_41 } = __VLS_39.slots;
let __VLS_42;
/** @ts-ignore @type { | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button'] | typeof __VLS_components.aButton | typeof __VLS_components.AButton | typeof __VLS_components['a-button']} */
aButton;
// @ts-ignore
const __VLS_43 = __VLS_asFunctionalComponent1(__VLS_42, new __VLS_42({
    type: "primary",
    htmlType: "submit",
    size: "large",
    ...{ style: {} },
}));
const __VLS_44 = __VLS_43({
    type: "primary",
    htmlType: "submit",
    size: "large",
    ...{ style: {} },
}, ...__VLS_functionalComponentArgsRest(__VLS_43));
const { default: __VLS_47 } = __VLS_45.slots;
// @ts-ignore
[];
var __VLS_45;
// @ts-ignore
[];
var __VLS_39;
// @ts-ignore
[];
var __VLS_3;
var __VLS_4;
// @ts-ignore
[];
const __VLS_export = (await import('vue')).defineComponent({});
export default {};
