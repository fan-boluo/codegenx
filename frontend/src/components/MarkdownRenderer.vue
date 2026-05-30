<template>
  <div class="markdown-content" v-html="renderedMarkdown"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'

import 'highlight.js/styles/github.css'

interface Props {
  content: string
}

const props = defineProps<Props>()

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  highlight: function (str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return (
          '<pre class="hljs"><code>' +
          hljs.highlight(str, { language: lang, ignoreIllegals: true }).value +
          '</code></pre>'
        )
      } catch { /* fall through */ }
    }
    return '<pre class="hljs"><code>' + md.utils.escapeHtml(str) + '</code></pre>'
  },
})

const renderedMarkdown = computed(() => {
  return md.render(props.content)
})
</script>

<style scoped>
.markdown-content {
  line-height: 1.6;
  color: var(--text-primary);
  word-wrap: break-word;
  font-size: 14px;
}

.markdown-content :deep(h1),
.markdown-content :deep(h2),
.markdown-content :deep(h3),
.markdown-content :deep(h4),
.markdown-content :deep(h5),
.markdown-content :deep(h6) {
  margin: 1.4em 0 0.5em 0;
  font-weight: 600;
  line-height: 1.3;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}

.markdown-content :deep(h1) {
  font-size: 1.3em;
  border-bottom: 1px solid var(--border-default);
  padding-bottom: 0.3em;
}
.markdown-content :deep(h2) {
  font-size: 1.15em;
  border-bottom: 1px solid var(--border-default);
  padding-bottom: 0.25em;
}
.markdown-content :deep(h3) { font-size: 1.05em; }

.markdown-content :deep(p) { margin: 0.6em 0; }

.markdown-content :deep(ul),
.markdown-content :deep(ol) {
  margin: 0.6em 0;
  padding-left: 1.5em;
}

.markdown-content :deep(li) { margin: 0.2em 0; }

.markdown-content :deep(blockquote) {
  margin: 0.8em 0;
  padding: 0.5em 1em;
  border-left: 3px solid var(--accent-primary);
  background: var(--accent-primary-subtle);
  color: var(--text-secondary);
  border-radius: 0 6px 6px 0;
}

.markdown-content :deep(code) {
  background: var(--bg-subtle);
  padding: 0.2em 0.4em;
  border-radius: 4px;
  font-family: 'SF Mono', 'Fira Code', 'Fira Mono', Menlo, Consolas, monospace;
  font-size: 0.88em;
  color: #1E293B;
  border: 1px solid var(--border-default);
}

.markdown-content :deep(pre) {
  background: var(--bg-subtle);
  border: 1px solid var(--border-default);
  border-radius: 8px;
  padding: 14px;
  overflow-x: auto;
  margin: 0.8em 0;
}

.markdown-content :deep(pre code) {
  background: transparent;
  padding: 0;
  border-radius: 0;
  border: none;
  font-size: 0.85em;
  line-height: 1.5;
  color: inherit;
}

.markdown-content :deep(.hljs) {
  background: transparent !important;
}

.markdown-content :deep(table) {
  border-collapse: collapse;
  margin: 0.8em 0;
  width: 100%;
}

.markdown-content :deep(table th),
.markdown-content :deep(table td) {
  border: 1px solid var(--border-default);
  padding: 0.5em 0.8em;
  text-align: left;
}

.markdown-content :deep(table th) {
  background: var(--bg-subtle);
  font-weight: 600;
  color: var(--text-primary);
  font-size: 0.85em;
}

.markdown-content :deep(table tr:nth-child(even)) {
  background: var(--bg-page);
}

.markdown-content :deep(a) {
  color: var(--accent-primary);
  text-decoration: none;
}
.markdown-content :deep(a:hover) {
  text-decoration: underline;
}

.markdown-content :deep(img) {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  margin: 0.5em 0;
}

.markdown-content :deep(hr) {
  border: none;
  border-top: 1px solid var(--border-default);
  margin: 1.2em 0;
}
</style>
