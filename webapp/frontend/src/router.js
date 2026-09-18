import { createRouter, createWebHistory } from 'vue-router'
import Workbench from './views/Workbench.vue'
import StockDetail from './views/StockDetail.vue'
import Tasks from './views/Tasks.vue'

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
    { path: '/tasks', component: Tasks },
  ],
})
