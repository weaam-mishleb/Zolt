import { useEffect, useRef, useState } from 'react'
import { searchProducts } from '../api'
import ProductImage from './ProductImage.jsx'
import { useDebounce } from '../hooks/useDebounce'
import { useProductImages } from '../hooks/useProductImages.js'

// "55 גרם" / "1 ק"ג" — package size from the feed, so generic butcher-counter
// names ("בשר אדום טרי") become distinguishable in the dropdown.
function sizeLabel(p) {
  const q = p.quantity != null ? parseFloat(p.quantity) : null
  const qty = q && q > 0 ? (Number.isInteger(q) ? String(q) : q.toFixed(1)) : ''
  return [qty, p.unit_qty || ''].filter(Boolean).join(' ').trim() || null
}

export default function SearchBar({ onAdd }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  // Second step of the image pipeline: search returns only what the backend has
  // already cached, so ask it to resolve the rest once.
  const images = useProductImages(results)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const debounced = useDebounce(query, 300)
  const boxRef = useRef(null)

  useEffect(() => {
    const term = debounced.trim()
    if (term.length < 2) {
      setResults([])
      setError(null)
      return
    }
    let active = true
    setLoading(true)
    setError(null)
    searchProducts(term, 8)
      .then((data) => {
        if (!active) return
        setResults(data)
        setOpen(true)
      })
      .catch(() => {
        if (!active) return
        setError('לא ניתן להתחבר לשרת')
        setResults([])
        setOpen(true)
      })
      .finally(() => active && setLoading(false))
    return () => {
      active = false
    }
  }, [debounced])

  useEffect(() => {
    const onDocClick = (e) => boxRef.current && !boxRef.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  function handleAdd(product) {
    onAdd(product)
    setQuery('')
    setResults([])
    setOpen(false)
  }

  return (
    <div ref={boxRef} className="relative">
      <div className="relative">
        <span className="pointer-events-none absolute inset-y-0 right-5 flex items-center text-xl text-slate-400">
          🔍
        </span>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onFocus={() => (results.length || error) && setOpen(true)}
          placeholder="חפשו מוצר..."
          className="w-full rounded-2xl border border-slate-200/70 bg-white py-4 pr-14 pl-24 text-lg shadow-lg shadow-slate-900/5 outline-none ring-1 ring-slate-900/[0.03] transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
        />
        {loading && (
          <span className="absolute inset-y-0 left-5 flex items-center text-sm text-slate-400">
            טוען…
          </span>
        )}
      </div>

      {open && (results.length > 0 || error) && (
        <ul className="animate-in absolute z-20 mt-2 max-h-96 w-full overflow-auto rounded-2xl border border-slate-200/70 bg-white p-2 shadow-xl ring-1 ring-slate-900/5">
          {error && <li className="px-3 py-3 text-center text-sm text-rose-600">{error}</li>}
          {results.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => handleAdd(p)}
                className="group flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-right transition hover:bg-emerald-50"
              >
                {/* Same tile the basket and the comparison use, so a product keeps
                    one identity from search to result. It is placeholder-first by
                    design: Open Food Facts has a usable image for 7% of our real
                    GTINs (measured, scripts/off_coverage.py), so the generated
                    monogram is the normal case rather than a fallback. */}
                <ProductImage
                  barcode={p.barcode}
                  name={p.name}
                  src={p.image_url || images[p.id] || null}
                  size="sm"
                  className="ring-1 ring-slate-200/70"
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium text-slate-800">{p.name}</span>
                  {(p.manufacturer || sizeLabel(p) || p.is_weighted || p.availability > 0) && (
                    <span className="block truncate text-xs text-slate-400">
                      {[
                        p.manufacturer,
                        sizeLabel(p),
                        p.is_weighted ? '⚖️ במשקל' : null,
                        p.availability > 0 ? `ב-${p.availability} סניפים` : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  )}
                </span>
                <span className="shrink-0 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-semibold text-white opacity-90 transition group-hover:opacity-100">
                  הוסף +
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
