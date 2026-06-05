<template>
  <a-layout-footer class="footer">
    <div class="footer-content">
      <div class="footer-brand">
        <span class="brand-name">CodeGen<span class="brand-accent">X</span></span>
        <span class="brand-divider">|</span>
        <span class="brand-tagline">AI 驱动的数据应用平台</span>
      </div>
      <p class="copyright">© {{ currentYear }} CodeGenX. All rights reserved.</p>
    </div>
  </a-layout-footer>
</template>

<script setup lang="ts">
const currentYear = new Date().getFullYear()
</script>

<style scoped>
.footer {
  text-align: center;
  padding: 32px 24px 40px;
  margin-top: 80px;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-default);
}

.footer-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}

.footer-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  font-family: var(--font-sans);
  font-size: 14px;
}

.brand-name {
  font-family: var(--font-sans);
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.brand-accent {
  color: var(--accent-primary);
  font-weight: 700;
}

.brand-divider {
  color: var(--border-strong);
  font-weight: 300;
}

.brand-tagline {
  color: var(--text-secondary);
  font-weight: 400;
}

.copyright {
  margin: 0;
  color: var(--text-muted);
  font-size: 13px;
}
</style>
