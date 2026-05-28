<template>
  <div id="appChatPage">
    <div class="header-bar">
      <div class="header-left">
        <h1 class="app-name">{{ appInfo?.appName || '新建应用对话' }}</h1>
      </div>
      <div v-if="appId" class="header-right">
        <a-button type="default" @click="showAppDetail">
          <template #icon>
            <InfoCircleOutlined />
          </template>
          应用详情
        </a-button>

        <a-button type="default" @click="toggleSourceView" :loading="loadingSourceTree">
          <template #icon>
            <CodeOutlined />
          </template>
          {{ rightPanelMode === 'source' ? '返回预览' : '查看源码' }}
        </a-button>
        <a-tooltip v-if="!canOperateApp" :title="readOnlyTooltip" placement="bottom">
          <span>
            <a-button type="primary" ghost :loading="downloading" disabled>
              <template #icon>
                <DownloadOutlined />
              </template>
              下载代码
            </a-button>
          </span>
        </a-tooltip>
        <a-button v-else type="primary" ghost @click="downloadCode" :loading="downloading">
          <template #icon>
            <DownloadOutlined />
          </template>
          下载代码
        </a-button>

        <a-tooltip v-if="!canOperateApp" :title="readOnlyTooltip" placement="bottom">
          <span>
            <a-button type="primary" :loading="deploying" disabled>
              <template #icon>
                <CloudUploadOutlined />
              </template>
              部署
            </a-button>
          </span>
        </a-tooltip>
        <a-button v-else type="primary" @click="deployApp" :loading="deploying">
          <template #icon>
            <CloudUploadOutlined />
          </template>
          部署
        </a-button>
      </div>
    </div>

    <div class="main-content">
      <div class="chat-section">
        <div class="messages-container" ref="messagesContainer">
          <div v-if="hasMoreHistory" class="load-more-container">
            <a-button type="link" @click="loadMoreHistory" :loading="loadingHistory" size="small">
              加载更多历史消息
            </a-button>
          </div>

          <div v-if="!appId && !messages.length" class="create-mode-hint">
            <p class="hint-title">直接开始对话</p>
            <p class="hint-description">
              在下方输入需求并发送，系统会先创建应用，再进入与你已有应用相同的生成对话页面。
            </p>
            <a-tag v-if="pendingPrompt" color="gold">已带入示例提示词</a-tag>
          </div>

          <div v-for="(messageItem, index) in messages" :key="index" class="message-item">
            <div v-if="messageItem.type === 'user'" class="user-message">
              <div class="message-content">{{ messageItem.content }}</div>
              <div class="message-avatar">
                <a-avatar :src="loginUserStore.loginUser.userAvatar" />
              </div>
            </div>
            <div v-else class="ai-message">
              <div class="message-avatar">
                <a-avatar :src="aiAvatar" />
              </div>
              <div class="message-content">
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
                <span class="element-tag">
                  选中元素：{{ selectedElementInfo.tagName.toLowerCase() }}
                </span>
                <span v-if="selectedElementInfo.id" class="element-id">
                  #{{ selectedElementInfo.id }}
                </span>
                <span v-if="selectedElementInfo.className" class="element-class">
                  .{{ selectedElementInfo.className.split(' ').join('.') }}
                </span>
              </div>
              <div class="element-details">
                <div v-if="selectedElementInfo.textContent" class="element-item">
                  内容: {{ selectedElementInfo.textContent.substring(0, 50) }}
                  {{ selectedElementInfo.textContent.length > 50 ? '...' : '' }}
                </div>
                <div v-if="selectedElementInfo.pagePath" class="element-item">
                  页面路径: {{ selectedElementInfo.pagePath }}
                </div>
                <div class="element-item">
                  选择器:
                  <code class="element-selector-code">{{ selectedElementInfo.selector }}</code>
                </div>
              </div>
            </div>
          </template>
        </a-alert>

        <div class="input-container">
          <div class="input-wrapper">
            <a-tooltip v-if="!canOperateApp" :title="readOnlyTooltip" placement="top">
              <a-textarea
                v-model:value="userInput"
                :placeholder="getInputPlaceholder()"
                :rows="4"
                :maxlength="1000"
                @keydown.enter.prevent="sendMessage"
                :disabled="isGenerating || isCreatingApp || !canOperateApp"
              />
            </a-tooltip>
            <a-textarea
              v-else
              v-model:value="userInput"
              :placeholder="getInputPlaceholder()"
              :rows="4"
              :maxlength="1000"
              @keydown.enter.prevent="sendMessage"
              :disabled="isGenerating || isCreatingApp"
            />
            <div class="input-actions">
              <a-button v-if="isGenerating" danger @click="stopGeneration" :loading="isStoppingGeneration">
                <template #icon>
                  <StopOutlined />
                </template>
              </a-button>
              <a-button
                v-else
                type="primary"
                @click="sendMessage"
                :loading="isCreatingApp"
                :disabled="!canOperateApp"
              >
                <template #icon>
                  <SendOutlined />
                </template>
              </a-button>
            </div>
          </div>
        </div>
      </div>

      <div class="preview-section">
        <template v-if="rightPanelMode === 'source'">
          <div class="preview-header">
            <h3>源码文件</h3>
            <div class="preview-actions">
              <span v-if="selectedSourceFile" class="source-file-path">{{ selectedSourceFile }}</span>
            </div>
          </div>
          <div class="source-content">
            <div class="source-tree-panel">
              <div v-if="loadingSourceTree" class="source-tree-loading">
                <a-spin size="small" />
              </div>
              <div v-else-if="!sourceFileTree.length" class="source-tree-empty">
                <p>暂无源码文件</p>
              </div>
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
                <div class="placeholder-icon">📄</div>
                <p>点击左侧文件查看源码</p>
              </div>
              <div v-else-if="loadingSourceFile" class="source-loading">
                <a-spin size="small" />
                <span>加载中...</span>
              </div>
              <pre v-else class="source-code-pre"><code class="hljs" v-html="highlightedContent"></code></pre>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="preview-header">
            <h3>生成后的网页展示</h3>
            <div class="preview-actions">
              <a-button
                v-if="canOperateApp && previewUrl"
                type="link"
                :danger="isEditMode"
                @click="toggleEditMode"
                :class="{ 'edit-mode-active': isEditMode }"
                style="padding: 0; height: auto; margin-right: 12px"
              >
                <template #icon>
                  <EditOutlined />
                </template>
                {{ isEditMode ? '退出编辑' : '编辑模式' }}
              </a-button>
              <a-button v-if="previewUrl" type="link" @click="openInNewTab">
                <template #icon>
                  <ExportOutlined />
                </template>
                新窗口打开
              </a-button>
            </div>
          </div>
          <div class="preview-content">
            <div v-if="!previewUrl" class="preview-placeholder">
              <div class="placeholder-icon">🌐</div>
              <p>网站文件生成完成后将在这里展示</p>
            </div>
            <div v-else class="preview-frame-shell">
              <div v-if="previewLoading" class="preview-loading preview-loading-overlay">
                <a-spin size="large" />
                <p>正在加载最新页面...</p>
              </div>
              <iframe
                :key="previewFrameUrl"
                :src="previewFrameUrl"
                class="preview-iframe"
                frameborder="0"
                @load="onIframeLoad"
              ></iframe>
            </div>
          </div>
        </template>
      </div>
    </div>

    <AppDetailModal
      v-model:open="appDetailVisible"
      :app="appInfo"
      :show-actions="isOwner || isAdmin"
      @edit="editApp"
      @delete="deleteApp"
    />

    <DeploySuccessModal
      v-model:open="deployModalVisible"
      :deploy-url="deployUrl"
      @open-site="openDeployedSite"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser'
import {
  addApp,
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
import 'highlight.js/styles/github.css'

import {
  CloudUploadOutlined,
  CodeOutlined,
  DownloadOutlined,
  EditOutlined,
  ExportOutlined,
  InfoCircleOutlined,
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
const pendingPrompt = ref('')
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
const hasMoreHistory = ref(false)
const lastCreateTime = ref<string>()
const historyLoaded = ref(false)

const previewUrl = ref('')
const previewReady = ref(false)
const previewVersion = ref(0)

const deploying = ref(false)
const deployModalVisible = ref(false)
const deployUrl = ref('')

const downloading = ref(false)

const previewLoading = computed(() => Boolean(previewUrl.value) && !previewReady.value)

const previewFrameUrl = computed(() => {
  if (!previewUrl.value) {
    return ''
  }

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

// Source code view
const rightPanelMode = ref<'preview' | 'source'>('preview')
const sourceFileTree = ref<any[]>([])
const selectedSourceFile = ref('')
const sourceFileContent = ref('')
const loadingSourceTree = ref(false)
const loadingSourceFile = ref(false)

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
    return sourceFileContent.value
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
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

const handleSourceFileSelect = async (_keys: string[], { node }: any) => {
  if (!node.isLeaf) return
  const filePath: string = node.key
  if (filePath === selectedSourceFile.value) return
  selectedSourceFile.value = filePath
  sourceFileContent.value = ''
  loadingSourceFile.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const res = await request.get(`${baseURL}/api/app/code/file/${appId.value}`, {
      params: { path: filePath },
    })
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

const toggleSourceView = async () => {
  if (rightPanelMode.value === 'source') {
    rightPanelMode.value = 'preview'
    return
  }
  rightPanelMode.value = 'source'
  selectedSourceFile.value = ''
  sourceFileContent.value = ''
  await loadSourceTree()
}

const normalizeUserId = (userId?: string | number | null) => {
  if (userId === undefined || userId === null || userId === '') {
    return ''
  }
  return String(userId)
}

const getAuthHeaders = (): Record<string, string> => {
  const token = localStorage.getItem('token')
  if (!token) {
    return {}
  }
  return {
    Authorization: `Bearer ${token}`,
  }
}

const createClientId = () => {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const applyMessageChunk = (chunk: unknown) => {
  if (chunk === undefined || chunk === null) {
    return
  }

  const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
  if (!targetMessage) {
    return
  }

  targetMessage.content = `${targetMessage.content || ''}${String(chunk)}`
  targetMessage.loading = false
  scrollToBottom()
}

const getAppSessionStorageKey = (currentAppId: string) => `codegenx:app-chat-session:${currentAppId}`

const clearChatSessionId = () => {
  sessionId.value = ''
}

const ensureChatSessionId = (currentAppId?: string) => {
  if (!currentAppId) {
    clearChatSessionId()
    return ''
  }

  const storageKey = getAppSessionStorageKey(currentAppId)
  const storedSessionId = localStorage.getItem(storageKey)?.trim()
  if (storedSessionId) {
    sessionId.value = storedSessionId
    return storedSessionId
  }

  const createdSessionId = createClientId()
  localStorage.setItem(storageKey, createdSessionId)
  sessionId.value = createdSessionId
  return createdSessionId
}

const applyMessageChunk = (chunk: unknown) => {
  if (chunk === undefined || chunk === null) {
    return
  }

  const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
  if (!targetMessage) {
    return
  }

  targetMessage.content = `${targetMessage.content || ''}${String(chunk)}`
  targetMessage.loading = false
  scrollToBottom()
}

const finishStream = () => {
  isGenerating.value = false
  isStoppingGeneration.value = false
  stopRequested.value = false
  clearActiveGeneration(activeGenerationRequestId.value)

  setTimeout(async () => {
    await fetchAppInfo()
    updatePreview()
  }, 1000)
}

const handleStreamEvent = (event: { event_type: string; data: any; state?: string }) => {
  const eventType = event.event_type
  const eventData = event.data

  if (eventType === 'LLM_Response_Chunk') {
    applyMessageChunk(eventData)
    return
  }

  if (eventType === 'RequestCompleted' || eventType === 'RequestStopped') {
    if (stopRequested.value) {
      const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
      if (targetMessage && !targetMessage.content) {
        targetMessage.content = '已停止本次生成。'
      }
    }
    finishStream()
    return
  }

  if (eventType === 'Error') {
    const errorText = typeof eventData === 'string' ? eventData : eventData?.message || JSON.stringify(eventData)
    const targetMessage = getMessageAt(activeGenerationMessageIndex.value ?? -1)
    if (targetMessage) {
      targetMessage.content = '抱歉，当前生成失败，请重试。'
      targetMessage.loading = false
    }
    message.error(errorText)
    finishStream()
    return
  }

  console.debug('收到未处理的事件:', eventType, eventData)
}

const isOwner = computed(() => {
  if (!appId.value) {
    return true
  }

  const appOwnerId = normalizeUserId(appInfo.value?.userId)
  const loginUserId = normalizeUserId(loginUserStore.loginUser.id)

  if (!appOwnerId || !loginUserId) {
    return false
  }

  return appOwnerId === loginUserId
})

const canOperateApp = computed(() => isOwner.value)

const isAdmin = computed(() => {
  return loginUserStore.loginUser.userRole === 'admin'
})

const showAppDetail = () => {
  appDetailVisible.value = true
}

const getMessageAt = (index: number) => {
  return messages.value[index]
}

const clearActiveGeneration = (requestId?: string) => {
  if (requestId && activeGenerationRequestId.value && activeGenerationRequestId.value !== requestId) {
    return
  }
  activeGenerationRequestId.value = ''
  activeGenerationSessionId.value = ''
  activeGenerationMessageIndex.value = null
  stopRequested.value = false
  isStoppingGeneration.value = false
}

const loadChatHistory = async (isLoadMore = false) => {
  if (!appId.value || loadingHistory.value) {
    return
  }

  loadingHistory.value = true
  try {
    const params: API.listAppChatHistoryParams = {
      appId: Number(appId.value),
      pageSize: 10,
    }

    if (isLoadMore && lastCreateTime.value) {
      params.lastCreateTime = lastCreateTime.value
    }

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

        if (isLoadMore) {
          messages.value.unshift(...historyMessages)
        } else {
          messages.value = historyMessages
        }

        lastCreateTime.value = chatHistories[chatHistories.length - 1]?.createTime
        hasMoreHistory.value = chatHistories.length === 10
      } else {
        hasMoreHistory.value = false
      }

      historyLoaded.value = true
    }
  } catch (error) {
    console.error('加载对话历史失败：', error)
    message.error('加载对话历史失败')
  } finally {
    loadingHistory.value = false
  }
}

const loadMoreHistory = async () => {
  await loadChatHistory(true)
}

const fetchAppInfo = async (options?: { appId?: string; autoGenerateInitialMessage?: boolean }) => {
  const id = options?.appId ?? (route.params.id as string | undefined)
  const autoGenerateInitialMessage = options?.autoGenerateInitialMessage ?? false

  if (!id) {
    appId.value = undefined
    clearChatSessionId()
    appInfo.value = {}
    messages.value = []
    previewUrl.value = ''
    previewReady.value = false
    hasMoreHistory.value = false
    historyLoaded.value = true
    lastCreateTime.value = undefined
    pendingPrompt.value = typeof route.query.prompt === 'string' ? route.query.prompt : ''
    return
  }

  appId.value = id
  ensureChatSessionId(id)
  pendingPrompt.value = ''

  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data

      await loadChatHistory()

      if (messages.value.length >= 2) {
        updatePreview()
      }

      if (
        appInfo.value.initPrompt &&
        isOwner.value &&
        messages.value.length === 0 &&
        historyLoaded.value &&
        autoGenerateInitialMessage
      ) {
        await sendInitialMessage(appInfo.value.initPrompt)
      }
      return
    }

    message.error('获取应用信息失败')
    router.push('/')
  } catch (error) {
    console.error('获取应用信息失败：', error)
    message.error('获取应用信息失败')
    router.push('/')
  }
}

const sendInitialMessage = async (prompt: string) => {
  messages.value.push({
    type: 'user',
    content: prompt,
  })

  const aiMessageIndex = messages.value.length
  messages.value.push({
    type: 'ai',
    content: '',
    loading: true,
  })

  await nextTick()
  scrollToBottom()

  isGenerating.value = true
  await generateCode(prompt, aiMessageIndex)
}

const sendMessage = async () => {
  if (!canOperateApp.value || !userInput.value.trim() || isGenerating.value || isCreatingApp.value) {
    return
  }

  let outgoingMessage = userInput.value.trim()
  if (selectedElementInfo.value) {
    let elementContext = `\n\n选中元素信息：`
    if (selectedElementInfo.value.pagePath) {
      elementContext += `\n- 页面路径: ${selectedElementInfo.value.pagePath}`
    }
    elementContext += `\n- 标签: ${selectedElementInfo.value.tagName.toLowerCase()}\n- 选择器: ${selectedElementInfo.value.selector}`
    if (selectedElementInfo.value.textContent) {
      elementContext += `\n- 当前内容: ${selectedElementInfo.value.textContent.substring(0, 100)}`
    }
    outgoingMessage += elementContext
  }

  if (!appId.value) {
    await createAppAndStartChat(outgoingMessage)
    return
  }

  userInput.value = ''
  messages.value.push({
    type: 'user',
    content: outgoingMessage,
  })

  if (selectedElementInfo.value) {
    clearSelectedElement()
    if (isEditMode.value) {
      toggleEditMode()
    }
  }

  const aiMessageIndex = messages.value.length
  messages.value.push({
    type: 'ai',
    content: '',
    loading: true,
  })

  await nextTick()
  scrollToBottom()

  isGenerating.value = true
  await generateCode(outgoingMessage, aiMessageIndex)
}

const createAppAndStartChat = async (initPrompt: string) => {
  isCreatingApp.value = true
  try {
    const createRes = await addApp({ initPrompt })
    if (createRes.data.code !== 0 || !createRes.data.data) {
      message.error(`创建应用失败，${createRes.data.message ?? '请稍后重试'}`)
      return
    }

    const createdAppId = String(createRes.data.data)
    appId.value = createdAppId
    suppressAutoInitialMessage.value = true
    await router.replace(`/app/chat/${createdAppId}`)
    await fetchAppInfo({ appId: createdAppId, autoGenerateInitialMessage: false })

    userInput.value = ''
    messages.value.push({
      type: 'user',
      content: initPrompt,
    })

    const aiMessageIndex = messages.value.length
    messages.value.push({
      type: 'ai',
      content: '',
      loading: true,
    })

    await nextTick()
    scrollToBottom()

    isGenerating.value = true
    await generateCode(initPrompt, aiMessageIndex)
  } catch (error) {
    console.error('创建应用失败：', error)
    message.error('创建应用失败')
  } finally {
    isCreatingApp.value = false
  }
}

const generateCode = async (userMessage: string, aiMessageIndex: number) => {
  const currentSessionId = ensureChatSessionId(appId.value)
  const requestId = createClientId()

  activeGenerationRequestId.value = requestId
  activeGenerationSessionId.value = currentSessionId
  activeGenerationMessageIndex.value = aiMessageIndex
  stopRequested.value = false
  isStoppingGeneration.value = false

  const controller = new AbortController()
  abortController.value = controller

  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const url = `${baseURL}/api/app/chat/gen/code`

    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({
        appId: Number(appId.value),
        message: userMessage,
        sessionId: currentSessionId,
        requestId,
        traceId: createClientId(),
      }),
      signal: controller.signal,
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const reader = response.body?.getReader()
    if (!reader) {
      throw new Error('No response body')
    }

    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed) continue
        try {
          const event = JSON.parse(trimmed)
          if (event.event_type) {
            handleStreamEvent(event)
          }
        } catch {
          // skip malformed lines
        }
      }
    }

    // Process remaining buffer
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim())
        if (event.event_type) {
          handleStreamEvent(event)
        }
      } catch {
        // skip
      }
    }

    finishStream()
  } catch (error: any) {
    if (error.name === 'AbortError') {
      return
    }
    handleError(error, aiMessageIndex, requestId)
  } finally {
    if (abortController.value === controller) {
      abortController.value = null
    }
  }
}

const handleError = (error: unknown, aiMessageIndex: number, requestId?: string) => {
  console.error('生成代码失败：', error)
  const targetMessage = getMessageAt(aiMessageIndex)
  if (targetMessage) {
    targetMessage.content = '抱歉，生成过程中出现了错误，请重试。'
    targetMessage.loading = false
  }
  message.error('生成失败，请重试')
  isGenerating.value = false
  clearActiveGeneration(requestId)
}

const stopGeneration = async () => {
  if (
    !isGenerating.value ||
    isStoppingGeneration.value ||
    !appId.value ||
    !activeGenerationRequestId.value ||
    !activeGenerationSessionId.value
  ) {
    return
  }

  isStoppingGeneration.value = true
  stopRequested.value = true

  try {
    abortController.value?.abort()

    const baseURL = request.defaults.baseURL || API_BASE_URL
    const url = `${baseURL}/api/app/chat/stop`

    await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getAuthHeaders(),
      },
      body: JSON.stringify({
        appId: Number(appId.value),
        sessionId: activeGenerationSessionId.value,
        requestId: activeGenerationRequestId.value,
        traceId: createClientId(),
        reason: 'user-stop',
      }),
    })
    message.info('停止请求已发送')
  } catch (error) {
    console.error('停止生成失败：', error)
    stopRequested.value = false
    isStoppingGeneration.value = false
    message.error('停止失败，请重试')
  }
}

const updatePreview = () => {
  const deployKey = appInfo.value?.deployKey
  if (deployKey) {
    previewUrl.value = getStaticPreviewUrl(deployKey)
    previewReady.value = false
    previewVersion.value += 1
    return
  }

  if (appId.value) {
    previewUrl.value = getGeneratedPreviewUrl(appId.value)
    previewReady.value = false
    previewVersion.value += 1
    return
  }

  previewUrl.value = ''
  previewReady.value = false
}

const scrollToBottom = () => {
  if (messagesContainer.value) {
    messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight
  }
}

const downloadCode = async () => {
  if (!canOperateApp.value) {
    message.warning(readOnlyTooltip)
    return
  }
  if (!appId.value) {
    message.error('应用ID不存在')
    return
  }

  downloading.value = true
  try {
    const baseURL = request.defaults.baseURL || ''
    const url = `${baseURL}/api/app/download/${appId.value}`
    const response = await fetch(url, {
      method: 'GET',
      headers: {
        ...getAuthHeaders(),
      },
      credentials: 'include',
    })

    if (!response.ok) {
      throw new Error(`下载失败: ${response.status}`)
    }

    const contentDisposition = response.headers.get('Content-Disposition')
    const fileName = contentDisposition?.match(/filename="(.+)"/)?.[1] || `app-${appId.value}.zip`
    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = downloadUrl
    link.download = fileName
    link.click()
    URL.revokeObjectURL(downloadUrl)
    message.success('代码下载成功')
  } catch (error) {
    console.error('下载失败：', error)
    message.error('下载失败，请重试')
  } finally {
    downloading.value = false
  }
}

const deployApp = async () => {
  if (!canOperateApp.value) {
    message.warning(readOnlyTooltip)
    return
  }
  if (!appId.value) {
    message.error('应用ID不存在')
    return
  }

  deploying.value = true
  try {
    const res = await deployAppApi({
      appId: appId.value as unknown as number,
    })

    if (res.data.code === 0 && res.data.data) {
      const deployResult = res.data.data as unknown as { deployUrl?: string; deployKey?: string }
      deployUrl.value = deployResult.deployUrl || ''
      if (deployResult.deployKey) {
        appInfo.value.deployKey = deployResult.deployKey
      }
      await fetchAppInfo()
      updatePreview()
      deployModalVisible.value = true
      message.success('部署成功')
    } else {
      message.error('部署失败：' + res.data.message)
    }
  } catch (error) {
    console.error('部署失败：', error)
    message.error('部署失败，请重试')
  } finally {
    deploying.value = false
  }
}

const openInNewTab = () => {
  if (previewUrl.value) {
    window.open(previewUrl.value, '_blank')
  }
}

const openDeployedSite = () => {
  if (deployUrl.value) {
    window.open(deployUrl.value, '_blank')
  }
}

const onIframeLoad = () => {
  previewReady.value = true
  const iframe = document.querySelector('.preview-iframe') as HTMLIFrameElement
  if (iframe) {
    visualEditor.init(iframe)
    visualEditor.onIframeLoad()
  }
}

const editApp = () => {
  if (appInfo.value?.id) {
    router.push(`/app/edit/${appInfo.value.id}`)
  }
}

const deleteApp = async () => {
  if (!appInfo.value?.id) {
    return
  }

  try {
    const res = await deleteAppApi({ id: appInfo.value.id })
    if (res.data.code === 0) {
      message.success('删除成功')
      appDetailVisible.value = false
      router.push('/')
    } else {
      message.error('删除失败：' + res.data.message)
    }
  } catch (error) {
    console.error('删除失败：', error)
    message.error('删除失败')
  }
}

const toggleEditMode = () => {
  if (!canOperateApp.value) {
    message.warning(readOnlyTooltip)
    return
  }

  const iframe = document.querySelector('.preview-iframe') as HTMLIFrameElement
  if (!iframe || !previewReady.value) {
    message.warning('请等待页面加载完成')
    return
  }

  isEditMode.value = visualEditor.toggleEditMode()
}

const clearSelectedElement = () => {
  selectedElementInfo.value = null
  visualEditor.clearSelection()
}

const getInputPlaceholder = () => {
  if (!appId.value) {
    return '先输入你的需求并发送，系统会自动创建应用并开始生成'
  }
  if (!canOperateApp.value) {
    return readOnlyTooltip
  }
  if (selectedElementInfo.value) {
    return `正在编辑 ${selectedElementInfo.value.tagName.toLowerCase()} 元素，描述您想要的修改...`
  }
  return '请描述你想生成的网站，越详细效果越好哦'
}

watch(
  () => route.query.prompt,
  (prompt) => {
    if (appId.value) {
      return
    }
    pendingPrompt.value = typeof prompt === 'string' ? prompt : ''
    if (pendingPrompt.value) {
      userInput.value = pendingPrompt.value
    }
  },
  { immediate: true }
)

const handleWindowMessage = (event: MessageEvent) => {
  visualEditor.handleIframeMessage(event)
}

onMounted(() => {
  fetchAppInfo()
  window.addEventListener('message', handleWindowMessage)
})

watch(
  () => route.params.id,
  async (newId, oldId) => {
    if (newId === oldId) {
      return
    }
    const shouldAutoGenerate = !suppressAutoInitialMessage.value
    suppressAutoInitialMessage.value = false
    await fetchAppInfo({
      appId: typeof newId === 'string' ? newId : undefined,
      autoGenerateInitialMessage: shouldAutoGenerate,
    })
  }
)

onUnmounted(() => {
  window.removeEventListener('message', handleWindowMessage)
  abortController.value?.abort()
})
</script>

<style scoped>
#appChatPage {
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 16px;
  background: #fdfdfd;
}

.header-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.code-gen-type-tag {
  font-size: 12px;
}

.app-name {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1a1a1a;
}

.header-right {
  display: flex;
  gap: 12px;
}

.main-content {
  flex: 1;
  display: flex;
  gap: 16px;
  padding: 8px;
  overflow: hidden;
}

.chat-section {
  flex: 2;
  display: flex;
  flex-direction: column;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.messages-container {
  flex: 0.9;
  padding: 16px;
  overflow-y: auto;
  scroll-behavior: smooth;
}

.message-item {
  margin-bottom: 12px;
}

.user-message {
  display: flex;
  justify-content: flex-end;
  align-items: flex-start;
  gap: 8px;
}

.ai-message {
  display: flex;
  justify-content: flex-start;
  align-items: flex-start;
  gap: 8px;
}

.message-content {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 12px;
  line-height: 1.5;
  word-wrap: break-word;
}

.user-message .message-content {
  background: #1890ff;
  color: white;
}

.ai-message .message-content {
  background: #f5f5f5;
  color: #1a1a1a;
  padding: 8px 12px;
}

.message-avatar {
  flex-shrink: 0;
}

.loading-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #666;
}

.load-more-container {
  text-align: center;
  padding: 8px 0;
  margin-bottom: 16px;
}

.create-mode-hint {
  margin-bottom: 20px;
  padding: 18px;
  border-radius: 14px;
  background: linear-gradient(135deg, #fff7ed 0%, #eff6ff 100%);
  border: 1px solid rgba(29, 78, 216, 0.08);
}

.hint-title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 600;
  color: #172033;
}

.hint-description {
  margin: 0 0 10px;
  line-height: 1.7;
  color: #5f6b85;
}

.input-container {
  padding: 16px;
  background: white;
}

.input-wrapper {
  position: relative;
}

.input-wrapper .ant-input {
  padding-right: 50px;
}

.input-actions {
  position: absolute;
  bottom: 8px;
  right: 8px;
  display: flex;
  gap: 8px;
}

.preview-section {
  flex: 3;
  display: flex;
  flex-direction: column;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  overflow: hidden;
}

.preview-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid #e8e8e8;
}

.preview-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}

.preview-actions {
  display: flex;
  gap: 8px;
}

.preview-content {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.preview-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
}

.placeholder-icon {
  font-size: 48px;
  margin-bottom: 16px;
}

.preview-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #666;
}

.preview-frame-shell {
  position: relative;
  width: 100%;
  height: 100%;
}

.preview-loading-overlay {
  position: absolute;
  inset: 0;
  background: rgba(255, 255, 255, 0.88);
  z-index: 1;
}

.preview-loading p {
  margin-top: 16px;
}

.preview-iframe {
  width: 100%;
  height: 100%;
  border: none;
}

.source-content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.source-tree-panel {
  width: 260px;
  min-width: 160px;
  border-right: 1px solid #e8e8e8;
  overflow-y: auto;
  padding: 8px 0;
  flex-shrink: 0;
}

.source-tree-loading,
.source-tree-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px 16px;
  color: #999;
}

.source-tree-empty p {
  margin: 0;
  font-size: 13px;
}

.source-code-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.source-placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
}

.source-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  color: #666;
}

.source-code-pre {
  flex: 1;
  margin: 0;
  padding: 16px;
  overflow: auto;
  height: 100%;
  font-family: 'Monaco', 'Menlo', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.6;
  background: #f6f8fa;
  border: none;
}

.source-code-pre code {
  font-family: inherit;
  background: transparent;
  padding: 0;
  border: none;
  white-space: pre;
}

.source-file-path {
  font-size: 12px;
  color: #666;
  font-family: 'Monaco', 'Menlo', monospace;
  max-width: 400px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected-element-alert {
  margin: 0 16px;
}

@media (max-width: 1024px) {
  .main-content {
    flex-direction: column;
  }

  .chat-section,
  .preview-section {
    flex: none;
    height: 50vh;
  }
}

@media (max-width: 768px) {
  .header-bar {
    padding: 12px 16px;
  }

  .app-name {
    font-size: 16px;
  }

  .main-content {
    padding: 8px;
    gap: 8px;
  }

  .message-content {
    max-width: 85%;
  }

  .selected-element-alert {
    margin: 0 16px;
  }

  .selected-element-info {
    line-height: 1.4;
  }

  .element-header {
    margin-bottom: 8px;
  }

  .element-details {
    margin-top: 8px;
  }

  .element-item {
    margin-bottom: 4px;
    font-size: 13px;
  }

  .element-item:last-child {
    margin-bottom: 0;
  }

  .element-tag {
    font-family: 'Monaco', 'Menlo', monospace;
    font-size: 14px;
    font-weight: 600;
    color: #007bff;
  }

  .element-id {
    color: #28a745;
    margin-left: 4px;
  }

  .element-class {
    color: #ffc107;
    margin-left: 4px;
  }

  .element-selector-code {
    font-family: 'Monaco', 'Menlo', monospace;
    background: #f6f8fa;
    padding: 2px 4px;
    border-radius: 3px;
    font-size: 12px;
    color: #d73a49;
    border: 1px solid #e1e4e8;
  }

  .edit-mode-active {
    background-color: #52c41a !important;
    border-color: #52c41a !important;
    color: white !important;
  }

  .edit-mode-active:hover {
    background-color: #73d13d !important;
    border-color: #73d13d !important;
  }
}
</style>

