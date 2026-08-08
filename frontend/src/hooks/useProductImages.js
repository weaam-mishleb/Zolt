import { useEffect, useState } from 'react'

import { resolveProductImages } from '../api'

/**
 * Resolve images for products the API returned WITHOUT one.
 *
 * Search and comparison responses carry `image_url` only when it is already
 * cached server-side — they never call a provider, so a keystroke is never
 * waiting on Open Food Facts. This hook is the second step: it asks the backend
 * to go look, once, for the ids that came back empty.
 *
 * Resolved urls are remembered for the life of the page, so re-rendering a list
 * or reopening the dropdown does not re-ask. The backend caches permanently in
 * `products.image_url`; this map only avoids repeating the round trip.
 *
 * Returns {product_id: url}. Merge it over `image_url` at the call site — never
 * instead of it, or an already-cached image would blink out while this resolves.
 */
export function useProductImages(products) {
  const [resolved, setResolved] = useState({})

  // Only the ids that lack a url AND that we have not already asked about. Joined
  // into a string so the effect compares by value; an array literal would be a new
  // reference every render and loop forever.
  const pending = [
    ...new Set(
      (products || [])
        .filter((p) => p && p.id && !p.image_url && resolved[p.id] === undefined)
        .map((p) => p.id),
    ),
  ]
  const key = pending.join(',')

  useEffect(() => {
    if (!key) return
    let cancelled = false
    const ids = key.split(',').map(Number)
    resolveProductImages(ids)
      .then((map) => {
        if (cancelled) return
        // Record a null for every id we asked about, not just the hits. Without
        // that, a product with no image stays "pending" forever and every render
        // fires the request again.
        const next = {}
        for (const id of ids) next[id] = map?.[id] ?? null
        setResolved((prev) => ({ ...prev, ...next }))
      })
      .catch(() => {
        if (cancelled) return
        const next = {}
        for (const id of ids) next[id] = null
        setResolved((prev) => ({ ...prev, ...next }))
      })
    return () => {
      cancelled = true
    }
  }, [key])

  return resolved
}
