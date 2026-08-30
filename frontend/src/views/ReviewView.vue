<script setup>
// M10 实现：FSRS 翻卡打分 + 诊断式复习（给症状让用户诊断根因）
</script>

<template>
  <section class="placeholder">
    <h1>复习</h1>
    <p>M10 里程碑实现：到期卡翻卡打分（easy/good/hard/wrong），坑卡片采用诊断式——给症状，你来诊断根因。</p>
    <p class="hint">复习状态存 SQLite reviews 表（FSRS 简化算法），图谱颜色随熟练度变化（M11 遗忘地图）。</p>
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
