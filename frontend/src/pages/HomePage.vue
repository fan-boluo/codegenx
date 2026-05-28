<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { ArrowRightOutlined, EditOutlined, MessageOutlined, PlusOutlined, RocketOutlined } from '@ant-design/icons-vue'
import { listMyAppVoByPage } from '@/api/appController'
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
    message.error(`获取应用列表失败，${res.data.message ?? '请稍后重试'}`)
  } catch (error) {
    console.error('获取应用列表失败:', error)
    message.error('获取应用列表失败')
  } finally {
    loading.value = false
  }
}

const openCreateChat = (prompt?: string) => {
  if (!isLoggedIn.value) {
    router.push('/user/login')
    return
  }
  router.push({
    path: '/app/chat',
    query: prompt ? { prompt } : undefined,
  })
}

const selectPromptExample = (example: string) => {
  openCreateChat(example)
}

const goToChat = (appId?: number) => {
  if (!appId) {
    return
  }
  router.push(`/app/chat/${appId}`)
}

const goToEdit = (appId?: number) => {
  if (!appId) {
    return
  }
  router.push(`/app/edit/${appId}`)
}

watch(
  () => loginUserStore.loginUser.id,
  () => {
    fetchMyApps()
  },
  { immediate: true }
)
</script>

<template>
  <div id="homePage">
    <section v-if="!isLoggedIn" class="guest-panel">
      <div class="guest-card">
        <p class="section-eyebrow">Start Here</p>
        <h2 class="section-title">登录后，这里就是你的应用工作台</h2>
        <p class="section-description">
          首页会根据你的应用状态展示内容。没有应用时只提示如何创建，有应用后只展示应用卡片列表。
        </p>
        <a-button type="primary" size="large" @click="router.push('/user/login')">
          去登录
          <template #icon>
            <ArrowRightOutlined />
          </template>
        </a-button>
      </div>
    </section>

    <section v-else-if="loading" class="apps-panel loading-panel">
      <a-spin size="large" />
    </section>

    <section v-else-if="!hasApps" class="create-guide-panel">
      <div class="create-guide-card">
        <p class="section-eyebrow">Create App</p>
        <h2 class="section-title">你还没有应用</h2>
        <p class="section-description">
          点击下方按钮直接进入生成对话。首次发送需求时，系统会自动创建应用并开始生成。
        </p>
        <div class="guide-actions">
          <a-button type="primary" size="large" @click="openCreateChat()">
            <template #icon>
              <PlusOutlined />
            </template>
            创建应用
          </a-button>
        </div>
        <div class="example-list">
          <span class="example-label">可以这样描述你的应用</span>
          <div class="example-tags">
            <a-tag
              v-for="example in promptExamples"
              :key="example"
              class="example-tag"
              @click="selectPromptExample(example)"
            >
              {{ example }}
            </a-tag>
          </div>
        </div>
      </div>
    </section>

    <section v-else class="apps-panel">
      <div class="section-header">
        <div>
          <p class="section-eyebrow">My Apps</p>
          <h2 class="section-title">我的应用列表</h2>
          <p class="section-description">当前共 {{ total }} 个应用，以下以卡片形式展示。</p>
        </div>
      </div>

      <div class="app-grid">
        <a-card v-for="app in apps" :key="app.id" class="app-card" :bordered="false">
          <div class="cover-shell">
            <img v-if="app.cover" :src="app.cover" :alt="app.appName" class="cover-image" />
            <div v-else class="cover-fallback">
              <RocketOutlined />
            </div>
          </div>

          <div class="app-body">
            <div class="app-heading">
              <h3 class="app-name">{{ app.appName || '未命名应用' }}</h3>
            </div>

            <p class="app-prompt">{{ app.initPrompt || '暂无应用描述' }}</p>

            <div class="app-meta">
              <span>创建时间：{{ formatTime(app.createTime, 'YYYY-MM-DD') || '未知' }}</span>
              <span>更新时间：{{ formatRelativeTime(app.updateTime) || '刚刚' }}</span>
            </div>

            <div class="app-actions">
              <a-button type="primary" @click="goToChat(app.id)">
                <template #icon>
                  <MessageOutlined />
                </template>
                继续生成
              </a-button>
              <a-button @click="goToEdit(app.id)">
                <template #icon>
                  <EditOutlined />
                </template>
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
            <h3 class="add-app-title">添加应用</h3>
            <p class="add-app-description">新建一个应用，继续开始新的生成任务</p>
          </div>
        </a-card>
      </div>
    </section>
  </div>
</template>

<style scoped>
#homePage {
  min-height: calc(100vh - 64px);
  padding: 32px;
  background:
    radial-gradient(circle at top left, rgba(255, 194, 102, 0.28), transparent 28%),
    radial-gradient(circle at top right, rgba(69, 120, 255, 0.2), transparent 24%),
    linear-gradient(180deg, #fff9ef 0%, #f5f7fb 46%, #eef2f7 100%);
}

.apps-panel,
.guest-panel,
.create-guide-panel {
  max-width: 1240px;
  margin: 0 auto 28px;
}

.create-guide-panel {
  display: flex;
  min-height: calc(100vh - 64px - 92px);
}

.section-eyebrow,
.example-label {
  margin: 0 0 10px;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: #b45309;
}

.section-description,
.modal-tip {
  margin: 14px 0 0;
  max-width: 720px;
  font-size: 16px;
  line-height: 1.75;
  color: #5f6b85;
}

.apps-panel,
.guest-card,
.create-guide-card {
  padding: 28px;
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(20, 30, 58, 0.08);
  box-shadow: 0 20px 54px rgba(45, 62, 88, 0.08);
}

.loading-panel {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 280px;
}

.create-guide-card {
  width: 100%;
  min-height: 100%;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.guide-actions {
  display: flex;
  margin-top: 24px;
}

.section-header {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  align-items: flex-start;
  margin-bottom: 24px;
}

.section-title {
  margin: 0;
  font-size: 30px;
  line-height: 1.2;
  color: #172033;
}

.app-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 18px;
}

.app-card {
  overflow: hidden;
  border-radius: 24px;
  background: linear-gradient(180deg, #ffffff 0%, #f9fbff 100%);
  box-shadow: 0 14px 30px rgba(20, 30, 58, 0.08);
}

.add-app-card {
  cursor: pointer;
  min-height: 360px;
  border: 1px dashed rgba(29, 78, 216, 0.28);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.92) 0%, rgba(239, 246, 255, 0.96) 100%);
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease,
    border-color 0.2s ease;
}

.add-app-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 18px 36px rgba(20, 30, 58, 0.12);
  border-color: rgba(29, 78, 216, 0.45);
}

.add-app-content {
  height: 100%;
  min-height: 360px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  text-align: center;
  color: #172033;
}

.add-app-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  border-radius: 999px;
  background: linear-gradient(135deg, #dbeafe 0%, #ffedd5 100%);
  font-size: 30px;
  color: #1d4ed8;
}

.add-app-title {
  margin: 0;
  font-size: 22px;
  line-height: 1.3;
}

.add-app-description {
  max-width: 220px;
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: #5f6b85;
}

.cover-shell {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 180px;
  border-radius: 18px;
  background: linear-gradient(135deg, #e0f2fe 0%, #ffedd5 100%);
  overflow: hidden;
}

.cover-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.cover-fallback {
  font-size: 48px;
  color: #1d4ed8;
}

.app-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.app-heading {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  align-items: flex-start;
}

.app-name {
  margin: 0;
  font-size: 20px;
  line-height: 1.35;
  color: #172033;
}

.app-prompt {
  min-height: 72px;
  margin: 0;
  font-size: 14px;
  line-height: 1.75;
  color: #5f6b85;
  display: -webkit-box;
  overflow: hidden;
  line-clamp: 3;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
}

.app-meta {
  display: grid;
  gap: 6px;
  font-size: 13px;
  color: #7a869e;
}

.app-actions {
  display: flex;
  gap: 10px;
}

.create-panel {
  display: grid;
  gap: 16px;
}

.example-list {
  display: grid;
  gap: 10px;
}

.example-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.example-tag {
  cursor: pointer;
  padding: 6px 12px;
  border-radius: 999px;
}

@media (max-width: 960px) {
  #homePage {
    padding: 20px;
  }

  .create-guide-panel {
    min-height: calc(100vh - 64px - 68px);
  }

  .section-header {
    flex-direction: column;
    align-items: stretch;
  }
}

@media (max-width: 640px) {
  .apps-panel,
  .guest-card,
  .create-guide-card {
    padding: 20px;
  }

  .app-actions {
    flex-direction: column;
  }

  .app-grid {
    grid-template-columns: 1fr;
  }
}
</style>
