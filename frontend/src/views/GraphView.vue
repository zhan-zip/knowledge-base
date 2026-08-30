<script setup>
// M7 实现：3D/2D/列表 三视图图谱（数据源 GET /api/graph + wiki/_graph.md）
</script>

<template>
  <section class="placeholder">
    <h1>图谱总览</h1>
    <p>M7 里程碑实现：3D / 2D / 列表三视图，节点 = 概念/坑/主题，颜色将由熟练度驱动（遗忘地图 M11）。</p>
    <p class="hint">当前后端已就绪：混合检索、编译管道、Agent 聊天（<code>POST /api/chat</code>）。</p>
  </section>
</template>

<style scoped>
.placeholder {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
}
.hint {
  color: var(--fg-muted);
  font-size: 0.9rem;
}
</style>
