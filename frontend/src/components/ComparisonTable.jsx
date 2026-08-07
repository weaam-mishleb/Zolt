import { useState } from 'react'
import ProductImage from './ProductImage.jsx'
import PromotionBadge from './promotions/PromotionBadge.jsx'
import { useMediaQuery } from '../hooks/useMediaQuery.js'

const ils = new Intl.NumberFormat('he-IL', {
  style: 'currency',
  currency: 'ILS',
  maximumFractionDigits: 2,
})

const storeLabel = (s) => s.store_name || `סניף ${s.store_id}`

// A product the feed shipped with no name falls back to its code. Internal
// (non-GTIN) codes are stored namespaced as "<chainId>_<code>" so they stay
// unique across chains — that namespace is a storage detail, not something to
// put in front of a shopper.
const productCode = (barcode) => (barcode || '').replace(/^\d{13}_/, '')
const displayName = (p) => p.name || productCode(p.barcode)

function itemsByProduct(store) {
  const map = {}
  for (const it of store.items) map[it.product_id] = it
  return map
}

// Basket quantity is NOT on `products` — that array is a `ProductBrief`
// (id/name/barcode only, see schemas.py). It lives on the store items, where the
// backend writes one entry per requested product for EVERY store, found or not
// (comparison.py builds `items_out` by iterating `pids`). So any store is a
// complete source and they all agree; scanning them just tolerates a short list.
function qtyByProduct(stores) {
  const map = {}
  for (const s of stores) {
    for (const it of s.items) {
      if (map[it.product_id] == null && it.quantity != null) map[it.product_id] = it.quantity
    }
  }
  return map
}

// Weighted items (meat, produce) are legitimately fractional — 0.5 kg must not
// render as "×0.5000" or get rounded away to "×1".
const qtyLabel = (q) => `×${Number.isInteger(q) ? q : Number(q.toFixed(2))}`

function QtyBadge({ qty }) {
  if (qty == null) return null
  // `self-start` keeps it hugging its content — the flex-col parents in both
  // layouts would otherwise stretch it across the whole cell. If a sibling ever
  // joins it, space them with `gap`, never `ml-*`/`mr-*`: a physical margin
  // opens on the wrong side once the flow is RTL.
  return (
    <span className="inline-flex shrink-0 items-center justify-center self-start rounded-full bg-blue-100 px-2 py-0.5 text-[11px] font-bold leading-tight tabular-nums text-blue-700">
      <span className="sr-only">כמות </span>
      {qtyLabel(qty)}
    </span>
  )
}

// One basket line's price, shared by both layouts so the promotion rules cannot
// drift apart between them.
function LinePrice({ it, align = 'center' }) {
  if (!it || !it.found) {
    return (
      <span className="inline-block rounded-md bg-amber-100/80 px-2 py-1 text-xs font-semibold text-amber-700">
        חסר
      </span>
    )
  }
  const discounted = it.original_line_total != null && it.original_line_total > it.line_total
  if (!discounted) return ils.format(it.line_total)
  return (
    <span
      className={`flex flex-col leading-tight ${align === 'end' ? 'items-end' : 'items-center'}`}
    >
      <span className="flex items-baseline gap-1">
        <s className="text-[11px] text-slate-400 decoration-rose-400/70">
          <span className="sr-only">מחיר מקורי </span>
          {ils.format(it.original_line_total)}
        </s>
        <span className="font-bold text-violet-700">
          <span className="sr-only">במבצע </span>
          {ils.format(it.line_total)}
        </span>
      </span>
      {it.applied_promotion && (
        <PromotionBadge promo={it.applied_promotion} size="xs" showIcon={false} />
      )}
    </span>
  )
}

// ── Desktop matrix ─────────────────────────────────────────────────────────
// Sleek data-table cells: only subtle horizontal separators; the winner column
// is tinted green for grouping (no heavy grid lines).
// The store header is sticky VERTICALLY only, so `backdrop-blur` is safe here —
// it is the horizontally-pinned product column that Safari drops when a cell
// gets its own compositing layer. Keep the two apart.

// ── Column widths live here, once. ──
// Header and body cells MUST carry the same width AND the same horizontal
// padding, or the sticky header drifts out of line with the column scrolling
// under it. Bundling both in one constant is what stops them drifting apart.
const STORE_COL = 'w-[150px] min-w-[150px] px-3'
// 11rem, and it must STAY equal to `scroll-padding-inline-end` in index.css —
// that padding exists solely to clear this pinned column, so a mismatch lands
// every snap slightly off.
// Pinning the width is what fixed the "squished, cut-off" store cards: the store
// columns already carried min-w-[150px], but this column had no width at all,
// and auto table layout handed it whatever the longest product name asked for.
// All three of w/min-w/max-w are needed, and `max-w` is the one that carries the
// narrow end: without it the column took 371px of a 375px viewport.
const PRODUCT_COL = 'w-[11rem] min-w-[11rem] max-w-[11rem] px-3'

function thClass(isWinner, incomplete) {
  // `h-px` is load-bearing, not a typo. A table cell always stretches to the row
  // height, but a CHILD's `h-full` only resolves against a *definite* height —
  // with none, the child collapses to its own content and `mt-auto` has no slack
  // to push into, so every total sits at a different level. `h-px` supplies that
  // definite height (the cell still stretches to the row), which is what puts all
  // the prices on one baseline. Safari in particular needs it.
  const base =
    `snap-store-col sticky top-0 z-20 h-px ${STORE_COL} py-2.5 align-top ` +
    'border-b border-l border-gray-100 transition-colors duration-200'
  if (isWinner) return `${base} bg-emerald-50/90 backdrop-blur-md`
  if (incomplete) return `${base} bg-amber-50/70 backdrop-blur-md`
  return `${base} bg-white/80 backdrop-blur-md`
}

function tdClass(isWinner) {
  // Same STORE_COL as the header, so width and px-3 can never diverge. py-2
  // keeps rows tight so more products fit on a screen.
  const base =
    `snap-store-col ${STORE_COL} py-2 text-center tabular-nums ` +
    'border-b border-l border-gray-100 transition-all duration-200'
  return isWinner
    ? `${base} bg-emerald-50/50 font-bold text-emerald-800 group-hover:bg-emerald-100/60`
    : `${base} bg-white font-semibold text-slate-700 group-hover:bg-slate-50/70`
}

// Pinned-right product column.
// THE WEBKIT RTL FIX: solid opaque background + z-index, and NO transform on the
// sticky cell itself. A `transform`/`will-change` on a position:sticky element
// makes Safari drop it during scroll (the pinned column vanished and reappeared).
// The GPU layer is promoted once on the SCROLL CONTAINER instead (see below), so
// the whole scroll area repaints as one stable layer.
// The left edge gets a hairline border AND a soft shadow so the column reads as
// floating above the cards that scroll under it. A box-shadow is safe here where
// a blur is not: `box-shadow` paints into the existing layer, it does not promote
// a compositing layer the way backdrop-filter/transform/will-change do. That
// distinction is the whole reason this column survives a Safari scroll.
const STICKY =
  'sticky right-0 border-l border-slate-200 shadow-[-8px_0_12px_-8px_rgba(15,23,42,0.14)]'
// SOLID backgrounds, deliberately — and every hover state below is solid too.
// A translucent background on a pinned cell lets the scrolling store cards show
// straight through it. No backdrop-blur and no transform on these either: a
// compositing layer on a horizontally-pinned cell is exactly what made Safari
// drop this column mid-scroll.
// `align-bottom` puts the label just above its own column instead of floating in
// the middle of a header sized by the tallest store card, which read as a large
// empty box.
const stickyHead = `${STICKY} ${PRODUCT_COL} top-0 z-40 bg-slate-50 py-2.5 text-right align-bottom text-[11px] font-semibold uppercase tracking-widest text-slate-400 border-b border-gray-100`
// No per-row bottom border: the product column should read as ONE list, not a
// stack of disconnected cells. Separation comes from the row spacing itself —
// which is also why the tint runs unbroken from the header down.
const stickyBody = `${STICKY} ${PRODUCT_COL} z-30 bg-slate-50 py-2 text-right transition-colors duration-200 group-hover:bg-slate-100`

function ComparisonMatrix({ products, stores, winnerStoreId, qtyOf }) {
  const maps = Object.fromEntries(stores.map((s) => [s.store_id, itemsByProduct(s)]))

  return (
    // Single scroll context (w-full + overflow-x-auto). Table is min-w-max so it
    // grows past the viewport and scrolls natively in RTL.
    // Middle-click starts the browser's auto-scroll; on this RTL horizontal
    // scroller the GPU-layered sticky column then fails to repaint and part of
    // the table vanishes. Suppressing auto-scroll here (no links inside) removes
    // the trigger entirely.
    <div
      onMouseDown={(e) => {
        if (e.button === 1) e.preventDefault()
      }}
      // translateZ(0) promotes the scroll area to one stable GPU layer so the
      // pinned column repaints with it (instead of per-cell layers that Safari
      // dropped mid-scroll).
      style={{ transform: 'translateZ(0)' }}
      className="snap-stores scrollbar-hide w-full overflow-x-auto rounded-3xl border border-slate-200/70 bg-white shadow-sm ring-1 ring-slate-900/5"
    >
      <table className="w-full min-w-max border-separate border-spacing-0 text-sm">
        <thead>
          <tr>
            <th className={stickyHead}>מוצר</th>
            {stores.map((s) => {
              const isWinner = s.store_id === winnerStoreId
              return (
                <th key={s.store_id} className={thClass(isWinner, !s.is_complete)}>
                  {/* h-full resolves against the th's h-px (which the row then
                      stretches), giving `mt-auto` on the total real slack to
                      push into. */}
                  <div className="flex h-full flex-col items-center gap-1">
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
                    {s.address && <span className="text-[10px] text-slate-300">📍 {s.address}</span>}
                    {s.rank && <span className="text-[11px] text-slate-300">מקום {s.rank}</span>}
                    {s.pct_above_cheapest > 0 && (
                      <span className="text-[11px] font-semibold text-rose-400">
                        +{s.pct_above_cheapest}% מהזול
                      </span>
                    )}
                    {/* The running total lives HERE rather than in a footer: the
                        number the shopper is actually comparing has to stay on
                        screen while the products scroll past it.

                        `mt-auto` drops it to the bottom of the cell, so a store
                        whose address wrapped to two lines still shows its total
                        on the same horizontal line as every other store. */}
                    <span
                      className={`mt-auto pt-1.5 text-lg font-black tabular-nums tracking-tight ${
                        isWinner ? 'text-emerald-700' : 'text-slate-800'
                      }`}
                    >
                      {ils.format(s.total)}
                    </span>
                    {/* Rendered even at zero savings, as a fixed-height empty
                        slot. Bottom-aligning the total ALONE is not enough: a
                        column carrying a savings line underneath would push its
                        price up by exactly that line's height. Reserving the row
                        in every column is what makes the prices truly level. */}
                    <span className="h-[15px] whitespace-nowrap text-[11px] font-semibold leading-[15px] text-violet-600">
                      {s.total_savings > 0 ? `חסכת ${ils.format(s.total_savings)}` : ''}
                    </span>
                  </div>
                </th>
              )
            })}
          </tr>
        </thead>

        <tbody>
          {products.map((p) => (
            <tr key={p.id} className="group">
              <td className={stickyBody}>
                {/* Image + name share the sticky cell so the tile scrolls with
                    the product it belongs to. */}
                <div className="flex items-center gap-2.5">
                  <ProductImage
                    barcode={p.barcode}
                    name={p.name}
                    size="sm"
                    className="shadow-sm ring-1 ring-gray-100"
                  />
                  <span className="flex min-w-0 flex-col gap-1">
                    <span className="truncate text-[13px] font-semibold leading-tight text-slate-800">
                      {displayName(p)}
                    </span>
                    {/* The quantity sits on the SECOND line, not beside the
                        name. Measured: inline, the badge took ~48px of the
                        ~112px of text width this column has, which truncated
                        even a 15-character name. */}
                    <QtyBadge qty={qtyOf[p.id]} />
                  </span>
                </div>
              </td>
              {stores.map((s) => {
                const isWinner = s.store_id === winnerStoreId
                return (
                  <td key={s.store_id} className={tdClass(isWinner)}>
                    <LinePrice it={maps[s.store_id][p.id]} />
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Mobile: one branch at a time ───────────────────────────────────────────
// A phone gets a branch SWITCHER and a plain vertical list, not a scaled-down
// matrix. The matrix asks the shopper to hold a row and a column in their head
// while swiping a viewport narrower than two columns; the pinned product column
// then eats 176px of a 375px screen to repeat labels they just read. Here the
// branch is picked once and the list answers "what does MY basket cost here".
function MobileComparison({ products, stores, winnerStoreId, qtyOf }) {
  const [picked, setPicked] = useState(null)
  // Derived, never stored: a new comparison replaces `stores` wholesale, and a
  // selection kept in state would point at a branch that is no longer on screen.
  // Falling through picked → winner → first makes the reset automatic, with no
  // effect to keep in sync.
  const selected =
    stores.find((s) => s.store_id === picked) ||
    stores.find((s) => s.store_id === winnerStoreId) ||
    stores[0]
  const lines = itemsByProduct(selected)
  const isWinner = selected.store_id === winnerStoreId

  return (
    <div className="space-y-3">
      {/* Branch switcher. This scrolls sideways too, but it is a row of chips —
          nothing is pinned over it and no cell can be clipped mid-value. */}
      <div
        aria-label="בחירת סניף"
        className="snap-stores scrollbar-hide -mx-1 flex gap-2 overflow-x-auto px-1 py-1"
      >
        {stores.map((s) => {
          const active = s.store_id === selected.store_id
          return (
            <button
              key={s.store_id}
              type="button"
              aria-pressed={active}
              onClick={() => setPicked(s.store_id)}
              className={`snap-store-col flex shrink-0 flex-col items-start gap-0.5 rounded-2xl border px-3.5 py-2 text-right transition-colors ${
                active
                  ? 'border-emerald-500 bg-emerald-50 ring-1 ring-emerald-500/40'
                  : 'border-slate-200 bg-white'
              }`}
            >
              <span className="flex items-center gap-1 whitespace-nowrap text-[11px] font-semibold text-slate-500">
                {s.store_id === winnerStoreId && <span aria-hidden="true">🏆</span>}
                {!s.is_complete && <span aria-hidden="true">⚠</span>}
                {s.chain_name}
              </span>
              <span
                className={`whitespace-nowrap text-sm font-black tabular-nums ${
                  active ? 'text-emerald-700' : 'text-slate-800'
                }`}
              >
                {ils.format(s.total)}
              </span>
            </button>
          )
        })}
      </div>

      {/* The selected branch, in full */}
      <div
        className={`rounded-3xl border p-4 shadow-sm ${
          isWinner ? 'border-emerald-200 bg-emerald-50/60' : 'border-slate-200/70 bg-white'
        }`}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            {/* This card is the ONLY winner headline on a phone — the desktop
                banner is not rendered here, because at this width it and this
                card said the same thing twice, one under the other. */}
            {isWinner && (
              <p className="text-[11px] font-bold uppercase tracking-wider text-emerald-700">
                🏆 הסל הזול ביותר
              </p>
            )}
            <p className="truncate text-base font-black text-slate-900">{selected.chain_name}</p>
            <p className="truncate text-sm text-slate-500">{storeLabel(selected)}</p>
            {selected.address && (
              <p className="truncate text-xs text-slate-400">📍 {selected.address}</p>
            )}
          </div>
          <div className="shrink-0 text-left">
            <div
              className={`text-2xl font-black tabular-nums tracking-tight ${
                isWinner ? 'text-emerald-700' : 'text-slate-900'
              }`}
            >
              {ils.format(selected.total)}
            </div>
            {selected.total_savings > 0 && (
              <div className="text-[11px] font-semibold text-violet-600">
                חסכת {ils.format(selected.total_savings)}
              </div>
            )}
            {selected.pct_above_cheapest > 0 && (
              <div className="text-[11px] font-semibold text-rose-500">
                +{selected.pct_above_cheapest}% מהזול
              </div>
            )}
          </div>
        </div>
        {!selected.is_complete && (
          <p className="mt-3 rounded-xl bg-amber-100/70 px-3 py-2 text-xs font-semibold text-amber-800">
            ⚠{' '}
            {selected.missing_count === 1
              ? 'חסר כאן מוצר אחד'
              : `חסרים כאן ${selected.missing_count} מוצרים`}{' '}
            — הסכום חלקי
          </p>
        )}
      </div>

      {/* The basket, priced at the selected branch */}
      <ul className="divide-y divide-slate-100 overflow-hidden rounded-3xl border border-slate-200/70 bg-white shadow-sm">
        {products.map((p) => {
          const it = lines[p.id]
          const missing = !it || !it.found
          return (
            <li
              key={p.id}
              className={`flex items-center gap-3 px-3 py-2.5 ${missing ? 'bg-amber-50/40' : ''}`}
            >
              <ProductImage
                barcode={p.barcode}
                name={p.name}
                size="sm"
                className="shadow-sm ring-1 ring-gray-100"
              />
              <div className="flex min-w-0 flex-1 flex-col gap-1">
                <span className="truncate text-[13px] font-semibold leading-tight text-slate-800">
                  {displayName(p)}
                </span>
                <QtyBadge qty={qtyOf[p.id]} />
              </div>
              <div className="shrink-0 text-left text-sm font-bold tabular-nums text-slate-800">
                <LinePrice it={it} align="end" />
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export default function ComparisonTable({ result }) {
  const { products, stores, winner_store_id } = result
  // Tailwind's `md`. Below it the matrix is not rendered AT ALL, rather than
  // hidden with `md:hidden`: a capped comparison is 10 branches × 50 basket
  // lines, and shipping ~500 cells to a phone only to never paint them is part
  // of the cost this split exists to remove.
  const isDesktop = useMediaQuery('(min-width: 768px)')

  if (!stores.length) {
    return (
      <div className="animate-in mt-8 rounded-3xl border border-slate-200/70 bg-white p-10 text-center text-slate-500 shadow-sm ring-1 ring-slate-900/5">
        לא נמצאו סניפים בעיר זו שמחזיקים את מוצרי הסל. נסו עיר אחרת. 🏙️
      </div>
    )
  }

  const winner = stores.find((s) => s.store_id === winner_store_id) || null
  const incompleteStores = stores.filter((s) => !s.is_complete)
  const productName = (id) => products.find((p) => p.id === id)?.name || `#${id}`
  const qtyOf = qtyByProduct(stores)
  const Layout = isDesktop ? ComparisonMatrix : MobileComparison

  return (
    <div className="animate-in mt-8 space-y-5">
      {/* The winner banner is DESKTOP-ONLY. On a phone the selected-branch card
          is already the winner by default and carries the same chain, branch,
          address and total — the two stacked on top of each other read as the
          same card printed twice. The no-winner notice below has no such
          duplicate, so it shows at every width. */}
      {!winner ? (
        <div className="rounded-3xl border border-amber-200 bg-amber-50 p-4 text-amber-800 shadow-sm">
          ⚠ אף סניף בעיר אינו מחזיק את כל מוצרי הסל — מוצגות עלויות חלקיות בלבד.
        </div>
      ) : (
        isDesktop && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-3xl border border-emerald-100 bg-gradient-to-l from-emerald-50 to-teal-50/50 p-5 shadow-sm ring-1 ring-emerald-600/5">
          <div className="flex items-center gap-2.5">
            <span className="grid h-12 w-12 place-items-center rounded-2xl bg-white text-2xl shadow-sm ring-1 ring-emerald-600/10">
              🏆
            </span>
            <div>
              <p className="text-sm font-medium text-emerald-700">הסל הזול ביותר</p>
              <p className="text-lg font-black text-emerald-900">
                {winner.chain_name} · {storeLabel(winner)}
              </p>
              {winner.address && <p className="text-xs text-emerald-700/70">📍 {winner.address}</p>}
            </div>
          </div>
          <div className="text-left">
            <div className="text-3xl font-black text-emerald-700">{ils.format(winner.total)}</div>
            {winner.total_savings > 0 && (
              <div className="mt-0.5 text-xs font-semibold text-violet-600">
                כולל {ils.format(winner.total_savings)} הנחות מבצע
              </div>
            )}
          </div>
        </div>
        )
      )}

      <Layout
        products={products}
        stores={stores}
        winnerStoreId={winner_store_id}
        qtyOf={qtyOf}
      />

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
