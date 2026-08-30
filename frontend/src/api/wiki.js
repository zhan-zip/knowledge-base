// wiki 内容 API（M8 详情页使用，后端端点在 M8 实现）

export async function fetchNode(id) {
  const res = await fetch(`/api/node/${encodeURIComponent(id)}`)
  if (!res.ok) throw new Error(`获取节点失败: ${res.status}`)
  return res.json()
}

export async function fetchWiki(path) {
  const res = await fetch(`/api/wiki/${path}`)
  if (!res.ok) throw new Error(`获取 wiki 内容失败: ${res.status}`)
  return res.json()
}
