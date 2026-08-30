<script setup>
// M8 左栏：正向/反向相关节点 + 所属主题，点击跳转
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchRelated } from '../api/wiki'

const props = defineProps({ nodeId: { type: String, required: true } })
const router = useRouter()

const loading = ref(true)
const error = ref('')
const data = ref(null)

const TYPE_COLOR = { concept: '#4da3ff', bug: '#ff6b6b', topic: '#37d67a' }

async function load() {
  loading.value = true
  error.value = ''
  try {
    data.value = await fetchRelated(props.nodeId)
  } catch (e) {
    error.value = String(e.message || e)
  } finally {
    loading.value = false
  }
}

function go(id) {
  router.push('/node/' + encodeURIComponent(id))
}

watch(() => props.nodeId, load)
onMounted(load)
</script>

<template>
  <div class="related-panel">
    <div v-if="loading" class="tip">加载中…</div>
    <div v-else-if="error" class="tip bad">{{ error }}</div>
    <template v-else-if="data">
      <div class="group">
        <h3>所属主题</h3>
        <button v-for="t in data.topics" :key="t.id" class="link-item" @click="go(t.id)">
          <span class="dot" :style="{ background: TYPE_COLOR[t.type] }"></span>{{ t.name }}
        </button>
        <p v-if="!data.topics.length" class="empty">无</p>
      </div>
      <div class="group">
        <h3>关联（{{ data.outgoing.length }}）</h3>
        <button v-for="n in data.outgoing" :key="n.id" class="link-item" @click="go(n.id)">
          <span class="dot" :style="{ background: TYPE_COLOR[n.type] }"></span>{{ n.name }}
          <span class="tag">{{ n.type === 'bug' ? '坑' : n.type === 'topic' ? '主题' : '概念' }}</span>
        </button>
        <p v-if="!data.outgoing.length" class="empty">无</p>
      </div>
      <div class="group">
        <h3>被引用（{{ data.incoming.length }}）</h3>
        <button v-for="n in data.incoming" :key="n.id" class="link-item" @click="go(n.id)">
          <span class="dot" :style="{ background: TYPE_COLOR[n.type] }"></span>{{ n.name }}
          <span class="tag">{{ n.type === 'bug' ? '坑' : n.type === 'topic' ? '主题' : '概念' }}</span>
        </button>
        <p v-if="!data.incoming.length" class="empty">无</p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.related-panel { display: flex; flex-direction: column; gap: 1rem; }
.group h3 { margin: 0 0 0.4rem; font-size: 0.85rem; color: var(--fg-muted); }
.link-item {
  display: flex; align-items: center; gap: 0.45rem; width: 100%;
  padding: 0.4rem 0.55rem; margin-bottom: 0.3rem;
  border: 1px solid var(--border); border-radius: 8px; background: #fff;
  cursor: pointer; font-size: 0.88rem; text-align: left; color: var(--fg);
}
.link-item:hover { border-color: var(--accent); background: var(--accent-soft); }
.dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; }
.tag { margin-left: auto; font-size: 0.72rem; color: var(--fg-muted); }
.empty { color: var(--fg-muted); font-size: 0.82rem; margin: 0.1rem 0; }
.tip { color: var(--fg-muted); font-size: 0.85rem; }
.tip.bad { color: var(--danger); }
</style>
