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
      <button :class="{ on: tab === 'financials' }" @click="tab = 'financials'">财报数据</button>
      <button :class="{ on: tab === 'dcf' }" @click="tab = 'dcf'">DCF</button>
      <button :class="{ on: tab === 'analysis' }" @click="tab = 'analysis'">财报分析</button>
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
        <div class="item-list analysis-list">
          <div v-for="a in analyses" :key="a.id" class="item clickable"
               :class="{ active: activeAnalysisId === a.id }" @click="loadAnalysis(a)">
            {{ a.form_type }} {{ a.quarter || 'FY' + a.fiscal_year }}
            <span v-if="isStale(a.generator_version, 'analysis')" class="stale-badge"
                  title="旧版生成器数据，建议重新生成">旧版</span>
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

    <!-- 财报数据 -->
    <div v-else-if="tab === 'financials'">
      <template v-if="hasFinData">
        <table class="fin-table">
          <thead><tr>
            <th class="row-label">指标</th>
            <th v-for="c in fin.columns" :key="c.label" :class="{ qcol: c.kind === 'quarter' }">{{ c.label }}</th>
          </tr></thead>
          <tbody>
            <tr v-for="row in fin.rows" :key="row.key">
              <td class="row-label">{{ row.label }}</td>
              <td v-for="c in fin.columns" :key="c.label" :class="{ qcol: c.kind === 'quarter' }">
                {{ fmtFin(row.values[c.label], row.type) }}
              </td>
            </tr>
          </tbody>
        </table>
        <!-- DCF 基期信息：三成分明细表 + 每股估值（单位：亿$，DB 存百万 ÷100） -->
        <div class="dcf-base">
          <h3>DCF 基期信息<template v-if="fin.ttm">（{{ fin.ttm.base_period }}）</template></h3>
          <template v-if="fin.ttm">
            <table class="fin-table ttm-table">
              <thead><tr><th class="row-label">项目</th><th>金额</th><th class="note-col">说明</th></tr></thead>
              <tbody>
                <tr><td class="row-label">净利润</td><td>{{ fmtYi(fin.ttm.net_income) }}</td><td class="note">{{ fin.ttm.notes?.net_income || '-' }}</td></tr>
                <tr><td class="row-label">折旧摊销</td><td>{{ fmtYi(fin.ttm.depreciation) }}</td><td class="note">{{ fin.ttm.notes?.depreciation || '-' }}</td></tr>
                <tr><td class="row-label">CapEx</td><td>{{ fmtYi(fin.ttm.capex) }}</td><td class="note">{{ fin.ttm.notes?.capex || '-' }}</td></tr>
                <tr><td class="row-label">维护 CapEx</td><td>{{ fmtYi(fin.ttm.maintenance_capex) }}</td><td class="note">{{ fin.ttm.notes?.maintenance_capex || '-' }}</td></tr>
                <tr class="oe-row"><td class="row-label">基期 OE</td><td><b>{{ fmtYi(fin.ttm.owner_earnings) }}</b></td><td class="note">{{ fin.ttm.notes?.owner_earnings || '-' }}</td></tr>
                <tr><td class="row-label">流通股数</td><td>{{ fin.shares_outstanding != null ? fmtNum(fin.shares_outstanding) + 'M' : '-' }}</td><td class="note">最近一期财报</td></tr>
              </tbody>
            </table>
          </template>
          <p v-else class="empty">TTM 基期不可用（缺年报数据）</p>
          <template v-if="latestVal">
            <h3>每股估值<span class="src">（来自 {{ fmt(latestDcf.generated_at) }} DCF 报告）</span></h3>
            <table class="fin-table val-table">
              <thead><tr><th>每股内在价值</th><th>25%安全边际价</th><th>50%安全边际价</th><th>报告现价</th><th>现价 vs 内在价值</th></tr></thead>
              <tbody><tr>
                <td><b>{{ latestVal.intrinsic_value_per_share != null ? '$' + latestVal.intrinsic_value_per_share : '-' }}</b></td>
                <td>{{ latestVal.safety_25_price != null ? '$' + latestVal.safety_25_price : '-' }}</td>
                <td>{{ latestVal.safety_50_price != null ? '$' + latestVal.safety_50_price : '-' }}</td>
                <td>{{ latestVal.price != null ? '$' + latestVal.price : '-' }}</td>
                <td>{{ valPremium }}</td>
              </tr></tbody>
            </table>
          </template>
          <p v-else class="empty">暂无 DCF 报告，无法给出每股估值</p>
        </div>
        <TrendChart v-if="hasMetrics" :series="metricsSeries" title="营收/净利润趋势（百万$）" />
      </template>
      <p v-else-if="finLoading" class="empty">加载中…</p>
      <p v-else class="empty">暂无分析数据，请先生成财报分析</p>
    </div>

    <!-- DCF -->
    <div v-else-if="tab === 'dcf'">
      <div class="item-list">
        <div v-for="d in dcfList" :key="d.id" class="item clickable"
             :class="{ active: activeDcfId === d.id }" @click="loadDcf(d)">
          {{ fmt(d.generated_at) }}
          <span v-if="isStale(d.generator_version, 'dcf')" class="stale-badge"
                title="旧版生成器数据，建议重新生成">旧版</span>
          <span v-if="d.growth != null || d.discount != null" class="muted">
            （增长{{ d.growth }}% / 折现{{ d.discount }}%）
          </span>
          <span v-if="d.valuation" class="muted">
            <template v-if="d.valuation.intrinsic_value_per_share != null">
              每股 ${{ d.valuation.intrinsic_value_per_share }}，现价 ${{ d.valuation.price }}
            </template>
            <template v-else>内在价值 ${{ d.valuation.intrinsic_value_musd }}M，现价 ${{ d.valuation.price }}</template>
          </span>
          <button class="del-btn" :disabled="deletingDcfId === d.id"
                  @click.stop="deleteDcf(d)">删除</button>
        </div>
        <p v-if="!dcfList.length" class="empty">暂无 DCF 报告，可点击上方"DCF 估值"发起</p>
      </div>
      <!-- 只渲染当前选中（loadDcf 成功后 activeDcfId 指向）且有估值的报告 -->
      <DcfChart v-if="activeDcf" :key="activeDcf.id" :report="activeDcf" />
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
const fin = ref(null) // 财报数据 Tab：/financials 接口（columns/rows/ttm/shares/price）
const finLoading = ref(false)
const tab = ref('filings'), dcfModal = ref(false) // dcfModal：DCF 参数弹窗开关
const aliasEditing = ref(false), aliasInput = ref('')
const analysisMd = ref(''), dcfMd = ref('')
const activeAnalysisId = ref(null), activeDcfId = ref(null)
const viewMode = ref('list') // 分析 Tab 页内视图：list=列表，detail=单篇分析正文
const clearing = ref(false) // 清空分析请求进行中，防连点
const deletingDcfId = ref(null) // 删除请求进行中的 DCF 行 id，防连点
const dcfForm = reactive({ growth: '', discount: '', years: '', safety: '' })
const curVersions = ref(null) // 当前生成器版本（/api/versions，加载时取一次），用于旧版徽标
const message = ref(''), messageType = ref('success')
let toastTimer, pollTimer, seq = 0, finSeq = 0 // finSeq：financials 请求专用守卫（seq 会被 loadAnalysis/loadDcf 推进，不能复用）

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
// 财报数据 Tab 有无数据：columns 为空即无分析记录 → 空态提示先生成分析
const hasFinData = computed(() => !!fin.value && fin.value.columns.length > 0)
// 每股估值：取最新一条 DCF 报告的 valuation（dcfList 按 generated_at 倒序）
const latestDcf = computed(() => dcfList.value[0] || null)
const latestVal = computed(() => latestDcf.value?.valuation || null)
// 现价相对每股内在价值的溢价/折价（%）
const valPremium = computed(() => {
  const v = latestVal.value
  if (!v || v.price == null || !v.intrinsic_value_per_share) return '-'
  const p = Math.round((v.price / v.intrinsic_value_per_share - 1) * 1000) / 10
  return p > 0 ? `溢价 ${p}%` : p < 0 ? `折价 ${-p}%` : '持平'
})
// 金额（百万$）→ 亿$，一位小数千分位
const fmtYi = (v) => (v == null ? '-' : (v / 100).toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 }))
// 通用数字：一位小数千分位（股数等）
const fmtNum = (v) => (v == null ? '-' : v.toLocaleString('zh-CN', { minimumFractionDigits: 1, maximumFractionDigits: 1 }))
// 表格单元格：money_yi 后端已折算成亿$，直接千分位展示（不得再 ÷100）；ratio 带百分号、eps 带 $；缺数据显示 -（不编造）
function fmtFin(v, type) {
  if (v == null) return '-'
  if (type === 'money_yi') return fmtNum(v)
  if (type === 'ratio') return `${v}%`
  if (type === 'eps') return `$${v}`
  return v
}
// 详情页标题：form_type + 季度/财年 + 报告期
const analysisTitle = computed(() => {
  const a = analyses.value.find((x) => x.id === activeAnalysisId.value)
  if (!a) return ''
  return `${a.form_type} ${a.quarter || 'FY' + a.fiscal_year}${a.period ? ' · ' + a.period : ''}`
})
const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

// 旧版判断（仅展示提示，不阻断）：无版本号（legacy 旧数据）或 ≠ 当前生成器版本；
// 版本接口失败时不按版本号误标，但 legacy（无版本号）仍必标
const isStale = (gv, kind) =>
  gv == null || (curVersions.value?.[kind] != null && gv !== curVersions.value[kind])

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
  // 财报数据独立请求（并行 + 专用 finSeq 守卫）：不能放在 loadAll 尾部复用 seq——
  // 主 try 内 await loadAnalysis / loadDcf 会推进同一 seq，尾部 my===seq 永假，
  // fin 永不赋值、finLoading 永真 → Tab 永远卡「加载中…」
  loadFinancials()
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
      // 默认选中第一条（最新）分析并直接进入正文视图；列表仅在后退时可见（限高滚动）
      if (aList.length) await loadAnalysis(aList[0])
    }
    buildMetricsSeries(aList)
    // 数据刷新不踢出已选中的 DCF 报告：与分析 Tab 同款守卫，
    // 任务完成轮询触发 loadAll 时保持用户手动选中的第 N 条（正文与图表不动）
    if (dList.some((d) => d.id === activeDcfId.value)) return
    dcfMd.value = ''
    activeDcfId.value = null
    // DCF 默认选中第一条有估值的报告（都没有则第一条），自动加载正文与图表
    if (dList.length) loadDcf(dList.find((d) => d.valuation) || dList[0])
  } catch (e) {
    if (my === seq) showToast('加载失败', 'error')
  }
}

// 财报数据独立加载：失败只降级该 Tab（空态提示），不拖累详情页其余数据
async function loadFinancials() {
  const fmy = ++finSeq
  finLoading.value = true
  try {
    const finData = await api.financials(props.ticker)
    if (fmy === finSeq) fin.value = finData
  } catch { /* financials 失败静默：Tab 显示空态 */ } finally {
    // finLoading 随 financials 请求结束：主请求先返回时不能提前关 loading，
    // 否则财报表闪现误导性空态
    if (fmy === finSeq) finLoading.value = false
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

// 当前选中的 DCF 报告（需有估值才能画图）；未选中/无估值时不出图
const activeDcf = computed(() =>
  dcfList.value.find((x) => x.id === activeDcfId.value && x.valuation) || null)

async function deleteDcf(d) {
  if (!confirm('删除该条 DCF 报告？')) return
  deletingDcfId.value = d.id
  try {
    await api.deleteDcf(props.ticker, d.id)
    if (activeDcfId.value === d.id) { activeDcfId.value = null; dcfMd.value = '' }
    showToast('DCF 报告已删除')
    await loadAll()
  } catch (e) {
    showToast(e.message || '删除失败', 'error')
  } finally {
    deletingDcfId.value = null
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
const DCF_DEFAULTS = { growth: 8, discount: 10, years: 10, safety: 30 }
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

onMounted(() => {
  loadAll()
  // 当前生成器版本只需取一次（会话内不变），失败静默——只影响徽标展示
  api.versions().then((v) => { curVersions.value = v }).catch(() => {})
})
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
/* 分析列表态：限高约 5 行，超出滚动（默认直接进 detail 视图，列表仅后退时可见） */
.analysis-list { max-height: 200px; overflow-y: auto; }
.item { padding: 8px 12px; border-bottom: 1px solid #eee; }
.item.active { color: #0366d6; background: #f6f8fa; }
.detail-toolbar { display: flex; align-items: center; gap: 12px; padding: 8px 0 12px; }
.detail-title { font-weight: 600; }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.muted { color: #666; font-size: 13px; margin-left: 8px; }
/* DCF 行删除按钮：红色文字小按钮，@click.stop 防触发行点击 */
.del-btn {
  border: none; background: none; color: #dc2626; cursor: pointer;
  font-size: 12px; padding: 0; margin-left: 8px; vertical-align: middle;
}
.del-btn:hover { text-decoration: underline; }
.del-btn:disabled { opacity: .5; cursor: not-allowed; text-decoration: none; }
/* 旧版生成器产物徽标：红色小标签，悬停提示建议重新生成 */
.stale-badge {
  color: #dc2626; background: #fef2f2; border: 1px solid #dc2626;
  border-radius: 4px; font-size: 12px; padding: 0 6px; margin-left: 8px; cursor: help;
}
.sortable { cursor: pointer; user-select: none; white-space: nowrap; }
.sortable:hover { color: #2563eb; }
.sort-mark { margin-left: 2px; color: #2563eb; }
/* 财报数据 Tab：指标表（行=指标，列=报告期）；季度列浅色底与年报列区分 */
.fin-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.fin-table th, .fin-table td {
  border-bottom: 1px solid #eee; padding: 6px 10px;
  text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums;
}
.fin-table th { border-bottom: 2px solid #ddd; }
.fin-table .row-label { text-align: left; font-weight: 600; color: #444; }
.fin-table .qcol { background: #f6f8fa; }
/* DCF 基期信息段 */
.dcf-base {
  border: 1px solid #e5e7eb; border-radius: 8px;
  padding: 12px 16px; margin: 16px 0 4px; background: #fafafa;
}
.dcf-base h3 { margin: 0 0 8px; font-size: 14px; }
.dcf-base h3 .src { font-weight: normal; color: #666; font-size: 12px; }
.dcf-base .ttm-table { margin-bottom: 14px; }
/* 说明列：左对齐灰字，允许换行（推导串较长） */
.dcf-base .note { color: #666; font-size: 12px; text-align: left; white-space: normal; min-width: 220px; }
.dcf-base .oe-row td { background: #f0f7ff; }
.empty { color: #999; text-align: center; padding: 24px 0; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
