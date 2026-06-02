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
│       ├── schemas.py      # Pydantic responses
│       ├── routers/        # products, stores
│       └── services/       # search service
├── etl/
│   ├── config.py           # נתיבים, רשתות, גדלי batch, עמודות forward-fill
│   ├── normalize.py        # ניקוי/נרמול שורות (ברקוד, מחיר, קוד חנות)
│   ├── loader.py           # batch upsert (INSERT ... ON DUPLICATE KEY UPDATE)
│   └── run.py              # CLI: streaming + תזמור (תומך ‎--dry-run‎)
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
- `GET /health` — בדיקת תקינות כולל חיבור ל-DB.

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
