# Zolt — השוואת סלי קניות בין רשתות הסופרמרקט בישראל

פלטפורמת RTL מודרנית להשוואת מחירי סל קניות בין כל הסניפים של שלוש רשתות:
**שופרסל**, **רמי לוי** ו**אושר עד**.

## ארכיטקטורה (3-tier monolith)

| שכבה      | טכנולוגיה                         |
|-----------|-----------------------------------|
| Backend   | Python REST API (FastAPI)         |
| Database  | MySQL 8.0                         |
| Frontend  | React + Vite + Tailwind CSS (RTL) |

## מבנה הפרויקט

```
Zolt/
├── docker-compose.yml      # MySQL 8.0
├── .env / .env.example     # סודות והגדרות (‎.env‎ לא נכנס ל-git)
├── db/
│   └── init/
│       └── 01_schema.sql   # stores · products · prices (נטען אוטומטית באתחול)
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py         # FastAPI app + /health
│       ├── config.py       # הגדרות (.env)
│       ├── db.py           # engine + connection pool (SQLAlchemy)
│       ├── models.py       # ORM models (stores/products/prices)
│       ├── schemas.py      # Pydantic requests/responses
│       ├── security.py     # bcrypt + JWT (admin auth)
│       ├── scheduler.py    # APScheduler — ETL שבועי (ראשון 03:00)
│       ├── routers/        # products, stores, basket, admin
│       └── services/       # search + comparison
├── etl/
│   ├── config.py           # נתיבים, רשתות, גדלי batch, עמודות forward-fill
│   ├── normalize.py        # ניקוי/נרמול שורות (ברקוד, מחיר, קוד חנות)
│   ├── loader.py           # batch upsert (INSERT ... ON DUPLICATE KEY UPDATE)
│   ├── run.py              # CLI: streaming + תזמור (תומך ‎--dry-run‎)
│   ├── refresh.py          # הורדה מ-Kaggle → ETL (ל-Scheduler)
│   └── downloaders/        # kaggle_download.py
├── secrets/                # kaggle.json (מוחרג מ-git)
└── archive/                # ה-Kaggle dataset המקומי (לא נכנס ל-git)
```

## בסיס הנתונים — סכימה

- **stores** — סניף פיזי. מפתח טבעי: `(chain_id, sub_chain_id, store_code)`, אינדקס על `city`.
- **products** — מוצר לפי ברקוד. `UNIQUE(barcode)` + `FULLTEXT(name)` לחיפוש ואוטו-קומפליט.
- **prices** — מחיר מוצר בסניף. **`UNIQUE(product_id, store_id)`** — היעד ל-Upsert של ה-ETL,
  עם מפתחות זרים ל-`products` ול-`stores`.

מזהי הרשתות (chain EAN): שופרסל `7290027600007` · רמי לוי `7290058140886` · אושר עד `7290103152017`.

## הרצה מקומית

> דרוש Docker Desktop.

```bash
cp .env.example .env      # התאמת סיסמאות לפי הצורך
docker compose up -d      # מרים MySQL ומריץ את db/init/01_schema.sql אוטומטית
```

### Backend (FastAPI)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload      # http://127.0.0.1:8000  ·  docs: /docs
```

נקודות קצה:
- `GET /products/search?q=...&limit=...` — חיפוש מוצרים / אוטו-קומפליט (FULLTEXT + fallback ל-LIKE).
- `GET /stores?city=...&chain=...` — רשימת סניפים מסוננת לפי עיר/רשת.
- `GET /stores/cities` — רשימת ערים (לתפריט הסינון בצד הלקוח).
- `POST /basket/compare` — השוואת סל: מקבל עיר + מערך `{product_id, quantity}`, מחזיר
  לכל סניף את `Σ(מחיר×כמות)`, ממוין עולה. סניפים שלמים מתחרים על המקום הראשון (`rank`),
  וסניפים עם פריט חסר מוצגים אך מסומנים (`is_complete=false`, `missing_product_ids`).
- `POST /admin/login` — התחברות אדמין (bcrypt) → מחזיר JWT בתוקף 24 שעות.
- `GET /admin/me`, `GET /admin/scheduler`, `POST /admin/etl/refresh` — מוגנים ב-JWT
  (`Authorization: Bearer <token>`); סטטוס המתזמן והפעלת ETL ידנית.
- `GET /health` — בדיקת תקינות כולל חיבור ל-DB.

### Admin auth + תזמון ETL אוטומטי

- **התחברות**: `POST /admin/login` עם `{username, password}` → JWT (HS256, 24h). שם המשתמש
  וה-hash (bcrypt) נטענים מ-`.env` (`ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`). יצירת hash:
  `python -m backend.app.security 'הסיסמה'`.
- **מתזמן**: ב-startup ה-app מפעיל `APScheduler` עם job שבועי — **כל יום ראשון 03:00** —
  שמוריד את הדאטהסט מ-Kaggle (`erlichsefi/israeli-supermarkets-data`), מחלץ ל-`archive/`,
  ומריץ את ה-ETL (שלב 3). מפתח ה-API ב-`secrets/kaggle.json` (מוחרג מ-git;
  `KAGGLE_CONFIG_DIR` מוגדר אוטומטית). אפשר להפעיל ידנית עם `POST /admin/etl/refresh`.

### ETL (טעינת הדאטה)

```bash
pip install -r etl/requirements.txt
python -m etl.run --dry-run        # פירסור ואימות בלבד (ללא DB)
python -m etl.run                  # טעינה בפועל ל-MySQL (snapshot)
python -m etl.run --full           # שימוש בקטלוג המחירים המלא
```

ה-ETL קורא את קבצי ה-CSV המקומיים (פורמט "מחירים שקופים"), עושה streaming ב-chunks
(חסכוני בזיכרון), עושה **forward-fill** לעמודות הזהות (chain/store) שמופיעות רק בראש כל בלוק,
מנרמל קודים (הסרת אפסים מובילים), ומבצע **batch upsert של 1,000** עם
`INSERT ... ON DUPLICATE KEY UPDATE`. החיבור מחיר→חנות נעשה לפי `(chain_id, store_code)`.

מקור הנתונים: ה-Kaggle dataset ‏"israeli-supermarkets-data" (קבצי CSV/JSON בפורמט "מחירים שקופים")
שכבר חולץ מקומית לתיקיית `archive/`.
