import { defineStore } from 'pinia'
import { api } from '../api'

export const useTaskStore = defineStore('tasks', {
  state: () => ({ tasks: [], timer: null }),
  getters: {
    active: (s) => s.tasks.filter(t => t.status === 'pending' || t.status === 'running'),
  },
  actions: {
    startPolling() {
      if (this.timer) return
      const tick = async () => {
        try { this.tasks = await api.tasks() } catch { /* 忽略瞬时错误 */ }
      }
      tick()
      this.timer = setInterval(tick, 2000)
    },
    async submit(type, ticker, params) {
      const t = await api.createTask(type, ticker, params)
      await new Promise(r => setTimeout(r, 300))
      this.tasks = await api.tasks()
      return t
    },
  }
})
