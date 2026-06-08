<template>
  <div class="global-header">
    <div class="header-left">
      <a
        class="header-logo"
        href="/"
        @click.prevent="$router.push('/')"
      >
        <img src="../../public/logo.png" alt="logo" class="logo-img" />
        <span class="logo-text">CodeGenX</span>
      </a>
      <a-menu
        v-if="menuItems.length"
        v-model:selectedKeys="selectedKeys"
        mode="horizontal"
        :items="menuItems"
        @click="handleMenuClick"
        class="header-menu"
      />
    </div>
    <div class="header-right">
      <div v-if="loginUserStore.loginUser.id" class="user-info">
        <a-dropdown>
          <div class="user-dropdown-trigger">
            <a-avatar :src="loginUserStore.loginUser.userAvatar" :size="28" />
            <span class="user-name">{{ loginUserStore.loginUser.userName }}</span>
          </div>
          <template #overlay>
            <a-menu @click="doLogout">
              <a-menu-item key="logout">
                <LogoutOutlined />
                <span>退出登录</span>
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </div>
      <a-button v-else type="primary" href="/user/login" size="small">登录</a-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type MenuProps, message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser.ts'
import { userLogout } from '@/api/userController.ts'
import { useIdleTimeout } from '@/composables/useIdleTimeout'
import {
  LogoutOutlined,
  HomeOutlined,
  AppstoreOutlined,
  HistoryOutlined,
  DashboardOutlined,
} from '@ant-design/icons-vue'

const loginUserStore = useLoginUserStore()
const router = useRouter()

const selectedKeys = ref<string[]>([])

const originItems: MenuProps['items'] = [
  {
    key: '/',
    icon: () => h(HomeOutlined),
    label: '首页',
    title: '首页',
  },
  {
    key: '/projects',
    icon: () => h(AppstoreOutlined),
    label: '项目',
    title: '项目',
  },
  {
    key: '/admin/chatManage',
    icon: () => h(HistoryOutlined),
    label: '对话管理',
    title: '对话管理',
  },
  {
    key: '/admin/monitor',
    icon: () => h(DashboardOutlined),
    label: '监控中心',
    title: '监控中心',
  },
]

const filterMenus = (items: MenuProps['items']): MenuProps['items'] => {
  if (!items) return []
  return items.filter(item => {
    if (!item) return false
    const key = 'key' in item ? (item as any).key : ''
    if (key.startsWith('/admin')) {
      return loginUserStore.loginUser.userRole === 'admin'
    }
    return true
  })
}

const menuItems = computed<MenuProps['items']>(() => filterMenus(originItems))

const handleMenuClick: MenuProps['onClick'] = (e) => {
  const key = e.key as string
  selectedKeys.value = [key]
  if (key.startsWith('/')) {
    router.push(key)
  }
}

const clearLocalAuth = () => {
  loginUserStore.setLoginUser({ userName: '未登录' })
  localStorage.removeItem('token')
  // 清理所有 session 相关 localStorage
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith('codegenx:app-chat-session:')) {
      localStorage.removeItem(key)
    }
  }
}

const performIdleLogout = () => {
  if (!loginUserStore.loginUser.id) return
  clearLocalAuth()
  message.warning('长时间未操作，已自动退出登录')
  router.push('/user/login')
}

useIdleTimeout(performIdleLogout)

const doLogout = async () => {
  const res = await userLogout()
  if (res.data.code === 0) {
    clearLocalAuth()
    message.success('退出登录成功')
    await router.push('/user/login')
  } else {
    message.error('退出登录失败，' + res.data.message)
  }
}
</script>

<style scoped></style>
