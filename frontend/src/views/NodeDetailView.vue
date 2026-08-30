<script setup>
// M9 选区交互：选中正文文字 → 浮动工具条 [🤖问AI][💬加备注]
// - 问AI：选区作为强上下文传给 ChatPanel（SSE 回答围绕选中内容）
// - 加备注：offset(渲染后纯文本偏移) + text 双重定位，POST 存储，
//   渲染时按 text 匹配文本节点包裹 <mark>（渲染 DOM 与 Markdown 源的
//   字符级映射成本过高，offset 语义由"源文件偏移"务实调整，text 为准）
// - 点击右栏批注 → 滚动定位对应 mark
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { fetchAnnotations, fetchRelated, fetchWiki, addAnnotation } from '../api/wiki'
import RelatedPanel from '../components/RelatedPanel.vue'
import GraphMiniMap from '../components/GraphMiniMap.vue'
import ChatPanel from '../components/ChatPanel.vue'
import AnnotationPanel from '../components/AnnotationPanel.vue'

const route = useRoute()
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const nodeId = computed(() => decodeURIComponent(route.params.id || ''))

const loading = ref(true)
const error = ref('')
const page = ref(null)
const relatedNode = ref(null)
const annotations = ref([])

const contentEl = ref(null)      // 正文 article 容器
const chatPanelRef = ref(null)

// 选区状态
const selection = ref(null)      // {text, offset}
const toolbarPos = ref({ x: 0, y: 0 })
const noteMode = ref(false)      // 工具条切备注输入
const noteText = ref('')
const noteSaving = ref(false)
const toolbarFlip = ref(false)

const rendered = computed(() =>
  page.value ? md.render(page.value.content || '') : '')

const typeLabel = { concept: '概念', bug: '坑', topic: '主题' }

// ===== 数据加载 =====
// 竞态保护：onMounted immediate watch 可能与路由变化并发触发 load，
// 旧请求晚到会覆盖新状态（曾导致高亮/内容渲染错乱），序号丢弃过期结果
let loadSeq = 0

async function load() {
  const seq = ++loadSeq
  loading.value = true
  error.value = ''
  page.value = null
  selection.value = null
  noteMode.value = false
  try {
    const wiki = await fetchWiki(nodeId.value)
    if (seq !== loadSeq) return
    page.value = wiki
    try {
      const rel = await fetchRelated(nodeId.value)
      if (seq !== loadSeq) return
      relatedNode.value = rel.node
    } catch {
      relatedNode.value = null
    }
    await loadAnnotations()
  } catch (e) {
    if (seq !== loadSeq) return
    error.value = String(e.message || e)
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

async function loadAnnotations() {
  try {
    const data = await fetchAnnotations(nodeId.value)
    if (data.node === nodeId.value) annotations.value = data.annotations || []
  } catch {
    annotations.value = []
  }
}

watch(nodeId, load, { immediate: true })

// ===== 选区捕获 =====
// content-scroll 内 mouseup：读取选区/收起工具条；点击 detail-page 外也收起
function handleSelectionMouseUp(e) {
  if (noteMode.value) return
  if (e.target.closest('.sel-toolbar')) return  // 工具条内点击不处理
  const sel = window.getSelection()
  const text = sel ? sel.toString().trim() : ''
  if (!text || text.length < 2 || !contentEl.value) {
    selection.value = null
    return
  }
  const range = sel.getRangeAt(0)
  if (!contentEl.value.contains(range.commonAncestorContainer)) {
    selection.value = null
    return
  }
  // offset：渲染后正文纯文本的字符偏移
  const pre = range.cloneRange()
  pre.selectNodeContents(contentEl.value)
  pre.setEnd(range.startContainer, range.startOffset)
  const rect = range.getBoundingClientRect()
  selection.value = { text, offset: pre.toString().length }
  // fixed 定位（视口坐标，不受滚动容器 overflow 裁剪）；顶部空间不足时弹到选区下方
  const flip = rect.top < 90
  toolbarPos.value = {
    x: rect.left + rect.width / 2,
    y: flip ? rect.bottom + 8 : rect.top - 8,
  }
  toolbarFlip.value = flip
  noteMode.value = false
  noteText.value = ''
}

function onGlobalMouseDown(e) {
  if (!e.target.closest('.detail-page')) selection.value = null
}

onMounted(() => {
  document.addEventListener('mousedown', onGlobalMouseDown)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onGlobalMouseDown)
})

// ===== 工具条动作 =====
function askAI() {
  chatPanelRef.value?.openWithSelection(selection.value.text)
  selection.value = null
}

async function saveNote() {
  if (!noteText.value.trim() || !selection.value) return
  noteSaving.value = true
  try {
    await addAnnotation(nodeId.value, {
      offset: selection.value.offset,
      text: selection.value.text,
      note: noteText.value.trim(),
    })
    await loadAnnotations()
    await nextTick()
    highlightAll()
    selection.value = null
    noteMode.value = false
    noteText.value = ''
  } catch (e) {
    alert(`批注保存失败：${e.message || e}`)
  } finally {
    noteSaving.value = false
  }
}

// ===== 高亮渲染 =====
function wrapOne(text, note, annoId) {
  const root = contentEl.value
  if (!root) return false
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const tNodes = []
  const offs = []
  let full = ''
  while (walker.nextNode()) {
    offs.push(full.length)
    full += walker.currentNode.textContent
    tNodes.push(walker.currentNode)
  }
  const idx = full.indexOf(text)
  if (idx === -1) return false
  for (let i = 0; i < tNodes.length; i++) {
    const ns = offs[i]
    const ne = ns + tNodes[i].textContent.length
    if (ne <= idx || ns >= idx + text.length) continue
    const tn = tNodes[i]
    const s = Math.max(0, idx - ns)
    const e = Math.min(tn.textContent.length, idx + text.length - ns)
    const mark = document.createElement('mark')
    mark.className = 'anno-mark'
    mark.dataset.annoId = String(annoId)
    mark.title = note
    mark.style.background = '#fff3ad'
    mark.style.cursor = 'pointer'
    const tail = tn.splitText(e)
    const mid = tn.splitText(s)
    mid.parentNode.insertBefore(mark, mid)
    mark.appendChild(mid)
    void tail
  }
  return true
}

// 用 flush:'post' 的 watch 统一触发：DOM 更新完成后 ref 必然已连接
// （曾用 load() 里 await nextTick() 手动调用，与 Vue 渲染调度存在时序竞态，
//  ref 未连接导致高亮静默失败——nextTick 并不保证组件重渲染已完成）
function highlightAll() {
  // ref 竞态兜底：post flush 理论上 ref 已连接，实测偶发 null，用 querySelector 兜底
  const root = contentEl.value || document.querySelector('.content-scroll .content')
  if (!root) return
  // 幂等：先拆掉旧 mark（annotations 增删时 v-html 不会重建 DOM）
  root.querySelectorAll('mark.anno-mark').forEach(m => {
    const parent = m.parentNode
    parent.replaceChild(document.createTextNode(m.textContent), m)
    parent.normalize()
  })
  for (const a of annotations.value) {
    wrapOne(a.text, a.note, a.id)
  }
}

// loading 必须在依赖里：annotations/rendered 的变化都发生在 loading=false 之前，
// 而 loading=false 引起的"显示正文"渲染是最后一次，之后若无触发高亮永不执行
watch([annotations, rendered, loading], highlightAll, { flush: 'post' })

function locateAnnotation(a) {
  nextTick(() => {
    const mark = contentEl.value?.querySelector(`mark[data-anno-id="${a.id}"]`)
    if (mark) {
      mark.scrollIntoView({ behavior: 'smooth', block: 'center' })
      mark.style.background = '#ffd54f'
      setTimeout(() => { mark.style.background = '#fff3ad' }, 1200)
    }
  })
}
</script>

<template>
  <div class="detail-page">
    <div v-if="loading" class="tip">加载中…</div>
    <div v-else-if="error" class="tip bad">{{ error }}</div>

    <template v-else-if="page">
      <aside class="col col-left">
        <GraphMiniMap :node-id="nodeId" />
        <RelatedPanel :node-id="nodeId" />
      </aside>

      <section class="col col-mid">
        <div class="content-scroll" @mouseup="handleSelectionMouseUp">
          <header class="page-head">
            <span :class="['type-badge', page.type]">{{ typeLabel[page.type] || page.type }}</span>
            <h1>{{ page.name }}</h1>
          </header>
          <div class="meta-line">
            <span v-if="page.topic">主题：{{ page.topic }}</span>
            <span v-if="page.sources?.length">来源：{{ page.sources.join('、') }}</span>
          </div>
          <!-- eslint-disable-next-line vue/no-v-html — markdown-it html:false，内容受控 -->
          <article ref="contentEl" class="content" v-html="rendered"></article>

          <!-- 选区浮动工具条（fixed 视口定位） -->
          <div v-if="selection" class="sel-toolbar"
               :style="{ left: toolbarPos.x + 'px', top: toolbarPos.y + 'px',
                         transform: toolbarFlip ? 'translate(-50%, 0)' : 'translate(-50%, -100%)' }">
            <template v-if="!noteMode">
              <button @click="askAI">🤖 问AI</button>
              <button @click="noteMode = true; noteText = ''">💬 加备注</button>
            </template>
            <template v-else>
              <div class="note-box">
                <textarea v-model="noteText" rows="2" maxlength="500"
                          placeholder="写下你的备注…"></textarea>
                <div class="note-actions">
                  <button class="primary" :disabled="noteSaving || !noteText.trim()"
                          @click="saveNote">{{ noteSaving ? '保存中…' : '保存' }}</button>
                  <button @click="noteMode = false">取消</button>
                </div>
              </div>
            </template>
          </div>
        </div>
        <div class="chat-dock">
          <ChatPanel ref="chatPanelRef" :node-id="nodeId" :node-name="page.name" />
        </div>
      </section>

      <aside class="col col-right">
        <AnnotationPanel :node-id="nodeId" :node="relatedNode"
                         :annotations="annotations" @locate="locateAnnotation" />
      </aside>
    </template>
  </div>
</template>

<style scoped>
.detail-page {
  height: calc(100vh - 90px);
  display: grid; grid-template-columns: 250px 1fr 270px;
  gap: 0.9rem; align-items: stretch;
}
.col { min-height: 0; overflow: hidden; display: flex; flex-direction: column; }
.col-left, .col-right { overflow-y: auto; }
.col-mid {
  background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px;
  position: relative;
}
.content-scroll { flex: 1; overflow-y: auto; padding: 1.1rem 1.3rem; position: relative; }
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
.content { line-height: 1.75; font-size: 0.95rem; user-select: text; }
.content :deep(h1), .content :deep(h2), .content :deep(h3) { margin: 1em 0 0.4em; }
.content :deep(code) {
  background: var(--bg-hover); padding: 0.1rem 0.35rem; border-radius: 5px; font-size: 0.85em;
}
.content :deep(pre) { background: #f1f3f6; padding: 0.8rem; border-radius: 8px; overflow-x: auto; }
.content :deep(pre code) { background: none; padding: 0; }
.content :deep(ul), .content :deep(ol) { padding-left: 1.4em; }

.sel-toolbar {
  position: fixed; transform: translate(-50%, -100%);
  background: #1a1f26; border-radius: 9px; padding: 5px;
  display: flex; gap: 4px; z-index: 100; box-shadow: 0 6px 20px rgba(0,0,0,0.25);
}
.sel-toolbar button {
  border: none; background: transparent; color: #dce4f5;
  padding: 0.3rem 0.65rem; border-radius: 6px; cursor: pointer; font-size: 0.82rem;
  white-space: nowrap;
}
.sel-toolbar button:hover { background: rgba(255,255,255,0.12); }
.sel-toolbar button.primary { background: var(--accent); color: #fff; }
.sel-toolbar button:disabled { opacity: 0.5; cursor: not-allowed; }
.note-box { display: flex; flex-direction: column; gap: 5px; width: 240px; }
.note-box textarea {
  border: none; border-radius: 6px; padding: 0.45rem; font-size: 0.84rem; resize: none;
  font-family: inherit;
}
.note-actions { display: flex; gap: 5px; justify-content: flex-end; padding-bottom: 2px; }

.chat-dock { height: 280px; border-top: 1px solid var(--border); }
.tip { color: var(--fg-muted); padding: 3rem; text-align: center; }
.tip.bad { color: var(--danger); }
</style>
