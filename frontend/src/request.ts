import axios from 'axios'
import { message } from 'ant-design-vue'
import { API_BASE_URL } from '@/config/env'

const TOKEN_KEY = 'token'

export const clearLocalAuth = () => {
  localStorage.removeItem(TOKEN_KEY)
  for (const key of Object.keys(localStorage)) {
    if (key.startsWith('codegenx:app-chat-session:')) {
      localStorage.removeItem(key)
    }
  }
}

const redirectToLogin = (reason: string) => {
  if (window.location.pathname.includes('/user/login')) return
  message.warning(reason)
  const redirect = `${window.location.pathname}${window.location.search}${window.location.hash}`
  window.location.href = `/user/login?redirect=${encodeURIComponent(redirect)}`
}

// 创建 Axios 实例
const myAxios = axios.create({
  baseURL: API_BASE_URL,
  timeout: 60000,
  withCredentials: true,
})

// 全局请求拦截器
myAxios.interceptors.request.use(
  function (config) {
    const token = localStorage.getItem(TOKEN_KEY)
    if (token) {
      config.headers = config.headers ?? {}
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  function (error) {
    return Promise.reject(error)
  },
)

// 全局响应拦截器
myAxios.interceptors.response.use(
  function (response) {
    const { data } = response
    // 未登录 / token 过期 / token 被撤销
    if (data.code === 40100) {
      if (
        !response.request.responseURL.includes('user/get/login') &&
        !window.location.pathname.includes('/user/login')
      ) {
        redirectToLogin('登录已失效，请重新登录')
      }
    }
    return response
  },
  function (error) {
    // HTTP 401 → token 无效或过期，清本地状态跳登录
    if (error.response?.status === 401) {
      clearLocalAuth()
      redirectToLogin('登录已失效，请重新登录')
    }
    return Promise.reject(error)
  },
)

export default myAxios
