# 🚀 Deploying Zolt

A low-cost, modern split deployment:

| Layer | Platform | Why |
|-------|----------|-----|
| **Frontend** (React/Vite) | **Vercel** | Best-in-class static/SPA hosting, free |
| **Backend** (FastAPI) | **Render** | Free Python web service, Blueprint support |
| **Database** (MySQL 8) | **Railway** | Render has **no managed MySQL**; Railway does, and it supports `FULLTEXT` + `REGEXP_REPLACE` |

> Aiven also offers a free MySQL plan (note: it requires SSL). Everything below uses Railway for simplicity.

```
Vercel (frontend)  ──VITE_API_BASE_URL──▶  Render (FastAPI)  ──DATABASE_URL──▶  Railway (MySQL 8)
```

---

## ✅ Prerequisites
- The repo on GitHub (already done: `weaam-mishleb/Zolt`).
- Free accounts on **Vercel**, **Render**, **Railway**.
- Locally: the `archive/` dataset present (for seeding) and the project venv.

---

## 1 · Database — Railway (MySQL)
1. Railway → **New Project → Provision MySQL**.
2. Open the MySQL service → **Variables** and note the connection parts, or copy the ready-made **`MYSQL_URL`** (looks like `mysql://root:pass@host:port/railway`).
3. **Settings → Networking → enable the public TCP Proxy.** Use the **public** host/port in the URL (Render and your laptop connect over the internet, not Railway's private network).
4. Keep that URL handy — it's your `DATABASE_URL`.

## 2 · Schema + seed data (run locally, against the cloud DB)
```bash
source .venv/bin/activate
export DATABASE_URL='mysql://root:pass@PUBLIC_HOST:PORT/railway'

# a) create the tables on the cloud DB
python -m scripts.init_db

# b) load data (snapshot ≈ 400k rows — fast & fits free tiers)
python -m etl.run
#   …or the full catalog (~2.4M rows, larger DB, slower over the network):
# python -m etl.run --full
```
The ETL reads your local `archive/` files and streams them into the Railway DB.

## 3 · Backend — Render (FastAPI)
**Option A — Blueprint (recommended):** Render → **New → Blueprint** → pick the repo. It reads [`render.yaml`](render.yaml). Then fill the secrets it marks `sync: false`:
- `DATABASE_URL` — the Railway URL from step 1.
- `CORS_ORIGINS` — your Vercel URL (set after step 4, e.g. `https://zolt.vercel.app`).
- `ADMIN_PASSWORD_HASH` — generate it: `python -m backend.app.security 'your-admin-password'`.

**Option B — Manual web service:**
- Runtime **Python**, Build `pip install -r backend/requirements.txt`,
  Start `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`.
- Add the same env vars (also `PYTHON_VERSION=3.12.7`, `JWT_EXPIRE_HOURS=1`, `SCHEDULER_ENABLED=false`).

Deploy → your API is live at `https://zolt-api.onrender.com` (check `/docs` and `/health`).

## 4 · Frontend — Vercel (React/Vite)
1. Vercel → **Add New → Project** → import the repo.
2. **Root Directory: `frontend`** (Vercel auto-detects Vite; output `dist`).
3. **Environment Variable:** `VITE_API_BASE_URL = https://zolt-api.onrender.com`.
4. Deploy. [`frontend/vercel.json`](frontend/vercel.json) adds the SPA rewrite so `/admin` works.

## 5 · Connect CORS (the final wire-up)
1. Copy your live Vercel URL (e.g. `https://zolt.vercel.app`).
2. On Render, set `CORS_ORIGINS` to it and **Save** (Render redeploys).
3. Open the Vercel URL — search, compare, and `/admin` should all work end-to-end. 🎉

---

## 🤖 Seed the DB from the cloud — GitHub Actions (no Kaggle, no local DB upload)

If your local link is slow/flaky, let GitHub's fast US runners do the upload.
[`.github/workflows/etl.yml`](.github/workflows/etl.yml) pulls the price files from a
**GitHub Release of this repo** (no Kaggle key needed — uses the built-in token) and
streams the full ~2.4M prices into Railway.

**One-time: mirror the data on a Release** (you upload ~166 MB *gzipped*, once):
```bash
cd archive
gzip -kf price_full_file_shufersal.csv price_full_file_rami_levy.csv price_full_file_osher_ad.csv
```
Then GitHub → repo → **Releases → Draft a new release** → tag `dataset-v1` → drag the
three `archive/price_full_file_*.csv.gz` files as assets → **Publish**.
*(The tiny store CSVs are already committed in `db/seed_stores/`.)*

**Run it:**
1. Add **one** secret — *Settings → Secrets and variables → Actions*: `DATABASE_URL`
   (your Railway connection string).
2. Actions tab → **ETL → Cloud DB** → **Run workflow** (tag defaults to `dataset-v1`).
3. Watch the log; in a few minutes the cloud DB is fully loaded — your ISP never touched it. 🎉

---

## 📝 Notes & gotchas
- **Free Render web services sleep** after ~15 min idle (first request cold-starts in ~30–60s). Fine for a demo.
- **Scheduler is off in prod** (`SCHEDULER_ENABLED=false`) — sleeping instances + a 700 MB Kaggle download don't suit free tiers. Re-seed by re-running step 2 instead.
- **DB size:** the full catalog (~2.4M prices) can be ~0.5 GB with indexes. Start with the **snapshot** ETL; only go `--full` if your DB plan has the room.
- **FULLTEXT tokens:** locally we set `innodb_ft_min_token_size=2`; a managed MySQL may use the default `3`. Two-letter Hebrew searches then fall back to the built-in `LIKE` path, so search still works.
- **Secrets:** never commit real values — `DATABASE_URL`, `JWT_SECRET`, `ADMIN_PASSWORD_HASH` live only in the platform dashboards.

## 💸 Cost
Vercel (Hobby) + Render (Free web) + Railway (trial/usage) → **$0–5/mo** for a live demo.
