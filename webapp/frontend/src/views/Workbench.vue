<template>
  <div class="workbench" :class="{ dragging }">
    <aside class="wb-left" :style="{ width: leftWidth + 'px' }">
      <StockList />
    </aside>
    <div
      class="wb-divider"
      title="拖拽调整左栏宽度"
      @mousedown="startDrag"
    ></div>
    <main class="wb-center">
      <router-view />
      <div v-if="$route.name !== 'stock'" class="wb-empty">
        在左侧点击股票查看详情
      </div>
    </main>
    <TaskSidebar />
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import StockList from './StockList.vue'
import TaskSidebar from '../components/TaskSidebar.vue'

const LEFT_WIDTH_KEY = 'sg.workbench.leftWidth'
const MIN_WIDTH = 240
const MAX_RATIO = 0.6 // 左栏最大 60vw

function clampWidth(w) {
  const max = Math.floor(window.innerWidth * MAX_RATIO)
  return Math.min(Math.max(w, MIN_WIDTH), Math.max(max, MIN_WIDTH))
}

function initialWidth() {
  const saved = Number(localStorage.getItem(LEFT_WIDTH_KEY))
  return clampWidth(Number.isFinite(saved) && saved > 0 ? saved : 420)
}

const leftWidth = ref(initialWidth())
const dragging = ref(false)

let startX = 0
let startWidth = 0

function onMove(e) {
  leftWidth.value = clampWidth(startWidth + (e.clientX - startX))
}

function onUp() {
  dragging.value = false
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
  document.body.classList.remove('wb-col-resizing')
  localStorage.setItem(LEFT_WIDTH_KEY, String(leftWidth.value))
}

function startDrag(e) {
  e.preventDefault()
  dragging.value = true
  startX = e.clientX
  startWidth = leftWidth.value
  document.addEventListener('mousemove', onMove)
  document.addEventListener('mouseup', onUp)
  document.body.classList.add('wb-col-resizing')
}

onMounted(() => {
  // 窗口变窄后 localStorage 里的大宽度可能超限，挂载时按当前视口重新收敛
  leftWidth.value = clampWidth(leftWidth.value)
})
onUnmounted(() => {
  // 拖拽中组件被卸载（如路由跳走）也要摘掉 document 监听
  document.removeEventListener('mousemove', onMove)
  document.removeEventListener('mouseup', onUp)
  document.body.classList.remove('wb-col-resizing')
})
</script>
