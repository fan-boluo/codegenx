// @ts-ignore
/* eslint-disable */
import request from '@/request';
/** Add User POST /api/user/add */
export async function addUserApiUserAddPost(body, options) {
    return request('/api/user/add', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
/** Delete User POST /api/user/delete */
export async function deleteUserApiUserDeletePost(body, options) {
    return request('/api/user/delete', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
/** Get User By Id GET /api/user/get */
export async function getUserByIdApiUserGetGet(
// 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
params, options) {
    return request('/api/user/get', {
        method: 'GET',
        params: {
            ...params,
        },
        ...(options || {}),
    });
}
/** Get Login GET /api/user/get/login */
export async function getLoginApiUserGetLoginGet(options) {
    return request('/api/user/get/login', {
        method: 'GET',
        ...(options || {}),
    });
}
/** Get User Vo By Id GET /api/user/get/vo */
export async function getUserVoByIdApiUserGetVoGet(
// 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
params, options) {
    return request('/api/user/get/vo', {
        method: 'GET',
        params: {
            ...params,
        },
        ...(options || {}),
    });
}
/** List User Vo By Page POST /api/user/list/page/vo */
export async function listUserVoByPageApiUserListPageVoPost(body, options) {
    return request('/api/user/list/page/vo', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
/** User Login POST /api/user/login */
export async function userLoginApiUserLoginPost(body, options) {
    return request('/api/user/login', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
/** User Logout POST /api/user/logout */
export async function userLogoutApiUserLogoutPost(options) {
    return request('/api/user/logout', {
        method: 'POST',
        ...(options || {}),
    });
}
/** User Register POST /api/user/register */
export async function userRegisterApiUserRegisterPost(body, options) {
    return request('/api/user/register', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
/** Update User POST /api/user/update */
export async function updateUserApiUserUpdatePost(body, options) {
    return request('/api/user/update', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        data: body,
        ...(options || {}),
    });
}
