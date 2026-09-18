<template>
  <div style="display:flex; gap:12px; padding:16px 0; align-items:center">
    <div class="tabs">
      <button class="tab" :class="{ active: view === 'all' }" @click="switchView('all')">全部</button>
      <button class="tab" :class="{ active: view === 'fav' }" @click="switchView('fav')">收藏</button>
    </div>
    <input v-model="q" placeholder="搜索代码/公司名" style="flex:1; padding:8px 12px" @input="debouncedLoad" />
    <button class="btn" @click="sync" :disabled="syncing">同步股票列表</button>
  </div>
  <table>
    <thead><tr><th>代码</th><th>公司</th><th>市场</th><th>已下载财报</th><th style="width:220px">操作</th></tr></thead>
    <tbody>
      <tr v-for="s in stocks" :key="s.ticker" class="clickable" @click="$router.push(`/stocks/${s.ticker}`)">
        <td><strong>{{ s.ticker }}</strong></td>
        <td>{{ s.name_cn || s.name_en }}</td>
        <td>{{ s.market }}</td>
        <td>
          <span v-if="s.filed_count > 0">{{ s.filed_count }}</span>
          <span v-else class="muted">未下载</span>
        </td>
        <td @click.stop>
          <button class="star" :class="{ on: s.is_favorite }" :title="s.is_favorite ? '取消收藏' : '收藏'"
                  @click="toggleFav(s)">{{ s.is_favorite ? '★' : '☆' }}</button>
          <button class="btn sm" @click="runTask('download', s)">下载</button>
          <button class="btn sm" @click="runTask('analysis', s)">分析</button>
          <button class="btn sm" @click="runTask('dcf', s)">DCF</button>
        </td>
      </tr>
      <tr v-if="!stocks.length">
        <td colspan="5" class="empty">{{ view === 'fav' ? '暂无收藏，在列表中点击 ★ 收藏' : '无匹配股票' }}</td>
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
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
const q = ref(''), stocks = ref([]), total = ref(0), page = ref(1), size = 50, syncing = ref(false)
const view = ref('all') // 'all' | 'fav'
const message = ref(''), messageType = ref('success')
let timer, toastTimer, seq = 0
const TASK_LABEL = { download: '下载', analysis: '分析', dcf: 'DCF' }
const TASK_PARAMS = { download: { years: 5 }, analysis: {}, dcf: {} }
const debouncedLoad = () => { clearTimeout(timer); timer = setTimeout(() => { page.value = 1; load() }, 300) }
function switchView(v) {
  if (view.value === v) return
  view.value = v; page.value = 1; load()
}
function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}
async function load() {
  const my = ++seq
  try {
    const d = await api.stocks(q.value, page.value, size, view.value === 'fav')
    if (my !== seq) return
    stocks.value = d.items; total.value = d.total
  } catch (e) {
    if (my === seq) showToast('加载股票列表失败', 'error')
  }
}
async function toggleFav(s) {
  const target = !s.is_favorite
  try {
    const updated = await api.setFavorite(s.ticker, target)
    if (view.value === 'fav' && !target) {
      // 收藏视图里取消收藏：移除该行
      stocks.value = stocks.value.filter(x => x.ticker !== s.ticker)
      total.value--
    } else {
      Object.assign(s, updated)
    }
  } catch (e) {
    showToast(e.message || '收藏操作失败', 'error')
  }
}
async function runTask(type, s) {
  try {
    await api.createTask(type, s.ticker, TASK_PARAMS[type])
    showToast(`${TASK_LABEL[type]}任务已提交，请到任务中心查看进度`)
  } catch (e) {
    if (e?.status === 409) showToast('已有同类型任务进行中', 'error')
    else showToast(e.message || '任务提交失败', 'error')
  }
}
async function sync() {
  syncing.value = true
  try {
    await api.syncStocks()
    showToast('同步任务已提交，请到任务中心查看进度', 'success')
  } catch (e) {
    if (e?.status === 409) showToast('已有同步任务在进行中', 'error')
    else showToast('同步失败', 'error')
  } finally { syncing.value = false }
}
onMounted(load)
onUnmounted(() => { clearTimeout(timer); clearTimeout(toastTimer) })
</script>
<style scoped>
.tabs { display: inline-flex; border: 1px solid #ddd; border-radius: 6px; overflow: hidden; }
.tab { padding: 8px 18px; border: none; background: #fff; cursor: pointer; font-size: 14px; }
.tab.active { background: #2563eb; color: #fff; }
.muted { color: #999; }
.star {
  border: none; background: none; cursor: pointer; font-size: 16px;
  color: #ccc; padding: 2px 4px; vertical-align: middle;
}
.star.on { color: #f59e0b; }
.btn.sm { padding: 2px 8px; font-size: 12px; margin-left: 4px; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
.empty { color: #999; text-align: center; padding: 24px 0; }
</style>
