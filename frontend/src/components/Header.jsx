const CHAINS = ['שופרסל', 'רמי לוי', 'אושר עד']

export default function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-emerald-600 text-xl font-black text-white shadow-sm">
            Z
          </span>
          <div className="leading-tight">
            <h1 className="text-xl font-black tracking-tight text-slate-800">Zolt</h1>
            <p className="text-xs text-slate-400">השוואת סלי קניות</p>
          </div>
        </div>
        <nav className="hidden items-center gap-2 sm:flex">
          {CHAINS.map((c) => (
            <span
              key={c}
              className="rounded-full bg-slate-100 px-3 py-1 text-sm font-medium text-slate-500"
            >
              {c}
            </span>
          ))}
        </nav>
      </div>
    </header>
  )
}
