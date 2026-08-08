// Thin API client for the Zolt backend.
const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

async function http(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    const err = new Error(`HTTP ${res.status}`)
    err.status = res.status
    throw err
  }
  return res.json()
}

const auth = (token, options = {}) => ({
  ...options,
  headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) },
})

// ── Public ───────────────────────────────────────────────
export function searchProducts(q, limit = 8) {
  const params = new URLSearchParams({ q, limit: String(limit) })
  return http(`/products/search?${params.toString()}`)
}

export function resolveProductImages(ids) {
  // Batched on purpose: one request per tile would multiply a metered backend
  // lookup by the length of the list. The endpoint caps at 50 ids.
  const params = new URLSearchParams({ ids: ids.join(',') })
  return http(`/products/images?${params.toString()}`)
}

export function contributeProductPhoto(productId, file) {
  // multipart, so no Content-Type header — the browser must set the boundary.
  const body = new FormData()
  body.append('photo', file)
  return fetch(`${BASE}/products/${productId}/photo`, { method: 'POST', body }).then((r) => r.json())
}

export function getCities() {
  return http('/stores/cities')
}

export function compareBasket(city, items) {
  return http('/basket/compare', { method: 'POST', body: JSON.stringify({ city, items }) })
}

export function basketSummary(items) {
  return http('/basket/summary', { method: 'POST', body: JSON.stringify({ items }) })
}

// ── Admin (JWT) ──────────────────────────────────────────
export function adminLogin(username, password) {
  return http('/admin/login', { method: 'POST', body: JSON.stringify({ username, password }) })
}

export function getSchedulerStatus(token) {
  return http('/admin/scheduler', auth(token))
}

export function runEtl(token, full = false) {
  return http(`/admin/etl/run?full=${full}`, auth(token, { method: 'POST' }))
}

export function getEtlStatus(token) {
  return http('/admin/etl/status', auth(token))
}

export function getEtlJobs(token, limit = 5) {
  return http(`/admin/etl/jobs?limit=${limit}`, auth(token))
}
