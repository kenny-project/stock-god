<template>
  <aside v-if="!collapsed" class="task-sidebar">
    <div class="ts-head">
      <span>任务中心</span>
      <button class="ts-fold" title="折叠侧栏" @click="toggleCollapsed">»</button>
    </div>
    <div class="ts-list">
      <template v-for="t in visibleTasks" :key="t.id">
        <div class="ts-row" :class="{ open: expandId === t.id }" @click="toggleLog(t.id)">
          <span class="ts-title">#{{ t.id }} {{ typeLabel(t.task_type) }} {{ tickerOf(t) }}</span>
          <span
            v-if="t.task_type === 'download' && t.progress_total > 0"
            class="ts-progress"
          >{{ t.progress_done || 0 }}/{{ t.progress_total }}</span>
          <span class="status" :class="t.status">
            {{ statusLabel(t.status) }}<template v-if="t.status === 'failed' && t.error_code"> [{{ t.error_code }}]</template>
          </span>
          <button
            v-if="isCancellable(t)"
            class="btn sm ts-cancel"
            :disabled="cancelling.has(t.id)"
            title="取消任务"
            @click.stop="cancel(t)"
          >取消</button>
        </div>
        <div v-if="expandId === t.id" class="ts-log" @click.stop>
          <div v-if="t.status === 'failed' && (t.error_code || t.error_summary)" class="ts-error">
            [{{ t.error_code }}] {{ t.error_summary }}
          </div>
          <LogViewer :task-id="t.id" />
        </div>
      </template>
      <div v-if="!visibleTasks.length" class="ts-empty">暂无任务</div>
    </div>
    <div class="ts-foot">
      <router-link to="/tasks">全部任务 →</router-link>
    </div>
    <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
  </aside>

  <aside v-else class="task-sidebar-rail" title="展开任务中心" @click="toggleCollapsed">
    <span v-if="taskStore.active.length" class="ts-dot"></span>
    <span class="ts-rail-text">任务中心</span>
  </aside>
</template>

<script setup>
import { ref, computed, reactive, onUnmounted } from 'vue'
import { api } from '../api'
import { useTaskStore } from '../stores/tasks'
import LogViewer from './LogViewer.vue'

const COLLAPSED_KEY = 'sg.sidebar.collapsed'

const taskStore = useTaskStore()
const collapsed = ref(localStorage.getItem(COLLAPSED_KEY) === '1')
const expandId = ref(null)
const message = ref(''), messageType = ref('success')
let toastTimer

const TYPE_LABELS = {
  download: '下载财报',
  analysis: '财报分析',
  dcf: 'DCF 估值',
  sync_stocks: '同步股票列表',
}
const STATUS_LABELS = {
  pending: '排队',
  running: '运行中',
  success: '成功',
  failed: '失败',
  cancelled: '已取消',
}
const typeLabel = (t) => TYPE_LABELS[t] || t
const statusLabel = (s) => STATUS_LABELS[s] || s
const tickerOf = (t) => (t.stock ? t.stock.ticker : '-')
const isCancellable = (t) => ['pending', 'running'].includes(t.status)

// 后端已 limit 200，侧栏只展示最近 20 条；全量见「全部任务」
const visibleTasks = computed(() => taskStore.tasks.slice(0, 20))

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  localStorage.setItem(COLLAPSED_KEY, collapsed.value ? '1' : '0')
}

// 互斥展开：点另一行收起当前日志
function toggleLog(id) {
  expandId.value = expandId.value === id ? null : id
}

function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}

const cancelling = reactive(new Set()) // 取消请求 in-flight 的任务 id，防止连发重复 cancel

async function cancel(t) {
  if (cancelling.has(t.id)) return
  cancelling.add(t.id)
  try {
    await api.cancelTask(t.id)
    showToast('已请求取消')
  } catch (e) {
    showToast(`取消失败: ${e.message}`, 'error')
  } finally {
    cancelling.delete(t.id)
  }
}

onUnmounted(() => clearTimeout(toastTimer))
</script>

<style scoped>
.ts-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid #eee; font-weight: 600;
}
.ts-fold {
  border: 1px solid #d0d7de; background: #f6f8fa; border-radius: 4px;
  cursor: pointer; padding: 0 8px; line-height: 20px; font-size: 13px;
}
.ts-list { flex: 1; overflow-y: auto; min-height: 0; }
.ts-row {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 12px; border-bottom: 1px solid #f0f0f0;
  cursor: pointer; font-size: 13px;
}
.ts-row:hover { background: #f6f8fa; }
.ts-row.open { background: #f6f8fa; }
.ts-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ts-progress { flex-shrink: 0; font-size: 12px; color: #57606a; font-variant-numeric: tabular-nums; }
.ts-cancel { flex-shrink: 0; padding: 2px 8px; font-size: 12px; }
.ts-log { padding: 0 12px 8px; background: #fafbfc; border-bottom: 1px solid #f0f0f0; }
.ts-error {
  color: #dc2626; background: #ffebe9;
  padding: 6px 10px; border-radius: 4px; margin: 6px 0 2px; font-size: 13px;
}
.ts-empty { color: #999; text-align: center; padding: 24px 0; font-size: 13px; }
.ts-foot {
  padding: 10px 12px; border-top: 1px solid #eee;
}
.ts-foot a { color: #0366d6; text-decoration: none; font-size: 13px; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
