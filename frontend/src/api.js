// Thin API client for the Zolt backend.
const BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://127.0.0.1:8000'

async function http(path, options) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`)
  }
  return res.json()
}

export function searchProducts(q, limit = 8) {
  const params = new URLSearchParams({ q, limit: String(limit) })
  return http(`/products/search?${params.toString()}`)
}

export function getCities() {
  return http('/stores/cities')
}

// Used in Step 7.
export function compareBasket(city, items) {
  return http('/basket/compare', {
    method: 'POST',
    body: JSON.stringify({ city, items }),
  })
}
