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
  // 每股内在价值与股价同量纲；旧报告无 per_share 时该柱为 null（echarts 渲染为空），
  // 标题后缀提示，避免再拿市值口径（$M）和股价（$）混画差 5600 倍的图
  const hasPerShare = v.intrinsic_value_per_share != null
  chart.setOption({
    title: { text: `DCF 估值 vs 现价（报告 #${props.report.id}）${hasPerShare ? '' : '（无每股数据）'}` },
    tooltip: {},
    xAxis: { type: 'category', data: ['每股内在价值', '当前股价'] },
    yAxis: { type: 'value' },
    series: [{ type: 'bar', data: [
      { value: v.intrinsic_value_per_share ?? null, itemStyle: { color: '#1f883d' } },
      { value: v.price, itemStyle: { color: '#d0d7de' } },
    ] }],
  })
  window.addEventListener('resize', onResize)
})
onUnmounted(() => { window.removeEventListener('resize', onResize); chart?.dispose() })
</script>
