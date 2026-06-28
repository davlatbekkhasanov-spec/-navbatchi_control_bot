"""SQLite ma'lumotlar bazasi — jadvallar va so'rovlar."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

import config
from employee_registry import SHORT_NAME_TO_TG

# Boshlang'ich xodimlar va guruhlar
SEED_EMPLOYEES = [
    ("Sindor", 2, 2),      # chorshanba, 2-guruh
    ("Muslim", 6, 1),      # yakshanba, 1-guruh
    ("Ziyod", 4, 2),       # juma, 2-guruh
    ("Abdullo", 3, 1),     # payshanba, 1-guruh
    ("Oxun", 0, 3),        # dushanba, 3-guruh
    ("Ozod", 1, 3),        # seshanba, 3-guruh
    ("Tulqin", 1, 3),      # seshanba, 3-guruh
    ("Tolib", 6, 2),       # yakshanba, 2-guruh
    ("Farrux", 5, 1),      # shanba, 1-guruh
    ("Admin sinov", 6, 1), # admin sinov profili
]

SEED_GROUPS = [
    (1, "1-guruh", [0, 2, 4]),   # dushanba, chorshanba, juma
    (2, "2-guruh", [1, 3]),      # seshanba, payshanba
    (3, "3-guruh", [5, 6]),      # shanba, yakshanba
]


def _ensure_data_dir() -> None:
    """data/ papkasini yaratish."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection():
    """SQLite ulanish kontekst menejeri."""
    _ensure_data_dir()
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _sync_employee_telegram_ids(cur) -> None:
    """Qisqa ismlarni jamoa Telegram ID bilan bog'lash."""
    for row in cur.execute("SELECT id, full_name, telegram_user_id FROM employees").fetchall():
        key = (row["full_name"] or "").strip().lower()
        tg_id = SHORT_NAME_TO_TG.get(key)
        if not tg_id:
            continue
        if row["telegram_user_id"] != tg_id:
            cur.execute(
                "UPDATE employees SET telegram_user_id = ? WHERE id = ?",
                (tg_id, row["id"]),
            )


def init_db() -> None:
    """Jadvallarni yaratish va boshlang'ich ma'lumotlarni kiritish."""
    _ensure_data_dir()
    with get_connection() as conn:
        cur = conn.cursor()
        cur.executescript("""
            CREATE TABLE IF NOT EXISTS duty_groups (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                duty_days TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL UNIQUE,
                rest_day INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                telegram_user_id INTEGER UNIQUE,
                is_active INTEGER DEFAULT 1,
                FOREIGN KEY (group_id) REFERENCES duty_groups(id)
            );

            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT DEFAULT 'started',
                start_time TEXT,
                submit_time TEXT,
                before_count INTEGER DEFAULT 0,
                after_count INTEGER DEFAULT 0,
                score INTEGER DEFAULT 0,
                admin_comment TEXT,
                FOREIGN KEY (employee_id) REFERENCES employees(id),
                FOREIGN KEY (group_id) REFERENCES duty_groups(id),
                UNIQUE(employee_id, date)
            );

            CREATE TABLE IF NOT EXISTS report_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER NOT NULL,
                photo_file_id TEXT NOT NULL,
                photo_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (report_id) REFERENCES reports(id)
            );

            CREATE TABLE IF NOT EXISTS scheduler_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_type TEXT NOT NULL,
                run_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(task_type, run_date)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

        # Guruhlarni seed qilish
        for gid, name, days in SEED_GROUPS:
            cur.execute(
                "INSERT OR IGNORE INTO duty_groups (id, name, duty_days) VALUES (?, ?, ?)",
                (gid, name, json.dumps(days)),
            )

        # Xodimlarni seed qilish
        for name, rest_day, group_id in SEED_EMPLOYEES:
            cur.execute(
                """INSERT OR IGNORE INTO employees (full_name, rest_day, group_id)
                   VALUES (?, ?, ?)""",
                (name, rest_day, group_id),
            )
        _sync_employee_telegram_ids(cur)


# ─── Guruh va xodim so'rovlari ───────────────────────────────────────────────

def get_group_by_weekday(weekday: int) -> dict | None:
    """Berilgan hafta kuni uchun navbatchilik guruhini qaytarish."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM duty_groups").fetchall()
        for row in rows:
            days = json.loads(row["duty_days"])
            if weekday in days:
                return dict(row)
    return None


def get_employees_by_group(group_id: int, exclude_rest_day: int | None = None) -> list[dict]:
    """Guruh xodimlarini qaytarish (dam olish kunidagi xodimlarni chiqarib)."""
    with get_connection() as conn:
        if exclude_rest_day is not None:
            rows = conn.execute(
                """SELECT * FROM employees
                   WHERE group_id = ? AND is_active = 1 AND rest_day != ?
                   ORDER BY full_name""",
                (group_id, exclude_rest_day),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM employees
                   WHERE group_id = ? AND is_active = 1
                   ORDER BY full_name""",
                (group_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_today_duty_employees(target_date: date | None = None) -> tuple[dict | None, list[dict]]:
    """Bugungi navbatchi guruh va xodimlarini qaytarish."""
    if target_date is None:
        target_date = date.today()
    weekday = target_date.weekday()
    group = get_group_by_weekday(weekday)
    if not group:
        return None, []
    employees = get_employees_by_group(group["id"], exclude_rest_day=weekday)
    return group, employees


ADMIN_DEMO_NAME = "Admin sinov"


def get_group_by_id(group_id: int) -> dict | None:
    """Guruh ID bo'yicha."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM duty_groups WHERE id = ?", (group_id,)).fetchone()
        return dict(row) if row else None


def ensure_admin_demo_employee(telegram_id: int) -> dict:
    """Admin uchun sinov profilini bog'lash (haqiqiy xodimlarga tegmaydi)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE full_name = ? AND is_active = 1",
            (ADMIN_DEMO_NAME,),
        ).fetchone()
        if not row:
            conn.execute(
                "INSERT INTO employees (full_name, rest_day, group_id) VALUES (?, ?, ?)",
                (ADMIN_DEMO_NAME, 6, 1),
            )
            row = conn.execute(
                "SELECT * FROM employees WHERE full_name = ?",
                (ADMIN_DEMO_NAME,),
            ).fetchone()
    demo = dict(row)
    existing = get_employee_by_telegram_id(telegram_id)
    if existing:
        return existing
    link_employee_telegram(demo["id"], telegram_id)
    return get_employee_by_id(demo["id"]) or demo


def get_employee_by_telegram_id(telegram_id: int) -> dict | None:
    """Telegram ID bo'yicha xodimni topish."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE telegram_user_id = ? AND is_active = 1",
            (telegram_id,),
        ).fetchone()
        return dict(row) if row else None


def get_employee_by_id(employee_id: int) -> dict | None:
    """ID bo'yicha xodim."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        return dict(row) if row else None


def link_employee_telegram(employee_id: int, telegram_id: int) -> None:
    """Xodimni Telegram akkauntiga bog'lash."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE employees SET telegram_user_id = ? WHERE id = ?",
            (telegram_id, employee_id),
        )


def get_all_employees() -> list[dict]:
    """Barcha faol xodimlar."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT e.*, g.name as group_name
               FROM employees e
               JOIN duty_groups g ON e.group_id = g.id
               WHERE e.is_active = 1
               ORDER BY e.group_id, e.full_name"""
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_groups() -> list[dict]:
    """Barcha navbatchilik guruhlari."""
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM duty_groups ORDER BY id").fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["duty_days_list"] = json.loads(d["duty_days"])
            result.append(d)
        return result


# ─── Hisobot so'rovlari ──────────────────────────────────────────────────────

def get_report(employee_id: int, report_date: str) -> dict | None:
    """Kunlik hisobotni olish."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE employee_id = ? AND date = ?",
            (employee_id, report_date),
        ).fetchone()
        return dict(row) if row else None


def create_report(employee_id: int, group_id: int, report_date: str) -> dict:
    """Yangi hisobot yaratish."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO reports (employee_id, group_id, date, status, start_time)
               VALUES (?, ?, ?, 'started', ?)""",
            (employee_id, group_id, report_date, now),
        )
        row = conn.execute(
            "SELECT * FROM reports WHERE employee_id = ? AND date = ?",
            (employee_id, report_date),
        ).fetchone()
        return dict(row)


def update_report(report_id: int, **fields: Any) -> None:
    """Hisobot maydonlarini yangilash."""
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    vals = list(fields.values()) + [report_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE reports SET {cols} WHERE id = ?", vals)


def get_report_by_id(report_id: int) -> dict | None:
    """Hisobot ID bo'yicha."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None


def try_submit_report(report_id: int, *, score: int, submit_time: str) -> dict | None:
    """Ish jarayonidagi hisobotni avtomatik qabul qilish."""
    final_score = score + config.SCORE_ACCEPTED
    with get_connection() as conn:
        conn.execute(
            """UPDATE reports
               SET status = 'accepted', score = ?, submit_time = ?
               WHERE id = ? AND status = 'started'""",
            (final_score, submit_time, report_id),
        )
        if conn.total_changes == 0:
            return None
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None


def add_photo(report_id: int, file_id: str, photo_type: str) -> None:
    """Hisobotga rasm qo'shish."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO report_photos (report_id, photo_file_id, photo_type, created_at)
               VALUES (?, ?, ?, ?)""",
            (report_id, file_id, photo_type, now),
        )
        col = "before_count" if photo_type == "before" else "after_count"
        conn.execute(
            f"UPDATE reports SET {col} = {col} + 1 WHERE id = ?",
            (report_id,),
        )


def get_report_photos(report_id: int) -> list[dict]:
    """Hisobot rasmlarini olish."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM report_photos WHERE report_id = ? ORDER BY id",
            (report_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_today_reports(report_date: str) -> list[dict]:
    """Kunlik barcha hisobotlar (xodim nomi bilan)."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT r.*, e.full_name
               FROM reports r
               JOIN employees e ON r.employee_id = e.id
               WHERE r.date = ?
               ORDER BY e.full_name""",
            (report_date,),
        ).fetchall()
        return [dict(r) for r in rows]


def create_no_report_penalty(employee_id: int, group_id: int, report_date: str) -> None:
    """Hisobot yubormagan xodimga jarima yozish."""
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM reports WHERE employee_id = ? AND date = ?",
            (employee_id, report_date),
        ).fetchone()
        if existing:
            return
        conn.execute(
            """INSERT INTO reports (employee_id, group_id, date, status, score)
               VALUES (?, ?, ?, 'no_report', ?)""",
            (employee_id, group_id, report_date, config.SCORE_NO_REPORT),
        )


# ─── Reyting ─────────────────────────────────────────────────────────────────

def get_monthly_rating(year: int, month: int) -> list[dict]:
    """Oylik reyting — xodimlar bo'yicha jami ball."""
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT e.full_name, e.group_id, g.name as group_name,
                      COALESCE(SUM(r.score), 0) as total_score,
                      COUNT(r.id) as report_count
               FROM employees e
               JOIN duty_groups g ON e.group_id = g.id
               LEFT JOIN reports r ON r.employee_id = e.id
                   AND strftime('%Y', r.date) = ?
                   AND strftime('%m', r.date) = ?
               WHERE e.is_active = 1
               GROUP BY e.id
               ORDER BY total_score DESC, e.full_name""",
            (str(year), f"{month:02d}"),
        ).fetchall()
        return [dict(r) for r in rows]


# ─── Scheduler log ───────────────────────────────────────────────────────────

def was_task_run(task_type: str, run_date: str) -> bool:
    """Bugun bu vazifa bajarilganmi?"""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM scheduler_log WHERE task_type = ? AND run_date = ?",
            (task_type, run_date),
        ).fetchone()
        return row is not None


def mark_task_run(task_type: str, run_date: str) -> None:
    """Vazifa bajarilganini belgilash."""
    now = datetime.now().isoformat()
    with get_connection() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO scheduler_log (task_type, run_date, created_at)
               VALUES (?, ?, ?)""",
            (task_type, run_date, now),
        )


# ─── Sozlamalar ──────────────────────────────────────────────────────────────

def get_setting(key: str) -> str | None:
    """Sozlama qiymatini olish."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    """Sozlama saqlash."""
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


def get_group_chat_id() -> int | None:
    """Guruh chat ID — avval .env, keyin bazadan."""
    if config.GROUP_CHAT_ID:
        return config.GROUP_CHAT_ID
    val = get_setting("group_chat_id")
    try:
        return int(val) if val else None
    except (TypeError, ValueError):
        return None
