// @ts-ignore
/* eslint-disable */
import request from '@/request'

/** Delete App DELETE /api/app */
export async function deleteAppApiAppDelete(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.deleteAppApiAppDeleteParams,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app', {
    method: 'DELETE',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** Get App GET /api/app/${param0} */
export async function getAppApiAppAppIdGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getAppApiAppAppIdGetParams,
  options?: { [key: string]: any }
) {
  const { app_id: param0, ...queryParams } = params
  return request<any>(`/api/app/${param0}`, {
    method: 'GET',
    params: { ...queryParams },
    ...(options || {}),
  })
}

/** Add App POST /api/app/add */
export async function addAppApiAppAddPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/add', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Admin Delete App POST /api/app/admin/delete */
export async function adminDeleteAppApiAppAdminDeletePost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/admin/delete', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Get App Vo By Admin GET /api/app/admin/get/vo */
export async function getAppVoByAdminApiAppAdminGetVoGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getAppVoByAdminApiAppAdminGetVoGetParams,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/admin/get/vo', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** List All Apps For Admin POST /api/app/admin/list/page/vo */
export async function listAllAppsForAdminApiAppAdminListPageVoPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/admin/list/page/vo', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Admin Update App POST /api/app/admin/update */
export async function adminUpdateAppApiAppAdminUpdatePost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/admin/update', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Chat To Gen Code Get GET /api/chat/gen/code */
export async function chatToGenCodeGetApiAppChatGenCodeGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.chatToGenCodeGetApiAppChatGenCodeGetParams,
  options?: { [key: string]: any }
) {
  return request<any>('/api/chat/gen/code', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** Chat To Gen Code Post POST /api/chat/gen/code */
export async function chatToGenCodePostApiAppChatGenCodePost(
  body: API.AppChatRequest,
  options?: { [key: string]: any }
) {
  return request<any>('/api/chat/gen/code', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Add App POST /api/app/create */
export async function addAppApiAppCreatePost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/create', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Delete App Post POST /api/app/delete */
export async function deleteAppPostApiAppDeletePost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/delete', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Deploy App POST /api/app/deploy */
export async function deployAppApiAppDeployPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/deploy', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Download App Code GET /api/app/download/${param0} */
export async function downloadAppCodeApiAppDownloadAppIdGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.downloadAppCodeApiAppDownloadAppIdGetParams,
  options?: { [key: string]: any }
) {
  const { app_id: param0, ...queryParams } = params
  return request<any>(`/api/app/download/${param0}`, {
    method: 'GET',
    params: { ...queryParams },
    ...(options || {}),
  })
}

/** Get App Vo GET /api/app/get/vo */
export async function getAppVoApiAppGetVoGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getAppVoApiAppGetVoGetParams,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/get/vo', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** List Good Apps POST /api/app/good/list/page/vo */
export async function listGoodAppsApiAppGoodListPageVoPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/good/list/page/vo', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** List My Apps POST /api/app/my/list/page/vo */
export async function listMyAppsApiAppMyListPageVoPost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/my/list/page/vo', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Serve Static Resource GET /api/app/static/${param0} */
export async function serveStaticResourceApiAppStaticDeployKeyGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.serveStaticResourceApiAppStaticDeployKeyGetParams,
  options?: { [key: string]: any }
) {
  const { deploy_key: param0, ...queryParams } = params
  return request<any>(`/api/app/static/${param0}`, {
    method: 'GET',
    params: {
      ...queryParams,
    },
    ...(options || {}),
  })
}

/** Serve Static Resource GET /api/app/static/${param0}/${param1} */
export async function serveStaticResourceApiAppStaticDeployKeyResourcePathGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.serveStaticResourceApiAppStaticDeployKeyResourcePathGetParams,
  options?: { [key: string]: any }
) {
  const { deploy_key: param0, resource_path: param1, ...queryParams } = params
  return request<any>(`/api/app/static/${param0}/${param1}`, {
    method: 'GET',
    params: { ...queryParams },
    ...(options || {}),
  })
}

/** Update App POST /api/app/update */
export async function updateAppApiAppUpdatePost(
  body: Record<string, any>,
  options?: { [key: string]: any }
) {
  return request<any>('/api/app/update', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}
