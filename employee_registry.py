"""Jamoa Telegram ID — boshqa nazorat botlari bilan bir xil ro'yxat."""

from __future__ import annotations

TUVALOV_FARRUX_TG_ID = 7703650930
CANONICAL_TUVALOV = "Tuvalov Farrux"
DAVLATBEK_ADMIN_ID = 1432810519
DEFAULT_GROUP_ID = -1001877019294

BUILTIN_ADMIN_IDS: frozenset[int] = frozenset({DAVLATBEK_ADMIN_ID})

TG_EMPLOYEE: dict[int, str] = {
    7844168817: "Ergashev Ozodbek",
    5412958249: "Ravshanov Oxunjon",
    8547365654: "Ruziboev Sindor",
    6931958983: "Mustafoev Abdullo",
    6991673998: "Sagdullaev Yunus",
    5465963344: "Shernazarov Tolib",
    6001619806: "Samadov Tulqin",
    5732350707: "Toxirov Muslimbek",
    8440127425: "Ravshanov Ziyodullo",
    TUVALOV_FARRUX_TG_ID: CANONICAL_TUVALOV,
}

# Navbatchi bazasidagi qisqa ismlar → kanonik
SHORT_NAME_TO_TG: dict[str, int] = {
    "ozod": 7844168817,
    "oxun": 5412958249,
    "sindor": 8547365654,
    "abdullo": 6931958983,
    "tulqin": 6001619806,
    "tolib": 5465963344,
    "muslim": 5732350707,
    "ziyod": 8440127425,
    "farrux": TUVALOV_FARRUX_TG_ID,
}


def builtin_team_ids() -> frozenset[int]:
    return frozenset(TG_EMPLOYEE.keys()) | BUILTIN_ADMIN_IDS


def is_team_member(telegram_id: int | None, *, extra_admin_ids: frozenset[int] | set[int] | None = None) -> bool:
    if not telegram_id:
        return False
    uid = int(telegram_id)
    if uid in builtin_team_ids():
        return True
    if extra_admin_ids and uid in extra_admin_ids:
        return True
    return False
