<template>
  <div id="appChatPage">
    <div class="header-bar">
      <div class="header-left">
        <h1 class="app-name">{{ appInfo?.appName || '新建应用对话' }}</h1>
        <a-tag v-if="appInfo?.codeGenType" color="blue" class="code-gen-type-tag">
          {{ formatCodeGenType(appInfo.codeGenType) }}
        </a-tag>
      </div>
      <div v-if="appId" class="header-right">
        <a-button type="default" @click="showAppDetail">
          <template #icon>
            <InfoCircleOutlined />
          </template>
          应用详情
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
              <a-button
                type="primary"
                @click="sendMessage"
                :loading="isGenerating || isCreatingApp"
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
import { formatCodeGenType } from '@/utils/codeGenTypes'
import request from '@/request'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import AppDetailModal from '@/components/AppDetailModal.vue'
import DeploySuccessModal from '@/components/DeploySuccessModal.vue'
import aiAvatar from '@/assets/aiAvatar.png'
import { API_BASE_URL, getGeneratedPreviewUrl, getStaticPreviewUrl } from '@/config/env'
import { VisualEditor, type ElementInfo } from '@/utils/visualEditor'

import {
  CloudUploadOutlined,
  DownloadOutlined,
  EditOutlined,
  ExportOutlined,
  InfoCircleOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'

interface Message {
  type: 'user' | 'ai'
  content: string
  loading?: boolean
  createTime?: string
}

type ParsedSseEvent = {
  event: string
  data: string
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
const messagesContainer = ref<HTMLElement>()

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

const parseSseEvents = (chunkBuffer: string): { events: ParsedSseEvent[]; remaining: string } => {
  const normalizedBuffer = chunkBuffer.replace(/\r\n/g, '\n')
  const eventBlocks = normalizedBuffer.split('\n\n')
  const remaining = eventBlocks.pop() ?? ''

  const events = eventBlocks
    .map((block) => {
      let event = 'message'
      const dataLines: string[] = []

      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) {
          event = line.slice(6).trim()
          continue
        }
        if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trimStart())
        }
      }

      return {
        event,
        data: dataLines.join('\n'),
      }
    })
    .filter((item) => item.event || item.data)

  return {
    events,
    remaining,
  }
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
  let streamCompleted = false
  let fullContent = ''
  const currentSessionId = ensureChatSessionId(appId.value)
  const requestId = createClientId()

  const finishStream = () => {
    if (streamCompleted) {
      return
    }

    streamCompleted = true
    isGenerating.value = false

    setTimeout(async () => {
      await fetchAppInfo()
      updatePreview()
    }, 1000)
  }

  const applyMessageChunk = (chunk: unknown) => {
    if (chunk === undefined || chunk === null) {
      return
    }

    fullContent += String(chunk)
    const targetMessage = getMessageAt(aiMessageIndex)
    if (!targetMessage) {
      return
    }
    targetMessage.content = fullContent
    targetMessage.loading = false
    scrollToBottom()
  }

  const handleBusinessError = (eventData: string) => {
    const errorMessage = '生成过程中出现错误，请稍后重试'
    let traceId = ''

    try {
      const errorData = eventData ? JSON.parse(eventData) : {}
      traceId = errorData.traceId || ''
    } catch (error) {
      console.error('解析业务错误事件失败:', error, eventData)
    }

    const targetMessage = getMessageAt(aiMessageIndex)
    if (targetMessage) {
      targetMessage.content = '抱歉，当前生成失败，请重试。'
      targetMessage.loading = false
    }
    message.error(traceId ? `${errorMessage} traceId: ${traceId}` : errorMessage)
    streamCompleted = true
    isGenerating.value = false
  }

  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const url = `${baseURL}/api/app/chat/gen/code`
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        ...getAuthHeaders(),
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
      },
      body: JSON.stringify({
        appId: Number(appId.value),
        message: userMessage,
        sessionId: currentSessionId,
        requestId,
        stream: true,
      }),
    })

    if (!response.ok) {
      const errorText = await response.text()
      throw new Error(errorText || `生成失败: ${response.status}`)
    }

    const responseBody = response.body
    if (!responseBody) {
      throw new Error('流式响应为空')
    }

    const reader = responseBody.getReader()
    const decoder = new TextDecoder('utf-8')
    let chunkBuffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }

      chunkBuffer += decoder.decode(value, { stream: true })
      const { events, remaining } = parseSseEvents(chunkBuffer)
      chunkBuffer = remaining

      for (const currentEvent of events) {
        if (streamCompleted) {
          return
        }

        if (currentEvent.event === 'message') {
          try {
            const parsed = JSON.parse(currentEvent.data)
            applyMessageChunk(parsed.d)
          } catch (error) {
            console.error('解析消息失败:', error, currentEvent)
            handleError(error, aiMessageIndex)
            return
          }
          continue
        }

        if (currentEvent.event === 'business-error') {
          handleBusinessError(currentEvent.data)
          return
        }

        if (currentEvent.event === 'done') {
          finishStream()
          return
        }
      }
    }

    chunkBuffer += decoder.decode()
    const { events } = parseSseEvents(`${chunkBuffer}\n\n`)
    for (const currentEvent of events) {
      if (currentEvent.event === 'message') {
        try {
          const parsed = JSON.parse(currentEvent.data)
          applyMessageChunk(parsed.d)
        } catch (error) {
          console.error('解析尾部消息失败:', error, currentEvent)
          handleError(error, aiMessageIndex)
          return
        }
      }

      if (currentEvent.event === 'business-error') {
        handleBusinessError(currentEvent.data)
        return
      }

      if (currentEvent.event === 'done') {
        finishStream()
        return
      }
    }

    if (!streamCompleted) {
      finishStream()
    }
  } catch (error) {
    console.error('创建流式请求失败：', error)
    handleError(error, aiMessageIndex)
  }
}

const handleError = (error: unknown, aiMessageIndex: number) => {
  console.error('生成代码失败：', error)
  const targetMessage = getMessageAt(aiMessageIndex)
  if (targetMessage) {
    targetMessage.content = '抱歉，生成过程中出现了错误，请重试。'
    targetMessage.loading = false
  }
  message.error('生成失败，请重试')
  isGenerating.value = false
}

const updatePreview = () => {
  const deployKey = appInfo.value?.deployKey
  if (deployKey) {
    previewUrl.value = getStaticPreviewUrl(deployKey)
    previewReady.value = false
    previewVersion.value += 1
    return
  }

  if (appId.value && appInfo.value?.codeGenType) {
    previewUrl.value = getGeneratedPreviewUrl(appId.value, appInfo.value.codeGenType)
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

