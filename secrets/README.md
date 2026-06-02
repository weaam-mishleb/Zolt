# secrets/

קבצי סוד מקומיים — **לא נכנסים ל-git** (ראו `.gitignore`).

## `kaggle.json`

מַקְמוּ כאן את קובץ ה-Kaggle API token:

```
Zolt/secrets/kaggle.json
```

הקובץ נוצר ב-Kaggle תחת **Account → API → Create New Token**, ונראה כך:

```json
{ "username": "your_kaggle_user", "key": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" }
```

ה-backend מגדיר `KAGGLE_CONFIG_DIR` לתיקייה הזו, כך שחבילת `kaggle` תמצא את הקובץ
אוטומטית בזמן ההורדה המתוזמנת. מומלץ להריץ `chmod 600 secrets/kaggle.json`.
