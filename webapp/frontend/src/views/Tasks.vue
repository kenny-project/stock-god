<template>
  <div class="tasks-page">
    <h2 style="margin:8px 0">任务中心</h2>
    <table>
      <thead>
        <tr><th>#</th><th>类型</th><th>股票</th><th>状态</th><th>创建</th><th>结束</th><th>操作</th></tr>
      </thead>
      <tbody>
        <template v-for="t in taskStore.tasks" :key="t.id">
          <tr>
            <td>{{ t.id }}</td>
            <td>{{ typeLabel(t.task_type) }}</td>
            <td>{{ t.stock ? t.stock.ticker : '-' }}</td>
            <td>
              <span
                v-if="t.task_type === 'download' && t.progress_total > 0"
                class="task-progress"
              >{{ Math.min(t.progress_done || 0, t.progress_total) }}/{{ t.progress_total }}</span>
              <span class="status" :class="t.status">
                {{ statusLabel(t.status) }}<template v-if="t.status === 'failed' && t.error_code"> [{{ t.error_code }}]</template>
              </span>
            </td>
            <td>{{ fmt(t.created_at) }}</td>
            <td>{{ fmt(t.finished_at) }}</td>
            <td>
              <button v-if="['pending', 'running'].includes(t.status)" class="btn" :disabled="cancelling.has(t.id)" @click="cancel(t)">取消</button>
              <button class="btn" @click="toggleLog(t.id)">日志</button>
            </td>
          </tr>
          <tr v-if="expandId === t.id">
            <td colspan="7">
              <div v-if="t.status === 'failed' && (t.error_code || t.error_summary)" class="task-error">
                [{{ t.error_code }}] {{ t.error_summary }}
              </div>
              <LogViewer :task-id="t.id" />
            </td>
          </tr>
        </template>
        <tr v-if="!taskStore.tasks.length"><td colspan="7" class="empty">暂无任务</td></tr>
      </tbody>
    </table>
    <div v-if="message" class="toast" :class="messageType">{{ message }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, onUnmounted } from 'vue'
import { api } from '../api'
import { useTaskStore } from '../stores/tasks'
import LogViewer from '../components/LogViewer.vue'

const taskStore = useTaskStore()
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
const fmt = (s) => (s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '')

function showToast(text, type = 'success') {
  message.value = text; messageType.value = type
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { message.value = '' }, 3000)
}

function toggleLog(id) {
  expandId.value = expandId.value === id ? null : id // 互斥展开
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

onMounted(() => {
  taskStore.startPolling() // 全局轮询已启动时为幂等调用
})
onUnmounted(() => clearTimeout(toastTimer))
</script>

<style scoped>
.task-error {
  color: #dc2626;
  background: #ffebe9;
  padding: 6px 10px;
  border-radius: 4px;
  margin-bottom: 4px;
  font-size: 13px;
}
.empty { color: #999; text-align: center; padding: 24px 0; }
.task-progress { margin-right: 6px; font-size: 12px; color: #57606a; font-variant-numeric: tabular-nums; }
.toast {
  position: fixed; top: 16px; left: 50%; transform: translateX(-50%);
  z-index: 1000; padding: 10px 20px; border-radius: 6px;
  color: #fff; font-size: 14px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
}
.toast.success { background: #16a34a; }
.toast.error { background: #dc2626; }
</style>
