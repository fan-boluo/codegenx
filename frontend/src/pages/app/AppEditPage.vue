<template>
  <div id="appEditPage">
    <div class="page-header">
      <h1>编辑项目信息</h1>
    </div>

    <div class="edit-container">
      <a-card title="项目信息" :loading="loading" class="edit-card">
        <a-descriptions :column="2" bordered style="margin-bottom: 24px">
          <a-descriptions-item label="项目ID">{{ appInfo.id }}</a-descriptions-item>
          <a-descriptions-item label="创建者">
            {{ appInfo.userName || '未知用户' }}
          </a-descriptions-item>
          <a-descriptions-item label="创建时间">{{
            formatTime(appInfo.createTime)
          }}</a-descriptions-item>
          <a-descriptions-item label="更新时间">{{
            formatTime(appInfo.updateTime)
          }}</a-descriptions-item>
        </a-descriptions>

        <a-form
          :model="formData"
          :rules="rules"
          layout="vertical"
          @finish="handleSubmit"
          ref="formRef"
        >
          <a-form-item label="项目名称" name="appName">
            <a-input
              v-model:value="formData.appName"
              placeholder="请输入项目名称"
              :maxlength="50"
              show-count
            />
          </a-form-item>
          <a-form-item label="数据库" name="dbName">
            <a-input :value="appInfo?.dbName" placeholder="数据库名称" disabled />
          </a-form-item>

          <a-form-item label="项目描述" name="initPrompt">
            <a-textarea
              v-model:value="formData.initPrompt"
              placeholder="请介绍一下这个项目"
              :rows="4"
              :maxlength="1000"
              show-count
              disabled
            />
            <div class="form-tip">初始提示词不可修改</div>
          </a-form-item>

          <a-form-item>
            <a-space>
              <a-button type="primary" html-type="submit" :loading="submitting">保存修改</a-button>
              <a-button @click="resetForm">重置</a-button>
              <a-button type="link" @click="goToChat">进入对话</a-button>
            </a-space>
          </a-form-item>
        </a-form>
      </a-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { useLoginUserStore } from '@/stores/loginUser'
import { getAppVoById, updateApp, updateAppByAdmin } from '@/api/appController'
import { formatTime } from '@/utils/time'
import UserInfo from '@/components/UserInfo.vue'
import { getStaticPreviewUrl } from '@/config/env'
import type { FormInstance } from 'ant-design-vue'

const route = useRoute()
const router = useRouter()
const loginUserStore = useLoginUserStore()

const appInfo = ref<API.AppVO>({})
const loading = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()

const formData = reactive({
  appName: '',
  cover: '',
  priority: 0,
  initPrompt: '',
  deployKey: '',
})

const isAdmin = computed(() => loginUserStore.loginUser.userRole === 'admin')

const rules = {
  appName: [
    { required: true, message: '请输入项目名称', trigger: 'blur' },
    { min: 1, max: 50, message: '项目名称长度在1-50个字符', trigger: 'blur' },
  ],
}

const fetchAppInfo = async () => {
  const id = route.params.id as string
  if (!id) {
    message.error('项目ID不存在')
    router.push('/')
    return
  }
  loading.value = true
  try {
    const res = await getAppVoById({ id: id as unknown as number })
    if (res.data.code === 0 && res.data.data) {
      appInfo.value = res.data.data
      if (!isAdmin.value && appInfo.value.userId !== loginUserStore.loginUser.id) {
        message.error('您没有权限编辑此项目')
        router.push('/')
        return
      }
      formData.appName = appInfo.value.appName || ''
      formData.cover = appInfo.value.cover || ''
      formData.priority = appInfo.value.priority || 0
      formData.initPrompt = appInfo.value.initPrompt || ''
      formData.deployKey = appInfo.value.deployKey || ''
    } else {
      message.error('获取项目信息失败')
      router.push('/')
    }
  } catch (error) {
    console.error('获取项目信息失败：', error)
    message.error('获取项目信息失败')
    router.push('/')
  } finally {
    loading.value = false
  }
}

const handleSubmit = async () => {
  if (!appInfo.value?.id) return
  submitting.value = true
  try {
    let res
    if (isAdmin.value) {
      res = await updateAppByAdmin({
        id: appInfo.value.id,
        appName: formData.appName,
        initPrompt: formData.initPrompt,
      })
    } else {
      res = await updateApp({
        id: appInfo.value.id,
        appName: formData.appName,
        initPrompt: formData.initPrompt,
      })
    }
    if (res.data.code === 0) {
      message.success('修改成功')
      await fetchAppInfo()
    } else {
      message.error('修改失败：' + res.data.message)
    }
  } catch (error) {
    console.error('修改失败：', error)
    message.error('修改失败')
  } finally {
    submitting.value = false
  }
}

const resetForm = () => {
  if (appInfo.value) {
    formData.appName = appInfo.value.appName || ''
    formData.cover = appInfo.value.cover || ''
    formData.priority = appInfo.value.priority || 0
  }
  formRef.value?.clearValidate()
}

const goToChat = () => {
  if (appInfo.value?.id) {
    router.push(`/app/chat/${appInfo.value.id}`)
  }
}
const openPreview = () => {
  if (appInfo.value?.deployKey) {
    const url = getStaticPreviewUrl(appInfo.value.deployKey)
    window.open(url, '_blank')
  }
}

onMounted(() => {
  fetchAppInfo()
})
</script>

<style scoped>
#appEditPage {
  padding: 32px 24px;
  max-width: 880px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 24px;
}

.page-header h1 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.edit-card,
.info-card {
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-card);
  box-shadow: var(--shadow-sm);
}

.cover-preview {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--border-default);
  border-radius: 6px;
  background: var(--bg-subtle);
}

.form-tip {
  font-size: 12px;
  color: var(--text-muted);
  margin-top: 4px;
}

:deep(.ant-descriptions-item-label) {
  background: var(--bg-subtle);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
}

:deep(.ant-descriptions-item-content) {
  background: transparent;
  color: var(--text-primary);
}
</style>
