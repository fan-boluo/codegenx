<script setup lang="ts">
import { useRoute } from 'vue-router'
import { computed } from 'vue'
import BasicLayout from '@/layouts/BasicLayout.vue'

const route = useRoute()
const isFullScreen = computed(() => {
  const path = route.path
  return path.startsWith('/app/chat')
})
</script>

<template>
  <router-view v-if="isFullScreen" />
  <BasicLayout v-else />
</template>
