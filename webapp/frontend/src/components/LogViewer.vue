<template>
  <div class="log-viewer">
    <div v-if="error" class="log-error">日志加载失败</div>
    <div v-if="hasEarlier" class="log-toolbar">
      <button class="btn" :disabled="loadingEarlier" @click="loadEarlier">加载更早日志</button>
    </div>
    <pre ref="preRef" class="log-pre">{{ content || '（暂无日志）' }}</pre>
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick, watch } from 'vue'
import { api } from '../api'

// 日志契约（backend/api/reports.py task_log）：
//   GET /api/tasks/{id}/log?offset=N → { content, size, next_offset }
//   offset<=0：返回文件尾部 64KB（起点 max(0, size-64K)），next_offset = 读取结束位置（通常= size）
//   offset>0 ：从该字节偏移向后读至多 64KB，next_offset = 读取结束位置
// 一次性快照模式：打开（挂载/切换任务）那一刻取一次尾部 64KB，不做实时轮询；
//   向前翻用 headOffset-64K 请求更早一段并前插。headOffset 是缓冲区首字节偏移，
//   由尾部读取语义推得：max(0, size-65536)。
const CHUNK = 65536

const props = defineProps({
  taskId: { type: Number, required: true },
})

const preRef = ref(null)
const content = ref('')
const hasEarlier = ref(false)
const loadingEarlier = ref(false)
const error = ref(false)

let headOffset = 0   // 当前缓冲区最早字节偏移（用于"加载更早"）
let seq = 0          // 请求代际号：切换任务后，在途旧响应一律作废

function scrollToBottom() {
  const el = preRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadTail() {
  const mySeq = ++seq
  error.value = false
  try {
    const r = await api.taskLog(props.taskId, 0)
    if (mySeq !== seq) return // 已切到其他任务，丢弃过期响应
    content.value = r.content
    headOffset = Math.max(0, r.size - CHUNK)
    hasEarlier.value = headOffset > 0
    await nextTick()
    scrollToBottom()
  } catch {
    if (mySeq === seq) error.value = true // 行内提示，不抛未处理异常
  }
}

async function loadEarlier() {
  if (loadingEarlier.value || headOffset <= 0) return
  loadingEarlier.value = true
  const mySeq = seq
  try {
    const offset = Math.max(0, headOffset - CHUNK)
    const el = preRef.value
    const prevHeight = el ? el.scrollHeight : 0
    const prevTop = el ? el.scrollTop : 0
    const r = await api.taskLog(props.taskId, offset)
    if (mySeq !== seq) return // 已切到其他任务，丢弃过期响应
    content.value = r.content + content.value
    headOffset = offset
    hasEarlier.value = headOffset > 0
    // 保持视口稳定：前插后把滚动条下移新增高度
    await nextTick()
    if (el) el.scrollTop = prevTop + (el.scrollHeight - prevHeight)
  } catch {
    if (mySeq === seq) error.value = true
  } finally {
    loadingEarlier.value = false
  }
}

function reset() {
  content.value = ''
  hasEarlier.value = false
  error.value = false
  headOffset = 0
  loadTail()
}

watch(() => props.taskId, reset)
onMounted(reset)
</script>

<style scoped>
.log-toolbar { padding: 4px 0; }
.log-error { color: #dc2626; font-size: 13px; margin: 4px 0; }
.log-pre {
  background: #0d1117;
  color: #c9d1d9;
  max-height: 360px;
  overflow: auto;
  font-size: 12px;
  line-height: 1.5;
  padding: 12px;
  border-radius: 6px;
  margin: 8px 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
</style>
