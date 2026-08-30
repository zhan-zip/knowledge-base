<script setup>
// M8 实现：三栏详情（左相关+小地图 / 中内容+聊天+选区 / 右批注）
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <section class="placeholder">
    <h1>节点详情</h1>
    <p>节点 id：<code>{{ route.params.id }}</code></p>
    <p>M8 里程碑实现：左栏相关概念/坑 + 小地图，中栏 Markdown + 选区问AI/备注 + 底部聊天，右栏批注与熟练度。</p>
  </section>
</template>

<style scoped>
.placeholder {
  background: var(--bg-card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 2rem;
}
code {
  background: var(--accent-soft);
  padding: 0.1rem 0.4rem;
  border-radius: 6px;
}
</style>
