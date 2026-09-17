<template><div ref="el" style="height:360px; margin:16px 0"></div></template>
<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ series: Array, title: String })
const el = ref(null)
let chart
function render() {
  if (!chart || !props.series.length) return
  chart.setOption({
    title: { text: props.title },
    tooltip: { trigger: 'axis' },
    legend: {},
    xAxis: { type: 'category', data: props.series[0].years },
    yAxis: { type: 'value' },
    series: props.series.map(s => ({ name: s.name, type: 'line', data: s.values, smooth: true })),
  }, true)  // notMerge=true，重渲染时完全替换
}
function onResize() { chart?.resize() }
onMounted(() => { chart = echarts.init(el.value); render(); window.addEventListener('resize', onResize) })
onUnmounted(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
watch(() => props.series, render, { deep: true })
</script>
