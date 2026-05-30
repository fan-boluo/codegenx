<template>
  <a-modal
    v-model:open="visible"
    title="项目详情"
    :footer="null"
    width="480px"
  >
    <div class="app-detail-content">
      <div class="app-basic-info">
        <div class="info-item">
          <span class="info-label">创建者</span>
          <UserInfo :user="app?.user" size="small" />
        </div>
        <div class="info-divider"></div>
        <div class="info-item">
          <span class="info-label">创建时间</span>
          <span class="info-value">{{ formatTime(app?.createTime) }}</span>
        </div>
      </div>

      <div v-if="showActions" class="app-actions">
        <a-space>
          <a-button type="primary" @click="handleEdit">
            <template #icon><EditOutlined /></template>
            修改
          </a-button>
          <a-popconfirm
            title="确定要删除这个项目吗？"
            @confirm="handleDelete"
            ok-text="确定"
            cancel-text="取消"
          >
            <a-button danger>
              <template #icon><DeleteOutlined /></template>
              删除
            </a-button>
          </a-popconfirm>
        </a-space>
      </div>
    </div>
  </a-modal>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { EditOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import UserInfo from './UserInfo.vue'
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

const emit = defineEmits<Emits>()

const visible = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value),
})

const handleEdit = () => { emit('edit') }
const handleDelete = () => { emit('delete') }
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

.app-actions {
  padding-top: 16px;
  border-top: 1px solid var(--border-default);
}
</style>
