/**
 * Visual language for promotions.
 *
 * Each reward_kind gets its OWN colour + icon so a user can scan a long cart and
 * recognise deal types without reading: "1+1" should never look like "30% off".
 * The palette deliberately avoids emerald (reserved for "this store wins") and
 * amber (reserved for "item missing") so promotions never compete with the two
 * signals the comparison table already owns.
 */

const KINDS = {
  NTH_FREE: {
    icon: '🎁',
    label: 'מבצע כמותי',
    className: 'bg-violet-50 text-violet-700 ring-violet-200',
  },
  BUNDLE_PRICE: {
    icon: '📦',
    label: 'מחיר מארז',
    className: 'bg-sky-50 text-sky-700 ring-sky-200',
  },
  PCT_OFF: {
    icon: '🏷️',
    label: 'הנחה באחוזים',
    className: 'bg-fuchsia-50 text-fuchsia-700 ring-fuchsia-200',
  },
  AMOUNT_OFF: {
    icon: '💸',
    label: 'הנחה בסכום',
    className: 'bg-teal-50 text-teal-700 ring-teal-200',
  },
  FIXED_PRICE: {
    icon: '🎯',
    label: 'מחיר מבצע',
    className: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  },
  UNKNOWN: {
    icon: '✨',
    label: 'מבצע',
    className: 'bg-slate-100 text-slate-600 ring-slate-200',
  },
}

/** Short human text for a promo — falls back to the feed description. */
export function promoLabel(promo) {
  if (!promo) return ''
  switch (promo.reward_kind) {
    case 'NTH_FREE':
      return promo.min_qty >= 2 ? `${promo.min_qty - 1}+1` : 'מתנה'
    case 'BUNDLE_PRICE':
      return `${promo.min_qty} ב-₪${Number(promo.discounted_price).toFixed(2)}`
    case 'PCT_OFF':
      return `${Math.round(Number(promo.discount_rate) * 100)}% הנחה`
    case 'AMOUNT_OFF':
      return promo.min_basket_amount
        ? `₪${promo.discount_amount} הנחה מעל ₪${promo.min_basket_amount}`
        : `₪${promo.discount_amount} הנחה`
    case 'FIXED_PRICE':
      return `ב-₪${Number(promo.discounted_price).toFixed(2)}`
    default:
      return promo.description || 'מבצע'
  }
}

export default function PromotionBadge({ promo, size = 'sm', showIcon = true }) {
  if (!promo) return null
  const kind = KINDS[promo.reward_kind] || KINDS.UNKNOWN
  const sizing = size === 'xs' ? 'px-1.5 py-0.5 text-[10px]' : 'px-2 py-0.5 text-xs'

  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full font-semibold ring-1 ${kind.className} ${sizing}`}
      /* The description is the raw feed text — surface it on hover as the
         source of truth, but never rely on it for the price itself. */
      title={promo.description || kind.label}
    >
      {showIcon && <span aria-hidden="true">{kind.icon}</span>}
      <span>{promoLabel(promo)}</span>
      <span className="sr-only">({kind.label})</span>
    </span>
  )
}

export { KINDS as PROMOTION_KINDS }
