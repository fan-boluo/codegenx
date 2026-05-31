<template>
  <div id="appChatPage" class="ide-layout">
    <!-- Left Sidebar -->
    <div v-if="appId" class="left-sidebar" :style="{ width: sidebarWidth + 'px' }">
      <div class="sidebar-header">
        <div class="sidebar-project-icon">
          <AppstoreOutlined />
        </div>
        <span class="sidebar-project-name" :title="appInfo?.appName || '项目'">
          {{ appInfo?.appName || '项目' }}
        </span>
      </div>

      <div class="sidebar-tabs">
        <div
          class="sidebar-tab"
          :class="{ active: leftPanelTab === 'data' }"
          @click="leftPanelTab = 'data'"
        >
          <DatabaseOutlined />
          <span>数据表</span>
        </div>
        <div
          class="sidebar-tab"
          :class="{ active: leftPanelTab === 'files' }"
          @click="switchToFilesTab"
        >
          <FolderOutlined />
          <span>项目文件</span>
        </div>
      </div>

      <div class="sidebar-content">
        <template v-if="leftPanelTab === 'data'">
          <div v-if="loadingDbTables" class="sidebar-loading">
            <a-spin size="small" />
          </div>
          <div v-else-if="!dbTables.length" class="sidebar-empty">
            <p>{{ appInfo?.dbName ? '暂无数据表' : '未配置项目库' }}</p>
          </div>
          <a-tree
            v-else
            :tree-data="dbTableTree"
            :default-expand-all="true"
            class="sidebar-tree"
          />
        </template>
        <template v-else>
          <div v-if="loadingSourceTree" class="sidebar-loading">
            <a-spin size="small" />
          </div>
          <div v-else-if="!sourceFileTree.length" class="sidebar-empty">
            <p>暂无项目文件</p>
          </div>
          <a-tree
            v-else
            :tree-data="sourceFileTree"
            :selected-keys="activeFileTab ? [activeFileTab] : []"
            :default-expand-all="true"
            @select="handleSourceFileSelect"
            class="sidebar-tree"
          />
        </template>
      </div>

      <div class="sidebar-actions">
        <a-tooltip title="项目详情" placement="right">
          <a-button type="text" size="small" @click="showAppDetail">
            <template #icon><QuestionCircleOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-tooltip :title="canOperateApp ? '下载代码' : readOnlyTooltip" placement="right">
          <a-button type="text" size="small" @click="downloadCode" :loading="downloading" :disabled="!canOperateApp">
            <template #icon><DownloadOutlined /></template>
          </a-button>
        </a-tooltip>
        <a-tooltip :title="canOperateApp ? '部署' : readOnlyTooltip" placement="right">
          <a-button type="text" size="small" @click="deployApp" :loading="deploying" :disabled="!canOperateApp">
            <template #icon><CloudUploadOutlined /></template>
          </a-button>
        </a-tooltip>
      </div>
    </div>

    <!-- Resizer: left sidebar ↔ center -->
    <div
      v-if="appId"
      class="resizer"
      @mousedown="onResizeStart('sidebar', $event)"
    />

    <!-- Center Work Area -->
    <div class="center-work-area">
      <div class="file-tab-bar">
        <div
          v-for="tab in fileTabs"
          :key="tab.path"
          class="file-tab"
          :class="{ active: tab.path === activeFileTab }"
          @click="activateFileTab(tab.path)"
        >
          <span class="file-tab-name">{{ tab.name }}</span>
          <span class="file-tab-close" @click.stop="closeFileTab(tab.path)">
            <CloseOutlined />
          </span>
        </div>
      </div>

      <div class="file-content">
        <div v-if="!fileTabs.length" class="center-empty">
          <div class="center-empty-icon">
            <FileTextOutlined />
          </div>
          <p class="center-empty-title">暂无打开的文件</p>
          <p class="center-empty-hint">从左侧「项目文件」中点击文件查看源码</p>
        </div>

        <div v-else-if="activeTabData?.isLoading" class="center-loading">
          <a-spin size="small" />
          <span>加载中...</span>
        </div>

        <pre v-else class="code-pre"><code class="hljs" v-html="highlightedActiveContent"></code></pre>
      </div>
    </div>

    <!-- Resizer: center ↔ right chat -->
    <div
      class="resizer"
      @mousedown="onResizeStart('chat', $event)"
    />

    <!-- Right Chat Panel -->
    <div class="right-chat-panel" :style="{ width: chatWidth + 'px' }">
      <div class="messages-container" ref="messagesContainer">
        <div v-if="!appId && !messages.length" class="create-mode-hint">
          <div class="hint-icon">
            <MessageOutlined />
          </div>
          <p class="hint-title">开始新项目对话</p>
          <p class="hint-description">
            请先在首页点击「创建项目」新建项目，或从项目列表进入已有项目。
          </p>
        </div>

        <div v-for="(messageItem, index) in messages" :key="index" class="message-item">
          <div v-if="messageItem.type === 'user'" class="user-message">
            <div class="message-bubble user-bubble">
              {{ messageItem.content }}
            </div>
            <div class="message-avatar">
              <a-avatar :src="loginUserStore.loginUser.userAvatar" :size="28" />
            </div>
          </div>
          <div v-else class="ai-message">
            <div class="message-avatar">
              <a-avatar :src="aiAvatar" :size="28" />
            </div>
            <div class="message-bubble ai-bubble">
              <MarkdownRenderer v-if="messageItem.content" :content="messageItem.content" />
              <div v-if="messageItem.loading" class="loading-indicator">
                <a-spin size="small" />
                <span>AI 正在思考...</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <a-alert
        v-if="selectedElementInfo"
        class="selected-element-alert"
        type="info"
        closable
        @close="clearSelectedElement"
      >
        <template #message>
          <div class="selected-element-info">
            <div class="element-header">
              <span class="element-tag">{{ selectedElementInfo.tagName.toLowerCase() }}</span>
              <span v-if="selectedElementInfo.id" class="element-id">#{{ selectedElementInfo.id }}</span>
              <span v-if="selectedElementInfo.className" class="element-class">.{{ selectedElementInfo.className.split(' ').join('.') }}</span>
            </div>
            <div class="element-details">
              <div v-if="selectedElementInfo.textContent" class="element-item">
                内容: {{ selectedElementInfo.textContent.substring(0, 50) }}{{ selectedElementInfo.textContent.length > 50 ? '...' : '' }}
              </div>
              <div v-if="selectedElementInfo.pagePath" class="element-item">页面路径: {{ selectedElementInfo.pagePath }}</div>
              <div class="element-item">选择器: <code class="element-selector-code">{{ selectedElementInfo.selector }}</code></div>
            </div>
          </div>
        </template>
      </a-alert>

      <div class="input-container">
        <div class="input-wrapper">
          <a-textarea
            v-model:value="userInput"
            :placeholder="getInputPlaceholder()"
            :rows="3"
            :maxlength="1000"
            @keydown.enter.prevent="sendMessage"
            :disabled="isGenerating || isCreatingApp || !canOperateApp"
            class="chat-input"
          />
          <div class="input-actions">
            <a-button v-if="isGenerating" danger @click="stopGeneration" :loading="isStoppingGeneration" size="small">
              <template #icon><StopOutlined /></template>
            </a-button>
            <a-button v-else type="primary" @click="sendMessage" :loading="isCreatingApp" :disabled="!canOperateApp" size="small">
              <template #icon><SendOutlined /></template>
            </a-button>
          </div>
        </div>
      </div>
    </div>

    <AppDetailModal v-model:open="appDetailVisible" :app="appInfo" :show-actions="isOwner || isAdmin" @edit="editApp" @delete="deleteApp" />
    <DeploySuccessModal v-model:open="deployModalVisible" :deploy-url="deployUrl" @open-site="openDeployedSite" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser'
import {
  deleteApp as deleteAppApi,
  deployApp as deployAppApi,
  getAppVoById,
} from '@/api/appController'
import request from '@/request'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import AppDetailModal from '@/components/AppDetailModal.vue'
import DeploySuccessModal from '@/components/DeploySuccessModal.vue'
import aiAvatar from '@/assets/aiAvatar.png'
import { API_BASE_URL, getGeneratedPreviewUrl, getStaticPreviewUrl } from '@/config/env'
import { VisualEditor, type ElementInfo } from '@/utils/visualEditor'
import hljs from 'highlight.js'

import {
  AppstoreOutlined,
  CloseOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  FileTextOutlined,
  FolderOutlined,
  MessageOutlined,
  QuestionCircleOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons-vue'

interface Message {
  type: 'user' | 'ai'
  content: string
  loading?: boolean
  createTime?: string
}

interface FileTab {
  path: string
  name: string
  content: string
  isLoading: boolean
  lastAccessed: number
}

const MAX_OPEN_TABS = 5

const route = useRoute()
const router = useRouter()
const loginUserStore = useLoginUserStore()

const appInfo = ref<API.AppVO>({})
const appId = ref<string>()
const sessionId = ref('')
const isCreatingApp = ref(false)
const suppressAutoInitialMessage = ref(false)

const messages = ref<Message[]>([])
const userInput = ref('')
const isGenerating = ref(false)
const isStoppingGeneration = ref(false)
const messagesContainer = ref<HTMLElement>()
const activeGenerationRequestId = ref('')
const activeGenerationSessionId = ref('')
const activeGenerationMessageIndex = ref<number | null>(null)
const stopRequested = ref(false)
const abortController = ref<AbortController | null>(null)

const previewUrl = ref('')
const previewReady = ref(false)
const previewVersion = ref(0)

const deploying = ref(false)
const deployModalVisible = ref(false)
const deployUrl = ref('')

const downloading = ref(false)

// Resizable panels
const sidebarWidth = ref(260)
const chatWidth = ref(380)
const resizing = ref<'sidebar' | 'chat' | null>(null)

const onResizeStart = (target: 'sidebar' | 'chat', e: MouseEvent) => {
  resizing.value = target
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
  e.preventDefault()
}

const onResizeMove = (e: MouseEvent) => {
  if (resizing.value === 'sidebar') {
    const w = Math.max(200, Math.min(400, e.clientX))
    sidebarWidth.value = w
  } else if (resizing.value === 'chat') {
    const w = Math.max(260, Math.min(600, window.innerWidth - e.clientX))
    chatWidth.value = w
  }
}

const onResizeEnd = () => {
  resizing.value = null
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
}

const previewFrameUrl = computed(() => {
  if (!previewUrl.value) return ''
  try {
    const nextUrl = new URL(previewUrl.value, window.location.origin)
    nextUrl.searchParams.set('_preview_v', String(previewVersion.value))
    return nextUrl.toString()
  } catch {
    const separator = previewUrl.value.includes('?') ? '&' : '?'
    return `${previewUrl.value}${separator}_preview_v=${previewVersion.value}`
  }
})

const isEditMode = ref(false)
const selectedElementInfo = ref<ElementInfo | null>(null)
const visualEditor = new VisualEditor({
  onElementSelected: (elementInfo: ElementInfo) => {
    selectedElementInfo.value = elementInfo
  },
})

const appDetailVisible = ref(false)
const readOnlyTooltip = '无法在别人的作品下操作哦~'

const leftPanelTab = ref<'data' | 'files'>('data')
const sourceFileTree = ref<any[]>([])
const loadingSourceTree = ref(false)

const fileTabs = ref<FileTab[]>([])
const activeFileTab = ref<string>('')

const activeTabData = computed(() => {
  return fileTabs.value.find(t => t.path === activeFileTab.value)
})

const highlightedActiveContent = computed(() => {
  if (!activeTabData.value?.content) return ''
  const ext = activeFileTab.value.split('.').pop() ?? ''
  const langMap: Record<string, string> = {
    js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
    vue: 'xml', html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', py: 'python', md: 'markdown', yaml: 'yaml', yml: 'yaml',
    sh: 'bash', sql: 'sql', java: 'java', go: 'go', rs: 'rust',
    xml: 'xml', txt: 'plaintext',
  }
  const lang = langMap[ext.toLowerCase()]
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(activeTabData.value.content, { language: lang, ignoreIllegals: true }).value
    }
    return hljs.highlightAuto(activeTabData.value.content).value
  } catch {
    return activeTabData.value.content.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
})

const dbTables = ref<any[]>([])
const loadingDbTables = ref(false)

const dbTableTree = computed(() => {
  return dbTables.value.map((t) => ({
    key: t.name,
    title: t.name,
    isLeaf: true,
  }))
})

const buildSourceTree = (nodes: any[]): any[] => {
  return nodes.map((node) => ({
    key: node.path,
    title: node.name,
    isLeaf: node.type === 'file',
    children: node.children ? buildSourceTree(node.children) : undefined,
  }))
}

const loadSourceTree = async () => {
  if (!appId.value) return
  loadingSourceTree.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const res = await request.get(`${baseURL}/api/app/code/tree/${appId.value}`)
    if (res.data.code === 0) {
      sourceFileTree.value = buildSourceTree(res.data.data || [])
    } else {
      message.error('获取源码目录失败：' + res.data.message)
    }
  } catch (error) {
    console.error('获取源码目录失败:', error)
    message.error('获取源码目录失败')
  } finally {
    loadingSourceTree.value = false
  }
}

const loadDbTables = async () => {
  if (!appId.value) return
  loadingDbTables.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const res = await request.get(`${baseURL}/api/app/db/tables/${appId.value}`)
    if (res.data.code === 0) {
      dbTables.value = res.data.data || []
    } else {
      dbTables.value = []
    }
  } catch (error) {
    console.error('获取数据表失败:', error)
    dbTables.value = []
  } finally {
    loadingDbTables.value = false
  }
}

const switchToFilesTab = async () => {
  leftPanelTab.value = 'files'
  if (!sourceFileTree.value.length) {
    await loadSourceTree()
  }
}

const loadFileContent = async (filePath: string): Promise<string> => {
  const baseURL = request.defaults.baseURL || API_BASE_URL
  const res = await request.get(`${baseURL}/api/app/code/file/${appId.value}`, { params: { path: filePath } })
  if (res.data.code === 0) {
    return res.data.data ?? ''
  }
  throw new Error(res.data.message || '读取文件失败')
}

const openFileInTab = async (filePath: string, fileName: string) => {
  const existing = fileTabs.value.find(t => t.path === filePath)
  if (existing) {
    existing.lastAccessed = Date.now()
    activeFileTab.value = filePath
    return
  }

  if (fileTabs.value.length >= MAX_OPEN_TABS) {
    let oldest = fileTabs.value[0]
    for (let i = 1; i < fileTabs.value.length; i++) {
      if (fileTabs.value[i].lastAccessed < oldest.lastAccessed) {
        oldest = fileTabs.value[i]
      }
    }
    const idx = fileTabs.value.indexOf(oldest)
    fileTabs.value.splice(idx, 1)
  }

  const newTab: FileTab = {
    path: filePath,
    name: fileName,
    content: '',
    isLoading: true,
    lastAccessed: Date.now(),
  }
  fileTabs.value.push(newTab)
  activeFileTab.value = filePath

  try {
    newTab.content = await loadFileContent(filePath)
  } catch (error: any) {
    console.error('读取文件失败:', error)
    message.error('读取文件失败：' + (error.message || '未知错误'))
    closeFileTab(filePath)
  } finally {
    newTab.isLoading = false
  }
}

const activateFileTab = (path: string) => {
  const tab = fileTabs.value.find(t => t.path === path)
  if (tab) {
    tab.lastAccessed = Date.now()
    activeFileTab.value = path
  }
}

const closeFileTab = (path: string) => {
  const idx = fileTabs.value.findIndex(t => t.path === path)
  if (idx === -1) return
  fileTabs.value.splice(idx, 1)

  if (activeFileTab.value === path) {
    if (fileTabs.value.length > 0) {
      let mostRecent = fileTabs.value[0]
      for (const t of fileTabs.value) {
        if (t.lastAccessed > mostRecent.lastAccessed) {
          mostRecent = t
        }
      }
      activeFileTab.value = mostRecent.path
    } else {
      activeFileTab.value = ''
    }
  }
}

const handleSourceFileSelect = async (_keys: string[], { node }: any) => {
  if (!node.isLeaf) return
  const filePath: string = node.key
  const fileName: string = node.title
  await openFileInTab(filePath, fileName)
}

const normalizeUserId = (userId?: string | number | null) => {
  if (userId === undefined || userId === null || userId === '') return ''
  return String(userId)
}

const getAuthHeaders = (): Record<string, string> => {
  const token = localStorage.getItem('token')
  if (!token) return {}
  return { Authorization: `Bearer ${token}` }
}

const createClientId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const getAppSessionStorageKey = (currentAppId: string) => `codegenx:app-chat-session:${currentAppId}`

const clearChatSessionId = () => { sessionId.value = '' }

const ensureChatSessionId = (currentAppId?: string) => {
  if (!currentAppId) { clearChatSessionId(); return '' }
  const storageKey = getAppSessionStorageKey(currentAppId)
  const storedSessionId = localStorage.getItem(storageKey)?.trim()
  if (storedSessionId) { sessionId.value = storedSessionId; return storedSessionId }
  const createdSessionId = createClientId()
  localStorage.setItem(storageKey, createdSessionId)
  sessionId.value = createdSessionId
  return createdSessionId
}

const getMessageAt = (index: number) => messages.value[index]

const applyMessageChunk = (chunk: unknown) => {
  if (chunk === undefined || chunk === null) return
  const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
  if (!targetMessage) return
  targetMessage.content = `${targetMessage.content || ''}${String(chunk)}`
  targetMessage.loading = false
  scrollToBottom()
}

const finishStream = () => {
  isGenerating.value = false
  isStoppingGeneration.value = false
  stopRequested.value = false
  clearActiveGeneration(activeGenerationRequestId.value)
  setTimeout(async () => { await refreshAfterGeneration() }, 1000)
}

const refreshAfterGeneration = async () => {
  await fetchAppInfo()
  updatePreview()
  if (leftPanelTab.value === 'files') {
    await loadSourceTree()
  }
}

const handleStreamEvent = (event: { event_type: string; data: any; state?: string }) => {
  const eventType = event.event_type
  const eventData = event.data
  if (eventType === 'LLM_Response_Chunk') { applyMessageChunk(eventData); return }
  if (eventType === 'RequestCompleted' || eventType === 'RequestStopped') {
    if (stopRequested.value) {
      const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
      if (targetMessage && !targetMessage.content) { targetMessage.content = '已停止本次生成。' }
    }
    finishStream(); return
  }
  if (eventType === 'Error') {
    const errorText = typeof eventData === 'string' ? eventData : eventData?.message || JSON.stringify(eventData)
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
    if (targetMessage) { targetMessage.content = '抱歉，当前生成失败，请重试。'; targetMessage.loading = false }
    message.error(errorText); finishStream(); return
  }
  console.debug('收到未处理的事件:', eventType, eventData)
}

const isOwner = computed(() => {
  if (!appId.value) return true
  const appOwnerId = normalizeUserId(appInfo.value?.userId)
  const loginUserId = normalizeUserId(loginUserStore.loginUser.id)
  if (!appOwnerId || !loginUserId) return false
  return appOwnerId === loginUserId
})

const canOperateApp = computed(() => isOwner.value)
const isAdmin = computed(() => loginUserStore.loginUser.userRole === 'admin')
const showAppDetail = () => { appDetailVisible.value = true }

const clearActiveGeneration = (requestId?: string) => {
  if (requestId && activeGenerationRequestId.value && activeGenerationRequestId.value !== requestId) return
  activeGenerationRequestId.value = ''
  activeGenerationSessionId.value = ''
  activeGenerationMessageIndex.value = null
  stopRequested.value = false
  isStoppingGeneration.value = false
}

const fetchAppInfo = async (options?: { appId?: string; autoGenerateInitialMessage?: boolean }) => {
  const id = options?.appId ?? (route.params.id as string | undefined)
  const autoGenerateInitialMessage = options?.autoGenerateInitialMessage ?? false
  if (!id) {
    appId.value = undefined; clearChatSessionId(); appInfo.value = {}
    messages.value = []; previewUrl.value = ''; previewReady.value = false
    return
  }
  appId.value = id; ensureChatSessionId(id)
  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data
      if (messages.value.length >= 2) { updatePreview() }
      if (appInfo.value.initPrompt && isOwner.value && messages.value.length === 0 && autoGenerateInitialMessage) {
        await sendInitialMessage(appInfo.value.initPrompt)
      }
      await loadSourceTree()
      await loadDbTables()
      return
    }
    message.error('获取项目信息失败'); router.push('/')
  } catch (error) {
    console.error('获取项目信息失败：', error)
    message.error('获取项目信息失败'); router.push('/')
  }
}

const sendInitialMessage = async (prompt: string) => {
  messages.value.push({ type: 'user', content: prompt })
  const aiMessageIndex = messages.value.length
  messages.value.push({ type: 'ai', content: '', loading: true })
  await nextTick(); scrollToBottom()
  isGenerating.value = true
  await generateCode(prompt, aiMessageIndex)
}

const sendMessage = async () => {
  if (!canOperateApp.value || !userInput.value.trim() || isGenerating.value || isCreatingApp.value) return
  let outgoingMessage = userInput.value.trim()
  if (selectedElementInfo.value) {
    let elementContext = `\n\n选中元素信息：`
    if (selectedElementInfo.value.pagePath) { elementContext += `\n- 页面路径: ${selectedElementInfo.value.pagePath}` }
    elementContext += `\n- 标签: ${selectedElementInfo.value.tagName.toLowerCase()}\n- 选择器: ${selectedElementInfo.value.selector}`
    if (selectedElementInfo.value.textContent) { elementContext += `\n- 当前内容: ${selectedElementInfo.value.textContent.substring(0, 100)}` }
    outgoingMessage += elementContext
  }
  if (!appId.value) {
    message.info('请先创建项目再开始对话')
    return
  }
  userInput.value = ''
  messages.value.push({ type: 'user', content: outgoingMessage })
  if (selectedElementInfo.value) { clearSelectedElement(); if (isEditMode.value) { toggleEditMode() } }
  const aiMessageIndex = messages.value.length
  messages.value.push({ type: 'ai', content: '', loading: true })
  await nextTick(); scrollToBottom()
  isGenerating.value = true
  await generateCode(outgoingMessage, aiMessageIndex)
}

const generateCode = async (userMessage: string, aiMessageIndex: number) => {
  const currentSessionId = ensureChatSessionId(appId.value)
  const requestId = createClientId()
  activeGenerationRequestId.value = requestId; activeGenerationSessionId.value = currentSessionId
  activeGenerationMessageIndex.value = aiMessageIndex
  stopRequested.value = false; isStoppingGeneration.value = false
  const controller = new AbortController(); abortController.value = controller
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const url = `${baseURL}/api/chat/gen/code`
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ appId: Number(appId.value), message: userMessage, sessionId: currentSessionId, requestId, traceId: createClientId() }),
      signal: controller.signal,
    })
    if (!response.ok) { throw new Error(`HTTP ${response.status}`) }
    const reader = response.body?.getReader()
    if (!reader) { throw new Error('No response body') }
    const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n'); buffer = lines.pop() || ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try { const event = JSON.parse(trimmed); if (event.event_type) { handleStreamEvent(event) } } catch { /* skip */ }
      }
    }
    if (buffer.trim()) { try { const event = JSON.parse(buffer.trim()); if (event.event_type) { handleStreamEvent(event) } } catch { /* skip */ } }
    finishStream()
  } catch (error: any) {
    if (error.name === 'AbortError') { return }
    handleError(error, aiMessageIndex, requestId)
  } finally { if (abortController.value === controller) { abortController.value = null } }
}

const handleError = (error: unknown, aiMessageIndex: number, requestId?: string) => {
  console.error('执行失败：', error)
  const targetMessage = getMessageAt(aiMessageIndex)
  if (targetMessage) { targetMessage.content = '抱歉，执行中出现了错误，请重试。'; targetMessage.loading = false }
  message.error('执行失败，请重试'); isGenerating.value = false; clearActiveGeneration(requestId)
}

const stopGeneration = async () => {
  if (!isGenerating.value || isStoppingGeneration.value || !appId.value || !activeGenerationRequestId.value || !activeGenerationSessionId.value) return
  isStoppingGeneration.value = true; stopRequested.value = true
  try {
    abortController.value?.abort()
    const baseURL = request.defaults.baseURL || API_BASE_URL
    await fetch(`${baseURL}/api/chat/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ appId: Number(appId.value), sessionId: activeGenerationSessionId.value, requestId: activeGenerationRequestId.value, traceId: createClientId(), reason: 'user-stop' }),
    })
    message.info('停止请求已发送')
  } catch (error) { console.error('停止生成失败：', error); stopRequested.value = false; isStoppingGeneration.value = false; message.error('停止失败，请重试') }
}

const updatePreview = () => {
  const deployKey = appInfo.value?.deployKey
  if (deployKey) { previewUrl.value = getStaticPreviewUrl(deployKey); previewReady.value = false; previewVersion.value += 1; return }
  if (appId.value) { previewUrl.value = getGeneratedPreviewUrl(appId.value); previewReady.value = false; previewVersion.value += 1; return }
  previewUrl.value = ''; previewReady.value = false
}

const scrollToBottom = () => { if (messagesContainer.value) { messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight } }

const downloadCode = async () => {
  if (!canOperateApp.value) { message.warning(readOnlyTooltip); return }
  if (!appId.value) { message.error('项目ID不存在'); return }
  downloading.value = true
  try {
    const baseURL = request.defaults.baseURL || ''
    const response = await fetch(`${baseURL}/api/app/download/${appId.value}`, { method: 'GET', headers: { ...getAuthHeaders() }, credentials: 'include' })
    if (!response.ok) throw new Error(`下载失败: ${response.status}`)
    const contentDisposition = response.headers.get('Content-Disposition')
    const fileName = contentDisposition?.match(/filename="(.+)"/)?.[1] || `app-${appId.value}.zip`
    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = downloadUrl; link.download = fileName; link.click()
    URL.revokeObjectURL(downloadUrl); message.success('代码下载成功')
  } catch (error) { console.error('下载失败：', error); message.error('下载失败，请重试') }
  finally { downloading.value = false }
}

const deployApp = async () => {
  if (!canOperateApp.value) { message.warning(readOnlyTooltip); return }
  if (!appId.value) { message.error('项目ID不存在'); return }
  deploying.value = true
  try {
    const res = await deployAppApi({ appId: appId.value as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      const deployResult = res.data.data as unknown as { deployUrl?: string; deployKey?: string }
      deployUrl.value = deployResult.deployUrl || ''
      if (deployResult.deployKey) { appInfo.value.deployKey = deployResult.deployKey }
      await fetchAppInfo(); updatePreview(); deployModalVisible.value = true; message.success('部署成功')
    } else { message.error('部署失败：' + res.data.message) }
  } catch (error) { console.error('部署失败：', error); message.error('部署失败，请重试') }
  finally { deploying.value = false }
}

const openDeployedSite = () => { if (deployUrl.value) { window.open(deployUrl.value, '_blank') } }

const editApp = () => { if (appInfo.value?.id) { router.push(`/app/edit/${appInfo.value.id}`) } }
const deleteApp = async () => {
  if (!appInfo.value?.id) return
  try {
    const res = await deleteAppApi({ id: appInfo.value.id })
    if (res.data.code === 0) { message.success('删除成功'); appDetailVisible.value = false; router.push('/') }
    else { message.error('删除失败：' + res.data.message) }
  } catch (error) { console.error('删除失败：', error); message.error('删除失败') }
}

const toggleEditMode = () => {
  if (!canOperateApp.value) { message.warning(readOnlyTooltip); return }
  message.info('编辑模式需要网页预览，请先点击"预览网页"在新窗口打开后再编辑')
}

const clearSelectedElement = () => { selectedElementInfo.value = null; visualEditor.clearSelection() }

const getInputPlaceholder = () => {
  if (!appId.value) return '请先创建项目，再开始对话'
  if (!canOperateApp.value) return readOnlyTooltip
  if (selectedElementInfo.value) return `正在编辑 ${selectedElementInfo.value.tagName.toLowerCase()} 元素，描述您想要的修改...`
  return '请描述你的需求，越详细效果越好哦'
}

watch(() => route.params.id, async (newId, oldId) => {
  if (newId === oldId) return
  const shouldAutoGenerate = !suppressAutoInitialMessage.value
  suppressAutoInitialMessage.value = false
  fileTabs.value = []
  activeFileTab.value = ''
  await fetchAppInfo({ appId: typeof newId === 'string' ? newId : undefined, autoGenerateInitialMessage: shouldAutoGenerate })
})

onMounted(() => { fetchAppInfo() })

onUnmounted(() => {
  abortController.value?.abort()
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
})
</script>

<style scoped>
#appChatPage.ide-layout {
  height: calc(100vh - 64px);
  display: flex;
  overflow: hidden;
  background: var(--bg-page);
}

/* ====== Left Sidebar ====== */
.left-sidebar {
  min-width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-default);
}

.sidebar-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border-default);
}

.sidebar-project-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 6px;
  background: var(--accent-primary-subtle);
  color: var(--accent-primary);
  font-size: 16px;
  flex-shrink: 0;
}

.sidebar-project-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sidebar-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-default);
}

.sidebar-tab {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 10px 8px;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
  border-bottom: 2px solid transparent;
}

.sidebar-tab:hover {
  color: var(--text-primary);
  background: var(--bg-page);
}

.sidebar-tab.active {
  color: var(--accent-primary);
  border-bottom-color: var(--accent-primary);
  font-weight: 600;
}

.sidebar-content {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.sidebar-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.sidebar-empty {
  display: flex;
  justify-content: center;
  padding: 24px 16px;
  color: var(--text-muted);
  font-size: 13px;
}

.sidebar-empty p {
  margin: 0;
}

.sidebar-tree {
  padding: 4px 8px;
}

.sidebar-tree :deep(.ant-tree-node-content-wrapper) {
  font-size: 13px;
}

.sidebar-actions {
  display: flex;
  justify-content: center;
  gap: 2px;
  padding: 6px 8px;
  border-top: 1px solid var(--border-default);
  background: var(--bg-subtle);
}

.sidebar-actions :deep(.ant-btn-text) {
  color: var(--text-muted);
}

.sidebar-actions :deep(.ant-btn-text:hover) {
  color: var(--accent-primary);
  background: var(--accent-primary-subtle);
}

/* ====== Resizer ====== */
.resizer {
  width: 4px;
  flex-shrink: 0;
  cursor: col-resize;
  background: transparent;
  transition: background 0.15s;
  position: relative;
  z-index: 10;
}

.resizer:hover,
.resizer:active {
  background: var(--accent-primary);
}

/* ====== Center Work Area ====== */
.center-work-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  overflow: hidden;
}

.file-tab-bar {
  display: flex;
  align-items: center;
  height: 36px;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border-default);
  overflow-x: auto;
  flex-shrink: 0;
}

.file-tab-bar::-webkit-scrollbar {
  height: 0;
}

.file-tab {
  position: relative;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 0 12px;
  height: 100%;
  font-size: 13px;
  color: var(--text-secondary);
  cursor: pointer;
  border-right: 1px solid var(--border-default);
  white-space: nowrap;
  transition: background 0.15s;
  user-select: none;
}

.file-tab:hover {
  background: var(--bg-hover);
}

.file-tab.active {
  background: var(--bg-surface);
  color: var(--text-primary);
  font-weight: 500;
}

.file-tab.active::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--accent-primary);
}

.file-tab-name {
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-tab-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  border-radius: 3px;
  font-size: 10px;
  opacity: 0;
  transition: opacity 0.1s, background 0.1s;
}

.file-tab:hover .file-tab-close {
  opacity: 0.6;
}

.file-tab-close:hover {
  opacity: 1 !important;
  background: rgba(239, 68, 68, 0.15);
  color: #EF4444;
}

.file-content {
  flex: 1;
  overflow: hidden;
  display: flex;
}

.center-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-muted);
}

.center-empty-icon {
  font-size: 48px;
  color: var(--border-strong);
}

.center-empty-title {
  margin: 0;
  font-size: 15px;
  font-weight: 500;
  color: var(--text-secondary);
}

.center-empty-hint {
  margin: 0;
  font-size: 13px;
  color: var(--text-muted);
}

.center-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  color: var(--text-muted);
}

.code-pre {
  flex: 1;
  margin: 0;
  padding: 16px;
  overflow: auto;
  background: var(--bg-page);
  font-family: 'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  border: none;
}

.code-pre code {
  font-family: inherit;
  background: transparent;
  padding: 0;
  border: none;
  white-space: pre;
  color: var(--text-primary);
}

/* ====== Right Chat Panel ====== */
.right-chat-panel {
  min-width: 260px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-left: 1px solid var(--border-default);
  overflow: hidden;
}

.messages-container {
  flex: 1;
  padding: 16px;
  overflow-y: auto;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.message-item {
  margin-bottom: 4px;
}

.user-message {
  display: flex;
  justify-content: flex-end;
  align-items: flex-end;
  gap: 8px;
}

.ai-message {
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
  gap: 8px;
}

.message-bubble {
  padding: 10px 16px;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 14px;
}

.user-bubble {
  max-width: 80%;
  background: var(--accent-primary-subtle);
  border: 1px solid var(--accent-primary-light);
  color: var(--text-primary);
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  max-width: 85%;
  background: var(--bg-page);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
}

.message-avatar {
  flex-shrink: 0;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}

.create-mode-hint {
  margin-bottom: 20px;
  padding: 20px;
  border-radius: 8px;
  background: var(--bg-page);
  border: 1px solid var(--border-default);
  text-align: center;
}

.hint-icon {
  font-size: 24px;
  color: var(--accent-primary);
  margin-bottom: 10px;
}

.hint-title {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.hint-description {
  margin: 0 0 12px;
  line-height: 1.6;
  color: var(--text-secondary);
}

.input-container {
  padding: 12px 16px 16px;
  background: var(--bg-page);
  border-top: 1px solid var(--border-default);
}

.input-wrapper {
  position: relative;
}

.chat-input {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  font-size: 14px;
  border-radius: 8px;
  resize: none;
  transition: border-color 0.15s;
}
.chat-input:focus {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
}

.input-actions {
  position: absolute;
  bottom: 8px;
  right: 8px;
  display: flex;
  gap: 8px;
}

/* ====== Selected Element Alert ====== */
.selected-element-alert {
  margin: 0 16px;
}

.element-tag {
  font-weight: 600;
  color: var(--accent-primary);
}

.element-id {
  color: var(--text-secondary);
  margin-left: 4px;
}

.element-class {
  color: var(--text-muted);
  margin-left: 4px;
}

.element-selector-code {
  background: var(--bg-subtle);
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 12px;
}

/* ====== Responsive ====== */
@media (max-width: 1280px) {
  .right-chat-panel {
    width: 320px;
    min-width: 280px;
  }
}

@media (max-width: 1024px) {
  .left-sidebar {
    width: 48px;
    min-width: 48px;
  }
  .left-sidebar .sidebar-project-name,
  .left-sidebar .sidebar-tab span,
  .left-sidebar .sidebar-actions :deep(.ant-btn-text) {
    display: none;
  }
  .sidebar-header {
    justify-content: center;
    padding: 14px 8px;
  }
  .sidebar-tab {
    padding: 10px 4px;
    justify-content: center;
  }
  .sidebar-actions {
    flex-direction: column;
    align-items: center;
  }
  .right-chat-panel {
    width: 300px;
    min-width: 260px;
  }
}

@media (max-width: 768px) {
  #appChatPage.ide-layout {
    min-width: 768px;
    overflow-x: auto;
  }
  .message-bubble {
    max-width: 90%;
  }
}
</style>
