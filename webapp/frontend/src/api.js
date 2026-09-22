async function req(url, opts = {}) {
  const r = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw Object.assign(new Error(body.detail || r.statusText), { status: r.status })
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  stocks: (q = '', page = 1, size = 50, favorite = false, index = '', hasFilings = false) =>
    req(`/api/stocks?q=${encodeURIComponent(q)}&page=${page}&size=${size}` +
        `${favorite ? '&favorite=true' : ''}${index ? `&index=${index}` : ''}` +
        `${hasFilings ? '&has_filings=true' : ''}`),
  setFavorite: (ticker, favorite) =>
    req(`/api/stocks/${ticker}/favorite`, { method: 'POST', body: JSON.stringify({ favorite }) }),
  setAliases: (ticker, aliases) =>
    req(`/api/stocks/${ticker}/aliases`, { method: 'POST', body: JSON.stringify({ aliases }) }),
  stock: (t) => req(`/api/stocks/${t}`),
  analyses: (t) => req(`/api/stocks/${t}/analyses`),
  financials: (t) => req(`/api/stocks/${t}/financials`),
  analysis: (t, id) => req(`/api/stocks/${t}/analyses/${id}`),
  clearAnalyses: (t) => req(`/api/stocks/${t}/analyses`, { method: 'DELETE' }),
  dcf: (t) => req(`/api/stocks/${t}/dcf`),
  dcfOne: (t, id) => req(`/api/stocks/${t}/dcf/${id}`),
  deleteDcf: (t, id) => req(`/api/stocks/${t}/dcf/${id}`, { method: 'DELETE' }),
  versions: () => req('/api/versions'),
  tasks: (status = '') => req(`/api/tasks${status ? `?status=${status}` : ''}`),
  task: (id) => req(`/api/tasks/${id}`),
  taskLog: (id, offset = 0) => req(`/api/tasks/${id}/log?offset=${offset}`),
  createTask: (task_type, ticker, params = {}) =>
    req('/api/tasks', { method: 'POST', body: JSON.stringify({ task_type, ticker, params }) }),
  cancelTask: (id) => req(`/api/tasks/${id}/cancel`, { method: 'POST' }),
  clearTasks: () => req('/api/tasks', { method: 'DELETE' }),
  syncStocks: () => req('/api/stocks/sync', { method: 'POST' }),
}
