<template>
  <div style="display:flex; gap:12px; padding:16px 0">
    <input v-model="q" placeholder="搜索代码/公司名" style="flex:1; padding:8px 12px" @input="debouncedLoad" />
    <button class="btn" @click="sync" :disabled="syncing">同步股票列表</button>
  </div>
  <table>
    <thead><tr><th>代码</th><th>公司</th><th>市场</th><th>操作</th></tr></thead>
    <tbody>
      <tr v-for="s in stocks" :key="s.ticker" class="clickable" @click="$router.push(`/stocks/${s.ticker}`)">
        <td><strong>{{ s.ticker }}</strong></td>
        <td>{{ s.name_cn || s.name_en }}</td>
        <td>{{ s.market }}</td>
        <td><router-link :to="`/stocks/${s.ticker}`">详情</router-link></td>
      </tr>
    </tbody>
  </table>
  <div style="padding:12px 0">
    <button class="btn" :disabled="page <= 1" @click="page--; load()">上一页</button>
    第 {{ page }} 页 / 共 {{ Math.ceil(total / size) }} 页（{{ total }} 只）
    <button class="btn" :disabled="page * size >= total" @click="page++; load()">下一页</button>
  </div>
</template>
<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'
const q = ref(''), stocks = ref([]), total = ref(0), page = ref(1), size = 50, syncing = ref(false)
let timer
const debouncedLoad = () => { clearTimeout(timer); timer = setTimeout(() => { page.value = 1; load() }, 300) }
async function load() {
  const d = await api.stocks(q.value, page.value, size)
  stocks.value = d.items; total.value = d.total
}
async function sync() { syncing.value = true; try { await api.syncStocks() } finally { syncing.value = false } }
onMounted(load)
</script>
