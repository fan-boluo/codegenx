<template>
  <div id="appChatPage">
    <div class="header-bar">
      <div class="header-left">
        <div class="header-indicator"></div>
        <h1 class="app-name">{{ appInfo?.appName || '新建项目对话' }}</h1>
        <span v-if="appId" class="app-id-badge">#{{ appId }}</span>
      </div>
      <div v-if="appId" class="header-right">
        <a-button size="small" @click="showAppDetail">
          <template #icon><InfoCircleOutlined /></template>
          项目详情
        </a-button>

        <a-popconfirm
          title="新会话将从空白上下文开始，历史消息仍保留。确定？"
          @confirm="startNewSession"
          ok-text="确定"
          cancel-text="取消"
        >
          <a-button size="small" :disabled="isGenerating">
            <template #icon><ReloadOutlined /></template>
            新会话
          </a-button>
        </a-popconfirm>

        <a-button v-if="previewUrl" size="small" @click="openInNewTab">
          <template #icon><ExportOutlined /></template>
          预览网页
        </a-button>

        <a-button size="small" @click="openSourceDrawer" :loading="loadingSourceTree">
          <template #icon><CodeOutlined /></template>
          查看源码
        </a-button>

        <a-tooltip v-if="!canOperateApp" :title="readOnlyTooltip" placement="bottom">
          <span>
            <a-button size="small" :loading="downloading" disabled>
              <template #icon><DownloadOutlined /></template>
              下载代码
            </a-button>
          </span>
        </a-tooltip>
        <a-button v-else size="small" @click="downloadCode" :loading="downloading">
          <template #icon><DownloadOutlined /></template>
          下载代码
        </a-button>

        <a-tooltip v-if="!canOperateApp" :title="readOnlyTooltip" placement="bottom">
          <span>
            <a-button type="primary" size="small" :loading="deploying" disabled>
              <template #icon><CloudUploadOutlined /></template>
              部署
            </a-button>
          </span>
        </a-tooltip>
        <a-button v-else type="primary" size="small" @click="deployApp" :loading="deploying">
          <template #icon><CloudUploadOutlined /></template>
          部署
        </a-button>
      </div>
    </div>

    <div class="main-content">
      <div v-if="appId" class="left-panel">
        <div class="panel-tabs">
          <div
            class="panel-tab"
            :class="{ active: leftPanelTab === 'data' }"
            @click="leftPanelTab = 'data'"
          >
            <DatabaseOutlined />
            <span>数据表</span>
          </div>
          <div
            class="panel-tab"
            :class="{ active: leftPanelTab === 'files' }"
            @click="switchToFilesTab"
          >
            <FolderOutlined />
            <span>项目文件</span>
          </div>
        </div>

        <div class="panel-content">
          <template v-if="leftPanelTab === 'data'">
            <div v-if="loadingDbTables" class="panel-loading">
              <a-spin size="small" />
            </div>
            <div v-else-if="!dbTables.length" class="panel-empty">
              <p>{{ appInfo?.dbName ? '暂无数据表' : '未配置项目库' }}</p>
            </div>
            <a-tree
              v-else
              :tree-data="dbTableTree"
              :default-expand-all="true"
              class="panel-tree"
            />
          </template>

          <template v-else>
            <div v-if="loadingSourceTree" class="panel-loading">
              <a-spin size="small" />
            </div>
            <div v-else-if="!sourceFileTree.length" class="panel-empty">
              <p>暂无项目文件</p>
            </div>
            <a-tree
              v-else
              :tree-data="sourceFileTree"
              :selected-keys="selectedSourceFile ? [selectedSourceFile] : []"
              :default-expand-all="true"
              @select="handleSourceFileSelect"
              class="panel-tree"
            />
          </template>
        </div>
      </div>

      <div class="chat-section">
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
    </div>

    <a-drawer
      :open="sourceDrawerOpen"
      title="源码查看"
      placement="right"
      :width="800"
      @close="sourceDrawerOpen = false"
    >
      <div class="source-drawer-content">
        <div class="source-tree-panel">
          <div v-if="loadingSourceTree" class="source-tree-loading"><a-spin size="small" /></div>
          <div v-else-if="!sourceFileTree.length" class="source-tree-empty"><p>暂无源码文件</p></div>
          <a-tree
            v-else
            :tree-data="sourceFileTree"
            :selected-keys="selectedSourceFile ? [selectedSourceFile] : []"
            :default-expand-all="true"
            @select="handleSourceFileSelect"
          />
        </div>
        <div class="source-code-panel">
          <div v-if="!selectedSourceFile" class="source-placeholder">
            <p>点击左侧文件查看源码</p>
          </div>
          <div v-else-if="loadingSourceFile" class="source-loading">
            <a-spin size="small" /><span>加载中...</span>
          </div>
          <pre v-else class="source-code-pre"><code class="hljs" v-html="highlightedContent"></code></pre>
        </div>
      </div>
    </a-drawer>

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
import { listAppChatHistory } from '@/api/chatHistoryController'
import request from '@/request'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import AppDetailModal from '@/components/AppDetailModal.vue'
import DeploySuccessModal from '@/components/DeploySuccessModal.vue'
import aiAvatar from '@/assets/aiAvatar.png'
import { API_BASE_URL, getGeneratedPreviewUrl, getStaticPreviewUrl } from '@/config/env'
import { VisualEditor, type ElementInfo } from '@/utils/visualEditor'
import hljs from 'highlight.js'

import {
  CloudUploadOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  ExportOutlined,
  FolderOutlined,
  InfoCircleOutlined,
  MessageOutlined,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons-vue'

interface Message {
  type: 'user' | 'ai'
  content: string
  loading?: boolean
  createTime?: string
}

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

const loadingHistory = ref(false)
const historyLoaded = ref(false)

const previewUrl = ref('')
const previewReady = ref(false)
const previewVersion = ref(0)

const deploying = ref(false)
const deployModalVisible = ref(false)
const deployUrl = ref('')

const downloading = ref(false)

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

const leftPanelTab = ref<'data' | 'files'>('files')
const sourceFileTree = ref<any[]>([])
const selectedSourceFile = ref('')
const sourceFileContent = ref('')
const loadingSourceTree = ref(false)
const loadingSourceFile = ref(false)
const sourceDrawerOpen = ref(false)

const openSourceDrawer = async () => {
  sourceDrawerOpen.value = true
  selectedSourceFile.value = ''
  sourceFileContent.value = ''
  await loadSourceTree()
}

const dbTables = ref<any[]>([])
const loadingDbTables = ref(false)

const dbTableTree = computed(() => {
  return dbTables.value.map((t) => ({
    key: t.name,
    title: t.name,
    isLeaf: true,
  }))
})

const highlightedContent = computed(() => {
  if (!sourceFileContent.value) return ''
  const ext = selectedSourceFile.value.split('.').pop() ?? ''
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
      return hljs.highlight(sourceFileContent.value, { language: lang, ignoreIllegals: true }).value
    }
    return hljs.highlightAuto(sourceFileContent.value).value
  } catch {
    return sourceFileContent.value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
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

const handleSourceFileSelect = async (_keys: string[], { node }: any) => {
  if (!node.isLeaf) return
  const filePath: string = node.key
  if (filePath === selectedSourceFile.value) return
  selectedSourceFile.value = filePath
  sourceFileContent.value = ''
  loadingSourceFile.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const res = await request.get(`${baseURL}/api/app/code/file/${appId.value}`, { params: { path: filePath } })
    if (res.data.code === 0) {
      sourceFileContent.value = res.data.data ?? ''
    } else {
      message.error('读取文件失败：' + res.data.message)
    }
  } catch (error) {
    console.error('读取文件失败:', error)
    message.error('读取文件失败')
  } finally {
    loadingSourceFile.value = false
  }
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

const startNewSession = () => {
  if (!appId.value) return
  const storageKey = getAppSessionStorageKey(appId.value)
  localStorage.removeItem(storageKey)
  const newSessionId = createClientId()
  localStorage.setItem(storageKey, newSessionId)
  sessionId.value = newSessionId
  messages.value = []
  historyLoaded.value = true
  message.success('已切换到新会话')
}

const clearActiveGeneration = (requestId?: string) => {
  if (requestId && activeGenerationRequestId.value && activeGenerationRequestId.value !== requestId) return
  activeGenerationRequestId.value = ''
  activeGenerationSessionId.value = ''
  activeGenerationMessageIndex.value = null
  stopRequested.value = false
  isStoppingGeneration.value = false
}

const loadChatHistory = async () => {
  if (!appId.value || loadingHistory.value) return
  loadingHistory.value = true
  try {
    const params: API.listAppChatHistoryParams = { appId: Number(appId.value), sessionId: sessionId.value ?? '' }
    const res = await listAppChatHistory(params)
    if (res.data.code === 0 && res.data.data) {
      const rawData = res.data.data
      const chatHistories = Array.isArray(rawData) ? rawData : rawData.records || []
      if (chatHistories.length > 0) {
        const historyMessages: Message[] = chatHistories
          .map((chat: API.ChatHistory) => ({
            type: (chat.messageType === 'user' ? 'user' : 'ai') as 'user' | 'ai',
            content: chat.message || '',
            createTime: chat.createTime,
          }))
          .reverse()
        messages.value = historyMessages
      }
      historyLoaded.value = true
    }
  } catch (error) {
    console.error('加载对话历史失败：', error)
    message.error('加载对话历史失败')
  } finally { loadingHistory.value = false }
}

const fetchAppInfo = async (options?: { appId?: string; autoGenerateInitialMessage?: boolean }) => {
  const id = options?.appId ?? (route.params.id as string | undefined)
  const autoGenerateInitialMessage = options?.autoGenerateInitialMessage ?? false
  if (!id) {
    appId.value = undefined; clearChatSessionId(); appInfo.value = {}
    messages.value = []; previewUrl.value = ''; previewReady.value = false
    historyLoaded.value = true
    return
  }
  appId.value = id; ensureChatSessionId(id)
  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data
      await loadChatHistory()
      if (messages.value.length >= 2) { updatePreview() }
      if (appInfo.value.initPrompt && isOwner.value && messages.value.length === 0 && historyLoaded.value && autoGenerateInitialMessage) {
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
  console.error('生成代码失败：', error)
  const targetMessage = getMessageAt(aiMessageIndex)
  if (targetMessage) { targetMessage.content = '抱歉，生成过程中出现了错误，请重试。'; targetMessage.loading = false }
  message.error('生成失败，请重试'); isGenerating.value = false; clearActiveGeneration(requestId)
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

const openInNewTab = () => { if (previewUrl.value) { window.open(previewUrl.value, '_blank') } }
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
  return '请描述你想生成的网站，越详细效果越好哦'
}

watch(() => route.params.id, async (newId, oldId) => {
  if (newId === oldId) return
  const shouldAutoGenerate = !suppressAutoInitialMessage.value
  suppressAutoInitialMessage.value = false
  await fetchAppInfo({ appId: typeof newId === 'string' ? newId : undefined, autoGenerateInitialMessage: shouldAutoGenerate })
})

onMounted(() => { fetchAppInfo() })

onUnmounted(() => { abortController.value?.abort() })
</script>

<style scoped>
#appChatPage {
  height: calc(100vh - 64px);
  display: flex;
  flex-direction: column;
  padding: 16px;
}

/* Header Bar */
.header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  margin-bottom: 12px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-indicator {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22C55E;
}

.app-name {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.app-id-badge {
  font-size: 12px;
  color: var(--text-muted);
  background: var(--bg-subtle);
  border: 1px solid var(--border-default);
  padding: 2px 8px;
  border-radius: 4px;
}

.header-right {
  display: flex;
  gap: 8px;
}

/* Main Content Layout */
.main-content {
  flex: 1;
  display: flex;
  gap: 12px;
  overflow: hidden;
}

/* Left Panel */
.left-panel {
  width: 260px;
  min-width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  overflow: hidden;
}

.panel-tabs {
  display: flex;
  border-bottom: 1px solid var(--border-default);
}

.panel-tab {
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

.panel-tab:hover {
  color: var(--text-primary);
  background: var(--bg-page);
}

.panel-tab.active {
  color: var(--accent-primary);
  border-bottom-color: var(--accent-primary);
  font-weight: 600;
}

.panel-content {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
}

.panel-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.panel-empty {
  display: flex;
  justify-content: center;
  padding: 24px 16px;
  color: var(--text-muted);
  font-size: 13px;
}

.panel-empty p {
  margin: 0;
}

.panel-tree {
  padding: 4px 8px;
}

.panel-tree :deep(.ant-tree-node-content-wrapper) {
  font-size: 13px;
}

/* Chat Section */
.chat-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  overflow: hidden;
}

.messages-container {
  flex: 0.9;
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

/* Message Bubbles */
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
  max-width: 65%;
  background: var(--accent-primary-subtle);
  border: 1px solid var(--accent-primary-light);
  color: var(--text-primary);
  border-bottom-right-radius: 4px;
}

.ai-bubble {
  max-width: 75%;
  background: var(--bg-page);
  border: 1px solid var(--border-default);
  color: var(--text-primary);
  border-bottom-left-radius: 4px;
}

.message-avatar {
  flex-shrink: 0;
}

/* Loading */
.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
}

/* Create Mode Hint */
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

/* Input Container */
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

/* Source Drawer */
.source-drawer-content {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.source-tree-panel {
  width: 240px;
  min-width: 160px;
  border-right: 1px solid var(--border-default);
  overflow-y: auto;
  padding: 8px 0;
  flex-shrink: 0;
  background: var(--bg-page);
}

.source-tree-loading,
.source-tree-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  color: var(--text-muted);
}
.source-tree-empty p { margin: 0; font-size: 13px; }

.source-code-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.source-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
}

.source-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  color: var(--text-muted);
}

.source-code-pre {
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

.source-code-pre code {
  font-family: inherit;
  background: transparent;
  padding: 0;
  border: none;
  white-space: pre;
  color: var(--text-primary);
}

.source-file-path {
  font-size: 11px;
  color: var(--text-muted);
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* Selected Element Alert */
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

/* Responsive */
@media (max-width: 1024px) {
  .main-content {
    flex-direction: column;
  }
  .left-panel {
    width: 100%;
    max-height: 200px;
    flex-shrink: 0;
  }
  .chat-section {
    flex: none;
    height: 0;
    flex: 1;
  }
}

@media (max-width: 768px) {
  .header-bar {
    padding: 10px 14px;
  }
  .app-name {
    font-size: 14px;
  }
  .message-bubble {
    max-width: 85%;
  }
  .user-bubble {
    max-width: 75%;
  }
  .ai-bubble {
    max-width: 85%;
  }
}
</style>
