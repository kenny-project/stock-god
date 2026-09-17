<template>
  <div style="display:flex; gap:12px; padding:16px 0">
    <input v-model="q" placeholder="搜索代码/公司名" style="flex:1; padding:8px 12px" @input="debouncedLoad" />
    <button class="btn" @click="sync" :disabled="syncing">同步股票列表</button>
  </div>
  <table>
    <thead><tr><th>代码</th><th>公司</th><th>市场</th></tr></thead>
    <tbody>
      <tr v-for="s in stocks" :key="s.ticker" class="clickable" @click="$router.push(`/stocks/${s.ticker}`)">
        <td><strong>{{ s.ticker }}</strong></td>
        <td>{{ s.name_cn || s.name_en }}</td>
        <td>{{ s.market }}</td>
      </tr>
    </tbody>
  </table>
  <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
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
const message = ref(''), messageType = ref('success')
let timer, toastTimer
const debouncedLoad = () => { clearTimeout(timer); timer = setTimeout(() => { page.value = 1; load() }, 300) }
function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}
async function load() {
  const d = await api.stocks(q.value, page.value, size)
  stocks.value = d.items; total.value = d.total
}
async function sync() {
  syncing.value = true
  try {
    await api.syncStocks()
    showToast('同步任务已提交，请到任务中心查看进度', 'success')
  } catch (e) {
    if (e?.response?.status === 409) showToast('已有同步任务在进行中', 'error')
    else showToast('同步失败', 'error')
  } finally { syncing.value = false }
}
onMounted(load)
</script>
<style scoped>
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
