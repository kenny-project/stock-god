<template>
  <div class="ad-page">
    <div class="ad-toolbar">
      <button class="btn" @click="goBack">← 返回个股页</button>
      <h2 class="ad-title">
        {{ meta }}
        <span v-if="stale" class="stale-badge" title="旧版生成器产物，建议回到个股页重新生成分析">旧版数据</span>
      </h2>
      <!-- 历史分析切换：财报分析 Tab 移除后，这里是浏览全部历史分析的入口 -->
      <select v-if="list.length" class="ad-select" :value="id" @change="switchAnalysis">
        <option v-for="a in list" :key="a.id" :value="a.id">
          {{ a.form_type }} {{ a.quarter || 'FY' + a.fiscal_year }}{{ a.period ? ' · ' + a.period : '' }}
        </option>
      </select>
    </div>
    <p v-if="loading" class="empty">加载中…</p>
    <p v-else-if="error" class="empty">{{ error }}</p>
    <MarkdownViewer v-else-if="md" :source="md" />
    <p v-else class="empty">暂无分析正文</p>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api.js'
import MarkdownViewer from '../components/MarkdownViewer.vue'

const props = defineProps({ ticker: String, id: String })
const router = useRouter()

const md = ref('')
const loading = ref(true)
const error = ref('')
const curVersions = ref(null)
const list = ref([]) // 全部历史分析（元信息与下拉切换用）

const cur = computed(() => list.value.find((a) => a.id === Number(props.id)) || null)
const meta = computed(() => {
  const a = cur.value
  if (!a) return props.ticker
  return `${props.ticker} — ${a.form_type} ${a.quarter || 'FY' + a.fiscal_year}${a.period ? ' · ' + a.period : ''}`
})
// 元信息缺失（列表拉取失败）时退化为正文接口自带的 form_type/fiscal_year
const stale = computed(() => {
  const gv = cur.value ? cur.value.generator_version : null
  return gv == null || (curVersions.value?.analysis != null && gv !== curVersions.value.analysis)
})

async function loadContent() {
  loading.value = true
  error.value = ''
  try {
    const d = await api.analysis(props.ticker, props.id)
    md.value = d.markdown
  } catch (e) {
    error.value = e.message || '加载失败'
    md.value = ''
  } finally {
    loading.value = false
  }
}

async function loadList() {
  try {
    list.value = await api.analyses(props.ticker)
  } catch {
    list.value = [] // 列表拉不到不影响正文展示
  }
}

function switchAnalysis(e) {
  router.replace(`/stocks/${props.ticker}/analysis/${e.target.value}`)
}

function goBack() {
  router.push(`/stocks/${props.ticker}`)
}

watch(() => props.id, loadContent, { immediate: true })
loadList()
api.versions().then((v) => (curVersions.value = v)).catch(() => {})
</script>

<style scoped>
.ad-page { max-width: 980px; margin: 0 auto; padding: 16px; }
.ad-toolbar { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; flex-wrap: wrap; }
.ad-title { font-size: 16px; margin: 0; }
.ad-select { max-width: 320px; }
.stale-badge {
  color: #dc2626; background: #fef2f2; border: 1px solid #dc2626;
  border-radius: 4px; font-size: 12px; padding: 0 6px; margin-left: 8px; cursor: help;
}
.empty { color: #999; text-align: center; padding: 24px 0; }
</style>
