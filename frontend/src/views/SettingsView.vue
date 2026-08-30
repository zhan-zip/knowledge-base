<script setup>
// M6.5 实现：LLM 服务配置管理页（DeepSeek / Claude / OpenAI 三卡片）
</script>

<template>
  <section class="placeholder">
    <h1>设置</h1>
    <p>M6.5 里程碑实现：默认服务/编译服务切换，三张服务卡片（启用开关、Base URL、API Key 脱敏、模型列表拉取、测试连接、保存配置）。</p>
    <p class="hint">后端配置 API 已就绪：GET/PUT <code>/api/services</code>、模型列表与连接测试端点。</p>
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
code {
  background: var(--accent-soft);
  padding: 0.1rem 0.4rem;
  border-radius: 6px;
}
</style>
