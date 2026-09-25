const __VLS_props = withDefaults(defineProps(), {
    size: 'default',
    showName: true,
});
const __VLS_defaults = {
    size: 'default',
    showName: true,
};
const __VLS_ctx = {
    ...{},
    ...{},
    ...{},
};
let __VLS_components;
let __VLS_intrinsics;
let __VLS_directives;
__VLS_asFunctionalElement1(__VLS_intrinsics.div, __VLS_intrinsics.div)({
    ...{ class: "user-info" },
});
/** @type {__VLS_StyleScopedClasses['user-info']} */ ;
let __VLS_0;
/** @ts-ignore @type { | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar'] | typeof __VLS_components.aAvatar | typeof __VLS_components.AAvatar | typeof __VLS_components['a-avatar']} */
aAvatar;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent1(__VLS_0, new __VLS_0({
    src: (__VLS_ctx.user?.userAvatar || undefined),
    size: (__VLS_ctx.size),
}));
const __VLS_2 = __VLS_1({
    src: (__VLS_ctx.user?.userAvatar || undefined),
    size: (__VLS_ctx.size),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
const { default: __VLS_5 } = __VLS_3.slots;
(__VLS_ctx.user?.userName?.charAt(0) || 'U');
// @ts-ignore
[user, user, size,];
var __VLS_3;
if (__VLS_ctx.showName) {
    __VLS_asFunctionalElement1(__VLS_intrinsics.span, __VLS_intrinsics.span)({
        ...{ class: "user-name" },
    });
    /** @type {__VLS_StyleScopedClasses['user-name']} */ ;
    (__VLS_ctx.user?.userName || '未知用户');
}
// @ts-ignore
[user, showName,];
const __VLS_export = (await import('vue')).defineComponent({
    __defaults: __VLS_defaults,
    __typeProps: {},
});
export default {};
