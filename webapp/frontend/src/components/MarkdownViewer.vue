<template><div class="markdown" v-html="html"></div></template>
<script setup>
import { computed } from 'vue'
import MarkdownIt from 'markdown-it'
const props = defineProps({ source: String })
const md = new MarkdownIt({ html: false })
const html = computed(() => md.render(props.source || ''))
</script>
