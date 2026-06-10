<template>
  <div class="global-header">
    <div class="header-left">
      <a class="header-logo" href="/" @click.prevent="$router.push('/')">
        <img src="../../public/logo.png" alt="logo" class="logo-img" />
        <span class="logo-text">数据分析平台</span>
      </a>
      <a-menu
        v-if="loginUserStore.loginUser.id && menuItems.length"
        v-model:selectedKeys="selectedKeys"
        mode="horizontal"
        :items="menuItems"
        @click="handleMenuClick"
        class="header-menu"
      />
    </div>
    <div v-if="loginUserStore.loginUser.id" class="header-right">
      <div class="user-info">
        <a-dropdown>
          <div class="user-dropdown-trigger">
            <a-avatar :src="loginUserStore.loginUser.userAvatar || undefined" :size="28">
              {{ loginUserStore.loginUser.userName?.charAt(0) || 'U' }}
            </a-avatar>
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
  UserOutlined,
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
    key: '/admin/appManage',
    icon: () => h(AppstoreOutlined),
    label: '项目管理',
    title: '项目管理',
  },
  {
    key: '/admin/userManage',
    icon: () => h(UserOutlined),
    label: '用户管理',
    title: '用户管理',
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
  return items.filter((item) => {
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

<style scoped>
.global-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
}

.header-left {
  display: flex;
  align-items: center;
  flex: 1;
}

.header-logo {
  display: flex;
  align-items: center;
  margin-right: 40px;
  text-decoration: none;
  white-space: nowrap;
}

.logo-img {
  height: 32px;
  width: 32px;
  margin-right: 8px;
}

.logo-text {
  font-size: 23px;
  font-weight: 600;
  color: #1677ff;
}

.header-menu {
  flex: 1;
  border-bottom: none;
}

.header-right {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}

.user-info {
  display: flex;
  align-items: center;
  cursor: pointer;
}

.user-dropdown-trigger {
  display: flex;
  align-items: center;
  gap: 8px;
}

.user-name {
  margin-left: 8px;
  font-size: 14px;
  color: #333;
}
</style>
