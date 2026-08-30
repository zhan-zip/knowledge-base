<script setup>
// M8.5 左栏图谱小地图：小尺寸 echarts force 图，高亮当前节点，点击跳转
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { fetchGraph } from '../api/graph'

const props = defineProps({ nodeId: { type: String, required: true } })
const router = useRouter()

const el = ref(null)
let chart = null
let nodes = []
let links = []

const TYPE_COLOR = { concept: '#4da3ff', bug: '#ff6b6b', topic: '#37d67a' }

function currentId() {
  // 兼容编码/未编码
  try { return decodeURIComponent(props.nodeId) } catch { return props.nodeId }
}

function render() {
  if (!el.value || !window.echarts) return
  if (!chart) chart = window.echarts.init(el.value)
  const cur = currentId()
  chart.setOption({
    backgroundColor: 'transparent',
    animation: false,
    series: [{
      type: 'graph', layout: 'force', roam: false,
      force: { repulsion: 120, edgeLength: 45, gravity: 0.15 },
      data: nodes.map(n => {
        const isCur = n.id === cur
        return {
          id: n.id, name: n.name,
          symbolSize: isCur ? 22 : 6 + Math.min(n.val, 4) * 2,
          itemStyle: {
            color: TYPE_COLOR[n.type] || '#999',
            borderColor: isCur ? '#1a1f26' : 'transparent',
            borderWidth: isCur ? 3 : 0,
            opacity: isCur ? 1 : 0.45,
          },
          label: { show: isCur, position: 'right', fontSize: 10 },
        }
      }),
      links: links.map(l => ({ source: l.source, target: l.target })),
      lineStyle: { color: 'rgba(140,160,200,0.35)', width: 1 },
    }],
  })
  chart.off('click')
  chart.on('click', p => {
    if (p.dataType === 'node' && p.data.id !== currentId()) {
      router.push('/node/' + encodeURIComponent(p.data.id))
    }
  })
}

// 注入 graph 数据时带上 val（连接数）供节点大小使用
async function load() {
  try {
    const data = await fetchGraph()
    const deg = {}
    for (const l of data.links) {
      deg[l.source] = (deg[l.source] || 0) + 1
      deg[l.target] = (deg[l.target] || 0) + 1
    }
    nodes = (data.nodes || []).map(n => ({ ...n, val: deg[n.id] || 0 }))
    links = data.links || []
    // echarts 由 GraphView 的 loadLibs 全局注入；小地图自行兜底加载
    if (!window.echarts) {
      await new Promise((resolve, reject) => {
        const s = document.createElement('script')
        s.src = '/lib/echarts.min.js'
        s.onload = resolve
        s.onerror = reject
        document.head.appendChild(s)
      })
    }
    render()
  } catch { /* 图谱数据不可用时小地图静默隐藏 */ }
}

watch(() => props.nodeId, () => render())
onMounted(load)
onBeforeUnmount(() => { chart?.dispose(); chart = null })
</script>

<template>
  <div class="mini-map card">
    <h3>图谱小地图</h3>
    <div ref="el" class="map"></div>
  </div>
</template>

<style scoped>
.mini-map {
  background: var(--bg-card); border: 1px solid var(--border);
  border-radius: 10px; padding: 0.6rem 0.7rem; margin-bottom: 0.8rem;
}
h3 { margin: 0 0 0.3rem; font-size: 0.8rem; color: var(--fg-muted); }
.map { height: 200px; }
</style>
