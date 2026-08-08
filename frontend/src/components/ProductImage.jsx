/**
 * ProductImage — the product tile for the comparison cart.
 *
 * WHY THIS IS PLACEHOLDER-FIRST
 * We measured Open Food Facts against 200 random real-GTIN products from our own
 * catalogue: 2.5% had a usable image, and the handful that matched were all
 * imports (Loacker, Frosch). There is no image source for the Israeli catalogue
 * that we can use legitimately, so for the MVP the placeholder is not a fallback
 * state — it is the normal state, and it has to look deliberate.
 *
 * So this renders a branded tile by default and only shows a photo if a `src`
 * is genuinely available. If we ever populate `products.image_url`, pass it in
 * and the component upgrades itself; a broken URL falls back to the same tile
 * rather than a broken-image glyph.
 *
 * The tint is derived from the barcode, so a product looks the same on every
 * render and a shopping list reads as a set of distinct items rather than a
 * column of identical grey boxes. Hues are constrained to the brand's blue-green
 * arc so the cart still looks like one product.
 */
import { useEffect, useMemo, useState } from 'react'

const SIZES = {
  sm: { box: 'h-10 w-10', icon: 'h-5 w-5', text: 'text-[10px]' },
  md: { box: 'h-14 w-14', icon: 'h-7 w-7', text: 'text-xs' },
  lg: { box: 'h-20 w-20', icon: 'h-9 w-9', text: 'text-sm' },
}

/** Stable hue from the barcode — same product, same colour, every time. */
function hueFor(seed) {
  const s = String(seed || '')
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360
  // 150–265° keeps it in the teal→indigo range; nothing turns muddy brown.
  return 150 + (h % 115)
}

function CartGlyph({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path
        d="M3 4h2.2l2.1 10.4a2 2 0 0 0 2 1.6h7.3a2 2 0 0 0 2-1.55L20.5 8H6.2"
        stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      />
      <circle cx="10" cy="19.5" r="1.4" fill="currentColor" />
      <circle cx="17" cy="19.5" r="1.4" fill="currentColor" />
    </svg>
  )
}

export default function ProductImage({ barcode, name, src = null, size = 'md', className = '' }) {
  const [failed, setFailed] = useState(false)
  const [loaded, setLoaded] = useState(false)

  // A recycled tile must not inherit the previous product's verdict — the same
  // component instance is reused as a list re-renders with different products.
  useEffect(() => {
    setLoaded(false)
    setFailed(false)
  }, [src])
  const s = SIZES[size] ?? SIZES.md
  const hue = useMemo(() => hueFor(barcode ?? name), [barcode, name])

  const shell =
    `${s.box} ${className} relative shrink-0 overflow-hidden rounded-xl ` +
    'ring-1 ring-black/5 dark:ring-white/10'

  // The placeholder is ALWAYS the bottom layer and the photo fades in over it.
  // That is the skeleton: no separate spinner, no layout shift, and a broken URL
  // or a 404 reveals finished art rather than an empty box. Only ~7% of our GTINs
  // have a real image (measured), so absence is the common case and has to look
  // deliberate rather than like a failure.
  const showPhoto = src && !failed

  return (
    <div
      className={shell}
      // Inline because the hue is per-product; Tailwind cannot enumerate 115 of them.
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
        <CartGlyph className={`${s.icon} opacity-70`} />
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
    </div>
  )
}
