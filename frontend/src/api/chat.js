// 聊天 API：POST /api/chat SSE 流式（M5 后端）
//
// POST 无法用 EventSource，用 fetch + ReadableStream 手动解析 SSE：
// 每个事件为一行 `data: {json}\n\n`，类型 tool / delta / done / error。
//
// 用法：
//   await streamChat({ messages, service, onEvent })
//   onEvent(event) 收到 {type:'tool'|'delta'|'done'|'error', ...}

export async function streamChat({ messages, service = null, context = null, onEvent, signal }) {
  const res = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages, service, context }),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`聊天请求失败: ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE 事件以空行分隔；逐事件解析
    let idx
    while ((idx = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      const line = raw.split('\n').find((l) => l.startsWith('data: '))
      if (!line) continue
      try {
        onEvent(JSON.parse(line.slice(6)))
      } catch {
        // 单事件解析失败不中断整个流
        console.warn('SSE 事件解析失败:', raw)
      }
    }
  }
}
