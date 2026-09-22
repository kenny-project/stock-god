import { createRouter, createWebHistory } from 'vue-router'
import Workbench from './views/Workbench.vue'
import StockDetail from './views/StockDetail.vue'
import Tasks from './views/Tasks.vue'
import AnalysisDetail from './views/AnalysisDetail.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      component: Workbench,
      children: [
        // 中栏个股详情；props:true 把 :ticker 传给 StockDetail 的 defineProps
        { path: 'stocks/:ticker', name: 'stock', component: StockDetail, props: true },
        // path '' 时中栏 router-view 不渲染任何子路由，空态由 Workbench 内 v-if 判断
      ],
    },
    // 财报分析独立页（三态按钮 window.open 新 Tab 打开；未来替代财报分析 Tab）
    { path: '/stocks/:ticker/analysis/:id', name: 'analysis', component: AnalysisDetail, props: true },
    { path: '/tasks', component: Tasks },
  ],
})
