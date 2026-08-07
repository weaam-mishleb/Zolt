export default function Header() {
  return (
    <header className="sticky top-0 z-30 border-b border-slate-200/70 bg-white/70 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-3">
        <a href="/" className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-600 text-xl font-black text-white shadow-lg shadow-emerald-600/25">
            Z
          </span>
          <div className="leading-tight">
            <h1 className="text-xl font-black tracking-tight text-slate-800">Zolt</h1>
            <p className="text-xs text-slate-400">השוואת סלי קניות חכמה</p>
          </div>
        </a>
        {/* The three chain pills are gone. They were hardcoded when the
            database held exactly those three; naming any subset now understates
            the coverage and goes stale the moment a chain is added. */}
        <nav className="hidden items-center gap-2 sm:flex">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-3 py-1.5 text-sm font-semibold text-emerald-700 ring-1 ring-emerald-600/10">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-500 opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-600" />
            </span>
            מחירים מכל הרשתות
          </span>
        </nav>
      </div>
    </header>
  )
}
