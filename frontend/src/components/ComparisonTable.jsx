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

// ── Sleek data-table cells: only subtle horizontal separators; the winner
//    column is tinted green for grouping (no heavy grid lines). ──
function thClass(isWinner, incomplete) {
  const base = 'sticky top-0 z-10 min-w-[160px] px-5 py-4 align-top border-b border-slate-200/80'
  if (isWinner) return `${base} bg-emerald-50`
  if (incomplete) return `${base} bg-amber-50/60`
  return `${base} bg-white/95 backdrop-blur`
}

function tdClass(isWinner) {
  const base = 'px-5 py-3.5 text-center tabular-nums border-b border-slate-100 transition-colors'
  return isWinner
    ? `${base} bg-emerald-50/70 font-semibold text-emerald-800 group-hover:bg-emerald-100/70`
    : `${base} bg-white text-slate-600 group-hover:bg-slate-50/70`
}

function tfClass(isWinner, incomplete) {
  const base = 'px-5 py-4 text-center text-base font-black tabular-nums border-t border-slate-200/80'
  if (isWinner) return `${base} bg-emerald-100/70 text-emerald-800`
  if (incomplete) return `${base} bg-amber-50/60 text-amber-800`
  return `${base} bg-white text-slate-800`
}

// Pinned-right product column.
// THE WEBKIT RTL FIX: solid opaque bg + sticky on the cell itself + transform-gpu
// (will-change-transform) to force each pinned cell onto its own GPU layer, so
// Safari/WebKit never drops it while dragging the horizontal scrollbar.
const STICKY = 'sticky right-0 transform-gpu will-change-transform border-l border-slate-200/80'
const stickyHead = `${STICKY} top-0 z-30 bg-white px-5 py-4 text-right text-xs font-semibold uppercase tracking-wider text-slate-400 border-b border-slate-200/80`
const stickyBody = `${STICKY} z-20 bg-white px-5 py-3.5 text-right font-medium text-slate-700 border-b border-slate-100 group-hover:bg-slate-50/70`
const stickyFoot = `${STICKY} z-20 bg-white px-5 py-4 text-right font-bold text-slate-800 border-t border-slate-200/80`

export default function ComparisonTable({ result }) {
  const { products, stores, winner_store_id, complete_store_count, store_count, shown_store_count } =
    result

  if (!stores.length) {
    return (
      <div className="animate-in mt-8 rounded-3xl border border-slate-200/70 bg-white p-10 text-center text-slate-500 shadow-sm ring-1 ring-slate-900/5">
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
    <div className="animate-in mt-8 space-y-5">
      {/* Winner / no-winner banner */}
      {winner ? (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-emerald-100 bg-gradient-to-l from-emerald-50 to-teal-50/50 p-5 shadow-sm ring-1 ring-emerald-600/5">
          <div className="flex items-center gap-3">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white text-2xl shadow-sm ring-1 ring-emerald-600/10">
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

      {/* Single scroll context (w-full + overflow-x-auto). Table is min-w-max so it
          grows past the viewport and scrolls natively in RTL. */}
      <div className="w-full overflow-x-auto rounded-3xl border border-slate-200/70 bg-white shadow-sm ring-1 ring-slate-900/5">
        <table className="w-full min-w-max border-separate border-spacing-0 text-sm">
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
                      <span className="text-xs text-slate-400">{storeLabel(s)}</span>
                      {s.rank && <span className="text-[11px] text-slate-300">מקום {s.rank}</span>}
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
                        <span className="rounded-md bg-amber-100/80 px-2 py-1 text-xs font-semibold text-amber-700">
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

      {/* Explicit list of which products are missing where */}
      {incompleteStores.length > 0 && (
        <div className="rounded-3xl border border-amber-200/80 bg-amber-50/70 p-4 shadow-sm">
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
