import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

// 同一浏览器窗口最多同时打开的项目聊天页签数
export const MAX_OPEN_CHATS = 3

export interface OpenedChat {
  appId: string
  appName: string
  // 是否在本会话内激活过（实例懒挂载标记，不持久化）
  everActivated: boolean
}

interface PersistedChats {
  chats: { appId: string; appName: string }[]
  activeAppId?: string
}

const STORAGE_KEY = 'codegenx:opened-chats'

const readPersisted = (): PersistedChats | null => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as PersistedChats) : null
  } catch {
    return null
  }
}

export const useOpenedChatsStore = defineStore('openedChats', () => {
  // 刷新恢复：everActivated 一律 false，点开哪个才挂载哪个实例
  const saved = readPersisted()
  const chats = ref<OpenedChat[]>(
    (saved?.chats ?? []).map((c) => ({ ...c, everActivated: false })),
  )
  const activeAppId = ref<string | undefined>(saved?.activeAppId)
  // 生成中/有脏文件的页签集合（仅会话内，不持久化），页签栏据此显示指示点与关闭确认
  const generatingAppIds = ref<string[]>([])
  const dirtyAppIds = ref<string[]>([])

  const persist = () => {
    try {
      const data: PersistedChats = {
        chats: chats.value.map((c) => ({ appId: c.appId, appName: c.appName })),
        activeAppId: activeAppId.value,
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
    } catch {
      // 持久化失败不影响功能
    }
  }

  watch(chats, persist, { deep: true })
  watch(activeAppId, persist)

  const openChat = (appId?: string, appName?: string): boolean => {
    if (!appId) return false
    const existing = chats.value.find((c) => c.appId === appId)
    if (existing) {
      activeAppId.value = appId
      return true
    }
    if (chats.value.length >= MAX_OPEN_CHATS) {
      return false
    }
    chats.value.push({ appId, appName: appName || '', everActivated: false })
    activeAppId.value = appId
    return true
  }

  // 关闭页签。若关闭的是激活页签，返回新的激活页签 appId（调用方负责路由跳转），否则返回 undefined
  const closeChat = (appId: string): string | undefined => {
    const idx = chats.value.findIndex((c) => c.appId === appId)
    if (idx === -1) return undefined
    chats.value.splice(idx, 1)
    generatingAppIds.value = generatingAppIds.value.filter((id) => id !== appId)
    dirtyAppIds.value = dirtyAppIds.value.filter((id) => id !== appId)
    let newActive: string | undefined
    if (activeAppId.value === appId) {
      newActive = chats.value[Math.min(idx, chats.value.length - 1)]?.appId
      activeAppId.value = newActive
    }
    return newActive
  }

  // 激活页签并标记实例可挂载（懒挂载）
  const setActive = (appId: string) => {
    const target = chats.value.find((c) => c.appId === appId)
    if (!target) return
    target.everActivated = true
    activeAppId.value = appId
  }

  const renameTab = (appId: string, appName: string) => {
    const target = chats.value.find((c) => c.appId === appId)
    if (target) {
      target.appName = appName
    }
  }

  const setGenerating = (appId: string, on: boolean) => {
    generatingAppIds.value = on
      ? Array.from(new Set([...generatingAppIds.value, appId]))
      : generatingAppIds.value.filter((id) => id !== appId)
  }

  const setDirty = (appId: string, on: boolean) => {
    dirtyAppIds.value = on
      ? Array.from(new Set([...dirtyAppIds.value, appId]))
      : dirtyAppIds.value.filter((id) => id !== appId)
  }

  const clearAll = () => {
    chats.value = []
    activeAppId.value = undefined
    generatingAppIds.value = []
    dirtyAppIds.value = []
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
  }

  return {
    chats,
    activeAppId,
    generatingAppIds,
    dirtyAppIds,
    openChat,
    closeChat,
    setActive,
    renameTab,
    setGenerating,
    setDirty,
    clearAll,
  }
})
