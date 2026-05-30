<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowRightOutlined, EditOutlined, MessageOutlined, PlusOutlined, RocketOutlined } from '@ant-design/icons-vue'
import { addApp, listMyAppVoByPage } from '@/api/appController'
import { useLoginUserStore } from '@/stores/loginUser'
import { formatRelativeTime, formatTime } from '@/utils/time'

const router = useRouter()
const loginUserStore = useLoginUserStore()

const loading = ref(false)
const apps = ref<API.AppVO[]>([])
const total = ref(0)

const promptExamples = [
  '生成一个销售数据看板，按区域和时间维度展示营收、利润和 Top 10 产品',
  '做一个 A/B 实验分析报告页，展示实验组对照组的核心指标对比和置信区间',
  '帮我搭一个 API 调用量监控面板，展示 QPS、P99 延迟和错误率趋势',
]

const isLoggedIn = computed(() => Boolean(loginUserStore.loginUser.id))
const hasApps = computed(() => apps.value.length > 0)

const fetchMyApps = async () => {
  if (!isLoggedIn.value) {
    apps.value = []
    total.value = 0
    return
  }
  loading.value = true
  try {
    const res = await listMyAppVoByPage({
      pageNum: 1,
      pageSize: 12,
      sortField: 'updateTime',
      sortOrder: 'descend',
    })
    if (res.data.code === 0 && res.data.data) {
      apps.value = res.data.data.records ?? []
      total.value = res.data.data.totalRow ?? 0
      return
    }
    message.error(`获取项目列表失败，${res.data.message ?? '请稍后重试'}`)
  } catch (error) {
    console.error('获取项目列表失败:', error)
    message.error('获取项目列表失败')
  } finally {
    loading.value = false
  }
}

const createModalVisible = ref(false)
const createForm = ref({ appName: '', dbName: '', initPrompt: '' })
const isCreating = ref(false)
const pendingPromptExample = ref('')

const openCreateChat = (prompt?: string) => {
  if (!isLoggedIn.value) {
    router.push('/user/login')
    return
  }
  createForm.value = { appName: '', dbName: '', initPrompt: prompt || '' }
  pendingPromptExample.value = prompt || ''
  createModalVisible.value = true
}

const handleCreateProject = async () => {
  if (!createForm.value.appName.trim()) {
    message.warning('请输入项目名称')
    return
  }
  if (createForm.value.appName.trim().length > 20) {
    message.warning('项目名称不能超过20个字')
    return
  }
  if (!createForm.value.initPrompt.trim()) {
    message.warning('请输入项目需求描述')
    return
  }
  isCreating.value = true
  try {
    const payload: Record<string, any> = {
      appName: createForm.value.appName.trim(),
      initPrompt: createForm.value.initPrompt.trim(),
    }
    const dbName = createForm.value.dbName.trim()
    if (dbName) {
      payload.dbName = 'prj_' + dbName
    }
    const createRes = await addApp(payload)
    if (createRes.data.code !== 0 || !createRes.data.data) {
      message.error(`创建项目失败，${createRes.data.message ?? '请稍后重试'}`)
      return
    }
    const createdAppId = String(createRes.data.data)
    createModalVisible.value = false
    router.push(`/app/chat/${createdAppId}`)
  } catch (error) {
    console.error('创建项目失败:', error)
    message.error('创建项目失败')
  } finally {
    isCreating.value = false
  }
}

const selectPromptExample = (example: string) => {
  openCreateChat(example)
}

const goToChat = (appId?: number) => {
  if (!appId) return
  router.push(`/app/chat/${appId}`)
}

const goToEdit = (appId?: number) => {
  if (!appId) return
  router.push(`/app/edit/${appId}`)
}

watch(
  () => loginUserStore.loginUser.id,
  () => { fetchMyApps() },
  { immediate: true }
)
</script>

<template>
  <div id="homePage">
    <!-- Welcome / Guest State -->
    <section v-if="!isLoggedIn" class="welcome-section">
      <div class="welcome-card">
        <div class="welcome-icon">
          <RocketOutlined />
        </div>
        <h2 class="section-title">登录后，这里就是你的<span class="highlight">项目工作台</span></h2>
        <p class="section-description">
          首页会根据你的项目状态展示内容。没有项目时只提示如何创建，有项目后只展示项目卡片列表。
        </p>
        <div class="welcome-actions">
          <a-button type="primary" size="large" @click="router.push('/user/login')" class="cta-btn">
            进入平台
            <template #icon><ArrowRightOutlined /></template>
          </a-button>
        </div>
      </div>
    </section>

    <!-- Loading -->
    <section v-else-if="loading" class="loading-panel">
      <a-spin size="large" />
    </section>

    <!-- No Apps / Create Guide -->
    <section v-else-if="!hasApps" class="create-guide-panel">
      <div class="create-guide-card">
        <div class="guide-icon">
          <PlusOutlined />
        </div>
        <h2 class="section-title">你还没有<span class="highlight">项目</span></h2>
        <p class="section-description">
          点击下方按钮直接进入生成对话。首次发送需求时，系统会自动创建项目并开始生成。
        </p>
        <div class="guide-actions">
          <a-button type="primary" size="large" @click="openCreateChat()" class="cta-btn">
            <template #icon><PlusOutlined /></template>
            创建项目
          </a-button>
        </div>
        <div class="example-list">
          <span class="example-label">可以这样描述你的项目</span>
          <div class="example-tags">
            <span
              v-for="example in promptExamples"
              :key="example"
              class="example-tag"
              @click="selectPromptExample(example)"
            >
              {{ example }}
            </span>
          </div>
        </div>
      </div>
    </section>

    <!-- Apps Grid -->
    <section v-else class="apps-panel">
      <div class="section-header">
        <div>
          <p class="section-eyebrow">我的项目</p>
          <h2 class="section-title">项目列表</h2>
          <p class="section-description">当前共 {{ total }} 个项目</p>
        </div>
      </div>

      <div class="app-grid">
        <a-card v-for="(app, idx) in apps" :key="app.id" class="app-card" :bordered="false"
          :style="{ animationDelay: `${idx * 60}ms` }">
          <div class="cover-shell">
            <img v-if="app.cover" :src="app.cover" :alt="app.appName" class="cover-image" />
            <div v-else class="cover-fallback">
              <RocketOutlined />
            </div>
          </div>

          <div class="app-body">
            <div class="app-heading">
              <h3 class="app-name">{{ app.appName || '未命名项目' }}</h3>
            </div>

            <p class="app-prompt">{{ app.initPrompt || '暂无项目描述' }}</p>

            <div class="app-meta">
              <span>创建时间：{{ formatTime(app.createTime, 'YYYY-MM-DD') || '未知' }}</span>
              <span>更新时间：{{ formatRelativeTime(app.updateTime) || '刚刚' }}</span>
            </div>

            <div class="app-actions">
              <a-button type="primary" size="small" @click="goToChat(app.id)">
                <template #icon><MessageOutlined /></template>
                继续生成
              </a-button>
              <a-button size="small" @click="goToEdit(app.id)">
                <template #icon><EditOutlined /></template>
                编辑信息
              </a-button>
            </div>
          </div>
        </a-card>

        <a-card class="app-card add-app-card" :bordered="false" @click="openCreateChat()">
          <div class="add-app-content">
            <div class="add-app-icon">
              <PlusOutlined />
            </div>
            <h3 class="add-app-title">添加项目</h3>
            <p class="add-app-description">新建一个项目，继续开始新的生成任务</p>
          </div>
        </a-card>
      </div>
    </section>

    <a-modal
      v-model:open="createModalVisible"
      title="新建项目"
      :confirm-loading="isCreating"
      @ok="handleCreateProject"
      ok-text="创建项目"
      cancel-text="取消"
      :mask-closable="false"
    >
      <a-form layout="vertical" style="margin-top: 16px">
        <a-form-item label="项目名称" required>
          <a-input
            v-model:value="createForm.appName"
            placeholder="请输入项目名称"
            :maxlength="20"
            show-count
          />
        </a-form-item>
        <a-form-item label="项目库">
          <a-input-group compact>
            <a-input
              style="width: 60px; background: var(--bg-subtle); color: var(--text-muted); text-align: right;"
              value="prj_"
              disabled
            />
            <a-input
              v-model:value="createForm.dbName"
              placeholder="自定义库名后缀"
              style="width: calc(100% - 60px)"
            />
          </a-input-group>
        </a-form-item>
        <a-form-item label="项目需求描述" required>
          <a-textarea
            v-model:value="createForm.initPrompt"
            placeholder="描述您的项目需求..."
            :rows="4"
            :maxlength="1000"
            show-count
          />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<style scoped>
#homePage {
  min-height: calc(100vh - 64px);
  padding: 48px 32px;
}

.welcome-section,
.apps-panel,
.create-guide-panel {
  max-width: 1240px;
  margin: 0 auto 28px;
}

.create-guide-panel {
  display: flex;
  min-height: calc(100vh - 64px - 140px);
}

/* Welcome Card */
.welcome-card {
  text-align: center;
  padding: 64px 36px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.welcome-icon {
  font-size: 40px;
  color: var(--accent-primary);
  margin-bottom: 20px;
}

/* Create Guide Card */
.create-guide-card {
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  text-align: center;
  padding: 48px 36px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  box-shadow: var(--shadow-card);
}

.guide-icon {
  font-size: 36px;
  color: var(--accent-primary);
  margin-bottom: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 12px;
  background: var(--accent-primary-subtle);
}

/* Section Eyebrow */
.section-eyebrow {
  margin: 0 0 4px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--accent-primary);
}

/* Section Description */
.section-description {
  margin: 12px 0 0;
  max-width: 560px;
  font-size: 15px;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* Loading */
.loading-panel {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 280px;
}

/* Section Header */
.section-header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  margin-bottom: 24px;
}

/* Section Title */
.section-title {
  margin: 0;
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.02em;
  line-height: 1.2;
  color: var(--text-primary);
}

.highlight {
  color: var(--accent-primary);
}

/* Actions */
.welcome-actions,
.guide-actions {
  display: flex;
  justify-content: center;
  margin-top: 24px;
}

.cta-btn {
  height: 44px;
  padding: 0 28px;
  font-size: 15px;
  font-weight: 600;
}

/* App Grid */
.app-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 20px;
}

/* App Card */
.app-card {
  overflow: hidden;
  border-radius: 8px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  box-shadow: var(--shadow-card);
  transition: all 0.2s var(--ease-out);
  animation: fade-in-up 0.4s var(--ease-out) both;
}

.app-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-card-hover);
  border-color: var(--border-strong);
}

/* Add App Card */
.add-app-card {
  cursor: pointer;
  min-height: 380px;
  border: 1px dashed var(--border-strong);
  background: var(--bg-page);
  transition: all 0.2s var(--ease-out);
}
.add-app-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-card-hover);
  border-color: var(--accent-primary);
}

.add-app-content {
  height: 100%;
  min-height: 380px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  text-align: center;
}

.add-app-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 12px;
  background: var(--accent-primary-subtle);
  font-size: 24px;
  color: var(--accent-primary);
  transition: all 0.2s var(--ease-out);
}
.add-app-card:hover .add-app-icon {
  background: var(--accent-primary-light);
}

.add-app-title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}

.add-app-description {
  max-width: 220px;
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-secondary);
}

/* Cover Shell */
.cover-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 180px;
  background: var(--bg-subtle);
  overflow: hidden;
}

.cover-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  font-size: 48px;
  color: var(--text-muted);
  opacity: 0.5;
}

/* App Body */
.app-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px;
}

.app-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.app-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  line-height: 1.35;
  color: var(--text-primary);
}

.app-prompt {
  min-height: 44px;
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
  display: -webkit-box;
  overflow: hidden;
  line-clamp: 2;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.app-meta {
  display: grid;
  gap: 4px;
  font-size: 12px;
  color: var(--text-muted);
}

.app-actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

/* Example Tags */
.example-list {
  display: grid;
  gap: 10px;
  margin-top: 28px;
}

.example-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.example-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.example-tag {
  cursor: pointer;
  padding: 6px 14px;
  border-radius: 6px;
  font-size: 13px;
  color: var(--text-secondary);
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  transition: all 0.15s var(--ease-out);
}
.example-tag:hover {
  color: var(--accent-primary);
  background: var(--accent-primary-subtle);
  border-color: var(--accent-primary);
}

/* Override Ant Design card padding */
.app-card :deep(.ant-card-body) {
  padding: 16px;
  background: transparent;
}

@media (max-width: 960px) {
  #homePage {
    padding: 24px 20px;
  }
  .create-guide-panel {
    min-height: calc(100vh - 64px - 68px);
  }
  .section-header {
    flex-direction: column;
    align-items: stretch;
  }
  .section-title {
    font-size: 24px;
  }
}

@media (max-width: 640px) {
  .welcome-card,
  .create-guide-card {
    padding: 32px 20px;
  }
  .app-actions {
    flex-direction: column;
  }
  .app-grid {
    grid-template-columns: 1fr;
  }
}
</style>
