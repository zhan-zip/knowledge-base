<script setup>
// M8 右栏：熟练度 + 标签 + 批注列表（M9 提供选中文字添加批注）
import { onMounted, ref, watch } from 'vue'
import { fetchAnnotations } from '../api/wiki'

const props = defineProps({
  nodeId: { type: String, required: true },
  node: { type: Object, default: null },   // NodeDetailView 统一拉取的 related.node
})

const annotations = ref([])
const loading = ref(false)

const MASTERY_LABEL = { none: '未复习', low: '快忘（低）', mid: '巩固中', high: '稳固' }

async function load() {
  loading.value = true
  try {
    const data = await fetchAnnotations(props.nodeId)
    annotations.value = data.annotations || []
  } catch {
    annotations.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.nodeId, load)
onMounted(load)

defineExpose({ reload: load })  // M9 添加批注后刷新
</script>

<template>
  <div class="annotation-panel">
    <div v-if="node" class="meta card">
      <h3>熟练度</h3>
      <span :class="['mastery', node.mastery]">{{ MASTERY_LABEL[node.mastery] || node.mastery }}</span>
      <h3>标签</h3>
      <div class="tags">
        <span v-for="t in node.tags" :key="t" class="tag">{{ t }}</span>
        <span v-if="!node.tags?.length" class="empty">无</span>
      </div>
      <h3>主题</h3>
      <span class="topic">{{ node.topic || '无' }}</span>
    </div>

    <div class="card">
      <h3>批注（{{ annotations.length }}）</h3>
      <p v-if="loading" class="empty">加载中…</p>
      <p v-else-if="!annotations.length" class="empty">
        暂无批注。M9 支持选中正文文字后 [💬加备注]。
      </p>
      <div v-for="a in annotations" :key="a.id" class="anno">
        <p class="quote">"{{ a.text }}"</p>
        <p class="note">{{ a.note }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.annotation-panel { display: flex; flex-direction: column; gap: 0.8rem; }
.card {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.8rem 0.9rem;
}
h3 { margin: 0.2rem 0 0.4rem; font-size: 0.82rem; color: var(--fg-muted); }
.mastery { font-size: 0.9rem; font-weight: 600; }
.mastery.high { color: var(--ok); }
.mastery.mid { color: #b45309; }
.mastery.low { color: var(--danger); }
.mastery.none { color: var(--fg-muted); }
.tags { display: flex; flex-wrap: wrap; gap: 0.35rem; }
.tag {
  background: var(--accent-soft); color: var(--accent);
  padding: 0.1rem 0.55rem; border-radius: 20px; font-size: 0.78rem;
}
.topic { font-size: 0.88rem; }
.anno { border-top: 1px dashed var(--border); padding: 0.5rem 0; }
.anno:first-of-type { border-top: none; }
.quote { margin: 0; font-size: 0.8rem; color: var(--fg-muted); font-style: italic; }
.note { margin: 0.25rem 0 0; font-size: 0.86rem; }
.empty { color: var(--fg-muted); font-size: 0.82rem; }
</style>
