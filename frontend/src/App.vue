<script setup>
// 四视图应用骨架（M6）：header 导航 + router-view
</script>

<template>
  <div class="app-shell">
    <header class="app-header">
      <router-link to="/graph" class="brand">🧠 Knowledge-Base</router-link>
      <nav class="nav">
        <router-link to="/graph">图谱</router-link>
        <router-link to="/review">复习</router-link>
        <router-link to="/settings">设置</router-link>
      </nav>
    </header>
    <main class="app-main">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.6rem 1.2rem;
  border-bottom: 1px solid var(--border);
  background: var(--bg-header);
}
.brand {
  font-weight: 700;
  text-decoration: none;
  color: var(--fg);
  font-size: 1.05rem;
}
.nav {
  display: flex;
  gap: 0.4rem;
}
.nav a {
  padding: 0.35rem 0.8rem;
  border-radius: 8px;
  text-decoration: none;
  color: var(--fg-muted);
  font-size: 0.92rem;
}
.nav a:hover {
  color: var(--fg);
  background: var(--bg-hover);
}
.nav a.router-link-active {
  color: var(--accent);
  background: var(--accent-soft);
}
.app-main {
  flex: 1;
  padding: 1.2rem;
  max-width: 1200px;
  width: 100%;
  margin: 0 auto;
  box-sizing: border-box;
}
</style>
