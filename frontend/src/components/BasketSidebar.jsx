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
  notice,
}) {
  const count = items.reduce((sum, it) => sum + it.quantity, 0)

  return (
    <aside className="lg:sticky lg:top-24 lg:self-start">
      <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-bold text-slate-800">הסל שלי</h2>
          <span className="rounded-full bg-emerald-100 px-3 py-1 text-sm font-semibold text-emerald-700">
            {count} פריטים
          </span>
        </div>

        <label className="mb-1 block text-sm font-medium text-slate-600">עיר להשוואה</label>
        <select
          value={city}
          onChange={(e) => onCityChange(e.target.value)}
          className="mb-4 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-slate-700 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
        >
          <option value="">בחרו עיר…</option>
          {cities.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        {items.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-400">
            הסל ריק — חפשו מוצרים והוסיפו אותם 🛒
          </p>
        ) : (
          <ul className="mb-3 space-y-2">
            {items.map(({ product, quantity }) => (
              <li
                key={product.id}
                className="flex items-center gap-2 rounded-xl border border-slate-100 bg-slate-50 p-2"
              >
                <button
                  onClick={() => onRemove(product.id)}
                  title="הסר"
                  className="shrink-0 rounded-lg px-2 py-1 text-rose-400 transition hover:bg-rose-50 hover:text-rose-600"
                >
                  ✕
                </button>
                <span className="min-w-0 flex-1 truncate text-sm text-slate-700">{product.name}</span>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => onDec(product.id)}
                    className="h-7 w-7 rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-100"
                  >
                    −
                  </button>
                  <span className="w-6 text-center text-sm font-semibold text-slate-800">
                    {quantity}
                  </span>
                  <button
                    onClick={() => onInc(product.id)}
                    className="h-7 w-7 rounded-lg border border-slate-200 bg-white text-slate-600 transition hover:bg-slate-100"
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
          className="w-full rounded-2xl bg-emerald-600 py-3 font-bold text-white shadow-sm transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {comparing ? 'משווה…' : 'השוו מחירים'}
        </button>

        {!city && items.length > 0 && (
          <p className="mt-2 text-center text-xs text-slate-400">בחרו עיר כדי להשוות</p>
        )}
        {notice && (
          <p className="mt-3 rounded-xl bg-amber-50 p-2 text-center text-xs text-amber-700">{notice}</p>
        )}
      </div>
    </aside>
  )
}
