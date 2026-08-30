// LLM 服务配置 API（对应后端 M1.5 的 /api/services 端点组，M6.5 页面使用）

export async function fetchServices() {
  const res = await fetch('/api/services')
  if (!res.ok) throw new Error(`获取服务配置失败: ${res.status}`)
  return res.json()
}

export async function updateServices(data) {
  const res = await fetch('/api/services', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!res.ok) throw new Error(`保存配置失败: ${res.status}`)
  return res.json()
}

export async function fetchModels(serviceName) {
  const res = await fetch(`/api/services/${serviceName}/models`)
  if (!res.ok) throw new Error(`获取模型列表失败: ${res.status}`)
  return res.json()
}

export async function testService(serviceName) {
  const res = await fetch(`/api/services/${serviceName}/test`, { method: 'POST' })
  if (!res.ok) throw new Error(`测试请求失败: ${res.status}`)
  return res.json()
}
