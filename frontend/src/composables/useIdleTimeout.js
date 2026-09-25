import { onMounted, onUnmounted, ref } from 'vue';
const IDLE_TIMEOUT_MS_DEFAULT = 30 * 60 * 1000;
const ACTIVITY_EVENTS = [
    'mousemove',
    'keydown',
    'click',
    'scroll',
    'touchstart',
];
/**
 * 全局用户空闲检测组合式函数。
 * 超过 timeoutMs 无操作后调用 onTimeout 回调。
 * 在组件卸载时自动清理定时器和事件监听。
 */
export function useIdleTimeout(onTimeout, options = {}) {
    const { timeoutMs = IDLE_TIMEOUT_MS_DEFAULT } = options;
    const idle = ref(false);
    let timer = null;
    const resetTimer = () => {
        idle.value = false;
        if (timer !== null)
            clearTimeout(timer);
        timer = setTimeout(() => {
            idle.value = true;
            onTimeout();
        }, timeoutMs);
    };
    const handleActivity = () => {
        resetTimer();
    };
    onMounted(() => {
        for (const event of ACTIVITY_EVENTS) {
            document.addEventListener(event, handleActivity, { passive: true });
        }
        resetTimer();
    });
    onUnmounted(() => {
        if (timer !== null)
            clearTimeout(timer);
        for (const event of ACTIVITY_EVENTS) {
            document.removeEventListener(event, handleActivity);
        }
    });
    return { idle };
}
