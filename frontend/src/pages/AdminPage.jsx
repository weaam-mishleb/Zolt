import { useEffect, useState } from 'react'
import { adminLogin, getEtlStatus, getSchedulerStatus, runEtl } from '../api'

const TOKEN_KEY = 'zolt.admin.token'

export default function AdminPage() {
  const [token, setToken] = useState(() => sessionStorage.getItem(TOKEN_KEY) || '')

  if (!token) {
    return <LoginCard onToken={(t) => { sessionStorage.setItem(TOKEN_KEY, t); setToken(t) }} />
  }
  return <Dashboard token={token} onLogout={() => { sessionStorage.removeItem(TOKEN_KEY); setToken('') }} />
}

function Shell({ children }) {
  return (
    <div className="min-h-full bg-gradient-to-b from-slate-50 via-white to-emerald-50/40">
      <header className="border-b border-slate-200/70 bg-white/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <a href="/" className="flex items-center gap-3">
            <span className="grid h-9 w-9 place-items-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-600 text-lg font-black text-white shadow-lg shadow-emerald-600/25">
              Z
            </span>
            <div className="leading-tight">
              <h1 className="font-black text-slate-800">Zolt · ניהול</h1>
              <p className="text-xs text-slate-400">לוח בקרה</p>
            </div>
          </a>
          <a href="/" className="text-sm text-slate-500 transition hover:text-emerald-600">→ חזרה לאתר</a>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-10">{children}</main>
    </div>
  )
}

function LoginCard({ onToken }) {
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { access_token } = await adminLogin(username, password)
      onToken(access_token)
    } catch (err) {
      setError(err.status === 401 ? 'שם משתמש או סיסמה שגויים' : 'התחברות נכשלה — האם השרת פעיל?')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Shell>
      <div className="animate-in mx-auto max-w-sm rounded-3xl border border-slate-200/70 bg-white p-8 shadow-xl shadow-slate-900/5">
        <div className="mb-6 text-center">
          <div className="text-3xl">🔒</div>
          <h2 className="mt-2 text-xl font-black text-slate-800">כניסת מנהל</h2>
          <p className="text-sm text-slate-400">נדרשת הזדהות כדי להמשיך</p>
        </div>
        <form onSubmit={submit} className="space-y-3">
          <input
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="שם משתמש"
            className="w-full rounded-2xl border border-slate-200 px-4 py-2.5 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
          />
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="סיסמה"
            className="w-full rounded-2xl border border-slate-200 px-4 py-2.5 outline-none transition focus:border-emerald-400 focus:ring-4 focus:ring-emerald-100"
          />
          {error && <p className="rounded-xl bg-rose-50 p-2 text-center text-sm text-rose-600">{error}</p>}
          <button
            disabled={busy || !password}
            className="w-full rounded-2xl bg-gradient-to-l from-emerald-600 to-teal-500 py-3 font-bold text-white shadow-lg shadow-emerald-600/25 transition hover:-translate-y-0.5 disabled:translate-y-0 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300 disabled:shadow-none"
          >
            {busy ? 'מתחבר…' : 'התחבר'}
          </button>
        </form>
      </div>
    </Shell>
  )
}

function Dashboard({ token, onLogout }) {
  const [sched, setSched] = useState(null)
  const [etl, setEtl] = useState(null)
  const [full, setFull] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  const loadStatus = () => {
    getSchedulerStatus(token).then(setSched).catch(() => {})
    getEtlStatus(token).then(setEtl).catch((e) => e.status === 401 && onLogout())
  }

  useEffect(loadStatus, []) // eslint-disable-line react-hooks/exhaustive-deps

  // poll while an ETL run is in progress
  useEffect(() => {
    if (!etl?.running) return
    const id = setInterval(() => getEtlStatus(token).then(setEtl).catch(() => {}), 3000)
    return () => clearInterval(id)
  }, [etl?.running, token])

  async function handleRun() {
    setMsg('')
    setErr('')
    try {
      const r = await runEtl(token, full)
      setMsg(r.message || 'ה-ETL הופעל ברקע')
      setTimeout(loadStatus, 800)
    } catch (e) {
      setErr(e.status === 409 ? 'ETL כבר רץ כעת' : 'ההפעלה נכשלה')
    }
  }

  const running = etl?.running

  return (
    <Shell>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-2xl font-black text-slate-800">לוח בקרה</h2>
        <button onClick={onLogout} className="text-sm text-slate-500 transition hover:text-rose-500">
          התנתק
        </button>
      </div>

      <div className="grid gap-5 sm:grid-cols-2">
        {/* Manual ETL */}
        <div className="animate-in rounded-3xl border border-slate-200/70 bg-white p-6 shadow-xl shadow-slate-900/5">
          <h3 className="flex items-center gap-2 text-lg font-bold text-slate-800">⚙️ הרצת ETL ידנית</h3>
          <p className="mt-1 text-sm text-slate-400">
            טוען מחדש את הנתונים מהקבצים המקומיים אל בסיס הנתונים.
          </p>

          <label className="mt-4 flex items-center gap-2 text-sm text-slate-600">
            <input
              type="checkbox"
              checked={full}
              onChange={(e) => setFull(e.target.checked)}
              className="h-4 w-4 rounded accent-emerald-600"
            />
            קטלוג מלא (איטי יותר, נתונים מלאים)
          </label>

          <button
            onClick={handleRun}
            disabled={running}
            className="mt-4 w-full rounded-2xl bg-gradient-to-l from-emerald-600 to-teal-500 py-3 font-bold text-white shadow-lg shadow-emerald-600/25 transition hover:-translate-y-0.5 disabled:translate-y-0 disabled:cursor-not-allowed disabled:from-slate-300 disabled:to-slate-300 disabled:shadow-none"
          >
            {running ? 'ה-ETL רץ…' : 'הרץ ETL עכשיו'}
          </button>

          {msg && <p className="mt-3 rounded-xl bg-emerald-50 p-2 text-center text-sm text-emerald-700">{msg}</p>}
          {err && <p className="mt-3 rounded-xl bg-rose-50 p-2 text-center text-sm text-rose-600">{err}</p>}

          {etl && (etl.started_at || etl.running) && (
            <div className="mt-4 rounded-2xl bg-slate-50 p-3 text-xs text-slate-500">
              <div>
                סטטוס:{' '}
                <span className={running ? 'font-semibold text-amber-600' : 'font-semibold text-emerald-600'}>
                  {running ? 'רץ כעת' : etl.ok === false ? 'נכשל' : 'הסתיים'}
                </span>
              </div>
              {etl.error && <div className="mt-1 text-rose-500">שגיאה: {etl.error}</div>}
            </div>
          )}
        </div>

        {/* Scheduler status */}
        <div className="animate-in rounded-3xl border border-slate-200/70 bg-white p-6 shadow-xl shadow-slate-900/5">
          <h3 className="flex items-center gap-2 text-lg font-bold text-slate-800">⏰ מתזמן אוטומטי</h3>
          <p className="mt-1 text-sm text-slate-400">הורדה מ-Kaggle והרצת ETL מתוזמנת.</p>
          {sched ? (
            <dl className="mt-4 space-y-2 text-sm">
              <Row label="פעיל" value={sched.running ? 'כן ✅' : 'לא'} />
              <Row label="תזמון" value={sched.schedule} />
              <Row label="דאטהסט" value={sched.dataset} />
              <Row
                label="הרצה הבאה"
                value={sched.jobs?.[0]?.next_run_time?.replace('T', ' ').slice(0, 16) || '—'}
              />
            </dl>
          ) : (
            <p className="mt-4 text-sm text-slate-400">טוען…</p>
          )}
        </div>
      </div>
    </Shell>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex items-center justify-between gap-2 border-b border-slate-100 pb-1.5">
      <dt className="text-slate-400">{label}</dt>
      <dd className="font-medium text-slate-700">{value}</dd>
    </div>
  )
}
