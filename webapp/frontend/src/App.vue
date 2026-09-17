<template>
  <nav>
    <strong>stock-god</strong>
    <router-link to="/">股票</router-link>
    <router-link to="/tasks">任务中心</router-link>
    <span v-if="taskStore.active.length" class="badge">{{ taskStore.active.length }}</span>
  </nav>
  <router-view style="padding: 0 24px" />
</template>
<script setup>
import { onMounted } from 'vue'
import { useTaskStore } from './stores/tasks'
const taskStore = useTaskStore()
onMounted(() => taskStore.startPolling())
</script>
