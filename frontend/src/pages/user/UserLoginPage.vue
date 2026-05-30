<template>
  <div id="userLoginPage">
    <div class="login-card">
      <h2 class="title">用户登录</h2>

      <a-form :model="formState" name="basic" autocomplete="off" @finish="handleSubmit">
        <a-form-item name="userAccount" :rules="[{ required: true, message: '请输入账号' }]">
          <a-input v-model:value="formState.userAccount" placeholder="请输入账号" size="large" />
        </a-form-item>

        <a-form-item
          name="userPassword"
          :rules="[
            { required: true, message: '请输入密码' },
            { min: 8, message: '密码长度不能小于 8 位' },
          ]"
        >
          <a-input-password v-model:value="formState.userPassword" placeholder="请输入密码" size="large" />
        </a-form-item>

        <div class="tips">
          没有账号
          <RouterLink to="/user/register">去注册</RouterLink>
        </div>

        <a-form-item>
          <a-button type="primary" html-type="submit" size="large" style="width: 100%">登录</a-button>
        </a-form-item>
      </a-form>
    </div>
  </div>
</template>

<script lang="ts" setup>
import { reactive } from 'vue'
import { userLogin } from '@/api/userController.ts'
import { useLoginUserStore } from '@/stores/loginUser.ts'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'

const formState = reactive<API.UserLoginRequest>({
  userAccount: '',
  userPassword: '',
})

const router = useRouter()
const route = useRoute()
const loginUserStore = useLoginUserStore()

const handleSubmit = async (values: any) => {
  const res = await userLogin(values)
  if (res.data.code === 0 && res.data.data) {
    const token = res.data.data
    localStorage.setItem('token', token)
    await loginUserStore.fetchLoginUser()
    message.success('登录成功')
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/'
    router.replace(redirect)
  } else {
    message.error('登录失败，' + res.data.message)
  }
}
</script>

<style scoped>
#userLoginPage {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 64px - 100px);
  padding: 24px;
}

.login-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-default);
  border-radius: 10px;
  padding: 36px 40px;
  width: 100%;
  max-width: 420px;
  box-shadow: var(--shadow-card);
}

.title {
  text-align: center;
  margin: 0 0 28px;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.tips {
  text-align: right;
  color: var(--text-muted);
  font-size: 13px;
  margin-bottom: 16px;
}
</style>
