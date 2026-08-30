// 图谱 API（M7 实现后端 GET /api/graph 后启用）
// 数据源：wiki/_graph.md 邻接表（lint 生成）+ reviews 表熟练度

export async function fetchGraph() {
  const res = await fetch('/api/graph')
  if (!res.ok) throw new Error(`获取图谱数据失败: ${res.status}`)
  return res.json()
}
