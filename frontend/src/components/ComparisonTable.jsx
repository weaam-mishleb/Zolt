const ils = new Intl.NumberFormat('he-IL', {
  style: 'currency',
  currency: 'ILS',
  maximumFractionDigits: 2,
})

const storeLabel = (s) => s.store_name || `סניף ${s.store_id}`

function itemsByProduct(store) {
  const map = {}
  for (const it of store.items) map[it.product_id] = it
  return map
}

export default function ComparisonTable({ result }) {
  const { products, stores, winner_store_id, complete_store_count, store_count, shown_store_count } =
    result

  if (!stores.length) {
    return (
      <div className="animate-in mt-8 rounded-3xl border border-slate-200 bg-white p-10 text-center text-slate-500 shadow-sm">
        לא נמצאו סניפים בעיר זו שמחזיקים את מוצרי הסל. נסו עיר אחרת. 🏙️
      </div>
    )
  }

  const winner = stores.find((s) => s.store_id === winner_store_id) || null
  const maps = Object.fromEntries(stores.map((s) => [s.store_id, itemsByProduct(s)]))
  const incompleteStores = stores.filter((s) => !s.is_complete)
  const productName = (id) => products.find((p) => p.id === id)?.name || `#${id}`
  const shown = shown_store_count || stores.length

  return (
    <div className="animate-in mt-8 space-y-4">
      {/* Winner / no-winner banner */}
      {winner ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-emerald-200 bg-gradient-to-l from-emerald-50 to-teal-50 p-5 shadow-sm">
          <div className="flex items-center gap-3">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white text-2xl shadow-sm">
              🏆
            </span>
            <div>
              <p className="text-sm font-medium text-emerald-700">הסל הזול ביותר</p>
              <p className="text-lg font-black text-emerald-900">
                {winner.chain_name} · {storeLabel(winner)}
              </p>
            </div>
          </div>
          <div className="text-3xl font-black text-emerald-700">{ils.format(winner.total)}</div>
        </div>
      ) : (
        <div className="rounded-3xl border border-amber-200 bg-amber-50 p-4 text-amber-800 shadow-sm">
          ⚠ אף סניף בעיר אינו מחזיק את כל מוצרי הסל — מוצגות עלויות חלקיות בלבד.
        </div>
      )}

      <p className="px-1 text-sm text-slate-500">
        {shown < store_count
          ? `מוצגים ${shown} הסניפים המשתלמים מתוך ${store_count} בעיר`
          : `הושוו ${store_count} סניפים`}{' '}
        · {complete_store_count} מחזיקים את כל המוצרים
      </p>

      {/* Products × stores matrix */}
      <div className="overflow-x-auto rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-900/5">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr>
              <th className="sticky right-0 z-10 bg-slate-50 p-3 text-right font-semibold text-slate-600">
                מוצר
              </th>
              {stores.map((s) => {
                const isWinner = s.store_id === winner_store_id
                const incomplete = !s.is_complete
                return (
                  <th
                    key={s.store_id}
                    className={`min-w-[150px] p-3 align-top ${
                      isWinner ? 'bg-emerald-100' : incomplete ? 'bg-amber-50' : 'bg-slate-50'
                    }`}
                  >
                    <div className="flex flex-col items-center gap-1">
                      {isWinner && (
                        <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white shadow-sm">
                          🏆 הזול ביותר
                        </span>
                      )}
                      {incomplete && (
                        <span className="rounded-full bg-amber-500 px-2 py-0.5 text-[11px] font-bold text-white">
                          ⚠ חסרים {s.missing_count}
                        </span>
                      )}
                      <span className="font-bold text-slate-800">{s.chain_name}</span>
                      <span className="text-xs text-slate-500">{storeLabel(s)}</span>
                      {s.rank && <span className="text-[11px] text-slate-400">מקום {s.rank}</span>}
                    </div>
                  </th>
                )
              })}
            </tr>
          </thead>

          <tbody>
            {products.map((p, ri) => (
              <tr key={p.id} className={ri % 2 ? 'bg-slate-50/40' : ''}>
                <td className="sticky right-0 z-10 bg-inherit p-3 text-right font-medium text-slate-700">
                  {p.name || p.barcode}
                </td>
                {stores.map((s) => {
                  const it = maps[s.store_id][p.id]
                  const isWinner = s.store_id === winner_store_id
                  if (!it || !it.found) {
                    return (
                      <td key={s.store_id} className="p-3 text-center">
                        <span className="rounded-md bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-700">
                          חסר
                        </span>
                      </td>
                    )
                  }
                  return (
                    <td
                      key={s.store_id}
                      className={`p-3 text-center tabular-nums text-slate-700 ${
                        isWinner ? 'bg-emerald-50 font-semibold text-emerald-800' : ''
                      }`}
                    >
                      {ils.format(it.line_total)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>

          <tfoot>
            <tr className="border-t-2 border-slate-200">
              <td className="sticky right-0 z-10 bg-slate-50 p-3 text-right font-bold text-slate-800">
                סה״כ
              </td>
              {stores.map((s) => {
                const isWinner = s.store_id === winner_store_id
                const incomplete = !s.is_complete
                return (
                  <td
                    key={s.store_id}
                    className={`p-3 text-center text-base font-black tabular-nums ${
                      isWinner
                        ? 'bg-emerald-100 text-emerald-800'
                        : incomplete
                          ? 'bg-amber-50 text-amber-800'
                          : 'text-slate-800'
                    }`}
                  >
                    {ils.format(s.total)}
                    {incomplete && <span className="block text-[11px] font-medium">חלקי</span>}
                  </td>
                )
              })}
            </tr>
          </tfoot>
        </table>
      </div>

      {/* Explicit list of which products are missing where */}
      {incompleteStores.length > 0 && (
        <div className="rounded-3xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
          <p className="mb-2 font-semibold text-amber-800">מוצרים חסרים בסניפים</p>
          <ul className="space-y-1 text-sm text-amber-700">
            {incompleteStores.map((s) => (
              <li key={s.store_id}>
                <span className="font-medium">
                  {s.chain_name} · {storeLabel(s)}:
                </span>{' '}
                {s.missing_product_ids.map(productName).join(', ')}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
