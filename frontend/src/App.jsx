import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import SearchBar from './components/SearchBar.jsx'
import BasketSidebar from './components/BasketSidebar.jsx'
import ComparisonTable from './components/ComparisonTable.jsx'
import CartBreakdown from './components/promotions/CartBreakdown.jsx'
import { basketSummary, compareBasket, getCities } from './api'

const STORAGE_KEY = 'zolt.basket'

export default function App() {
  const [basket, setBasket] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? []
    } catch {
      return []
    }
  })
  const [cities, setCities] = useState([])
  const [city, setCity] = useState('')

  const [comparison, setComparison] = useState(null)
  const [comparing, setComparing] = useState(false)
  const [compareError, setCompareError] = useState('')

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(basket))
  }, [basket])

  useEffect(() => {
    getCities()
      .then(setCities)
      .catch(() => setCities([]))
  }, [])

  useEffect(() => {
    setComparison(null)
    setCompareError('')
    setSummary(null)
  }, [basket, city])

  // FR-3.6 — basket summary (estimated avg cost + per-chain coverage), fetched
  // together with the comparison and shown below the results table.
  // Best-effort: any failure simply hides the block.
  const [summary, setSummary] = useState(null)

  // The winning branch, or null when no branch carries the whole basket.
  // `?.` throughout: a comparison may arrive with no stores at all.
  const winnerStore =
    comparison?.stores?.find((s) => s.store_id === comparison.winner_store_id) ?? null

  function addProduct(product) {
    setBasket((prev) => {
      const existing = prev.find((it) => it.product.id === product.id)
      if (!existing && prev.length >= 50) {
        window.alert('הסל מוגבל ל-50 מוצרים שונים')
        return prev
      }
      if (existing) {
        return prev.map((it) =>
          it.product.id === product.id ? { ...it, quantity: it.quantity + 1 } : it,
        )
      }
      return [...prev, { product, quantity: 1 }]
    })
  }

  const inc = (id) =>
    setBasket((prev) =>
      prev.map((it) => (it.product.id === id ? { ...it, quantity: it.quantity + 1 } : it)),
    )

  const dec = (id) =>
    setBasket((prev) =>
      prev.flatMap((it) =>
        it.product.id === id
          ? it.quantity > 1
            ? [{ ...it, quantity: it.quantity - 1 }]
            : []
          : [it],
      ),
    )

  const setQty = (id, qty) =>
    setBasket((prev) =>
      prev.map((it) => (it.product.id === id ? { ...it, quantity: qty } : it)),
    )

  const remove = (id) => setBasket((prev) => prev.filter((it) => it.product.id !== id))
  const clear = () => {
    if (window.confirm('לרוקן את כל הסל?')) setBasket([])
  }

  async function handleCompare() {
    if (!basket.length || !city) return
    setComparing(true)
    setCompareError('')
    setComparison(null)
    setSummary(null)
    try {
      const items = basket.map((it) => ({ product_id: it.product.id, quantity: it.quantity }))
      basketSummary(items).then(setSummary).catch(() => setSummary(null))
      setComparison(await compareBasket(city, items))
    } catch {
      setCompareError('ההשוואה נכשלה — ודאו שהשרת פעיל ונסו שוב.')
    } finally {
      setComparing(false)
    }
  }

  return (
    <div className="flex min-h-full flex-col bg-gradient-to-b from-slate-50 via-white to-emerald-50/40">
      <Header />

      <main className="mx-auto grid w-full max-w-6xl flex-1 gap-6 px-4 py-8 lg:grid-cols-3">
        {/* min-w-0 lets the wide comparison table scroll inside this grid column
            instead of overflowing/clipping (grid items default to min-width:auto). */}
        <section className="min-w-0 lg:col-span-2">
          <div className="mb-6">
            <h2 className="mb-1 text-2xl font-black tracking-tight text-slate-800 sm:text-3xl">
              כמה עולה הסל שלכם?
            </h2>
            <p className="text-slate-500">
              השוו מחירים בזמן אמת מכל רשתות השיווק בישראל ומצאו את הסל המשתלם ביותר.
            </p>
          </div>

          <SearchBar onAdd={addProduct} />

          {comparing && (
            <div className="animate-in mt-8 rounded-3xl border border-slate-200 bg-white p-10 text-center text-slate-500 shadow-sm">
              משווים מחירים בין הסניפים… ⏳
            </div>
          )}

          {compareError && !comparing && (
            <div className="animate-in mt-8 rounded-3xl border border-rose-200 bg-rose-50 p-6 text-center text-rose-700 shadow-sm">
              {compareError}
            </div>
          )}

          {comparison && !comparing && <ComparisonTable result={comparison} />}

          {/* Itemised receipt for the winning branch — the table answers WHERE
              to shop, this answers WHY it is cheapest, line by line. Rendered
              only when a winner exists (no winner = no branch carried the whole
              basket, and a partial receipt would mislead). */}
          {comparison && !comparing && winnerStore && (
            <div className="mt-5">
              <CartBreakdown store={winnerStore} products={comparison.products} />
            </div>
          )}

          {/* FR-3.6 — basket summary below the results: estimated national
              average cost + how many basket items each chain carries */}
          {comparison && !comparing && summary?.estimated_total != null && (
            <div className="animate-in mt-5 rounded-3xl border border-slate-200/70 bg-white p-5 shadow-sm ring-1 ring-slate-900/5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="font-semibold text-slate-700">💡 עלות משוערת של הסל (ממוצע כלל הרשתות בארץ)</p>
                  <p className="mt-0.5 text-xs text-slate-400">
                    להשוואה מול המחיר שמצאנו לך בעיר — כך רואים כמה המנצח באמת משתלם
                  </p>
                </div>
                <div className="text-2xl font-black text-slate-800">₪{summary.estimated_total.toFixed(2)}</div>
              </div>
            </div>
          )}

          {!comparison && !comparing && !compareError && (
            <div className="relative mt-10 overflow-hidden rounded-3xl border border-slate-200/60 bg-white/70 p-12 text-center shadow-lg shadow-slate-900/5 backdrop-blur-xl">
              {/* A soft dot grid and a bloom behind the illustration. The dashed
                  border read as "form field with nothing in it"; this reads as a
                  deliberate resting state. */}
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 opacity-[0.55]"
                style={{
                  backgroundImage: 'radial-gradient(circle at 1px 1px, rgb(148 163 184 / 0.28) 1px, transparent 0)',
                  backgroundSize: '22px 22px',
                  maskImage: 'radial-gradient(ellipse 70% 60% at 50% 45%, #000 40%, transparent 100%)',
                  WebkitMaskImage: 'radial-gradient(ellipse 70% 60% at 50% 45%, #000 40%, transparent 100%)',
                }}
              />
              <div
                aria-hidden="true"
                className="pointer-events-none absolute left-1/2 top-8 h-40 w-40 -translate-x-1/2 rounded-full bg-emerald-300/25 blur-3xl"
              />
              <div className="relative">
                <EmptyStateArt stage={basket.length === 0 ? 'search' : city ? 'ready' : 'city'} />
                <h3 className="mt-6 text-lg font-black tracking-tight text-slate-800">
                  {basket.length === 0
                    ? 'הסל שלכם ריק'
                    : city
                      ? 'הסל מוכן להשוואה'
                      : 'כמעט שם'}
                </h3>
                <p className="mx-auto mt-1.5 max-w-sm text-sm leading-relaxed text-slate-500">
                  {basket.length === 0
                    ? 'התחילו בהקלדת שם מוצר בתיבת החיפוש, ואנחנו נמצא אותו בכל הרשתות.'
                    : city
                      ? 'לחצו "השוו מחירים" ונחשב את הסל בכל סניף בעיר שבחרתם.'
                      : 'בחרו עיר בסל כדי שנדע אילו סניפים להשוות עבורכם.'}
                </p>
              </div>
            </div>
          )}
        </section>

        <BasketSidebar
          items={basket}
          cities={cities}
          city={city}
          onCityChange={setCity}
          onInc={inc}
          onDec={dec}
          onSetQty={setQty}
          onRemove={remove}
          onClear={clear}
          onCompare={handleCompare}
          comparing={comparing}
        />
      </main>

      <footer className="mx-auto w-full max-w-6xl px-4 py-6 text-center text-xs text-slate-400">
        Zolt · השוואת מחירים
      </footer>
    </div>
  )
}

/**
 * EmptyStateArt — three states of the same scene, so the panel reads as
 * progress rather than as three unrelated placeholders.
 *
 * Inline SVG rather than an emoji: an emoji renders differently on every
 * platform and cannot be tinted to the brand. currentColor + the surrounding
 * text colour keeps it consistent.
 */
function EmptyStateArt({ stage }) {
  const tone =
    stage === 'ready' ? 'text-emerald-500' : stage === 'city' ? 'text-amber-500' : 'text-slate-400'
  return (
    <div className="relative mx-auto grid h-24 w-24 place-items-center">
      <div className="absolute inset-0 rounded-3xl bg-gradient-to-br from-white to-slate-50 shadow-sm ring-1 ring-slate-900/5" />
      <svg
        viewBox="0 0 48 48"
        fill="none"
        aria-hidden="true"
        className={`relative h-12 w-12 transition-colors duration-500 ${tone}`}
      >
        {/* the basket, in every state */}
        <path
          d="M7 15h34l-3.4 19a4 4 0 0 1-3.94 3.3H14.34A4 4 0 0 1 10.4 34L7 15Z"
          stroke="currentColor"
          strokeWidth="2.2"
          strokeLinejoin="round"
        />
        <path d="M17 15V11a7 7 0 0 1 14 0v4" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        {stage === 'search' && (
          <circle cx="24" cy="26" r="5" stroke="currentColor" strokeWidth="2.2" opacity=".45" />
        )}
        {stage === 'city' && (
          <path
            d="M24 21c-2.8 0-5 2.2-5 5 0 3.6 5 8 5 8s5-4.4 5-8c0-2.8-2.2-5-5-5Zm0 6.6a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2Z"
            fill="currentColor"
          />
        )}
        {stage === 'ready' && (
          <path
            d="m18 26.5 4.2 4.2L31 22"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
      </svg>
    </div>
  )
}
