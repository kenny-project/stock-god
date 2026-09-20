<template>
  <div v-if="detail">
    <h2 style="margin:8px 0">
      {{ detail.ticker }} — {{ detail.name_cn || detail.name_en }}
      <button class="alias-btn" title="编辑搜索别名" @click="openAliasEdit">✎ 别名</button>
      <span v-if="detail.aliases && detail.aliases.length" class="alias-list">
        （别名：{{ detail.aliases.join(' / ') }}）
      </span>
    </h2>

    <div v-if="aliasEditing" class="alias-panel">
      <input v-model="aliasInput" placeholder="多个别名用逗号分隔，如：google, 谷歌"
             style="flex:1; min-width:220px" @keyup.enter="saveAliases" />
      <button class="btn primary" @click="saveAliases">保存</button>
      <button class="btn" @click="aliasEditing = false">取消</button>
    </div>

    <div class="actions">
      <button class="btn" @click="submit('download', { years: 5 })">下载财报</button>
      <button class="btn" @click="submit('analysis', {})">生成分析</button>
      <button class="btn" :disabled="!analyses.length || clearing" @click="clearAllAnalyses">清空分析</button>
      <button class="btn" @click="openDcf">DCF 估值</button>
      <span v-for="t in detail.running_tasks" :key="t.id" class="status running">
        {{ taskLabel(t.task_type) }} #{{ t.id }} 进行中
      </span>
    </div>

    <!-- DCF 估值参数弹窗 -->
    <div v-if="dcfModal" class="modal-mask" @click.self="dcfModal = false">
      <div class="modal-card">
        <h3 class="modal-title">DCF 估值参数</h3>
        <div class="modal-body">
          <label>增长率% <input type="number" step="0.1" v-model="dcfForm.growth" /></label>
          <label>折现率% <input type="number" step="0.1" v-model="dcfForm.discount" /></label>
          <label>年限 <input type="number" step="1" v-model="dcfForm.years" /></label>
          <label>安全边际% <input type="number" step="1" v-model="dcfForm.safety" /></label>
        </div>
        <div class="modal-actions">
          <button class="btn primary" @click="submitDcf">开始估值</button>
          <button class="btn" @click="dcfModal = false">取消</button>
        </div>
      </div>
    </div>

    <div class="tabs">
      <button :class="{ on: tab === 'filings' }" @click="tab = 'filings'">财报</button>
      <button :class="{ on: tab === 'analysis' }" @click="tab = 'analysis'">财报分析</button>
      <button :class="{ on: tab === 'charts' }" @click="tab = 'charts'">图表</button>
      <button :class="{ on: tab === 'dcf' }" @click="tab = 'dcf'">DCF</button>
    </div>

    <!-- 财报 -->
    <div v-if="tab === 'filings'">
      <table>
        <thead><tr>
          <th class="sortable" @click="sortFilings('form_type')">
            类型<span v-if="filingsSort.key === 'form_type'" class="sort-mark">{{ filingsSort.dir === 'asc' ? '↑' : '↓' }}</span>
          </th>
          <th class="sortable" @click="sortFilings('period')">
            期间<span v-if="filingsSort.key === 'period'" class="sort-mark">{{ filingsSort.dir === 'asc' ? '↑' : '↓' }}</span>
          </th>
          <th class="sortable" @click="sortFilings('downloaded_at')">
            下载时间<span v-if="filingsSort.key === 'downloaded_at'" class="sort-mark">{{ filingsSort.dir === 'asc' ? '↑' : '↓' }}</span>
          </th>
          <th>文件</th>
        </tr></thead>
        <tbody>
          <tr v-for="f in sortedFilings" :key="f.id">
            <td>{{ f.form_type }}</td>
            <td>{{ f.period }}</td>
            <td>{{ fmt(f.downloaded_at) }}</td>
            <td class="file-links">
              <a :href="'/api/filings/' + f.id + '/file'" target="_blank">打开</a>
              <a :href="'/api/filings/' + f.id + '/file?download=1'">下载</a>
            </td>
          </tr>
          <tr v-if="!detail.filings.length"><td colspan="4" class="empty">尚未下载财报</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 财报分析 -->
    <div v-else-if="tab === 'analysis'">
      <template v-if="viewMode === 'list'">
        <div class="item-list">
          <div v-for="a in analyses" :key="a.id" class="item clickable"
               :class="{ active: activeAnalysisId === a.id }" @click="loadAnalysis(a)">
            {{ a.form_type }} {{ a.quarter || 'FY' + a.fiscal_year }}
            <span class="muted">{{ a.period }}</span>
          </div>
          <p v-if="!analyses.length" class="empty">暂无分析报告，请先下载财报并生成分析</p>
        </div>
      </template>
      <template v-else>
        <div class="detail-toolbar">
          <button class="btn" @click="viewMode = 'list'">← 返回</button>
          <span class="detail-title">{{ analysisTitle }}</span>
        </div>
        <MarkdownViewer v-if="analysisMd" :source="analysisMd" />
      </template>
    </div>

    <!-- 图表 -->
    <div v-else-if="tab === 'charts'">
      <TrendChart v-if="hasMetrics" :series="metricsSeries" title="营收/净利润趋势（百万$）" />
      <p v-else class="empty">暂无分析数据，先生成财报分析</p>
    </div>

    <!-- DCF -->
    <div v-else>
      <div class="item-list">
        <div v-for="d in dcfList" :key="d.id" class="item clickable"
             :class="{ active: activeDcfId === d.id }" @click="loadDcf(d)">
          {{ fmt(d.generated_at) }}
          <span v-if="d.growth != null || d.discount != null" class="muted">
            （增长{{ d.growth }}% / 折现{{ d.discount }}%）
          </span>
          <span v-if="d.valuation" class="muted">
            <template v-if="d.valuation.intrinsic_value_per_share != null">
              每股 ${{ d.valuation.intrinsic_value_per_share }}，现价 ${{ d.valuation.price }}
            </template>
            <template v-else>内在价值 ${{ d.valuation.intrinsic_value_musd }}M，现价 ${{ d.valuation.price }}</template>
          </span>
        </div>
        <p v-if="!dcfList.length" class="empty">暂无 DCF 报告，可点击上方"DCF 估值"发起</p>
      </div>
      <DcfChart v-for="d in dcfList.filter(x => x.valuation)" :key="d.id" :report="d" />
      <MarkdownViewer v-if="dcfMd" :source="dcfMd" />
    </div>
  </div>
  <p v-else class="empty">加载中…</p>

  <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../api'
import { useTaskStore } from '../stores/tasks'
import MarkdownViewer from '../components/MarkdownViewer.vue'
import TrendChart from '../components/TrendChart.vue'
import DcfChart from '../components/DcfChart.vue'

const props = defineProps({ ticker: String })
const taskStore = useTaskStore()

const detail = ref(null), analyses = ref([]), dcfList = ref([])
const metricsSeries = ref([]) // Task 14 图表数据：[{ name:'营收', years:[...], values:[...] }, ...]
const tab = ref('filings'), dcfModal = ref(false) // dcfModal：DCF 参数弹窗开关
const aliasEditing = ref(false), aliasInput = ref('')
const analysisMd = ref(''), dcfMd = ref('')
const activeAnalysisId = ref(null), activeDcfId = ref(null)
const viewMode = ref('list') // 分析 Tab 页内视图：list=列表，detail=单篇分析正文
const clearing = ref(false) // 清空分析请求进行中，防连点
const dcfForm = reactive({ growth: '', discount: '', years: '', safety: '' })
const message = ref(''), messageType = ref('success')
let toastTimer, pollTimer, seq = 0

const TASK_LABELS = { download: '下载财报', analysis: '生成分析', dcf: 'DCF 估值' }
const taskLabel = (t) => TASK_LABELS[t] || t
// 财报表排序：默认按期间降序（最新在前）；点击列头在升/降间切换，点其他列切到该列
const filingsSort = reactive({ key: 'period', dir: 'desc' })
function sortFilings(key) {
  if (filingsSort.key === key) {
    filingsSort.dir = filingsSort.dir === 'asc' ? 'desc' : 'asc'
  } else {
    filingsSort.key = key
    filingsSort.dir = key === 'form_type' ? 'asc' : 'desc'
  }
}
const sortedFilings = computed(() => {
  const mul = filingsSort.dir === 'asc' ? 1 : -1
  return [...(detail.value?.filings || [])].sort((a, b) => {
    const av = a[filingsSort.key] ?? '', bv = b[filingsSort.key] ?? ''
    return av < bv ? -mul : av > bv ? mul : 0
  })
})
// metricsSeries 恒含两条序列（构造函数 map 产出），需按真实数据有无判断空态
const hasMetrics = computed(() => metricsSeries.value.some((s) => s.values.some((v) => v != null)))
// 详情页标题：form_type + 季度/财年 + 报告期
const analysisTitle = computed(() => {
  const a = analyses.value.find((x) => x.id === activeAnalysisId.value)
  if (!a) return ''
  return `${a.form_type} ${a.quarter || 'FY' + a.fiscal_year}${a.period ? ' · ' + a.period : ''}`
})
const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}

function buildMetricsSeries(list) {
  // 图表只取年报口径（quarter 为空，含 10-K/20-F），季度数据不混入趋势
  const withMetrics = list.filter((a) => a.metrics && !a.quarter)
  metricsSeries.value = ['营收', '净利润'].map((key) => ({
    name: key,
    years: withMetrics.map((a) => 'FY' + a.fiscal_year),
    values: withMetrics.map((a) => a.metrics[key] ?? null),
  }))
}

async function loadAll() {
  const my = ++seq
  try {
    const [stock, aList, dList] = await Promise.all([
      api.stock(props.ticker), api.analyses(props.ticker), api.dcf(props.ticker),
    ])
    if (my !== seq) return
    detail.value = stock
    analyses.value = aList
    dcfList.value = dList
    // 数据刷新不踢出已打开的分析详情：仅当选中的分析不在新列表（被清空/换股）时才重置视图
    if (!aList.some((a) => a.id === activeAnalysisId.value)) {
      analysisMd.value = ''
      activeAnalysisId.value = null
      viewMode.value = 'list'
    }
    dcfMd.value = ''
    activeDcfId.value = null
    buildMetricsSeries(aList)
  } catch (e) {
    if (my === seq) showToast('加载失败', 'error')
  }
}

async function loadAnalysis(a) {
  const my = ++seq
  try {
    const d = await api.analysis(props.ticker, a.id)
    if (my !== seq) return
    activeAnalysisId.value = a.id
    analysisMd.value = d.markdown
    viewMode.value = 'detail'
  } catch {
    if (my === seq) showToast('加载分析报告失败', 'error')
  }
}

async function clearAllAnalyses() {
  if (!confirm(`清空 ${props.ticker} 的全部分析记录与文件？`)) return
  clearing.value = true
  try {
    await api.clearAnalyses(props.ticker)
    showToast('分析已清空')
    await loadAll()  // 会重置 viewMode/正文/选中态
  } catch (e) {
    showToast(e.message || '清空失败', 'error')
  } finally {
    clearing.value = false
  }
}

async function loadDcf(d) {
  const my = ++seq
  try {
    const r = await api.dcfOne(props.ticker, d.id)
    if (my !== seq) return
    activeDcfId.value = d.id
    dcfMd.value = r.markdown
  } catch {
    if (my === seq) showToast('加载 DCF 报告失败', 'error')
  }
}

function watchUntilDone(ticker) {
  clearInterval(pollTimer)
  const my = ++seq
  const poll = async () => {
    try {
      const d = await api.stock(ticker)
      if (my === seq && d.ticker === ticker) detail.value = d
      if (d.ticker === ticker && !d.running_tasks.length) {
        clearInterval(pollTimer)
        pollTimer = null
        loadAll()
      }
    } catch { /* 忽略瞬时网络错误，下轮重试 */ }
  }
  poll()
  pollTimer = setInterval(poll, 3000)
}

function openAliasEdit() {
  aliasInput.value = (detail.value.aliases || []).join(', ')
  aliasEditing.value = true
}

// 逗号（中英文）分隔 → 数组；去空白/空串由后端兜底
async function saveAliases() {
  const list = aliasInput.value.split(/[,，]/).map(s => s.trim()).filter(Boolean)
  try {
    const updated = await api.setAliases(props.ticker, list)
    detail.value = { ...detail.value, ...updated } // StockOut 字段并入当前 detail，不打断已打开的报表
    aliasEditing.value = false
    showToast(list.length ? '别名已保存' : '别名已清空')
  } catch (e) {
    showToast(e.message || '保存别名失败', 'error')
  }
}

async function submit(type, params) {
  try {
    await taskStore.submit(type, props.ticker, params)
    showToast('任务已提交，可在任务中心查看进度')
    watchUntilDone(props.ticker)
    return true
  } catch (e) {
    if (e?.status === 409) showToast('已有同类型任务进行中', 'error')
    else showToast(`提交失败: ${e.message}`, 'error')
    return false
  }
}

// 弹窗默认参数（与后端 dcf.py 默认一致）：增长率 8%、折现率 10%、年限 5、安全边际 30%
const DCF_DEFAULTS = { growth: 8, discount: 10, years: 5, safety: 30 }
function openDcf() {
  Object.assign(dcfForm, DCF_DEFAULTS)
  dcfModal.value = true
}

async function submitDcf() {
  const num = (v) => (v === '' || v == null ? null : Number(v))
  const g = num(dcfForm.growth), di = num(dcfForm.discount)
  const y = num(dcfForm.years), s = num(dcfForm.safety)
  const ok = await submit('dcf', { growth: g, discount: di, years: y, safety: s == null ? null : s / 100 })
  if (ok) dcfModal.value = false
}

onMounted(loadAll)
watch(() => props.ticker, () => {
  clearInterval(pollTimer)
  pollTimer = null
  loadAll()
})
onUnmounted(() => { clearInterval(pollTimer); clearTimeout(toastTimer) })
</script>

<style scoped>
.alias-btn {
  border: 1px solid #ddd; background: #fff; color: #666; cursor: pointer;
  font-size: 12px; padding: 2px 8px; border-radius: 4px; vertical-align: middle;
}
.alias-btn:hover { color: #2563eb; border-color: #2563eb; }
.alias-list { color: #666; font-size: 13px; font-weight: normal; }
.alias-panel { display: flex; gap: 8px; align-items: center; padding: 8px 0 12px; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 0 12px; }
/* DCF 参数弹窗：遮罩盖全屏（z-index 100，低于 toast 的 1000），点击遮罩空白处关闭 */
.modal-mask {
  position: fixed; inset: 0; z-index: 100; background: rgba(0, 0, 0, .45);
  display: flex; align-items: center; justify-content: center;
}
.modal-card {
  width: 360px; max-width: calc(100vw - 48px); background: #fff;
  border-radius: 8px; padding: 16px 20px; box-shadow: 0 8px 32px rgba(0, 0, 0, .2);
}
.modal-title { margin: 0 0 12px; font-size: 16px; }
.modal-body { display: flex; flex-direction: column; gap: 10px; }
.modal-body label { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.modal-body input { width: 90px; padding: 4px 8px; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
.item-list { padding: 8px 0; }
.item { padding: 8px 12px; border-bottom: 1px solid #eee; }
.item.active { color: #0366d6; background: #f6f8fa; }
.detail-toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 0 12px; }
.detail-title { font-weight: 600; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.muted { color: #666; font-size: 13px; margin-left: 8px; }
.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
.sortable:hover { color: #2563eb; }
.sort-mark { margin-left: 2px; color: #2563eb; }
.empty { color: #999; text-align: center; padding: 24px 0; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
