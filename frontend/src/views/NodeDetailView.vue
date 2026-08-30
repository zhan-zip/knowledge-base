<script setup>
// M8 三栏详情页：左相关 + 中内容/聊天 + 右批注
// 布局网格：左 250px / 中自适应 / 右 270px
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { fetchRelated, fetchWiki } from '../api/wiki'
import RelatedPanel from '../components/RelatedPanel.vue'
import ChatPanel from '../components/ChatPanel.vue'
import AnnotationPanel from '../components/AnnotationPanel.vue'

const route = useRoute()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const nodeId = computed(() => decodeURIComponent(route.params.id || ''))

const loading = ref(true)
const error = ref('')
const page = ref(null)          // GET /api/wiki/{id}
const relatedNode = ref(null)   // GET /api/node/{id}/related 的 node 元信息

const rendered = computed(() =>
  page.value ? md.render(page.value.content || '') : '')

const typeLabel = { concept: '概念', bug: '坑', topic: '主题' }

async function load() {
  loading.value = true
  error.value = ''
  page.value = null
  try {
    page.value = await fetchWiki(nodeId.value)
    try {
      relatedNode.value = (await fetchRelated(nodeId.value)).node
    } catch {
      relatedNode.value = null  // 相关信息失败不阻塞正文
    }
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

watch(nodeId, load)
onMounted(load)
</script>

<template>
  <div class="detail-page">
    <div v-if="loading" class="tip">加载中…</div>
    <div v-else-if="error" class="tip bad">{{ error }}</div>

    <template v-else-if="page">
      <aside class="col col-left">
        <RelatedPanel :node-id="nodeId" />
      </aside>

      <section class="col col-mid">
        <div class="content-scroll">
          <header class="page-head">
            <span :class="['type-badge', page.type]">{{ typeLabel[page.type] || page.type }}</span>
            <h1>{{ page.name }}</h1>
          </header>
          <div class="meta-line">
            <span v-if="page.topic">主题：{{ page.topic }}</span>
            <span v-if="page.sources?.length">来源：{{ page.sources.join('、') }}</span>
          </div>
          <!-- eslint-disable-next-line vue/no-v-html — markdown-it html:false，内容受控 -->
          <article class="content" v-html="rendered"></article>
        </div>
        <div class="chat-dock">
          <ChatPanel :node-id="nodeId" :node-name="page.name" />
        </div>
      </section>

      <aside class="col col-right">
        <AnnotationPanel :node-id="nodeId" :node="relatedNode" />
      </aside>
    </template>
  </div>
</template>

<style scoped>
.detail-page { height: calc(100vh - 90px); }
.detail-page > template, .detail-page > * { height: 100%; }
.detail-page {
  display: grid; grid-template-columns: 250px 1fr 270px;
  gap: 0.9rem; align-items: stretch;
}
.col { min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.col-left, .col-right { overflow-y: auto; }
.col-mid {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
}
.content-scroll { flex: 1; overflow-y: auto; padding: 1.1rem 1.3rem; }
.page-head { display: flex; align-items: center; gap: 0.6rem; }
.page-head h1 { margin: 0; font-size: 1.25rem; }
.type-badge {
  font-size: 0.75rem; padding: 0.12rem 0.55rem; border-radius: 20px;
  background: var(--accent-soft); color: var(--accent);
}
.type-badge.bug { background: #fdeaea; color: var(--danger); }
.type-badge.topic { background: #e6f7ee; color: var(--ok); }
.meta-line {
  display: flex; gap: 1rem; margin: 0.5rem 0 0.9rem;
  color: var(--fg-muted); font-size: 0.82rem; flex-wrap: wrap;
}
.content { line-height: 1.75; font-size: 0.95rem; }
.content :deep(h1), .content :deep(h2), .content :deep(h3) { margin: 1em 0 0.4em; }
.content :deep(code) {
  background: var(--bg-hover); padding: 0.1rem 0.35rem; border-radius: 5px; font-size: 0.85em;
}
.content :deep(pre) {
  background: #f1f3f6; padding: 0.8rem; border-radius: 8px; overflow-x: auto;
}
.content :deep(pre code) { background: none; padding: 0; }
.content :deep(ul), .content :deep(ol) { padding-left: 1.4em; }
.chat-dock { height: 280px; border-top: 1px solid var(--border); }
.tip { color: var(--fg-muted); padding: 3rem; text-align: center; }
.tip.bad { color: var(--danger); }
</style>
