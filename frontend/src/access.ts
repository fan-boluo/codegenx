import { useLoginUserStore } from '@/stores/loginUser'
import { message } from 'ant-design-vue'
import router from '@/router'

// 是否为首次获取登录用户
let firstFetchLoginUser = true
const publicRoutes = ['/user/login', '/user/register']

/**
 * 全局权限校验
 */
router.beforeEach(async (to) => {
  const loginUserStore = useLoginUserStore()
  let loginUser = loginUserStore.loginUser
  // 确保页面刷新，首次加载时，能够等后端返回用户信息后再校验权限
  if (firstFetchLoginUser) {
    await loginUserStore.fetchLoginUser()
    loginUser = loginUserStore.loginUser
    firstFetchLoginUser = false
  }

  if (!loginUser?.id && !publicRoutes.includes(to.path)) {
    return `/user/login?redirect=${encodeURIComponent(to.fullPath)}`
  }

  if (loginUser?.id && publicRoutes.includes(to.path)) {
    const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/'
    return redirect
  }

  const toUrl = to.fullPath
  if (toUrl.startsWith('/admin')) {
    if (!loginUser || loginUser.userRole !== 'admin') {
      message.error('没有权限')
      return `/user/login?redirect=${to.fullPath}`
    }
  }
  return true
})
