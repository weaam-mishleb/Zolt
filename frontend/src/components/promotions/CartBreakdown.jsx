import PromotionBadge, { promoLabel } from './PromotionBadge.jsx'

/**
 * "Why is this cart cheaper?" — the transparency panel.
 *
 * With promotions the total stops being a number the user can verify by adding
 * up the lines, so the UI has to show its work: original price struck through,
 * the discounted price beside it, and which promotion caused it. A cheaper
 * number nobody understands is a number nobody trusts.
 *
 * Expected shape (from POST /basket/compare):
 *   store: {
 *     chain_name, store_name, address, total, base_total, total_savings,
 *     items: [{ product_id, name, quantity, unit_price, line_total,
 *               original_line_total, applied_promotion, found }],
 *     applied_promotions: [{ id, reward_kind, description, savings, ... }]
 *   }
 */

const ils = new Intl.NumberFormat('he-IL', {
  style: 'currency',
  currency: 'ILS',
  maximumFractionDigits: 2,
})

/** Original price struck through + the price actually paid. */
export function PriceWithDiscount({ original, final, className = '' }) {
  const discounted = original != null && final != null && original > final
  if (!discounted) {
    return <span className={`tabular-nums ${className}`}>{ils.format(final ?? 0)}</span>
  }
  return (
    <span className={`inline-flex items-baseline gap-1.5 ${className}`}>
      {/* <s> carries the "no longer valid" meaning for assistive tech, and the
          sr-only labels stop a screen reader announcing two bare numbers. */}
      <s className="text-xs tabular-nums text-slate-400 decoration-rose-400/70">
        <span className="sr-only">מחיר מקורי </span>
        {ils.format(original)}
      </s>
      <span className="font-bold tabular-nums text-violet-700">
        <span className="sr-only">מחיר לאחר הנחה </span>
        {ils.format(final)}
      </span>
    </span>
  )
}

/** One basket line: name, qty, promo badge, and the price pair.
 *
 * `name` is resolved by the caller: the comparison API returns product ids on
 * the line and the names once, in `result.products`.
 */
export function CartLineRow({ item }) {
  if (!item?.found) {
    return (
      <li className="flex items-center justify-between gap-3 py-2.5">
        <span className="min-w-0 truncate text-slate-400 line-through">{item?.name}</span>
        <span className="shrink-0 rounded-md bg-amber-100/80 px-2 py-0.5 text-xs font-semibold text-amber-700">
          חסר בסניף
        </span>
      </li>
    )
  }

  return (
    <li className="flex items-start justify-between gap-3 py-2.5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="truncate font-medium text-slate-700">{item.name}</span>
          {item.quantity > 1 && (
            <span className="shrink-0 text-xs text-slate-400">× {item.quantity}</span>
          )}
          {item.applied_promotion && (
            <PromotionBadge promo={item.applied_promotion} size="xs" />
          )}
        </div>
        {item.applied_promotion?.description && (
          <p className="mt-0.5 truncate text-[11px] text-slate-400">
            {item.applied_promotion.description}
          </p>
        )}
      </div>
      <PriceWithDiscount
        original={item.original_line_total}
        final={item.line_total}
        className="shrink-0 text-sm"
      />
    </li>
  )
}

export default function CartBreakdown({ store, products = [] }) {
  if (!store) return null

  // Every field below tolerates absence: until promotions are loaded in an
  // environment the API returns plain base prices, and this panel must still
  // render a correct (just discount-free) receipt.
  const base = store.base_total ?? store.total
  const savings = store.total_savings ?? Math.max(0, base - store.total)
  const promos = store.applied_promotions ?? []
  const hasSavings = savings > 0.005

  const nameOf = (productId) =>
    products.find((p) => p.id === productId)?.name || `#${productId}`

  return (
    <section
      className="animate-in rounded-3xl border border-slate-200/70 bg-white p-5 shadow-sm ring-1 ring-slate-900/5"
      aria-label="פירוט הסל"
    >
      {/* Header — store identity + the headline number */}
      <header className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 pb-4">
        <div className="min-w-0">
          <h3 className="truncate text-lg font-black text-slate-800">
            {store.chain_name} · {store.store_name}
          </h3>
          {store.address && <p className="mt-0.5 text-xs text-slate-400">📍 {store.address}</p>}
        </div>
        <div className="text-left">
          <div className="text-2xl font-black text-emerald-700">{ils.format(store.total)}</div>
          {hasSavings && (
            <div className="mt-0.5 text-xs font-semibold text-violet-600">
              חסכת {ils.format(savings)}
            </div>
          )}
        </div>
      </header>

      {/* Per-line detail — this is the "show your work" part */}
      <ul className="divide-y divide-slate-100">
        {(store.items ?? []).map((item) => (
          <CartLineRow
            key={item.product_id}
            item={{ ...item, name: item.name ?? nameOf(item.product_id) }}
          />
        ))}
      </ul>

      {/* Applied promotions — named, with the saving each one produced */}
      {promos.length > 0 && (
        <div className="mt-3 rounded-2xl bg-violet-50/60 p-3 ring-1 ring-violet-100">
          <p className="mb-2 text-xs font-semibold text-violet-800">
            {promos.length} מבצעים הוחלו על הסל
          </p>
          <ul className="space-y-1.5">
            {promos.map((p) => (
              <li key={p.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="flex min-w-0 items-center gap-2">
                  <PromotionBadge promo={p} size="xs" />
                  <span className="truncate text-slate-500">{p.description}</span>
                </span>
                {p.savings > 0 && (
                  <span className="shrink-0 font-semibold tabular-nums text-violet-700">
                    −{ils.format(p.savings)}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Totals — base → discounts → paid. The arithmetic must be visible. */}
      <dl className="mt-4 space-y-1.5 border-t border-slate-100 pt-3 text-sm">
        <div className="flex justify-between text-slate-500">
          <dt>מחיר לפני מבצעים</dt>
          <dd className="tabular-nums">{ils.format(base)}</dd>
        </div>
        {hasSavings && (
          <div className="flex justify-between font-medium text-violet-700">
            <dt>סך ההנחות</dt>
            <dd className="tabular-nums">−{ils.format(savings)}</dd>
          </div>
        )}
        <div className="flex justify-between border-t border-slate-100 pt-1.5 text-base font-black text-slate-800">
          <dt>סה״כ לתשלום</dt>
          <dd className="tabular-nums text-emerald-700">{ils.format(store.total)}</dd>
        </div>
      </dl>

      {/* Honest caveat — promotions have terms we cannot fully model.
          Shown only when a promotion actually applied, so a plain receipt is
          not cluttered with a disclaimer about something that did not happen. */}
      {promos.length > 0 && (
        <p className="mt-3 text-[11px] leading-relaxed text-slate-400">
          המחירים והמבצעים מבוססים על הקבצים שהרשתות מפרסמות. ייתכנו מבצעי מועדון או תנאים
          נוספים שאינם משתקפים כאן — כדאי לאמת בקופה.
        </p>
      )}
    </section>
  )
}
