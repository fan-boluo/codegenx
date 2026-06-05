<template>
  <div id="appChatPage" class="ide-layout">
    <!-- Activity Bar (VS Code style) -->
    <div v-if="appId" class="activity-bar">
      <div class="activity-bar-top">
        <div
          class="activity-icon"
          :class="{ active: sidePanelTab === 'files' }"
          title="项目文件"
          @click="switchSidePanel('files')"
        >
          <FolderOutlined />
        </div>
        <div
          class="activity-icon"
          :class="{ active: sidePanelTab === 'data' }"
          title="数据表"
          @click="switchSidePanel('data')"
        >
          <DatabaseOutlined />
        </div>
      </div>
      <div class="activity-bar-bottom">
        <div class="activity-icon" title="项目详情" @click="showAppDetail">
          <QuestionCircleOutlined />
        </div>
      </div>
    </div>

    <!-- Side Panel -->
    <div
      v-if="appId && sidePanelVisible"
      class="side-panel"
      :style="{ width: sidePanelWidth + 'px' }"
    >
      <div class="side-panel-header">
        <span class="side-panel-title">
          <FolderOutlined v-if="sidePanelTab === 'files'" />
          <DatabaseOutlined v-else />
          <span>{{ sidePanelTab === 'files' ? '项目文件' : '数据表' }}</span>
        </span>
        <!-- File operation buttons -->
        <span v-if="sidePanelTab === 'files' && canOperateApp" class="side-panel-actions">
          <a-tooltip title="新建文件">
            <a-button type="text" size="small" @click="openCreateFileModal">
              <template #icon><FileAddOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-tooltip title="新建文件夹">
            <a-button type="text" size="small" @click="openCreateFolderModal">
              <template #icon><FolderAddOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-tooltip title="上传文件">
            <a-button type="text" size="small" @click="triggerFileUpload">
              <template #icon><UploadOutlined /></template>
            </a-button>
          </a-tooltip>
          <a-tooltip title="删除选中项">
            <a-button type="text" size="small" :disabled="!selectedFileNode" @click="deleteSelectedNode">
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </a-tooltip>
        </span>
      </div>

      <div class="side-panel-content">
        <template v-if="sidePanelTab === 'data'">
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
    </div>

    <!-- Resizer: side panel ↔ center -->
    <div
      v-if="appId && sidePanelVisible"
      class="resizer"
      @mousedown="onResizeStart('sidebar', $event)"
    />

    <!-- Center Work Area -->
    <div class="center-work-area">
      <div class="file-tab-bar">
        <div class="file-tab-bar-left">
          <div
            v-for="tab in fileTabs"
            :key="tab.path"
            class="file-tab"
            :class="{ active: tab.path === activeFileTab }"
            @click="activateFileTab(tab.path)"
          >
            <span class="file-tab-dirty" v-if="tab.isDirty" title="未保存的更改"></span>
            <span class="file-tab-name">{{ tab.name }}</span>
            <span class="file-tab-close" @click.stop="closeFileTab(tab.path)">
              <CloseOutlined />
            </span>
          </div>
        </div>
        <div class="file-tab-bar-right">
          <a-select
            v-model:value="selectedPythonEnv"
            size="small"
            style="width: 100px;"
            :options="pythonEnvOptions"
            placeholder="环境"
          />
          <a-tooltip title="运行脚本">
            <a-button
              type="text"
              size="small"
              :loading="runningScript"
              :disabled="!activeTabData || !activeTabData.name.endsWith('.py')"
              @click="runScript"
            >
              <template #icon><CaretRightOutlined /></template>
            </a-button>
          </a-tooltip>
        </div>
      </div>

      <div class="editor-area">
        <div v-if="!fileTabs.length" class="center-empty">
          <div class="center-empty-icon">
            <FileTextOutlined />
          </div>
          <p class="center-empty-title">暂无打开的文件</p>
          <p class="center-empty-hint">从左侧「项目文件」中点击文件查看源码</p>
        </div>

        <template v-else>
          <div class="editor-wrapper">
            <div v-if="showLoading" class="editor-loading-overlay">
              <a-spin size="default" />
              <span>加载中...</span>
            </div>

            <VueMonacoEditor
              v-if="activeTabData"
              :key="activeFileTab"
              v-model:value="activeTabData.content"
              :language="activeTabData.language"
              :theme="editorTheme"
              :options="editorOptions"
              class="monaco-editor-container"
              @mount="handleEditorMount"
            />
          </div>
        </template>
      </div>

      <!-- Resizer: editor ↔ output -->
      <div
        v-if="fileTabs.length"
        class="resizer-horizontal"
        @mousedown="onResizeStart('output', $event)"
      />

      <!-- Output Panel -->
      <div v-if="fileTabs.length" class="output-panel" :style="{ height: outputHeight + 'px' }">
        <div class="output-header">
          <span class="output-title">输出</span>
          <a-button type="text" size="small" @click="clearOutput">
            <template #icon><ClearOutlined /></template>
          </a-button>
        </div>
        <div class="output-content" ref="outputContainer">
          <div v-if="!outputLines.length" class="output-empty">运行输出将显示在这里</div>
          <div
            v-for="(line, i) in outputLines"
            :key="i"
            class="output-line"
            :class="'output-' + line.type"
          >{{ line.text }}</div>
        </div>
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

    <!-- Create File Modal -->
    <a-modal v-model:open="createFileModalVisible" title="新建文件" :mask-closable="false">
      <a-input v-model:value="newItemName" placeholder="输入文件名，如 index.html" @keydown.enter="doCreateFile" />
      <div v-if="newItemParentPath" style="margin-top: 8px; color: var(--text-muted); font-size: 12px;">
        创建位置：{{ newItemParentPath }}
      </div>
      <template #footer>
        <a-button @click="createFileModalVisible = false">取消</a-button>
        <a-button type="primary" :loading="fileOpLoading" @click="doCreateFile">创建</a-button>
      </template>
    </a-modal>

    <!-- Create Folder Modal -->
    <a-modal v-model:open="createFolderModalVisible" title="新建文件夹" :mask-closable="false">
      <a-input v-model:value="newItemName" placeholder="输入文件夹名，如 components" @keydown.enter="doCreateFolder" />
      <div v-if="newItemParentPath" style="margin-top: 8px; color: var(--text-muted); font-size: 12px;">
        创建位置：{{ newItemParentPath }}
      </div>
      <template #footer>
        <a-button @click="createFolderModalVisible = false">取消</a-button>
        <a-button type="primary" :loading="fileOpLoading" @click="doCreateFolder">创建</a-button>
      </template>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser'
import { deleteApp as deleteAppApi, getAppVoById } from '@/api/appController'
import request from '@/request'

import MarkdownRenderer from '@/components/MarkdownRenderer.vue'
import AppDetailModal from '@/components/AppDetailModal.vue'
import aiAvatar from '@/assets/aiAvatar.png'
import { API_BASE_URL } from '@/config/env'
import { VisualEditor, type ElementInfo } from '@/utils/visualEditor'
import { VueMonacoEditor, type MonacoEditor } from '@guolao/vue-monaco-editor'
import '@/config/monacoWorkers'

import {
  CaretRightOutlined,
  ClearOutlined,
  CloseOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  FileAddOutlined,
  FileTextOutlined,
  FolderAddOutlined,
  FolderOutlined,
  MessageOutlined,
  QuestionCircleOutlined,
  SendOutlined,
  StopOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'

interface MessageItem {
  type: 'user' | 'ai'
  content: string
  loading?: boolean
  createTime?: string
}

interface FileTab {
  path: string
  name: string
  content: string
  originalContent: string
  isLoading: boolean
  lastAccessed: number
  isDirty: boolean
  language: string
}

interface OutputLine {
  type: 'stdout' | 'stderr' | 'system'
  text: string
  timestamp: number
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

const messages = ref<MessageItem[]>([])
const userInput = ref('')
const isGenerating = ref(false)
const isStoppingGeneration = ref(false)
const messagesContainer = ref<HTMLElement>()
const activeGenerationRequestId = ref('')
const activeGenerationSessionId = ref('')
const activeGenerationMessageIndex = ref<number | null>(null)
const stopRequested = ref(false)
const abortController = ref<AbortController | null>(null)

// Resizable panels
const sidePanelWidth = ref(260)
const chatWidth = ref(380)
const outputHeight = ref(180)
const resizing = ref<'sidebar' | 'chat' | 'output' | null>(null)
const outputContainer = ref<HTMLElement>()

// Monaco editor state
const editorTheme = ref<'vs' | 'vs-dark'>('vs')
const monacoEditor = ref<MonacoEditor | null>(null)
const editorMounted = ref(false)
const outputLines = ref<OutputLine[]>([])

const showLoading = computed(() => {
  const tab = activeTabData.value
  if (!tab) return false
  if (tab.isLoading) return true
  if (!editorMounted.value) return true
  return false
})

const LANG_MAP: Record<string, string> = {
  js: 'javascript', ts: 'typescript', jsx: 'javascript', tsx: 'typescript',
  vue: 'html', html: 'html', css: 'css', scss: 'scss', less: 'less',
  json: 'json', py: 'python', md: 'markdown', yaml: 'yaml', yml: 'yaml',
  sh: 'bash', sql: 'sql', java: 'java', go: 'go', rs: 'rust',
  xml: 'xml', txt: 'plaintext', c: 'c', cpp: 'cpp', h: 'c',
}

const getLanguage = (fileName: string): string => {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? ''
  return LANG_MAP[ext] || 'plaintext'
}

const editorOptions = computed(() => ({
  readOnly: !canOperateApp.value,
  domReadOnly: !canOperateApp.value,
  minimap: { enabled: true },
  fontSize: 13,
  fontFamily: "'SF Mono', 'Fira Code', Menlo, Consolas, monospace",
  lineNumbers: 'on' as const,
  renderWhitespace: 'selection' as const,
  tabSize: 2,
  automaticLayout: true,
  scrollBeyondLastLine: false,
  wordWrap: 'off' as const,
  bracketPairColorization: { enabled: true },
  padding: { top: 8 },
}))

const onResizeStart = (target: 'sidebar' | 'chat' | 'output', e: MouseEvent) => {
  resizing.value = target
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', onResizeEnd)
  e.preventDefault()
}

const onResizeMove = (e: MouseEvent) => {
  if (resizing.value === 'sidebar') {
    const w = Math.max(200, Math.min(400, e.clientX))
    sidePanelWidth.value = w
  } else if (resizing.value === 'chat') {
    const w = Math.max(260, Math.min(600, window.innerWidth - e.clientX))
    chatWidth.value = w
  } else if (resizing.value === 'output') {
    const centerEl = document.querySelector('.center-work-area') as HTMLElement
    if (!centerEl) return
    const rect = centerEl.getBoundingClientRect()
    const h = Math.max(80, Math.min(rect.height * 0.5, rect.bottom - e.clientY))
    outputHeight.value = h
  }
}

const onResizeEnd = () => {
  resizing.value = null
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', onResizeEnd)
}

const isEditMode = ref(false)
const selectedElementInfo = ref<ElementInfo | null>(null)
const visualEditor = new VisualEditor({
  onElementSelected: (elementInfo: ElementInfo) => {
    selectedElementInfo.value = elementInfo
  },
})

const appDetailVisible = ref(false)
const readOnlyTooltip = '无法在别人的作品下操作哦~'

const sidePanelTab = ref<'data' | 'files'>('files')
const sidePanelVisible = ref(true)
const sourceFileTree = ref<any[]>([])
const loadingSourceTree = ref(false)

const fileTabs = ref<FileTab[]>([])
const activeFileTab = ref<string>('')
const selectedFileNode = ref<any>(null)

const activeTabData = computed(() => {
  return fileTabs.value.find(t => t.path === activeFileTab.value)
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

const switchSidePanel = (tab: 'data' | 'files') => {
  if (sidePanelTab.value === tab) {
    sidePanelVisible.value = !sidePanelVisible.value
    return
  }
  sidePanelTab.value = tab
  sidePanelVisible.value = true
  if (tab === 'files' && !sourceFileTree.value.length) {
    loadSourceTree()
  } else if (tab === 'data' && !dbTables.value.length) {
    loadDbTables()
  }
}

const switchToFilesTab = async () => {
  sidePanelTab.value = 'files'
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

const saveCurrentFile = async () => {
  const tab = activeTabData.value
  if (!tab || !tab.isDirty || !appId.value) return
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const res = await request.post(
      `${baseURL}/api/app/code/file/${appId.value}`,
      { content: tab.content },
      { params: { path: tab.path } },
    )
    if (res.data.code === 0) {
      tab.originalContent = tab.content
      tab.isDirty = false
      message.success('已保存')
    } else {
      message.error('保存失败：' + res.data.message)
    }
  } catch (error: any) {
    console.error('保存文件失败:', error)
    message.error('保存失败：' + (error.message || '未知错误'))
  }
}

const handleEditorMount = (editor: MonacoEditor) => {
  monacoEditor.value = editor
  editorMounted.value = true
  editor.addCommand(
    // Monaco KeyMod.CtrlCmd | Monaco KeyCode.KeyS
    2048 | 49,
    () => { saveCurrentFile() },
  )
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
    if (oldest.isDirty) {
      message.warning('请先保存或关闭未保存的文件后再打开新文件')
      return
    }
    const idx = fileTabs.value.indexOf(oldest)
    fileTabs.value.splice(idx, 1)
  }

  const newTab: FileTab = {
    path: filePath,
    name: fileName,
    content: '',
    originalContent: '',
    isLoading: true,
    lastAccessed: Date.now(),
    isDirty: false,
    language: getLanguage(fileName),
  }
  fileTabs.value.push(newTab)
  activeFileTab.value = filePath

  try {
    const text = await loadFileContent(filePath)
    newTab.content = text
    newTab.originalContent = text
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

  const tab = fileTabs.value[idx]
  if (tab.isDirty) {
    Modal.confirm({
      title: '未保存的更改',
      content: `文件 "${tab.name}" 有未保存的更改，确定要关闭吗？`,
      okText: '关闭',
      cancelText: '取消',
      okType: 'danger',
      onOk: () => { doCloseTab(idx, path) },
    })
    return
  }
  doCloseTab(idx, path)
}

const doCloseTab = (idx: number, path: string) => {
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
  selectedFileNode.value = node
  if (!node.isLeaf) return
  const filePath: string = node.key
  const fileName: string = node.title
  await openFileInTab(filePath, fileName)
}

// File operations
const createFileModalVisible = ref(false)
const createFolderModalVisible = ref(false)
const newItemName = ref('')
const newItemParentPath = ref('')
const fileUploadInput = ref<HTMLInputElement>()
const fileOpLoading = ref(false)

// Python env & script running
const selectedPythonEnv = ref('model')
const pythonEnvOptions = [
  { value: 'model', label: 'model' },
]
const runningScript = ref(false)

const runScript = async () => {
  const tab = activeTabData.value
  if (!tab || !appId.value) return
  if (tab.isDirty) {
    message.warning('请先保存文件再运行')
    return
  }
  runningScript.value = true
  appendOutput('system', `>>> 运行: python ${tab.name}`)
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const url = `${baseURL}/api/app/code/run/${appId.value}`
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({
        path: tab.path,
        env: selectedPythonEnv.value,
      }),
    })
    if (!response.ok) {
      appendOutput('stderr', `运行失败: HTTP ${response.status}`)
      return
    }
    const reader = response.body?.getReader()
    if (!reader) {
      appendOutput('stderr', '运行失败: 无响应')
      return
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
          if (event.event_type === 'output') {
            const text = typeof event.data === 'string' ? event.data : event.data?.text || ''
            appendOutput(event.data?.stream || 'stdout', text)
          } else if (event.event_type === 'done') {
            appendOutput('system', `>>> 脚本结束，退出码: ${event.data?.code ?? 0}`)
          } else if (event.event_type === 'error') {
            appendOutput('stderr', event.data?.message || event.data || '未知错误')
          }
        } catch {
          appendOutput('stdout', trimmed)
        }
      }
    }
    if (buffer.trim()) {
      try {
        const event = JSON.parse(buffer.trim())
        if (event.event_type === 'done') {
          appendOutput('system', `>>> 脚本结束，退出码: ${event.data?.code ?? 0}`)
        }
      } catch {
        appendOutput('stdout', buffer.trim())
      }
    }
  } catch (error: any) {
    appendOutput('stderr', `运行失败: ${error.message || '未知错误'}`)
  } finally {
    runningScript.value = false
  }
}

const openCreateFileModal = () => {
  const node = selectedFileNode.value
  newItemParentPath.value = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : ''
  newItemName.value = ''
  createFileModalVisible.value = true
}

const openCreateFolderModal = () => {
  const node = selectedFileNode.value
  newItemParentPath.value = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : ''
  newItemName.value = ''
  createFolderModalVisible.value = true
}

const doCreateFile = async () => {
  if (!newItemName.value.trim()) return
  fileOpLoading.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const filePath = newItemParentPath.value ? `${newItemParentPath.value}/${newItemName.value.trim()}` : newItemName.value.trim()
    const res = await request.post(`${baseURL}/api/app/code/file/create/${appId.value}`, null, {
      params: { path: filePath },
    })
    if (res.data.code === 0) {
      message.success('文件创建成功')
      createFileModalVisible.value = false
      await loadSourceTree()
    } else {
      message.error('创建失败：' + res.data.message)
    }
  } catch (error: any) {
    message.error('创建失败：' + (error.message || '未知错误'))
  } finally {
    fileOpLoading.value = false
  }
}

const doCreateFolder = async () => {
  if (!newItemName.value.trim()) return
  fileOpLoading.value = true
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const folderPath = newItemParentPath.value ? `${newItemParentPath.value}/${newItemName.value.trim()}` : newItemName.value.trim()
    const res = await request.post(`${baseURL}/api/app/code/folder/create/${appId.value}`, null, {
      params: { path: folderPath },
    })
    if (res.data.code === 0) {
      message.success('文件夹创建成功')
      createFolderModalVisible.value = false
      await loadSourceTree()
    } else {
      message.error('创建失败：' + res.data.message)
    }
  } catch (error: any) {
    message.error('创建失败：' + (error.message || '未知错误'))
  } finally {
    fileOpLoading.value = false
  }
}

const triggerFileUpload = () => {
  if (!fileUploadInput.value) {
    const input = document.createElement('input')
    input.type = 'file'
    input.style.display = 'none'
    input.addEventListener('change', handleFileUpload)
    document.body.appendChild(input)
    fileUploadInput.value = input
  }
  fileUploadInput.value.click()
}

const handleFileUpload = async (e: Event) => {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  try {
    const baseURL = request.defaults.baseURL || API_BASE_URL
    const node = selectedFileNode.value
    const parentPath = node ? (node.dataRef?.type === 'dir' ? node.key : node.key.substring(0, node.key.lastIndexOf('/') + 1) || '') : ''
    const filePath = parentPath ? `${parentPath}/${file.name}` : file.name
    const formData = new FormData()
    formData.append('file', file)
    const res = await request.post(`${baseURL}/api/app/code/file/upload/${appId.value}`, formData, {
      params: { path: filePath },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    if (res.data.code === 0) {
      message.success('上传成功')
      await loadSourceTree()
    } else {
      message.error('上传失败：' + res.data.message)
    }
  } catch (error: any) {
    message.error('上传失败：' + (error.message || '未知错误'))
  } finally {
    target.value = ''
  }
}

const deleteSelectedNode = () => {
  const node = selectedFileNode.value
  if (!node) return
  const name = node.title
  const isDir = node.dataRef?.type === 'dir'
  Modal.confirm({
    title: isDir ? '删除文件夹' : '删除文件',
    content: `确定要删除「${name}」吗？${isDir ? '将同时删除文件夹内的所有内容。' : ''}此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        const baseURL = request.defaults.baseURL || API_BASE_URL
        const res = await request.delete(`${baseURL}/api/app/code/${appId.value}`, {
          params: { path: node.key },
        })
        if (res.data.code === 0) {
          message.success('删除成功')

          // Close all tabs that are in/under the deleted path
          const deletedPath = node.key
          const deletedIsDir = node.dataRef?.type === 'dir'
          fileTabs.value = fileTabs.value.filter(t => {
            if (t.path === deletedPath) return false
            if (deletedIsDir && t.path.startsWith(deletedPath + '/')) return false
            return true
          })
          if (activeFileTab.value && !fileTabs.value.find(t => t.path === activeFileTab.value)) {
            activeFileTab.value = fileTabs.value.length > 0 ? fileTabs.value[fileTabs.value.length - 1].path : ''
          }

          selectedFileNode.value = null
          await loadSourceTree()
        } else {
          message.error('删除失败：' + res.data.message)
        }
      } catch (error: any) {
        message.error('删除失败：' + (error.message || '未知错误'))
      }
    },
  })
}

const clearOutput = () => {
  outputLines.value = []
}

const appendOutput = (type: OutputLine['type'], text: string) => {
  outputLines.value.push({ type, text, timestamp: Date.now() })
  if (outputContainer.value) {
    outputContainer.value.scrollTop = outputContainer.value.scrollHeight
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
  if (sidePanelTab.value === 'files') {
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
    appendOutput('stderr', errorText)
    message.error(errorText); finishStream(); return
  }
  if (eventType === 'Log_Chunk' || eventType === 'Terminal_Output') {
    const text = typeof eventData === 'string' ? eventData : eventData?.text || JSON.stringify(eventData)
    appendOutput('stdout', text)
    return
  }
  console.debug('收到未处理的事件:', eventType, eventData)
}

// Reset editor mounted flag when switching tabs (key changes, Monaco recreated)
watch(activeFileTab, () => {
  editorMounted.value = false
})

// Watch for content changes to track dirty state
watch(
  () => fileTabs.value.map(t => ({ path: t.path, content: t.content })),
  () => {
    for (const tab of fileTabs.value) {
      if (tab.originalContent !== undefined) {
        tab.isDirty = tab.content !== tab.originalContent
      }
    }
  },
  { deep: true },
)

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
    messages.value = []; sourceFileTree.value = []
    return
  }
  appId.value = id; ensureChatSessionId(id)
  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data
      if (messages.value.length >= 2) { /* generated */ }
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
      body: JSON.stringify({ appId: Number(appId.value), message: userMessage, sessionId: currentSessionId, requestId, traceId: createClientId(), dbName: appInfo.value?.dbName || null }),
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

const scrollToBottom = () => { if (messagesContainer.value) { messagesContainer.value.scrollTop = messagesContainer.value.scrollHeight } }

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

// Route navigation guard for unsaved changes
onBeforeUnmount(() => {
  const dirtyTabs = fileTabs.value.filter(t => t.isDirty)
  if (dirtyTabs.length > 0) {
    const names = dirtyTabs.map(t => t.name).join(', ')
    const leave = window.confirm(`以下文件有未保存的更改：\n${names}\n\n确定要离开吗？`)
    if (!leave) { throw new Error('Navigation blocked') }
  }
})

watch(() => route.params.id, async (newId, oldId) => {
  if (newId === oldId) return
  const shouldAutoGenerate = !suppressAutoInitialMessage.value
  suppressAutoInitialMessage.value = false
  fileTabs.value = []
  activeFileTab.value = ''
  sidePanelTab.value = 'files'
  sidePanelVisible.value = true
  await fetchAppInfo({ appId: typeof newId === 'string' ? newId : undefined, autoGenerateInitialMessage: shouldAutoGenerate })
})

onMounted(() => {
  sidePanelTab.value = 'files'
  fetchAppInfo()
})

onBeforeUnmount(() => {
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

/* ====== Activity Bar ====== */
.activity-bar {
  width: 48px;
  min-width: 48px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-default);
  padding: 8px 0;
}

.activity-bar-top,
.activity-bar-bottom {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.activity-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 6px;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  transition: all 0.15s;
  position: relative;
}

.activity-icon:hover {
  color: var(--text-primary);
  background: var(--bg-hover);
}

.activity-icon.active {
  color: var(--accent-primary);
}

.activity-icon.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 2px;
  height: 24px;
  background: var(--accent-primary);
  border-radius: 0 2px 2px 0;
}

/* ====== Side Panel ====== */
.side-panel {
  min-width: 200px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg-surface);
  border-right: 1px solid var(--border-default);
}

.side-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 12px;
  height: 36px;
  border-bottom: 1px solid var(--border-default);
  flex-shrink: 0;
}

.side-panel-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
}

.side-panel-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.side-panel-actions :deep(.ant-btn-text) {
  color: var(--text-muted);
}

.side-panel-actions :deep(.ant-btn-text:hover) {
  color: var(--accent-primary);
  background: var(--accent-primary-subtle);
}

.side-panel-content {
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

/* ====== Resizer (vertical) ====== */
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
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-default);
  flex-shrink: 0;
}

.file-tab-bar-left {
  display: flex;
  align-items: center;
  overflow-x: auto;
  flex: 1;
  height: 100%;
}

.file-tab-bar-left::-webkit-scrollbar {
  height: 0;
}

.file-tab-bar-right {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  flex-shrink: 0;
  border-left: 1px solid var(--border-default);
  height: 100%;
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

.file-tab-dirty {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--accent-primary, #2563eb);
  flex-shrink: 0;
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

/* ====== Editor Area ====== */
.editor-area {
  flex: 1;
  overflow: hidden;
  display: flex;
}

.monaco-editor-container {
  flex: 1;
  height: 100%;
}

.editor-wrapper {
  flex: 1;
  position: relative;
  overflow: hidden;
}

.editor-loading-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: var(--bg-surface);
  color: var(--text-muted);
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

/* ====== Resizer (horizontal, editor ↔ output) ====== */
.resizer-horizontal {
  height: 4px;
  flex-shrink: 0;
  cursor: row-resize;
  background: transparent;
  transition: background 0.15s;
  position: relative;
  z-index: 10;
}

.resizer-horizontal:hover,
.resizer-horizontal:active {
  background: var(--accent-primary);
}

/* ====== Output Panel ====== */
.output-panel {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  border-top: 1px solid var(--border-default);
  background: var(--bg-page);
}

.output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: var(--bg-subtle);
  border-bottom: 1px solid var(--border-default);
}

.output-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.output-header :deep(.ant-btn-text) {
  color: var(--text-muted);
  font-size: 12px;
}

.output-header :deep(.ant-btn-text:hover) {
  color: var(--text-primary);
}

.output-content {
  flex: 1;
  overflow-y: auto;
  padding: 6px 12px;
  font-family: 'SF Mono', 'Fira Code', Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
}

.output-empty {
  color: var(--text-muted);
  font-style: italic;
}

.output-line {
  white-space: pre-wrap;
  word-break: break-all;
}

.output-stdout {
  color: var(--text-primary);
}

.output-stderr {
  color: #ef4444;
}

.output-system {
  color: var(--accent-primary);
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
  background: var(--accent-primary);
  border: none;
  color: var(--text-inverse);
  border-bottom-right-radius: 4px;
  box-shadow: var(--shadow-sm);
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
  border-radius: var(--radius-sm);
  overflow: hidden;
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
  .side-panel {
    width: 48px;
    min-width: 48px;
  }
  .side-panel .side-panel-title span,
  .side-panel .side-panel-actions {
    display: none;
  }
  .side-panel-header {
    justify-content: center;
    padding: 0 4px;
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
