<template>
  <div class="app-shell">
    <nav>
      <strong>stock-god 股票</strong>
    </nav>
    <router-view />
  </div>
</template>
<script setup>
import { onMounted } from 'vue'
import { useTaskStore } from './stores/tasks'
const taskStore = useTaskStore()
onMounted(() => taskStore.startPolling())
</script>
