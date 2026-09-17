<template>
  <div v-if="detail">
    <h2 style="margin:8px 0">{{ detail.ticker }} — {{ detail.name_cn || detail.name_en }}</h2>

    <div class="actions">
      <button class="btn" @click="submit('download', { years: 5 })">下载财报</button>
      <button class="btn" @click="submit('analysis', {})">生成分析</button>
      <button class="btn" @click="showDcf = !showDcf">DCF 估值</button>
      <span v-for="t in detail.running_tasks" :key="t.id" class="status running">
        {{ taskLabel(t.task_type) }} #{{ t.id }} 进行中
      </span>
    </div>

    <div v-if="showDcf" class="dcf-panel">
      <label>增长率% <input type="number" step="0.1" v-model="dcfForm.growth" /></label>
      <label>折现率% <input type="number" step="0.1" v-model="dcfForm.discount" /></label>
      <label>年限 <input type="number" step="1" v-model="dcfForm.years" /></label>
      <label>安全边际% <input type="number" step="1" v-model="dcfForm.safety" /></label>
      <button class="btn primary" @click="submitDcf">开始估值</button>
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
        <thead><tr><th>类型</th><th>期间</th><th>下载时间</th><th>文件</th></tr></thead>
        <tbody>
          <tr v-for="f in detail.filings" :key="f.id">
            <td>{{ f.form_type }}</td>
            <td>{{ f.period }}</td>
            <td>{{ fmt(f.downloaded_at) }}</td>
            <td><a :href="'/api/filings/' + f.id + '/file'" target="_blank">打开/下载</a></td>
          </tr>
          <tr v-if="!detail.filings.length"><td colspan="4" class="empty">尚未下载财报</td></tr>
        </tbody>
      </table>
    </div>

    <!-- 财报分析 -->
    <div v-else-if="tab === 'analysis'">
      <div class="item-list">
        <div v-for="a in analyses" :key="a.id" class="item clickable"
             :class="{ active: activeAnalysisId === a.id }" @click="loadAnalysis(a)">
          {{ a.form_type }} FY{{ a.fiscal_year }}
          <span class="muted">{{ fmt(a.generated_at) }}</span>
        </div>
        <p v-if="!analyses.length" class="empty">暂无分析报告，请先下载财报并生成分析</p>
      </div>
      <MarkdownViewer v-if="analysisMd" :source="analysisMd" />
    </div>

    <!-- 图表（Task 14 接入 TrendChart/DcfChart，metricsSeries 已备好） -->
    <div v-else-if="tab === 'charts'">
      <p class="empty">图表组件将在下个任务接入</p>
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
            内在价值 ${{ d.valuation.intrinsic_value_musd }}M，现价 ${{ d.valuation.price }}
          </span>
        </div>
        <p v-if="!dcfList.length" class="empty">暂无 DCF 报告，可点击上方"DCF 估值"发起</p>
      </div>
      <MarkdownViewer v-if="dcfMd" :source="dcfMd" />
    </div>
  </div>
  <p v-else class="empty">加载中…</p>

  <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted, watch } from 'vue'
import { api } from '../api'
import { useTaskStore } from '../stores/tasks'
import MarkdownViewer from '../components/MarkdownViewer.vue'

const props = defineProps({ ticker: String })
const taskStore = useTaskStore()

const detail = ref(null), analyses = ref([]), dcfList = ref([])
const metricsSeries = ref([]) // Task 14 图表数据：[{ name:'营收', years:[...], values:[...] }, ...]
const tab = ref('filings'), showDcf = ref(false)
const analysisMd = ref(''), dcfMd = ref('')
const activeAnalysisId = ref(null), activeDcfId = ref(null)
const dcfForm = reactive({ growth: '', discount: '', years: '', safety: '' })
const message = ref(''), messageType = ref('success')
let toastTimer, pollTimer, seq = 0

const TASK_LABELS = { download: '下载财报', analysis: '生成分析', dcf: 'DCF 估值' }
const taskLabel = (t) => TASK_LABELS[t] || t
const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}

function buildMetricsSeries(list) {
  const withMetrics = list.filter((a) => a.metrics)
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
    analysisMd.value = ''
    dcfMd.value = ''
    activeAnalysisId.value = null
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
  } catch {
    if (my === seq) showToast('加载分析报告失败', 'error')
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

async function submit(type, params) {
  try {
    await taskStore.submit(type, props.ticker, params)
    showToast('任务已提交，可在任务中心查看进度')
    watchUntilDone(props.ticker)
  } catch (e) {
    if (e?.status === 409) showToast('已有同类型任务进行中', 'error')
    else showToast(`提交失败: ${e.message}`, 'error')
  }
}

function submitDcf() {
  const num = (v) => (v === '' || v == null ? null : Number(v))
  const g = num(dcfForm.growth), di = num(dcfForm.discount)
  const y = num(dcfForm.years), s = num(dcfForm.safety)
  submit('dcf', { growth: g, discount: di, years: y, safety: s == null ? null : s / 100 })
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
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 0 12px; }
.dcf-panel { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 12px; background: #f6f8fa; border-radius: 6px; margin-bottom: 12px; }
.dcf-panel label { font-size: 13px; display: flex; align-items: center; gap: 6px; }
.dcf-panel input { width: 90px; padding: 4px 8px; }
.item-list { padding: 8px 0; }
.item { padding: 8px 12px; border-bottom: 1px solid #eee; }
.item.active { color: #0366d6; background: #f6f8fa; }
.muted { color: #666; font-size: 13px; margin-left: 8px; }
.empty { color: #999; text-align: center; padding: 24px 0; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
