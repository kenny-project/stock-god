<template>
  <div class="tasks-page">
    <h2 style="margin:8px 0">任务中心</h2>
    <TasksPanel />
  </div>
</template>

<script setup>
// 任务列表主体抽到 TasksPanel（弹窗与全页共用），本页只保留路由壳，兼容书签直链
import { onMounted } from 'vue'
import { useTaskStore } from '../stores/tasks'
import TasksPanel from '../components/TasksPanel.vue'

const taskStore = useTaskStore()
onMounted(() => {
  taskStore.startPolling() // 全局轮询已启动时为幂等调用
})
</script>
