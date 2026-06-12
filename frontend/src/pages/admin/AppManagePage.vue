<template>
  <div id="appManagePage">
    <a-form layout="inline" :model="searchParams" @finish="doSearch">
      <a-form-item label="项目名称">
        <a-input v-model:value="searchParams.appName" placeholder="输入项目名称" />
      </a-form-item>
      <a-form-item label="创建者">
        <a-input v-model:value="searchParams.userId" placeholder="输入用户ID" />
      </a-form-item>
      <a-form-item>
        <a-button type="primary" html-type="submit">搜索</a-button>
      </a-form-item>
    </a-form>
    <a-divider />

    <a-table
      :columns="columns"
      :data-source="data"
      :pagination="pagination"
      @change="doTableChange"
      :scroll="{ x: 1200 }"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.dataIndex === 'id' || column.dataIndex === 'appName'">
          {{ record[column.dataIndex] }}
        </template>
        <template v-else-if="column.dataIndex === 'initPrompt'">
          <a-tooltip :title="record.initPrompt">
            <div class="prompt-text">{{ record.initPrompt }}</div>
          </a-tooltip>
        </template>
        <template v-else-if="column.dataIndex === 'createTime'">
          {{ formatTime(record.createTime) }}
        </template>
        <template v-else-if="column.dataIndex === 'user'">
          {{ record.userName || '未知用户' }}
        </template>
        <template v-else-if="column.key === 'action'">
          <a-space>
            <a-button type="primary" size="small" @click="editApp(record)">编辑</a-button>
            <a-popconfirm title="确定要删除这个项目吗？" @confirm="deleteApp(record.id)">
              <a-button danger size="small">删除</a-button>
            </a-popconfirm>
          </a-space>
        </template>
      </template>
    </a-table>
    <!-- 编辑弹窗 -->
    <a-modal
      v-model:open="editModalOpen"
      :title="`编辑项目 - ${currentApp?.appName}`"
      width="800px"
      @ok="handleEditOk"
      @cancel="handleEditCancel"
      :footer="null"
    >
      <div class="edit-form-container">
        <a-descriptions :column="2" bordered style="margin-bottom: 24px">
          <a-descriptions-item label="项目ID">{{ currentApp?.id }}</a-descriptions-item>
          <a-descriptions-item label="创建者">
            {{ currentApp?.userName || '未知用户' }}
          </a-descriptions-item>
          <a-descriptions-item label="创建时间">{{
            formatTime(currentApp?.createTime)
          }}</a-descriptions-item>
          <a-descriptions-item label="更新时间">{{
            formatTime(currentApp?.updateTime)
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
            <a-input :value="currentApp?.dbName" placeholder="数据库名称" disabled />
          </a-form-item>

          <a-form-item label="项目描述" name="initPrompt">
            <a-textarea
              v-model:value="formData.initPrompt"
              placeholder="请介绍一下这个项目"
              :rows="4"
              :maxlength="1000"
              show-count
            />
          </a-form-item>

          <a-form-item style="margin-bottom: 0">
            <a-space>
              <a-button type="primary" html-type="submit" :loading="submitting">保存修改</a-button>
              <a-button @click="handleEditCancel">关闭</a-button>
            </a-space>
          </a-form-item>
        </a-form>
      </div>
    </a-modal>
  </div>
</template>

<script lang="ts" setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  listAppVoByPageByAdmin,
  deleteAppByAdmin,
  updateAppByAdmin,
  getAppVoById,
} from '@/api/appController'
import { formatTime } from '@/utils/time'
const router = useRouter()

const columns = [
  { title: 'ID', dataIndex: 'id', width: 80, fixed: 'left' },
  { title: '项目名称', dataIndex: 'appName', width: 150 },
  { title: '项目描述', dataIndex: 'initPrompt', width: 200 },
  { title: '创建者', dataIndex: 'user', width: 120 },
  { title: '创建时间', dataIndex: 'createTime', width: 160 },
  { title: '操作', key: 'action', width: 220, fixed: 'right' },
]

const data = ref<API.AppVO[]>([])
const total = ref(0)

const searchParams = reactive<API.AppQueryRequest>({
  pageNum: 1,
  pageSize: 10,
})
// 弹窗相关
const editModalOpen = ref(false)
const currentApp = ref<API.AppVO>({})
const formData = reactive({
  appName: '',
  initPrompt: '',
})
const formRef = ref()
const submitting = ref(false)
const fetchData = async () => {
  try {
    const res = await listAppVoByPageByAdmin({ ...searchParams })
    if (res.data.data) {
      data.value = res.data.data.records ?? []
      total.value = res.data.data.totalRow ?? 0
    } else {
      message.error('获取数据失败，' + res.data.message)
    }
  } catch (error) {
    console.error('获取数据失败：', error)
    message.error('获取数据失败')
  }
}

onMounted(() => {
  fetchData()
})

const pagination = computed(() => ({
  current: searchParams.pageNum ?? 1,
  pageSize: searchParams.pageSize ?? 10,
  total: total.value,
  showSizeChanger: true,
  showTotal: (total: number) => `共 ${total} 条`,
}))

const doTableChange = (page: { current: number; pageSize: number }) => {
  searchParams.pageNum = page.current
  searchParams.pageSize = page.pageSize
  fetchData()
}

const doSearch = () => {
  searchParams.pageNum = 1
  fetchData()
}

const editApp = async (app: API.AppVO) => {
  try {
    const res = await getAppVoById({ id: app.id as unknown as number })
    console.log(res)
    if (res.data.code === 0 && res.data.data) {
      currentApp.value = res.data.data
      formData.appName = res.data.data.appName || ''
      formData.initPrompt = res.data.data.initPrompt || ''
      editModalOpen.value = true
    } else {
      message.error('获取项目信息失败')
    }
  } catch (error) {
    console.error('获取项目信息失败: ', error)
    message.error('获取项目信息失败')
  }
}

const handleSubmit = async () => {
  if (!currentApp.value?.id) return
  submitting.value = true
  try {
    const res = await updateAppByAdmin({
      id: currentApp.value.id,
      appName: formData.appName,
      initPrompt: formData.initPrompt,
    })
    if (res.data.code === 0) {
      message.success('修改成功')
      editModalOpen.value = false
      await fetchData()
    } else {
      message.error('修改失败: ' + res.data.message)
    }
  } catch (error) {
    console.error('修改失败: ', error)
    message.error('修改失败')
  } finally {
    submitting.value = false
  }
}

const handleEditOk = async () => {
  await handleSubmit()
}

const handleEditCancel = () => {
  editModalOpen.value = false
  currentApp.value = {}
  formData.appName = ''
  formData.initPrompt = ''
}

const rules = {
  appName: [
    { required: true, message: '请输入项目名称', trigger: 'blur' },
    { min: 1, max: 50, message: '项目名称长度在1-50个字符', trigger: 'blur' },
  ],
}

const toggleFeatured = async (app: API.AppVO) => {
  if (!app.id) return
  const newPriority = app.priority === 99 ? 0 : 99
  try {
    const res = await updateAppByAdmin({ id: app.id, priority: newPriority })
    if (res.data.code === 0) {
      message.success(newPriority === 99 ? '已设为精选' : '已取消精选')
      fetchData()
    } else {
      message.error('操作失败：' + res.data.message)
    }
  } catch (error) {
    console.error('操作失败：', error)
    message.error('操作失败')
  }
}

const deleteApp = async (id: number | undefined) => {
  if (!id) return
  try {
    const res = await deleteAppByAdmin({ id })
    if (res.data.code === 0) {
      message.success('删除成功')
      fetchData()
    } else {
      message.error('删除失败：' + res.data.message)
    }
  } catch (error) {
    console.error('删除失败：', error)
    message.error('删除失败')
  }
}
</script>

<style scoped>
#appManagePage {
  padding: 24px;
}

.no-cover {
  width: 80px;
  height: 60px;
  background: var(--bg-subtle);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 12px;
  border-radius: 4px;
}

.prompt-text {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.text-muted {
  color: var(--text-muted);
}
.edit-form-container {
  padding: 16px;
}

.edit-form-container :deep(.ant-descriptions-item-label) {
  background: var(--bg-subtle);
  color: var(--text-muted);
  font-size: 12px;
  font-weight: 500;
}

.edit-form-container :deep(.ant-descriptions-item-content) {
  background: transparent;
  color: var(--text-primary);
}
</style>
