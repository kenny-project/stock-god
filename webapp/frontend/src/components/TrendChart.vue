<template><div ref="el" style="height:360px; margin:16px 0"></div></template>
<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ series: Array, title: String, unit: { type: String, default: '' } })
const el = ref(null)
let chart
function render() {
  if (!chart || !props.series.length) return
  chart.setOption({
    title: { text: props.title },
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params]
        let html = arr[0].name + '<br/>'
        for (const p of arr) {
          const s = p.value != null ? Number(p.value).toFixed(3) : '-'
          html += `${p.marker} ${p.seriesName}: ${s}${props.unit ? ' ' + props.unit : ''}<br/>`
        }
        return html
      }
    },
    legend: {},
    grid: { bottom: 80 },
    xAxis: { type: 'category', data: props.series[0].years, axisLabel: { rotate: 45, interval: 0 } },
    yAxis: { type: 'value', axisLabel: { formatter: (value) => Number(value).toFixed(3) } },
    series: props.series.map(s => ({
      name: s.name, type: 'line', smooth: true,
      // derived[i]=true 的点（推算值）降透明度展示，x 轴 label 已带 *
      data: s.derived?.length
        ? s.values.map((v, i) => (s.derived[i] ? { value: v, itemStyle: { opacity: 0.35 } } : v))
        : s.values,
    })),
  }, true)  // notMerge=true，重渲染时完全替换
}
function onResize() { chart?.resize() }
onMounted(() => { chart = echarts.init(el.value); render(); window.addEventListener('resize', onResize) })
onUnmounted(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
watch(() => props.series, render, { deep: true })
</script>
