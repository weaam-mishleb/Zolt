import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import SearchBar from './components/SearchBar.jsx'
import BasketSidebar from './components/BasketSidebar.jsx'
import ComparisonTable from './components/ComparisonTable.jsx'
import { compareBasket, getCities } from './api'

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
  }, [basket, city])

  function addProduct(product) {
    setBasket((prev) => {
      const existing = prev.find((it) => it.product.id === product.id)
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

  const remove = (id) => setBasket((prev) => prev.filter((it) => it.product.id !== id))
  const clear = () => setBasket([])

  async function handleCompare() {
    if (!basket.length || !city) return
    setComparing(true)
    setCompareError('')
    setComparison(null)
    try {
      const items = basket.map((it) => ({ product_id: it.product.id, quantity: it.quantity }))
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
              חפשו מוצרים, הוסיפו לסל, והשוו מחירים בין שופרסל, רמי לוי ואושר עד.
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

          {!comparison && !comparing && !compareError && (
            <div className="mt-10 rounded-3xl border border-dashed border-slate-200 bg-white/60 p-12 text-center">
              {basket.length === 0 ? (
                <>
                  <div className="text-5xl">🛒</div>
                  <p className="mt-3 font-medium text-slate-500">
                    התחילו בהקלדת שם מוצר בתיבת החיפוש
                  </p>
                </>
              ) : (
                <>
                  <div className="text-5xl">⚖️</div>
                  <p className="mt-3 font-medium text-slate-500">
                    {city
                      ? 'לחצו "השוו מחירים" כדי לראות את הטבלה'
                      : 'בחרו עיר בסל ולחצו "השוו מחירים"'}
                  </p>
                </>
              )}
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
          onRemove={remove}
          onClear={clear}
          onCompare={handleCompare}
          comparing={comparing}
        />
      </main>

      <footer className="mx-auto w-full max-w-6xl px-4 py-6 text-center text-xs text-slate-400">
        Zolt · השוואת מחירים · <a href="/admin" className="transition hover:text-emerald-600">ניהול</a>
      </footer>
    </div>
  )
}
