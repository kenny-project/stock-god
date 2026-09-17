<template><div ref="el" style="height:320px; margin:16px 0"></div></template>
<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import * as echarts from 'echarts'
const props = defineProps({ report: Object })
const el = ref(null)
let chart
function onResize() { chart?.resize() }
onMounted(() => {
  chart = echarts.init(el.value)
  const v = props.report.valuation
  chart.setOption({
    title: { text: `DCF 估值 vs 现价（报告 #${props.report.id}）` },
    tooltip: {},
    xAxis: { type: 'category', data: ['内在价值', '当前股价'] },
    yAxis: { type: 'value' },
    series: [{ type: 'bar', data: [
      { value: v.intrinsic_value_musd, itemStyle: { color: '#1f883d' } },
      { value: v.price, itemStyle: { color: '#d0d7de' } },
    ] }],
  })
  window.addEventListener('resize', onResize)
})
onUnmounted(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
</script>
