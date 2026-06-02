import { useEffect, useRef, useState } from 'react'
import { searchProducts } from '../api'
import { useDebounce } from '../hooks/useDebounce'

export default function SearchBar({ onAdd }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
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
          placeholder="חפשו מוצר... (למשל: חלב, קוקה קולה, במבה)"
          className="w-full rounded-2xl border border-slate-200 bg-white py-4 pr-14 pl-24 text-lg shadow-sm shadow-slate-900/5 outline-none transition focus:border-emerald-400 focus:shadow-lg focus:shadow-emerald-600/10 focus:ring-4 focus:ring-emerald-100"
        />
        {loading && (
          <span className="absolute inset-y-0 left-5 flex items-center text-sm text-slate-400">
            טוען…
          </span>
        )}
      </div>

      {open && (results.length > 0 || error) && (
        <ul className="animate-in absolute z-20 mt-2 max-h-96 w-full overflow-auto rounded-2xl border border-slate-200 bg-white p-2 shadow-2xl shadow-slate-900/10">
          {error && <li className="px-3 py-3 text-center text-sm text-rose-600">{error}</li>}
          {results.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => handleAdd(p)}
                className="group flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2.5 text-right transition hover:bg-emerald-50"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-800">{p.name}</span>
                  {p.manufacturer && (
                    <span className="block truncate text-xs text-slate-400">{p.manufacturer}</span>
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
