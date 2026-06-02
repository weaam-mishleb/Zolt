import { useEffect, useState } from 'react'
import Header from './components/Header.jsx'
import SearchBar from './components/SearchBar.jsx'
import BasketSidebar from './components/BasketSidebar.jsx'
import { getCities } from './api'

const STORAGE_KEY = 'zolt.basket'

export default function App() {
  // basket: [{ product, quantity }]
  const [basket, setBasket] = useState(() => {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) ?? []
    } catch {
      return []
    }
  })
  const [cities, setCities] = useState([])
  const [city, setCity] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(basket))
  }, [basket])

  useEffect(() => {
    getCities()
      .then(setCities)
      .catch(() => setCities([]))
  }, [])

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
            : [] // drop when it would reach zero
          : [it],
      ),
    )

  const remove = (id) => setBasket((prev) => prev.filter((it) => it.product.id !== id))
  const clear = () => setBasket([])

  function handleCompare() {
    // The actual POST /basket/compare call + results table arrive in Step 7.
    setNotice('מנוע ההשוואה יחובר בשלב הבא 🔧')
    setTimeout(() => setNotice(''), 3000)
  }

  return (
    <div className="min-h-full bg-slate-50">
      <Header />

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-8 lg:grid-cols-3">
        <section className="lg:col-span-2">
          <div className="mb-6">
            <h2 className="mb-1 text-2xl font-black text-slate-800 sm:text-3xl">
              כמה עולה הסל שלכם?
            </h2>
            <p className="text-slate-500">
              חפשו מוצרים, הוסיפו לסל, והשוו מחירים בין שופרסל, רמי לוי ואושר עד.
            </p>
          </div>

          <SearchBar onAdd={addProduct} />

          {basket.length === 0 ? (
            <div className="mt-10 rounded-2xl border border-dashed border-slate-200 bg-white/60 p-10 text-center">
              <div className="text-4xl">🛒</div>
              <p className="mt-3 font-medium text-slate-500">
                התחילו בהקלדת שם מוצר בתיבת החיפוש
              </p>
            </div>
          ) : (
            <div className="mt-8 rounded-2xl border border-slate-200 bg-white p-10 text-center text-slate-400">
              אזור תוצאות ההשוואה — ייבנה בשלב 7
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
          comparing={false}
          notice={notice}
        />
      </main>
    </div>
  )
}
