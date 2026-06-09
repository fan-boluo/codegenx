<template>
  <a-layout class="basic-layout">
    <div class="layout-bg-decor" />
    <GlobalHeader />
    <a-layout-content class="main-content">
      <router-view />
    </a-layout-content>
  </a-layout>
</template>

<script setup lang="ts">
import GlobalHeader from '@/components/GlobalHeader.vue'

</script>

<style scoped>
.basic-layout {
  background: var(--bg-page);
  min-height: 100vh;
  position: relative;
  overflow: hidden;
}

.layout-bg-decor {
  position: fixed;
  inset: 0;
  pointer-events: none;
  z-index: 0;
  background:
    radial-gradient(ellipse 600px 400px at 90% 0%, rgba(59,130,246,0.04) 0%, transparent 70%),
    radial-gradient(ellipse 400px 500px at 5% 60%, rgba(99,102,241,0.03) 0%, transparent 70%),
    radial-gradient(ellipse 300px 300px at 70% 85%, rgba(59,130,246,0.03) 0%, transparent 70%);
}

.main-content {
  position: relative;
  z-index: 1;
  width: 100%;
  padding: 0;
  background: transparent;
  margin: 0;
}
</style>
