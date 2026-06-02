import { useEffect, useRef, useState } from 'react'

/**
 * Searchable city selector: type to filter, click or use ↑/↓/Enter to pick.
 */
export default function CityCombobox({ cities, value, onChange, placeholder = 'חיפוש עיר…' }) {
  const [query, setQuery] = useState(value || '')
  const [open, setOpen] = useState(false)
  const [highlight, setHighlight] = useState(0)
  const boxRef = useRef(null)

  useEffect(() => setQuery(value || ''), [value])

  useEffect(() => {
    const onDoc = (e) => boxRef.current && !boxRef.current.contains(e.target) && setOpen(false)
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  const term = query.trim()
  const filtered = (term ? cities.filter((c) => c.includes(term)) : cities).slice(0, 60)

  const select = (c) => {
    onChange(c)
    setQuery(c)
    setOpen(false)
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      setOpen(true)
      e.preventDefault()
      setHighlight((h) => {
        const next = e.key === 'ArrowDown' ? h + 1 : h - 1
        return Math.max(0, Math.min(next, filtered.length - 1))
      })
    } else if (e.key === 'Enter' && open && filtered[highlight]) {
      e.preventDefault()
      select(filtered[highlight])
    } else if (e.key === 'Escape') {
      setOpen(false)
    }
  }

  return (
    <div ref={boxRef} className="relative">
      <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-slate-400">
        📍
      </span>
      <input
        value={query}
        onChange={(e) => {
          setQuery(e.target.value)
          setOpen(true)
          setHighlight(0)
          if (e.target.value === '') onChange('')
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        role="combobox"
        aria-expanded={open}
        autoComplete="off"
        className="w-full rounded-2xl border border-slate-200 bg-white py-2.5 pr-9 pl-9 text-slate-700 shadow-sm outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
      />
      {value && (
        <button
          type="button"
          onClick={() => {
            onChange('')
            setQuery('')
            setOpen(false)
          }}
          title="נקה"
          className="absolute inset-y-0 left-2 flex items-center rounded-lg px-1.5 text-slate-400 transition hover:text-rose-500"
        >
          ✕
        </button>
      )}

      {open && (filtered.length > 0 || term) && (
        <ul className="animate-in absolute z-30 mt-2 max-h-64 w-full overflow-auto rounded-2xl border border-slate-200 bg-white p-1.5 shadow-xl">
          {filtered.map((c, i) => (
            <li key={c}>
              <button
                type="button"
                onMouseEnter={() => setHighlight(i)}
                onClick={() => select(c)}
                className={`flex w-full items-center gap-2 rounded-xl px-3 py-2 text-right text-sm transition ${
                  i === highlight ? 'bg-emerald-50 text-emerald-800' : 'text-slate-700 hover:bg-slate-50'
                }`}
              >
                <span className="text-slate-300">📍</span>
                {c}
              </button>
            </li>
          ))}
          {term && filtered.length === 0 && (
            <li className="px-3 py-3 text-center text-sm text-slate-400">לא נמצאה עיר בשם "{term}"</li>
          )}
        </ul>
      )}
    </div>
  )
}
