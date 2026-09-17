import { createRouter, createWebHistory } from 'vue-router'
import StockList from './views/StockList.vue'
import StockDetail from './views/StockDetail.vue'
import Tasks from './views/Tasks.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: StockList },
    { path: '/stocks/:ticker', component: StockDetail, props: true },
    { path: '/tasks', component: Tasks },
  ]
})
