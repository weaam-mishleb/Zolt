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

// ── per-cell class helpers (per-side border colors avoid clobbering) ──
function thClass(isWinner, incomplete) {
  const base =
    'min-w-[160px] px-4 py-4 align-top border-b border-b-slate-200 border-l border-l-slate-200/70'
  if (isWinner) return `${base} bg-emerald-100 border-l-emerald-300`
  if (incomplete) return `${base} bg-amber-50`
  return `${base} bg-slate-50`
}

function tdClass(isWinner) {
  const base = 'px-4 py-4 text-center tabular-nums border-b border-b-slate-100 border-l border-l-slate-100'
  return isWinner
    ? `${base} bg-emerald-50 font-semibold text-emerald-800 border-l-emerald-200 group-hover:bg-emerald-100`
    : `${base} bg-white text-slate-700 group-hover:bg-slate-50`
}

function tfClass(isWinner, incomplete) {
  const base = 'px-4 py-4 text-center text-base font-black tabular-nums border-l border-l-slate-200/70'
  if (isWinner) return `${base} bg-emerald-100 text-emerald-800 border-l-emerald-300`
  if (incomplete) return `${base} bg-amber-50 text-amber-800`
  return `${base} bg-slate-50 text-slate-800`
}

// Pinned-right product column — solid opaque bg + z-20 so it never glitches.
const STICKY = 'sticky right-0 z-20 border-l-2 border-l-slate-300'
const stickyHead = `${STICKY} bg-slate-50 px-4 py-4 text-right font-semibold text-slate-600 border-b border-b-slate-200`
const stickyFoot = `${STICKY} bg-slate-50 px-4 py-4 text-right font-bold text-slate-800`
const stickyBody = `${STICKY} bg-white px-4 py-4 text-right font-medium text-slate-700 border-b border-b-slate-100 group-hover:bg-slate-50`

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

      {/* Products × stores matrix — rounded frame + inner horizontal scroll */}
      <div className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-xl shadow-slate-900/5">
        <div className="overflow-x-auto">
          <table className="w-full border-separate border-spacing-0 text-sm">
            <thead>
              <tr>
                <th className={stickyHead}>מוצר</th>
                {stores.map((s) => {
                  const isWinner = s.store_id === winner_store_id
                  return (
                    <th key={s.store_id} className={thClass(isWinner, !s.is_complete)}>
                      <div className="flex flex-col items-center gap-1">
                        {isWinner && (
                          <span className="rounded-full bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white shadow-sm">
                            🏆 הזול ביותר
                          </span>
                        )}
                        {!s.is_complete && (
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
              {products.map((p) => (
                <tr key={p.id} className="group">
                  <td className={stickyBody}>{p.name || p.barcode}</td>
                  {stores.map((s) => {
                    const it = maps[s.store_id][p.id]
                    const isWinner = s.store_id === winner_store_id
                    if (!it || !it.found) {
                      return (
                        <td key={s.store_id} className={tdClass(isWinner)}>
                          <span className="rounded-md bg-amber-100 px-2 py-1 text-xs font-semibold text-amber-700">
                            חסר
                          </span>
                        </td>
                      )
                    }
                    return (
                      <td key={s.store_id} className={tdClass(isWinner)}>
                        {ils.format(it.line_total)}
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>

            <tfoot>
              <tr>
                <td className={stickyFoot}>סה״כ</td>
                {stores.map((s) => {
                  const isWinner = s.store_id === winner_store_id
                  return (
                    <td key={s.store_id} className={tfClass(isWinner, !s.is_complete)}>
                      {ils.format(s.total)}
                      {!s.is_complete && <span className="block text-[11px] font-medium">חלקי</span>}
                    </td>
                  )
                })}
              </tr>
            </tfoot>
          </table>
        </div>
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
