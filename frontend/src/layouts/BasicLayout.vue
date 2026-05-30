<template>
  <a-layout class="basic-layout">
    <GlobalHeader />
    <a-layout-content class="main-content">
      <router-view />
    </a-layout-content>
    <GlobalFooter />
  </a-layout>
</template>

<script setup lang="ts">
import GlobalHeader from '@/components/GlobalHeader.vue'
import GlobalFooter from '@/components/GlobalFooter.vue'
</script>

<style scoped>
.basic-layout {
  background: var(--bg-page);
  min-height: 100vh;
}

.main-content {
  width: 100%;
  padding: 0;
  background: transparent;
  margin: 0;
}
</style>
