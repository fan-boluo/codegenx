<template>
  <a-modal
    v-model:open="visible"
    title="项目详情"
    :footer="null"
    width="480px"
    closable
  >
    <div class="app-detail-content">
      <div class="app-basic-info">
        <div class="info-item">
          <span class="info-label">创建者</span>
          <span class="info-value">{{ app?.userName || '未知用户' }}</span>
        </div>
        <div class="info-divider"></div>
        <div class="info-item">
          <span class="info-label">创建时间</span>
          <span class="info-value">{{ formatTime(app?.createTime) }}</span>
        </div>
      </div>

    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatTime } from '@/utils/time'

interface Props {
  open: boolean
  app?: API.AppVO
  showActions?: boolean
}

interface Emits {
  (e: 'update:open', value: boolean): void
  (e: 'edit'): void
  (e: 'delete'): void
}

const props = withDefaults(defineProps<Props>(), {
  showActions: false,
})

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
})

</script>

<style scoped>
.app-detail-content {
  padding: 4px 0;
}

.app-basic-info {
  margin-bottom: 20px;
  background: var(--bg-subtle);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 16px;
}

.info-item {
  display: flex;
  align-items: center;
  padding: 6px 0;
}

.info-divider {
  height: 1px;
  background: var(--border-default);
  margin: 8px 0;
}

.info-label {
  width: 80px;
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.02em;
  text-transform: uppercase;
  flex-shrink: 0;
}

.info-value {
  color: var(--text-primary);
  font-size: 14px;
}

</style>
