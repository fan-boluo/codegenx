<template>
  <div class="app-card" :class="{ 'app-card--featured': featured }">
    <div class="app-preview">
      <img v-if="app.cover" :src="app.cover" :alt="app.appName" />
      <div v-else class="app-placeholder">
        <RocketOutlined />
      </div>
      <div class="app-overlay">
        <a-space>
          <a-button type="primary" size="small" @click="handleViewChat">查看对话</a-button>
          <a-button v-if="app.deployKey" size="small" @click="handleViewWork">查看作品</a-button>
        </a-space>
      </div>
    </div>
    <div class="app-info">
      <div class="app-info-left">
        <a-avatar :src="app.user?.userAvatar" :size="36">
          {{ app.user?.userName?.charAt(0) || 'U' }}
        </a-avatar>
      </div>
      <div class="app-info-right">
        <h3 class="app-title">{{ app.appName || '未命名项目' }}</h3>
        <p class="app-author">
          {{ app.user?.userName || (featured ? '官方' : '未知用户') }}
        </p>
      </div>
    </div>
    <div v-if="featured" class="featured-badge">精选</div>
  </div>
</template>

<script setup lang="ts">
import { RocketOutlined } from '@ant-design/icons-vue'

interface Props {
  app: API.AppVO
  featured?: boolean
}

interface Emits {
  (e: 'view-chat', appId: string | number | undefined): void
  (e: 'view-work', app: API.AppVO): void
}

const props = withDefaults(defineProps<Props>(), {
  featured: false,
})

const emit = defineEmits<Emits>()

const handleViewChat = () => {
  emit('view-chat', props.app.id)
}

const handleViewWork = () => {
  emit('view-work', props.app)
}
</script>

<style scoped>
.app-card {
  background: var(--bg-surface);
  border-radius: var(--radius-card);
  overflow: hidden;
  border: 1px solid var(--border-light);
  box-shadow: var(--shadow-sm);
  transition: all 0.25s ease;
  cursor: pointer;
  position: relative;
}

.app-card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-hover);
  border-color: var(--accent-primary);
}

.app-card--featured {
  border-color: var(--accent-primary-light);
}

.featured-badge {
  position: absolute;
  top: 12px;
  right: 12px;
  font-size: 11px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 4px;
  background: var(--accent-primary-subtle);
  color: var(--accent-primary);
  z-index: 5;
}

.app-preview {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  position: relative;
  background: var(--bg-subtle);
}

.app-preview img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.app-placeholder {
  font-size: 48px;
  color: var(--text-muted);
  opacity: 0.4;
}

.app-overlay {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.7);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.2s var(--ease-out);
}
.app-card:hover .app-overlay {
  opacity: 1;
}

.app-info {
  padding: 14px;
  display: flex;
  align-items: center;
  gap: 10px;
}

.app-info-left {
  flex-shrink: 0;
}

.app-info-right {
  flex: 1;
  min-width: 0;
}

.app-title {
  font-size: 14px;
  font-weight: 600;
  margin: 0 0 2px;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.app-author {
  font-size: 12px;
  color: var(--text-muted);
  margin: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
