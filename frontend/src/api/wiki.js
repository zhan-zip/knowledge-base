// wiki/节点 API（M8 端点）

export async function fetchWiki(id) {
  const res = await fetch(`/api/wiki/${id}`)
  if (!res.ok) throw new Error(`获取页面失败: ${res.status}`)
  return res.json()
}

export async function fetchRelated(id) {
  const res = await fetch(`/api/node/${id}/related`)
  if (!res.ok) throw new Error(`获取相关节点失败: ${res.status}`)
  return res.json()
}

export async function fetchAnnotations(id) {
  const res = await fetch(`/api/node/${id}/annotations`)
  if (!res.ok) throw new Error(`获取批注失败: ${res.status}`)
  return res.json()
}
