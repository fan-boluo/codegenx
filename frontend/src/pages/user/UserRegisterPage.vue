<template>
  <div id="userRegisterPage">
    <div class="register-card">
      <h2 class="title">用户注册</h2>

      <a-form :model="formState" name="basic" autocomplete="off" @finish="handleSubmit">
        <a-form-item name="userAccount" :rules="[{ required: true, message: '请输入账号' }]">
          <a-input v-model:value="formState.userAccount" placeholder="请输入账号" size="large" />
        </a-form-item>

        <a-form-item name="userName" :rules="[{ max: 20, message: '用户名不能超过20个字符' }]">
          <a-input v-model:value="formState.userName" placeholder="请输入用户名（可选）" size="large" />
        </a-form-item>

        <a-form-item
          name="userPassword"
          :rules="[
            { required: true, message: '请输入密码' },
            { min: 8, message: '密码不能小于 8 位' },
          ]"
        >
          <a-input-password v-model:value="formState.userPassword" placeholder="请输入密码" size="large" />
        </a-form-item>

        <a-form-item
          name="checkPassword"
          :rules="[
            { required: true, message: '请确认密码' },
            { min: 8, message: '密码不能小于 8 位' },
            { validator: validateCheckPassword },
          ]"
        >
          <a-input-password v-model:value="formState.checkPassword" placeholder="请确认密码" size="large" />
        </a-form-item>

        <div class="tips">
          已有账号？
          <RouterLink to="/user/login">去登录</RouterLink>
        </div>

        <a-form-item>
          <a-button type="primary" html-type="submit" size="large" style="width: 100%">注册</a-button>
        </a-form-item>
      </a-form>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useRouter } from 'vue-router'
import { userRegister } from '@/api/userController.ts'
import { message } from 'ant-design-vue'
import { reactive } from 'vue'

const router = useRouter()

type RegisterFormState = API.UserRegisterRequest & {
  userName?: string
}

const formState = reactive<RegisterFormState>({
  userAccount: '',
  userPassword: '',
  checkPassword: '',
  userName: '',
})

const validateCheckPassword = (rule: unknown, value: string, callback: (error?: Error) => void) => {
  if (value && value !== formState.userPassword) {
    callback(new Error('两次输入密码不一致'))
  } else {
    callback()
  }
}

const handleSubmit = async (values: RegisterFormState) => {
  const registerPayload: API.UserRegisterRequest = {
    userAccount: values.userAccount,
    userPassword: values.userPassword,
    checkPassword: values.checkPassword,
  }
  const res = await userRegister(registerPayload)
  if (res.data.code === 0) {
    message.success('注册成功')
    router.push({ path: '/user/login', replace: true })
  } else {
    message.error('注册失败，' + res.data.message)
  }
}
</script>

<style scoped>
#userRegisterPage {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: calc(100vh - 64px - 100px);
  padding: 24px;
}

.register-card {
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
  margin-bottom: 16px;
  color: var(--text-muted);
  font-size: 13px;
  text-align: right;
}
</style>
