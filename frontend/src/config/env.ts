/**
 * 环境变量配置
 */
// 应用部署域名
export const DEPLOY_DOMAIN = import.meta.env.VITE_DEPLOY_DOMAIN || 'http://localhost'

// API 基础地址
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8456'

// 静态资源地址
export const STATIC_BASE_URL = `${API_BASE_URL}/api/app/static`

// 获取部署应用的完整URL
export const getDeployUrl = (deployKey: string) => {
  return `${DEPLOY_DOMAIN}/${deployKey}`
}

// 获取静态资源预览URL
export const getStaticPreviewUrl = (deployKey: string) => {
  return `${STATIC_BASE_URL}/${deployKey}/`
}

export const getGeneratedPreviewUrl = (appId: string | number, codeGenType: string) => {
  return `${API_BASE_URL}/api/app/preview/${encodeURIComponent(codeGenType)}/${encodeURIComponent(String(appId))}/`
}
