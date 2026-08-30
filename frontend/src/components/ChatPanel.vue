<script setup>
// M8 中栏底部：聊天面板（SSE 流式，携带当前节点上下文）
// M9：支持选区文字作为强上下文（openWithSelection 由父组件调用）
import { nextTick, ref, watch } from 'vue'
import { streamChat } from '../api/chat'

const props = defineProps({
  nodeId: { type: String, default: '' },
  nodeName: { type: String, default: '' },
})

const messages = ref([])        // {role, content}
const input = ref('')
const busy = ref(false)
const toolHint = ref('')        // 工具执行过程提示
const pendingSelection = ref('') // M9：待提问的选中文字
const listEl = ref(null)

function openWithSelection(text) {
  pendingSelection.value = text
  input.value = ''
}

defineExpose({ openWithSelection })

watch(pendingSelection, scrollBottom)

async function scrollBottom() {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

async function send() {
  const text = input.value.trim()
  if (!text || busy.value) return
  input.value = ''
  toolHint.value = ''
  messages.value.push({ role: 'user', content: text })
  const assistant = { role: 'assistant', content: '' }
  messages.value.push(assistant)
  busy.value = true
  await scrollBottom()
  try {
    // 强上下文：当前节点 + 用户选中文字（后端拼接到 system prompt）
    let context = props.nodeId
      ? `用户正在查看知识库节点「${props.nodeName || props.nodeId}」（${props.nodeId}），回答尽量围绕该节点内容。`
      : null
    if (pendingSelection.value) {
      context = `${context || ''}\n用户在正文中选中了以下文字并就此提问（必须围绕这段文字回答）：\n<selection>${pendingSelection.value}</selection>`
    }
    await streamChat({
      messages: messages.value.slice(0, -1).map(m => ({ role: m.role, content: m.content })),
      context,
      onEvent: (ev) => {
        if (ev.type === 'tool') {
          toolHint.value = `🛠 正在调用工具 ${ev.name}…`
          scrollBottom()
        } else if (ev.type === 'delta') {
          assistant.content += ev.content
          scrollBottom()
        } else if (ev.type === 'done') {
          assistant.content = ev.full || assistant.content
        } else if (ev.type === 'error') {
          assistant.content += `\n\n[错误] ${ev.message}`
        }
      },
    })
  } catch (e) {
    assistant.content += `\n\n[请求失败] ${e.message || e}`
  } finally {
    busy.value = false
    toolHint.value = ''
    pendingSelection.value = ''  // 一次性使用
    scrollBottom()
  }
}
</script>

<template>
  <div class="chat-panel">
    <div ref="listEl" class="msg-list">
      <p v-if="!messages.length && !pendingSelection" class="empty">
        问点什么吧，或选中正文文字后 [🤖问AI]（回答自动检索知识库并附来源）
      </p>
      <p v-if="pendingSelection" class="selection-chip">
        📌 将针对选中文字提问："{{ pendingSelection.slice(0, 60) }}{{ pendingSelection.length > 60 ? '…' : '' }}"
        <button @click="pendingSelection = ''">×</button>
      </p>
      <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
        <div class="bubble">{{ m.content }}</div>
      </div>
      <p v-if="toolHint" class="tool-hint">{{ toolHint }}</p>
    </div>
    <div class="input-row">
      <input v-model="input" :disabled="busy" type="text"
             :placeholder="pendingSelection ? '针对选中文字提问…' : '针对当前节点提问…（Enter 发送）'"
             @keydown.enter="send" />
      <button :disabled="busy || !input.trim()" @click="send">
        {{ busy ? '回答中…' : '发送' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-panel { display: flex; flex-direction: column; height: 100%; }
.msg-list { flex: 1; overflow-y: auto; padding: 0.6rem; display: flex; flex-direction: column; gap: 0.5rem; }
.empty { color: var(--fg-muted); font-size: 0.82rem; text-align: center; margin: auto; }
.selection-chip {
  margin: 0; padding: 0.4rem 0.6rem; background: var(--accent-soft);
  border-radius: 8px; font-size: 0.8rem; color: var(--accent);
  display: flex; align-items: center; gap: 0.4rem;
}
.selection-chip button {
  margin-left: auto; border: none; background: none; cursor: pointer;
  color: var(--fg-muted); font-size: 0.9rem; line-height: 1;
}
.msg { display: flex; }
.msg.user { justify-content: flex-end; }
.bubble {
  max-width: 85%; padding: 0.45rem 0.7rem; border-radius: 10px;
  font-size: 0.86rem; line-height: 1.55; white-space: pre-wrap; word-break: break-word;
}
.msg.user .bubble { background: var(--accent); color: #fff; }
.msg.assistant .bubble { background: var(--bg-hover); }
.tool-hint { margin: 0; font-size: 0.78rem; color: var(--fg-muted); }
.input-row { display: flex; gap: 0.5rem; padding: 0.55rem 0.6rem; border-top: 1px solid var(--border); }
.input-row input {
  flex: 1; padding: 0.45rem 0.7rem; border: 1px solid var(--border);
  border-radius: 8px; font-size: 0.88rem;
}
.input-row button {
  padding: 0.45rem 0.9rem; border: none; border-radius: 8px;
  background: var(--accent); color: #fff; cursor: pointer; font-size: 0.88rem;
}
.input-row button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
