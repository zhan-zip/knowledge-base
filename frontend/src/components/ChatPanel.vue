<script setup>
// M8 中栏底部：聊天面板（SSE 流式，携带当前节点上下文）
import { nextTick, ref } from 'vue'
import { streamChat } from '../api/chat'

const props = defineProps({
  nodeId: { type: String, default: '' },
  nodeName: { type: String, default: '' },
})

const messages = ref([])        // {role, content}
const input = ref('')
const busy = ref(false)
const toolHint = ref('')        // 工具执行过程提示
const listEl = ref(null)

const sessionId = `node-${Date.now()}`  // 仅前端展示用

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
    await streamChat({
      messages: messages.value.slice(0, -1).map(m => ({ role: m.role, content: m.content })),
      // 当前节点上下文注入 system（后端 run_agent_turn context 参数）
      // 注意：两个模板字符串相邻会被 JS 解析为 tagged template 调用，必须显式拼接
      context: props.nodeId
        ? `用户正在查看知识库节点「${props.nodeName || props.nodeId}」（${props.nodeId}），回答尽量围绕该节点内容。`
        : null,
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
    scrollBottom()
  }
}
</script>

<template>
  <div class="chat-panel">
    <div ref="listEl" class="msg-list">
      <p v-if="!messages.length" class="empty">
        问点什么吧，比如"这个概念和什么相关？"（回答会自动检索知识库并附来源）
      </p>
      <div v-for="(m, i) in messages" :key="i" :class="['msg', m.role]">
        <div class="bubble">{{ m.content }}</div>
      </div>
      <p v-if="toolHint" class="tool-hint">{{ toolHint }}</p>
    </div>
    <div class="input-row">
      <input v-model="input" :disabled="busy" type="text"
             placeholder="针对当前节点提问…（Enter 发送）"
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
