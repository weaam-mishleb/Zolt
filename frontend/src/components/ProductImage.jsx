/**
 * ProductImage — a designed tile for every product, on every surface.
 *
 * THERE IS NO PHOTO PATH. That is the product decision, and it is the reason this
 * file is small: external sourcing was tried and removed. Open Food Facts had a
 * usable image for 7.0% of our real GTINs (measured, `scripts/off_coverage.py`),
 * and patchy coverage reads as broken — a Google fallback returned inconsistent
 * crops, and licensed catalogues are the only route to uniformity. So the tile is
 * generated, always, and the app makes one promise it can keep everywhere.
 *
 * Everything here is a pure function of the product. No network, no state, no
 * loading, nothing to fail — the same product renders identically in the search
 * dropdown, the cart and the comparison table, on first paint, forever.
 *
 * Two signals carry the identity:
 *   HUE from the MANUFACTURER, so a brand becomes recognisable down a long list —
 *     every Osem product shares a colour family. Falls back to the barcode, then
 *     the name, so a tile always has a stable seed and never changes between
 *     renders.
 *   GLYPH from an inferred CATEGORY. At 40px a carton, a bottle, an apple and a
 *     loaf are distinguishable in a way a Hebrew initial is not, which is why the
 *     mark is a shape rather than a letter.
 */
import { useMemo } from 'react'

const SIZES = {
  sm: { box: 'h-10 w-10', icon: 'h-5 w-5' },
  md: { box: 'h-14 w-14', icon: 'h-7 w-7' },
  lg: { box: 'h-20 w-20', icon: 'h-9 w-9' },
}

/**
 * FNV-1a — a deterministic 32-bit hash. Same seed, same tile, on every surface and
 * every device, forever.
 *
 * The previous `h * 31 + charCode` version clustered badly here: Hebrew codepoints
 * all sit in a narrow high range, so neighbouring product names produced adjacent
 * results and, once squeezed into a 115° arc, two shades of the same teal. FNV-1a
 * avalanches — one different character moves the whole value.
 */
function hash(seed) {
  let h = 0x811c9dc5
  const s = String(seed || '')
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return h >>> 0
}

// Six fixed stops instead of a continuous hue. A continuous range makes ADJACENT
// products land on near-identical tiles; discrete anchors guarantee that two rows
// in a list are either the same colour or clearly a different one, never almost.
// All six stay in the brand's cool arc — no muddy browns, no alarm reds in a
// grocery list — so the set still reads as one system.
const PALETTE = [158, 187, 212, 244, 268, 322]

// Hebrew keyword → category. ORDER MATTERS: first match wins, so families that a
// broader rule would swallow come first. חלב must be tested before the litre rule
// or every milk carton renders as a soft-drink bottle.
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

/** One flat 24×24 line glyph per category, stroked in currentColor. */
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
  size = 'md',
  className = '',
}) {
  const s = SIZES[size] ?? SIZES.md
  // Brand first: it groups a shelf visually. Barcode, then name, only as fallbacks.
  const hue = useMemo(
    () => PALETTE[hash(manufacturer || barcode || name) % PALETTE.length],
    [manufacturer, barcode, name],
  )
  const category = useMemo(
    () => categoryFor(name, unitOfMeasure, isWeighted),
    [name, unitOfMeasure, isWeighted],
  )

  return (
    <div
      className={
        `${s.box} ${className} relative shrink-0 overflow-hidden rounded-xl ` +
        'ring-1 ring-black/5 dark:ring-white/10'
      }
      // Inline because the hue is data-derived; Tailwind cannot enumerate a palette
      // it never sees at build time.
      style={{
        background: `linear-gradient(140deg,
          hsl(${hue} 62% 96%) 0%,
          hsl(${hue} 55% 90%) 55%,
          hsl(${hue + 12} 48% 86%) 100%)`,
      }}
      // Decorative: the product name is already displayed beside this tile, so
      // announcing it again would just make a screen reader repeat itself.
      role="presentation"
    >
      <div
        className="flex h-full w-full items-center justify-center"
        style={{ color: `hsl(${hue} 42% 42%)` }}
      >
        <CategoryGlyph category={category} className={`${s.icon} opacity-75`} />
      </div>
      {/* A soft top highlight gives the tile a light source, so it reads as a
          designed surface rather than a flat empty box. */}
      <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-white/45 to-transparent" />
    </div>
  )
}
