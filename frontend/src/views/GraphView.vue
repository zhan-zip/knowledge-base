<script setup>
// M7 图谱总览：3D（3d-force-graph）/ 2D（echarts force）/ 列表 三视图
// 图库 UMD 本地化（public/lib/），动态按序注入（与原型同版本：three 0.160 + 3d-force-graph 1.75）
// 数据：GET /api/graph（concepts/bugs/topics + related + reviews 熟练度）
// 已知坑：node 必须带 name；连线宽 ≥2 + 高透明度，否则不可见
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchGraph } from '../api/graph'

const router = useRouter()

const TYPE_STYLE = {
  concept: { label: '概念', color: '#4da3ff' },
  bug: { label: '坑', color: '#ff6b6b' },
  topic: { label: '主题', color: '#37d67a' },
}

// ===== 图库动态加载（按序注入，进程级缓存）=====
const _libPromise = { p: null }
function loadLibs() {
  if (!_libPromise.p) {
    const urls = ['/lib/three.min.js', '/lib/3d-force-graph.umd.js', '/lib/echarts.min.js']
    _libPromise.p = urls.reduce((chain, url) => chain.then(() => new Promise((resolve, reject) => {
      if (document.querySelector(`script[src="${url}"]`)) return resolve()
      const s = document.createElement('script')
      s.src = url
      s.onload = resolve
      s.onerror = () => reject(new Error(`图库加载失败: ${url}`))
      document.head.appendChild(s)
    })), Promise.resolve())
  }
  return _libPromise.p
}

// ===== 状态 =====
const viewMode = ref('3d')
const query = ref('')
const loading = ref(true)
const loadError = ref('')
const nodes = ref([])
const links = ref([])
const stats = ref({})
const container3d = ref(null)
const container2d = ref(null)

let graph3d = null
let chart2d = null

function matches(n) {
  const q = query.value.trim().toLowerCase()
  return !q || n.name.toLowerCase().includes(q)
}

function degree(n) {
  return links.value.filter(l => l.source === n.id || l.target === n.id).length
}

const filteredNodes = computed(() => nodes.value.filter(matches))

// ===== 3D =====
function render3D() {
  const F3 = window.ForceGraph3D
  const F = (F3 && typeof F3 === 'object' && F3.ForceGraph3D) ? F3.ForceGraph3D : F3
  const byId = Object.fromEntries(nodes.value.map(n => [n.id, n]))
  graph3d = F()(container3d.value)
    .graphData({
      nodes: nodes.value,
      links: links.value
        .map(l => ({ source: byId[l.source], target: byId[l.target] }))
        .filter(l => l.source && l.target),
    })
    .nodeId(d => d.id)
    .nodeLabel(d => `${d.name}（${TYPE_STYLE[d.type]?.label || d.type}）`)
    .nodeVal(d => 1 + degree(d))
    .nodeColor(d => (matches(d) ? (TYPE_STYLE[d.type]?.color || '#999') : 'rgba(150,160,190,0.08)'))
    .nodeResolution(14)
    .linkWidth(2.2)
    .linkColor(() => 'rgba(160,180,220,0.85)')
    .onNodeClick(d => router.push('/node/' + encodeURIComponent(d.id)))
    .onNodeHover(d => { document.body.style.cursor = d ? 'pointer' : 'default' })
    .cooldownTicks(120)
    .warmupTicks(30)
  try {
    const c = graph3d.controls()
    c.autoRotate = true
    c.autoRotateSpeed = 0.6
  } catch { /* controls 不可用时忽略 */ }
  resize3D()
}

function resize3D() {
  if (!graph3d || !container3d.value) return
  const parent = container3d.value.parentElement
  graph3d.width(parent.clientWidth).height(parent.clientHeight)
}

function apply3DFilter() {
  if (!graph3d) return
  graph3d.nodeColor(d => (matches(d) ? (TYPE_STYLE[d.type]?.color || '#999') : 'rgba(150,160,190,0.08)'))
    .nodeVal(d => (query.value ? (matches(d) ? 8 : 0.5) : 1 + degree(d)))
}

// ===== 2D =====
function render2D() {
  if (!window.echarts || !container2d.value) return
  if (!chart2d) chart2d = window.echarts.init(container2d.value)
  chart2d.setOption({
    backgroundColor: 'transparent',
    tooltip: { textStyle: { fontSize: 12 } },
    legend: { data: Object.values(TYPE_STYLE).map(v => v.label), textStyle: { color: '#6b7280' } },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      force: { repulsion: 320, edgeLength: 90, gravity: 0.08 },
      categories: Object.entries(TYPE_STYLE).map(([k, v]) => ({ name: v.label })),
      data: nodes.value.map(n => ({
        id: n.id,
        name: n.name,
        category: TYPE_STYLE[n.type]?.label,
        symbolSize: (1 + degree(n)) * 7,
        itemStyle: { color: TYPE_STYLE[n.type]?.color, opacity: matches(n) ? 1 : 0.12 },
        label: { show: matches(n), fontSize: 11 },
      })),
      links: links.value.map(l => ({ source: l.source, target: l.target })),
      label: { show: true, position: 'right' },
      lineStyle: { color: 'rgba(140,160,200,0.4)', width: 1.5, curveness: 0.08 },
      emphasis: { focus: 'adjacency', lineStyle: { width: 2, color: '#6c8cff' } },
    }],
  })
  chart2d.off('click')
  chart2d.on('click', p => {
    if (p.dataType === 'node') router.push('/node/' + encodeURIComponent(p.data.id))
  })
  chart2d.resize()
}

// ===== 视图切换 / 搜索联动 =====
watch(viewMode, mode => {
  nextTick(() => {
    if (mode === '2d') chart2d?.resize()
    if (mode === '3d') resize3D()
  })
})

watch(query, () => {
  apply3DFilter()
  if (viewMode.value === '2d') render2D()
})

function onResize() {
  resize3D()
  chart2d?.resize()
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  try {
    const data = await fetchGraph()
    nodes.value = data.nodes || []
    links.value = data.links || []
    stats.value = data.stats || {}
    // 先退出 loading 让 v-else 容器渲染出来（ref 才有值），再加载图库并绘制
    loading.value = false
    await nextTick()
    await loadLibs()
    await nextTick()
    render3D()
    render2D()
  } catch (e) {
    loadError.value = String(e.message || e)
    loading.value = false
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart2d?.dispose()
  chart2d = null
  graph3d = null
  // 3D 的 WebGL 上下文随 DOM 移除由浏览器回收
})
</script>

<template>
  <section class="graph-page">
    <div class="toolbar">
      <input v-model="query" class="search" type="text" placeholder="🔍 搜索节点…" />
      <div class="seg">
        <button :class="{ active: viewMode === '3d' }" @click="viewMode = '3d'">🌐 3D</button>
        <button :class="{ active: viewMode === '2d' }" @click="viewMode = '2d'">🕸 2D</button>
        <button :class="{ active: viewMode === 'list' }" @click="viewMode = 'list'">☰ 列表</button>
      </div>
      <span class="stats" v-if="stats.links !== undefined">
        {{ stats.concepts }} 概念 · {{ stats.bugs }} 坑 · {{ stats.topics }} 主题 · {{ stats.links }} 连线
      </span>
    </div>

    <div v-if="loading" class="tip">图谱加载中…</div>
    <div v-else-if="loadError" class="tip bad">加载失败：{{ loadError }}</div>
    <div v-else-if="!nodes.length" class="tip">知识库还没有节点——去编译一份资料吧（POST /api/chat 让 agent 编译，或 CLI: python -m backend.compiler.compile）</div>

    <template v-else>
      <div v-show="viewMode === '3d'" class="canvas-wrap"><div ref="container3d" class="canvas"></div></div>
      <div v-show="viewMode === '2d'" class="canvas-wrap"><div ref="container2d" class="canvas"></div></div>

      <div v-show="viewMode === 'list'" class="canvas-wrap">
        <div class="list-grid">
          <div v-for="n in filteredNodes" :key="n.id" class="node-card"
               @click="router.push('/node/' + encodeURIComponent(n.id))">
            <div class="row">
              <span class="swatch" :style="{ background: TYPE_STYLE[n.type]?.color }"></span>
              <h4>{{ n.name }}</h4>
              <span class="cnt">{{ degree(n) }} 连</span>
            </div>
            <p>{{ n.topic || '（无主题）' }} · 熟练度 {{ n.mastery }}</p>
          </div>
          <p v-if="!filteredNodes.length" class="tip">没有匹配节点</p>
        </div>
      </div>

      <div class="legend">
        <span v-for="(v, k) in TYPE_STYLE" :key="k" class="item">
          <span class="swatch" :style="{ background: v.color }"></span>{{ v.label }}
        </span>
        <span class="hint-text">拖拽旋转 · 滚轮缩放 · 点击节点进详情</span>
      </div>
    </template>
  </section>
</template>

<style scoped>
.graph-page { display: flex; flex-direction: column; gap: 0.8rem; height: calc(100vh - 90px); }
.toolbar { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; }
.search {
  padding: 0.45rem 0.9rem; border: 1px solid var(--border); border-radius: 8px;
  background: #fff; font-size: 0.9rem; width: 220px;
}
.seg { display: flex; gap: 4px; background: var(--bg-hover); padding: 3px; border-radius: 9px; }
.seg button {
  border: none; background: transparent; color: var(--fg-muted);
  padding: 0.35rem 0.9rem; border-radius: 7px; cursor: pointer; font-size: 0.85rem;
}
.seg button.active { background: var(--accent); color: #fff; }
.stats { color: var(--fg-muted); font-size: 0.82rem; margin-left: auto; }
.canvas-wrap { flex: 1; position: relative; border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: var(--bg-card); }
.canvas { position: absolute; inset: 0; }
.list-grid {
  position: absolute; inset: 0; overflow-y: auto; padding: 1rem;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 0.7rem; align-content: start;
}
.node-card {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 10px;
  padding: 0.8rem 0.9rem; cursor: pointer; transition: all 0.15s;
}
.node-card:hover { border-color: var(--accent); transform: translateY(-2px); }
.node-card .row { display: flex; align-items: center; gap: 0.5rem; }
.node-card h4 { margin: 0; font-size: 0.92rem; }
.node-card .cnt { margin-left: auto; font-size: 0.75rem; color: var(--accent); }
.node-card p { margin: 0.4rem 0 0; font-size: 0.78rem; color: var(--fg-muted); }
.legend {
  display: flex; align-items: center; gap: 1rem; font-size: 0.8rem; color: var(--fg-muted);
}
.legend .item { display: flex; align-items: center; gap: 0.35rem; }
.legend .hint-text { margin-left: auto; opacity: 0.75; }
.swatch { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.tip { color: var(--fg-muted); padding: 3rem; text-align: center; }
.tip.bad { color: var(--danger); }
</style>
