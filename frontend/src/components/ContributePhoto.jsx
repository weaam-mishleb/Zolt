import { useRef, useState } from 'react'

import { contributeProductPhoto } from '../api'

/**
 * A camera affordance on a tile that has no photo.
 *
 * This is the inverse of harvesting a retailer's packshots. Open Food Facts
 * coverage of our catalogue went 2.5% → 7.0% in two months on volunteer photos, and
 * a picture contributed here is licensed for us permanently — where a scraped image
 * decays the moment the source changes a URL or blocks us.
 *
 * The upload is PUBLIC, into an open database under OFF's licence. A shopper has to
 * be told that BEFORE the camera opens, not in a footer afterwards, so the first tap
 * explains and the second one is the consent.
 */
export default function ContributePhoto({ productId, productName, onDone }) {
  const inputRef = useRef(null)
  const [stage, setStage] = useState('idle') // idle | asking | sending | ok | error
  const [reason, setReason] = useState(null)

  async function send(file) {
    if (!file) return
    setStage('sending')
    const result = await contributeProductPhoto(productId, file).catch(() => ({ ok: false }))
    if (result?.ok) {
      setStage('ok')
      onDone?.(result.image_url || null)
    } else {
      setStage('error')
      setReason(result?.reason || 'upload_failed')
    }
  }

  if (stage === 'ok') {
    return (
      <span className="absolute inset-0 grid place-items-center bg-emerald-600/90 text-[10px] font-bold text-white">
        תודה!
      </span>
    )
  }

  return (
    <>
      <button
        type="button"
        // The tile is inside a <button> in the search list, so a nested button
        // would be invalid HTML and the click would bubble into "add to basket".
        // This is rendered only where the tile is NOT itself interactive; the
        // stopPropagation is the second line of defence.
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          if (stage === 'asking') inputRef.current?.click()
          else setStage('asking')
        }}
        title={
          stage === 'asking'
            ? 'התמונה תועלה למסד הפתוח Open Food Facts — הקישו שוב לאישור'
            : `הוסיפו תמונה ל${productName || 'מוצר'}`
        }
        aria-label={`הוסיפו תמונה למוצר ${productName || ''}`}
        className={`absolute bottom-0 left-0 grid h-4 w-4 place-items-center rounded-tr-md text-[9px] leading-none transition ${
          stage === 'asking'
            ? 'w-full rounded-none bg-amber-500 text-[8px] font-bold text-white'
            : 'bg-white/80 text-slate-500 opacity-0 group-hover:opacity-100 focus-visible:opacity-100'
        } ${stage === 'sending' ? 'animate-pulse' : ''}`}
      >
        {stage === 'sending' ? '…' : stage === 'asking' ? 'לאישור' : stage === 'error' ? '!' : '📷'}
      </button>
      {stage === 'error' && (
        <span className="sr-only">
          {reason === 'not_configured'
            ? 'תרומת תמונות אינה מוגדרת בשרת'
            : reason === 'not_a_gtin'
              ? 'למוצר הזה אין ברקוד תקני'
              : 'ההעלאה נכשלה'}
        </span>
      )}
      <input
        ref={inputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp"
        // `capture` opens the rear camera on a phone, which is where a shopper
        // actually is when a product has no photo.
        capture="environment"
        className="hidden"
        onChange={(e) => send(e.target.files?.[0])}
      />
    </>
  )
}
