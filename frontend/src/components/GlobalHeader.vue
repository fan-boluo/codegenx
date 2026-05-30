<template>
  <a-layout-header class="header">
    <div class="header-inner">
      <RouterLink to="/" class="header-brand">
        <span class="site-title">CodeGen<span class="title-accent">X</span></span>
      </RouterLink>

      <a-menu
        v-model:selectedKeys="selectedKeys"
        mode="horizontal"
        :items="menuItems"
        @click="handleMenuClick"
        class="nav-menu"
        :overflowedIndicator="null"
      />

      <div class="user-area">
        <div v-if="loginUserStore.loginUser.id" class="user-info">
          <a-dropdown>
            <a-space class="user-trigger">
              <a-avatar :src="loginUserStore.loginUser.userAvatar" :size="32" />
              <span class="user-name">{{ loginUserStore.loginUser.userName ?? '无名' }}</span>
            </a-space>
            <template #overlay>
              <a-menu>
                <a-menu-item @click="doLogout">
                  <LogoutOutlined />
                  退出登录
                </a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        </div>
        <div v-else>
          <a-button type="primary" href="/user/login" size="small">登录</a-button>
        </div>
      </div>
    </div>
  </a-layout-header>
</template>

<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { type MenuProps, message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser.ts'
import { userLogout } from '@/api/userController.ts'
import {
  LogoutOutlined,
  HomeOutlined,
  AppstoreOutlined,
  HistoryOutlined,
  DashboardOutlined,
} from '@ant-design/icons-vue'

const loginUserStore = useLoginUserStore()
const router = useRouter()
const selectedKeys = ref<string[]>(['/'])

router.afterEach((to) => {
  selectedKeys.value = [to.path]
})

const originItems = [
  {
    key: '/',
    icon: () => h(HomeOutlined),
    label: '我的项目',
    title: '我的项目',
  },
  {
    key: '/admin/appManage',
    icon: () => h(AppstoreOutlined),
    label: '项目管理',
    title: '项目管理',
  },
  {
    key: '/admin/userManage',
    icon: () => h(HomeOutlined),
    label: '用户管理',
    title: '用户管理',
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

const filterMenus = (menus = [] as MenuProps['items']) => {
  return menus?.filter((menu) => {
    const menuKey = menu?.key as string
    if (menuKey?.startsWith('/admin')) {
      const loginUser = loginUserStore.loginUser
      if (!loginUser || loginUser.userRole !== 'admin') {
        return false
      }
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

const doLogout = async () => {
  const res = await userLogout()
  if (res.data.code === 0) {
    loginUserStore.setLoginUser({ userName: '未登录' })
    message.success('退出登录成功')
    await router.push('/user/login')
  } else {
    message.error('退出登录失败，' + res.data.message)
  }
}
</script>

<style scoped>
.header {
  position: sticky;
  top: 0;
  z-index: 100;
  height: var(--header-height);
  padding: 0 32px;
  background: var(--bg-surface);
  border-bottom: 1px solid var(--border-default);
  display: flex;
  align-items: center;
}

.header-inner {
  display: flex;
  align-items: center;
  width: 100%;
  height: 100%;
  gap: 0;
}

.header-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  text-decoration: none;
  flex-shrink: 0;
}

.site-title {
  font-family: var(--font-sans);
  font-size: 20px;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.02em;
  white-space: nowrap;
}

.title-accent {
  color: var(--accent-primary);
  font-weight: 700;
}

.nav-menu {
  background: transparent !important;
  flex: 1;
  min-width: 0;
  overflow: visible !important;
  display: flex;
  justify-content: center;
  border-bottom: none !important;
}

.nav-menu :deep(.ant-menu-overflow) {
  display: none !important;
}

.nav-menu :deep(.ant-menu-overflow-item) {
  display: none !important;
}

.nav-menu :deep(.ant-menu-item),
.nav-menu :deep(.ant-menu-submenu) {
  display: inline-flex !important;
}

.nav-menu :deep(.ant-menu-item) {
  color: var(--text-secondary);
  font-family: var(--font-sans);
  font-size: 14px;
  font-weight: 500;
  border-radius: 6px;
  margin: 0 2px;
  padding: 0 16px;
  transition: all 0.15s var(--ease-out);
}

.nav-menu :deep(.ant-menu-item:hover) {
  color: var(--accent-primary) !important;
  background: var(--accent-primary-subtle);
}

.nav-menu :deep(.ant-menu-item-selected) {
  color: var(--accent-primary) !important;
  background: transparent;
  font-weight: 600;
}

.nav-menu :deep(.ant-menu-item-selected::after) {
  border-bottom: 2px solid var(--accent-primary) !important;
}

.user-area {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  margin-left: auto;
}

.user-trigger {
  cursor: pointer;
  padding: 4px 12px;
  border-radius: 6px;
  transition: background 0.15s var(--ease-out);
}

.user-trigger:hover {
  background: var(--bg-hover);
}

.user-name {
  font-family: var(--font-sans);
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.user-info {
  display: flex;
  align-items: center;
}
</style>
