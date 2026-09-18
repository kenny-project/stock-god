<template>
  <div style="display:flex; gap:12px; padding:16px 0 8px; align-items:center">
    <input v-model="q" placeholder="搜索代码/公司名/别名（支持中文）" style="flex:1; padding:8px 12px" @input="debouncedLoad" />
    <button class="btn" @click="sync" :disabled="syncing">同步</button>
  </div>
  <div style="padding:0 0 8px">
    <div class="tabs">
      <button class="tab" :class="{ active: view === 'all' }" @click="switchView('all')">全部</button>
      <button class="tab" :class="{ active: view === 'sp500' }" @click="switchView('sp500')">标普500</button>
      <button class="tab" :class="{ active: view === 'ndx100' }" @click="switchView('ndx100')">纳斯达克100</button>
      <button class="tab" :class="{ active: view === 'fav' }" @click="switchView('fav')">收藏</button>
      <button class="tab" :class="{ active: view === 'downloaded' }" @click="switchView('downloaded')">已下载</button>
    </div>
  </div>
  <table>
    <thead><tr><th>代码</th><th>公司</th><th>财报数</th><th style="width:36px"></th></tr></thead>
    <tbody>
      <tr v-for="s in stocks" :key="s.ticker" class="clickable" @click="$router.push(`/stocks/${s.ticker}`)">
        <td><strong>{{ s.ticker }}</strong></td>
        <td>{{ s.name_cn || s.name_en }}</td>
        <td>
          <span v-if="s.filed_count > 0">{{ s.filed_count }}</span>
          <span v-else class="muted">未下载</span>
        </td>
        <td @click.stop>
          <button class="star" :class="{ on: s.is_favorite }" :title="s.is_favorite ? '取消收藏' : '收藏'"
                  @click="toggleFav(s)">{{ s.is_favorite ? '★' : '☆' }}</button>
        </td>
      </tr>
      <tr v-if="!stocks.length">
        <td colspan="4" class="empty">{{ view === 'fav' ? '暂无收藏，在列表中点击 ★ 收藏' : '无匹配股票' }}</td>
      </tr>
    </tbody>
  </table>
  <!-- 无限滚动哨兵：拖到底自动加载下一页（滚动容器是 Workbench 左栏 .wb-left） -->
  <div ref="sentinel" style="height:1px"></div>
  <div v-if="loading && stocks.length" class="muted" style="text-align:center; padding:8px 0">加载中…</div>
  <div v-else-if="stocks.length && page * size >= total" class="muted" style="text-align:center; padding:8px 0">
    已加载全部 {{ total }} 只
  </div>
  <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
</template>
<script setup>
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { api } from '../api'
const q = ref(''), stocks = ref([]), total = ref(0), page = ref(1), size = 50, syncing = ref(false)
const view = ref('all') // 'all' | 'sp500' | 'ndx100' | 'fav' | 'downloaded'
const loading = ref(false) // 请求进行中：IntersectionObserver 重复触发 / 与重置加载竞态的闸门
const sentinel = ref(null)
const message = ref(''), messageType = ref('success')
let timer, toastTimer, seq = 0, io = null
const VIEW_PARAMS = {
  all: {},
  sp500: { index: 'sp500' },
  ndx100: { index: 'ndx100' },
  fav: { favorite: true },
  downloaded: { has_filings: true },
}
const debouncedLoad = () => { clearTimeout(timer); timer = setTimeout(resetLoad, 300) }
function switchView(v) {
  if (view.value === v) return
  view.value = v
  resetLoad()
}
function resetLoad() {
  page.value = 1
  load(false)
}
async function load(append) {
  const my = ++seq
  loading.value = true
  try {
    const p = VIEW_PARAMS[view.value]
    const d = await api.stocks(q.value, page.value, size, p.favorite || false,
                               p.index || '', p.has_filings || false)
    if (my !== seq) return
    stocks.value = append ? stocks.value.concat(d.items) : d.items
    total.value = d.total
    await nextTick()
  } catch (e) {
    if (my === seq) showToast('加载股票列表失败', 'error')
  } finally {
    if (my === seq) {
      loading.value = false
      // 首屏/过滤后条数太少没填满容器时，继续自动补页。
      // 必须在 loading 释放后调用：loadMore 首行守卫遇 loading=true 直接 return
      fillIfVisible()
    }
  }
}
function loadMore() {
  if (loading.value || page.value * size >= total.value) return
  page.value++
  load(true)
}
function fillIfVisible() {
  const el = sentinel.value
  if (!el) return
  const root = el.closest('.wb-left')
  const rect = el.getBoundingClientRect()
  const rootBottom = root ? root.getBoundingClientRect().bottom : window.innerHeight
  if (rect.top < rootBottom) loadMore()
}
function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}
async function toggleFav(s) {
  const target = !s.is_favorite
  try {
    const updated = await api.setFavorite(s.ticker, target)
    if (view.value === 'fav' && !target) {
      // 收藏视图里取消收藏：移除该行
      stocks.value = stocks.value.filter(x => x.ticker !== s.ticker)
      total.value--
      if (!stocks.value.length && page.value > 1) {
        // 已加载集合删空且非第一页：回退一页重新加载，避免页码越界
        page.value--
        load(false)
      }
    } else {
      Object.assign(s, updated)
    }
  } catch (e) {
    showToast(e.message || '收藏操作失败', 'error')
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
onMounted(() => {
  load(false)
  nextTick(() => {
    if (!sentinel.value) return
    // root 取最近的滚动容器（Workbench 左栏）；不在滚动容器内时回退为视口
    io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) loadMore()
    }, { root: sentinel.value.closest('.wb-left') || null, rootMargin: '160px 0px' })
    io.observe(sentinel.value)
  })
})
onUnmounted(() => { clearTimeout(timer); clearTimeout(toastTimer); io?.disconnect() })
</script>
<style scoped>
.tabs { display: inline-flex; border: 1px solid #ddd; border-radius: 6px; overflow: hidden; }
.tab { padding: 8px 12px; border: none; background: #fff; cursor: pointer; font-size: 14px; white-space: nowrap; }
.tab.active { background: #2563eb; color: #fff; }
.muted { color: #999; }
.star {
  border: none; background: none; cursor: pointer; font-size: 16px;
  color: #ccc; padding: 2px 4px; vertical-align: middle;
}
.star.on { color: #f59e0b; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
.empty { color: #999; text-align: center; padding: 24px 0; }
</style>
