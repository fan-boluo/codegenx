// @ts-ignore
/* eslint-disable */
import request from '@/request'

/** Add User POST /api/user/add */
export async function addUserApiUserAddPost(
  body: API.UserAddRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseStr_>('/api/user/add', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Delete User POST /api/user/delete */
export async function deleteUserApiUserDeletePost(
  body: API.DeleteRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseBool_>('/api/user/delete', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Get User By Id GET /api/user/get */
export async function getUserByIdApiUserGetGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getUserByIdApiUserGetGetParams,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseUserRawVO_>('/api/user/get', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** Get Login GET /api/user/get/login */
export async function getLoginApiUserGetLoginGet(options?: { [key: string]: any }) {
  return request<API.BaseResponseLoginUserVO_>('/api/user/get/login', {
    method: 'GET',
    ...(options || {}),
  })
}

/** Get User Vo By Id GET /api/user/get/vo */
export async function getUserVoByIdApiUserGetVoGet(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getUserVoByIdApiUserGetVoGetParams,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseUserVO_>('/api/user/get/vo', {
    method: 'GET',
    params: {
      ...params,
    },
    ...(options || {}),
  })
}

/** List User Vo By Page POST /api/user/list/page/vo */
export async function listUserVoByPageApiUserListPageVoPost(
  body: API.UserQueryRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponsePageDataUserVO_>('/api/user/list/page/vo', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** User Login POST /api/user/login */
export async function userLoginApiUserLoginPost(
  body: API.UserLoginRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseStr_>('/api/user/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** User Logout POST /api/user/logout */
export async function userLogoutApiUserLogoutPost(options?: { [key: string]: any }) {
  return request<API.BaseResponseBool_>('/api/user/logout', {
    method: 'POST',
    ...(options || {}),
  })
}

/** User Register POST /api/user/register */
export async function userRegisterApiUserRegisterPost(
  body: API.UserRegisterRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseStr_>('/api/user/register', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}

/** Update User POST /api/user/update */
export async function updateUserApiUserUpdatePost(
  body: API.UserUpdateRequest,
  options?: { [key: string]: any }
) {
  return request<API.BaseResponseBool_>('/api/user/update', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  })
}
