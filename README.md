# Navbatchi — Omborxona Navbatchilik Boti

Omborxona tozaligi va ozodaligi bo'yicha navbatchi xodimlarni boshqarish, foto-hisobot yig'ish, rahbar tasdiqlashi va oylik reyting chiqarish.

## Texnologiyalar

- Python 3.11+
- aiogram 3.x
- SQLite
- Railway deploy

## O'rnatish

```bash
git clone https://github.com/davlatbekkhasanov-spec/-navbatchi_control_bot.git
cd -navbatchi_control_bot
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env         # Sozlamalarni to'ldiring
python bot.py
```

## Sozlamalar (.env)

| O'zgaruvchi | Tavsif |
|-------------|--------|
| `BOT_TOKEN` | Telegram bot tokeni (@BotFather) |
| `ADMIN_IDS` | Admin Telegram ID lari (vergul bilan) |
| `GROUP_CHAT_ID` | Guruh chat ID (manfiy son) |
| `MORNING_HOUR` | Ertalabki xabar soati (default: 8) |
| `EVENING_HOUR` | Kechki xabar soati (default: 20) |

## Xodimlar va guruhlar

### 1-guruh (dushanba, chorshanba, juma)
Muslim, Abdullo, Farrux

### 2-guruh (seshanba, payshanba)
Sindor, Ziyod, Tolib

### 3-guruh (shanba, yakshanba)
Oxun, Ozod, Tulqin

**Qoida:** Hech bir xodim o'z dam olish kunida navbatchilikka tushmaydi.

## Bot buyruqlari

| Buyruq | Tavsif |
|--------|--------|
| `/start` | Botni ishga tushirish |
| `/today` | Bugungi navbatchilar (admin) |
| `/report_today` | Bugungi hisobot (admin) |
| `/rating` | Oylik reyting (admin) |
| `/employees` | Xodimlar ro'yxati (admin) |
| `/groups` | Navbatchilik guruhlari (admin) |
| `/help` | Yordam |

## Ball tizimi

| Harakat | Ball |
|---------|------|
| Vaqtida yuborish | +10 |
| OLDIN rasm | +10 |
| KEYIN rasm | +10 |
| Qabul qilindi | +20 |
| Qayta tozalash | -15 |
| Hisobot yo'q | -30 |

## Railway deploy

1. GitHub repoga push qiling
2. Railway da servis yarating va repoga ulang
3. **Variables** bo'limida `.env` qiymatlarini kiriting
4. Start command: `python bot.py`
5. Deploy tugmasini bosing

> **Eslatma:** SQLite fayl `data/navbatchi.db` da saqlanadi. Railway da persistent volume qo'shish tavsiya etiladi.

## Fayl strukturasi

```
navbatchi_control_bot/
├── bot.py           # Asosiy bot kodi
├── config.py        # Sozlamalar
├── database.py      # SQLite
├── keyboards.py     # Klaviaturalar
├── requirements.txt
├── Procfile
├── .env.example
└── data/            # Avtomatik yaratiladi
    └── navbatchi.db
```
