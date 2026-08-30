import { createRouter, createWebHistory } from 'vue-router'

// 四视图路由（实施计划 M6 定稿）
// /graph 图谱总览 /node/:id 详情 /review 复习 /settings 配置
const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/graph' },
    {
      path: '/graph',
      name: 'graph',
      component: () => import('../views/GraphView.vue'),
    },
    {
      path: '/node/:id',
      name: 'node',
      component: () => import('../views/NodeDetailView.vue'),
    },
    {
      path: '/review',
      name: 'review',
      component: () => import('../views/ReviewView.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('../views/SettingsView.vue'),
    },
  ],
})

export default router
