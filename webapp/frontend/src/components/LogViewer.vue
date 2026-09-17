<template>
  <div class="log-viewer">
    <div v-if="error" class="log-error">日志加载失败</div>
    <div v-if="hasEarlier" class="log-toolbar">
      <button class="btn" :disabled="loadingEarlier" @click="loadEarlier">加载更早日志</button>
    </div>
    <pre ref="preRef" class="log-pre" @scroll="onScroll">{{ content || '（暂无日志）' }}</pre>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, nextTick, watch } from 'vue'
import { api } from '../api'

// 日志契约（backend/api/reports.py task_log）：
//   GET /api/tasks/{id}/log?offset=N → { content, size, next_offset }
//   offset<=0：返回文件尾部 64KB（起点 max(0, size-64K)），next_offset = 读取结束位置（通常= size）
//   offset>0 ：从该字节偏移向后读至多 64KB，next_offset = 读取结束位置
// 因此：初始 offset=0 取尾部；实时追加用 tailOffset(=上次 next_offset) 向后增量读；
//       向前翻用 headOffset-64K 请求更早一段并前插。headOffset 是缓冲区首字节偏移，
//       由尾部读取语义推得：max(0, size-65536)。
const CHUNK = 65536

const props = defineProps({
  taskId: { type: Number, required: true },
  live: { type: Boolean, default: false },
})

const preRef = ref(null)
const content = ref('')
const hasEarlier = ref(false)
const loadingEarlier = ref(false)
const error = ref(false)

let headOffset = 0   // 当前缓冲区最早字节偏移（用于"加载更早"）
let tailOffset = 0   // 下次增量读取起点（= 上次响应的 next_offset）
let pollTimer = null
let polling = false
let nearBottom = true

function onScroll() {
  const el = preRef.value
  if (el) nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
}

function scrollToBottom() {
  const el = preRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function loadTail() {
  error.value = false
  try {
    const r = await api.taskLog(props.taskId, 0)
    content.value = r.content
    tailOffset = r.next_offset
    headOffset = Math.max(0, r.size - CHUNK)
    hasEarlier.value = headOffset > 0
    await nextTick()
    nearBottom = true
    scrollToBottom()
  } catch {
    error.value = true // 行内提示，不抛未处理异常
  }
}

async function pollAppend() {
  if (polling) return
  polling = true
  try {
    const r = await api.taskLog(props.taskId, tailOffset)
    if (r.next_offset < tailOffset) {
      // 文件被截断/轮转，重新取尾部
      tailOffset = 0
      headOffset = 0
      hasEarlier.value = false
      content.value = ''
      return
    }
    if (!r.content) return
    const wasNear = nearBottom
    content.value += r.content
    tailOffset = r.next_offset
    if (wasNear) {
      await nextTick()
      scrollToBottom()
    }
  } catch {
    /* 轮询失败静默，下轮重试 */
  } finally {
    polling = false
  }
}

async function loadEarlier() {
  if (loadingEarlier.value || headOffset <= 0) return
  loadingEarlier.value = true
  try {
    const offset = Math.max(0, headOffset - CHUNK)
    const el = preRef.value
    const prevHeight = el ? el.scrollHeight : 0
    const prevTop = el ? el.scrollTop : 0
    const r = await api.taskLog(props.taskId, offset)
    content.value = r.content + content.value
    headOffset = offset
    hasEarlier.value = headOffset > 0
    // 保持视口稳定：前插后把滚动条下移新增高度
    await nextTick()
    if (el) el.scrollTop = prevTop + (el.scrollHeight - prevHeight)
  } catch {
    error.value = true
  } finally {
    loadingEarlier.value = false
  }
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function startPoll() {
  stopPoll()
  pollTimer = setInterval(pollAppend, 2000)
}

function reset() {
  stopPoll()
  polling = false
  content.value = ''
  hasEarlier.value = false
  error.value = false
  headOffset = 0
  tailOffset = 0
  nearBottom = true
  loadTail()
  if (props.live) startPoll()
}

watch(() => props.taskId, reset)
watch(() => props.live, (v) => {
  if (v) {
    startPoll()
  } else {
    // 任务结束（live true→false）：停轮询前补拉一次增量，避免完成前最后写入的日志丢失。
    // pollAppend 内部自吞异常且与定时器无关，可直接调用；若此刻已有请求在飞，该次会跳过，
    // 由在飞请求兜底，同样不丢数据。
    pollAppend()
    stopPoll()
  }
})

onMounted(reset)
onUnmounted(stopPoll)
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
