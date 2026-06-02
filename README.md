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

מקור הנתונים: ה-Kaggle dataset ‏"israeli-supermarkets-data" (קבצי CSV/JSON בפורמט "מחירים שקופים")
שכבר חולץ מקומית לתיקיית `archive/`.
