/**
 * A product tile: a real photo when we have one, otherwise a designed placeholder.
 *
 * WHY THIS IS PLACEHOLDER-FIRST
 * Open Food Facts has a usable image for 7.0% of our real GTINs (re-measured with
 * scripts/off_coverage.py — it was 2.5% two months earlier, so the commons does
 * grow). ~93% of tiles are therefore placeholders under any sourcing strategy short
 * of a paid catalogue, which is the whole design brief: absence is the NORMAL case
 * and has to read as a deliberate system, while a photo must arrive without a
 * layout shift when it exists.
 *
 * Two signals do the work:
 *   HUE comes from the MANUFACTURER, so every Osem product sits in the same colour
 *     family and a brand becomes recognisable down a long list. Falls back to the
 *     barcode when the brand is unknown — stable either way, so a product never
 *     changes colour between renders.
 *   GLYPH comes from the CATEGORY, inferred from the name and unit of measure. A
 *     bottle, a carton, produce and a loaf are distinguishable at 40px in a way a
 *     Hebrew initial is not.
 */
import { useEffect, useMemo, useState } from 'react'

// ── PHOTOS ARE OFF ──────────────────────────────────────────────────────────
// Turned off on the product call: the Open Food Facts packshots are inconsistent
// in crop, background and aspect (measured 400×235 beside 144×400 in one list),
// and a grid of them looked worse than the glyph tiles do.
//
// A one-line switch rather than ripped-out plumbing, because nothing behind it is
// wasted: /products/images still resolves and caches, the contribution endpoint
// still works, and products.image_url keeps filling in. When the presentation
// problem is solved — a normalising proxy, fixed aspect boxes, or a licensed
// catalogue — flip this and every call site already passes `src`.
//
// The camera affordance follows the same switch: its entire payoff is a visible
// photo, so offering it while photos are hidden would promise something the
// shopper never sees.
const SHOW_PHOTOS = false

const SIZES = {
  sm: { box: 'h-10 w-10', icon: 'h-5 w-5', text: 'text-[10px]' },
  md: { box: 'h-14 w-14', icon: 'h-7 w-7', text: 'text-xs' },
  lg: { box: 'h-20 w-20', icon: 'h-9 w-9', text: 'text-sm' },
}

/** Stable hue from a seed — same brand (or product), same colour, every time. */
function hueFor(seed) {
  const s = String(seed || '')
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  // 150–265° keeps it in the teal→indigo range; nothing turns muddy brown.
  return 150 + (h % 115)
}

// Hebrew keyword → category. ORDER MATTERS: the first match wins, so families that
// would be swallowed by a broader rule come first — חלב has to be tested before the
// litre check, or every milk carton renders as a soft-drink bottle.
const CATEGORIES = [
  ['dairy', /חלב|גבינ|יוגורט|שמנת|קוטג|לבן|חמאה|מעדן/],
  ['produce', /עגבני|מלפפון|תפוח|בננה|גזר|בצל|פלפל|חסה|אבוקדו|תפוז|לימון|אבטיח|מלון|תות|ענב|תמר|בטטה|קישוא|חציל/],
  ['bakery', /לחם|פיתה|לחמני|בגט|חלה|מאפה|טוסט|בורקס|רוגלך/],
  ['meat', /בשר|עוף|הודו|דג |סלמון|אנטרקוט|שניצל|קבב|נקניק|טונה|פילה|כרעי|שוק /],
  ['drink', /שתי|מים|קולה|סודה|מיץ|בירה|יין|משקה|תה |קפה|אנרגיה/],
]

function categoryFor(name, unitOfMeasure, isWeighted) {
  const text = `${name || ''} ${unitOfMeasure || ''}`
  for (const [category, pattern] of CATEGORIES) {
    if (pattern.test(text)) return category
  }
  // Sold by weight with no keyword hit is overwhelmingly the produce counter.
  if (isWeighted) return 'produce'
  // A volume unit with no keyword hit is a bottle of something.
  if (/ליטר|מ"ל|מ'ל|מל\b|ml|liter|litre/i.test(text)) return 'drink'
  return 'packet'
}

/** One flat 24×24 line glyph per category, drawn in currentColor. */
function CategoryGlyph({ category, className }) {
  const st = {
    stroke: 'currentColor',
    strokeWidth: 1.9,
    strokeLinejoin: 'round',
    strokeLinecap: 'round',
    fill: 'none',
  }
  const paths = {
    // a gable-top carton — reads as milk rather than as a plain box
    dairy: <path d="M7 10.5 12 6l5 4.5V19a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-8.5ZM7 10.5h10M12 6V3.5" {...st} />,
    produce: (
      <>
        <path d="M12 8.5c-3 0-5 2.2-5 5.4C7 17.5 9.6 21 12 21s5-3.5 5-7.1c0-3.2-2-5.4-5-5.4Z" {...st} />
        <path d="M12 8.5V6m0 0c1.6-.4 2.8-1.4 3.2-2.6-1.7-.2-2.9.6-3.2 2.6Z" {...st} />
      </>
    ),
    bakery: (
      <>
        <path d="M4.5 13.5c0-3.3 3.4-6 7.5-6s7.5 2.7 7.5 6v4a1 1 0 0 1-1 1h-13a1 1 0 0 1-1-1v-4Z" {...st} />
        <path d="M9 9.8 7.7 12M13 9.6 11.7 12M17 10.2 15.7 12.4" {...st} />
      </>
    ),
    meat: (
      <>
        <path d="M6.5 14.8c0-4 2.7-7.3 6.3-7.3 3 0 5 2 5 4.8 0 1.7-.9 3-2.3 3.7-1.2.6-1.7 1.3-1.9 2.4-.2 1.2-1.1 1.9-2.5 1.9-2.7 0-4.6-2.2-4.6-5.5Z" {...st} />
        <circle cx="11" cy="13" r="1.9" {...st} />
      </>
    ),
    drink: <path d="M10.2 3.5h3.6V6c0 .9.4 1.3 1 1.9.7.7 1.2 1.5 1.2 2.7V19a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-8.4c0-1.2.5-2 1.2-2.7.6-.6 1-1 1-1.9V3.5Z" {...st} />,
    packet: (
      <>
        <path d="M7.5 7.5h9V19a1 1 0 0 1-1 1H8.5a1 1 0 0 1-1-1V7.5Z" {...st} />
        <path d="M7.5 7.5 9 4.5h6l1.5 3M11 11.5h2" {...st} />
      </>
    ),
  }
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      {paths[category] ?? paths.packet}
    </svg>
  )
}

export default function ProductImage({
  barcode,
  name,
  manufacturer = null,
  unitOfMeasure = null,
  isWeighted = false,
  src = null,
  size = 'md',
  className = '',
  children = null,
}) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // A recycled tile must not inherit the previous product's verdict — the same
  // component instance is reused as a list re-renders with different products.
  useEffect(() => {
    setLoaded(false)
    setFailed(false)
  }, [src])

  const s = SIZES[size] ?? SIZES.md
  // Brand first: it groups a shelf visually. Barcode only when the brand is blank.
  const hue = useMemo(() => hueFor(manufacturer || barcode || name), [manufacturer, barcode, name])
  const category = useMemo(
    () => categoryFor(name, unitOfMeasure, isWeighted),
    [name, unitOfMeasure, isWeighted],
  )

  const shell =
    `${s.box} ${className} relative shrink-0 overflow-hidden rounded-xl ` +
    'ring-1 ring-black/5 dark:ring-white/10'

  // The placeholder is ALWAYS the bottom layer and the photo fades in over it.
  // That is the skeleton: no separate spinner, no layout shift, and a broken URL
  // or a 404 reveals finished art rather than an empty box.
  const showPhoto = SHOW_PHOTOS && src && !failed

  return (
    <div
      className={shell}
      // Inline because the hue is per-brand; Tailwind cannot enumerate 115 of them.
      style={{
        background: `linear-gradient(140deg,
          hsl(${hue} 62% 96%) 0%,
          hsl(${hue} 55% 90%) 55%,
          hsl(${hue + 12} 48% 86%) 100%)`,
      }}
      // Decorative: the product name is already displayed next to this tile, so
      // announcing it again would just make a screen reader repeat itself.
      role="presentation"
    >
      <div
        className="flex h-full w-full items-center justify-center"
        style={{ color: `hsl(${hue} 42% 42%)` }}
      >
        <CategoryGlyph category={category} className={`${s.icon} opacity-75`} />
      </div>
      {/* A soft top highlight stops the tile reading as a flat empty box. */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/45 to-transparent" />
      {showPhoto && (
        <img
          src={src}
          alt={name || ''}
          loading="lazy"
          decoding="async"
          // BOTH are needed. An image served from the browser cache can finish
          // before React attaches the synthetic onLoad, so that event never fires
          // and the photo sits fully decoded at opacity 0 behind the placeholder —
          // measured, two of seven tiles. The ref runs after the element exists
          // and catches exactly that case.
          ref={(el) => {
            if (el && el.complete && el.naturalWidth > 0) setLoaded(true)
          }}
          onLoad={() => setLoaded(true)}
          // A dead URL must not leave a half-painted image on top of the tile:
          // `failed` unmounts this entirely and the placeholder below is already
          // in place, so there is nothing to fall back TO — it is just revealed.
          onError={() => setFailed(true)}
          className={`absolute inset-0 h-full w-full bg-white object-contain transition-opacity duration-300 ${
            loaded ? 'opacity-100' : 'opacity-0'
          }`}
        />
      )}
      {/* Overlay slot for the "contribute a photo" affordance. Rendered only while
          there is no photo, so it can never cover a real packshot — and not at all
          while SHOW_PHOTOS is off, since a contributed photo would stay invisible. */}
      {SHOW_PHOTOS && !showPhoto && children}
    </div>
  )
}
