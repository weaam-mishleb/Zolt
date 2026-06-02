import CityCombobox from './CityCombobox.jsx'

export default function BasketSidebar({
  items,
  cities,
  city,
  onCityChange,
  onInc,
  onDec,
  onRemove,
  onClear,
  onCompare,
  comparing,
}) {
  const count = items.reduce((sum, it) => sum + it.quantity, 0)

  return (
    <aside className="lg:sticky lg:top-24 lg:self-start">
      <div className="rounded-3xl border border-slate-200/70 bg-white p-6 shadow-sm ring-1 ring-slate-900/5">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="flex items-center gap-2 text-lg font-bold text-slate-800">
            <span>🛒</span> הסל שלי
          </h2>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-sm font-semibold text-emerald-700">
            {count} פריטים
          </span>
        </div>

        <label className="mb-1.5 block text-sm font-medium text-slate-600">עיר להשוואה</label>
        <CityCombobox cities={cities} value={city} onChange={onCityChange} />

        <div className="my-4 h-px bg-slate-100" />

        {items.length === 0 ? (
          <div className="py-8 text-center">
            <div className="text-3xl opacity-60">🧺</div>
            <p className="mt-2 text-sm text-slate-400">הסל ריק — חפשו מוצרים והוסיפו אותם</p>
          </div>
        ) : (
          <ul className="mb-3 space-y-2">
            {items.map(({ product, quantity }) => (
              <li
                key={product.id}
                className="group flex items-center gap-2 rounded-2xl bg-slate-50/70 p-2 ring-1 ring-slate-200/50 transition hover:bg-white hover:shadow-sm hover:ring-slate-200"
              >
                <button
                  onClick={() => onRemove(product.id)}
                  title="הסר"
                  className="shrink-0 rounded-lg px-2 py-1 text-slate-300 transition hover:bg-rose-50 hover:text-rose-600"
                >
                  ✕
                </button>
                <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{product.name}</span>
                <div className="flex shrink-0 items-center gap-1 rounded-full bg-white p-0.5 ring-1 ring-slate-200">
                  <button
                    onClick={() => onDec(product.id)}
                    className="grid h-7 w-7 place-items-center rounded-full text-slate-600 transition hover:bg-slate-100"
                  >
                    −
                  </button>
                  <span className="w-6 text-center text-sm font-bold text-slate-800">{quantity}</span>
                  <button
                    onClick={() => onInc(product.id)}
                    className="grid h-7 w-7 place-items-center rounded-full text-emerald-600 transition hover:bg-emerald-50"
                  >
                    +
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}

        {items.length > 0 && (
          <button
            onClick={onClear}
            className="mb-3 w-full text-xs text-slate-400 transition hover:text-rose-500"
          >
            נקה סל
          </button>
        )}

        <button
          onClick={onCompare}
          disabled={!items.length || !city || comparing}
          className="w-full rounded-2xl bg-gradient-to-l from-emerald-600 to-teal-500 py-3.5 font-bold text-white shadow-lg shadow-emerald-600/25 transition hover:-translate-y-0.5 hover:shadow-emerald-600/40 disabled:translate-y-0 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300 disabled:shadow-none"
        >
          {comparing ? 'משווה…' : 'השוו מחירים'}
        </button>

        {!city && items.length > 0 && (
          <p className="mt-2 text-center text-xs text-slate-400">בחרו עיר כדי להשוות</p>
        )}
      </div>
    </aside>
  )
}
