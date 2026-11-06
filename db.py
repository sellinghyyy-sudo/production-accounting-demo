"""Слой данных: SQLite, первичная загрузка из Excel, сохранение правок.

После первичной загрузки Excel-файлы системе больше не нужны:
все данные лежат в файле data/app.db.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("APP_DB_PATH", BASE_DIR / "data" / "app.db"))
IMPORT_DIR = BASE_DIR / "import"

# Описание участков. kind: text / date / num.
# Ключ секции = имя таблицы. Участки не знают друг о друге на уровне хранения.
SECTIONS: dict[str, dict] = {
    "blanks": {
        "title": "Заготовка",
        "prefix": "P",
        "file_hint": "01_Заготовка.xlsx",
        "columns": [
            ("record_id", "Запись", "text"),
            ("date", "Дата", "date"),
            ("batch_id", "Партия", "text"),
            ("employee", "Сотрудник", "text"),
            ("plan_qty", "План, шт", "num"),
            ("fact_qty", "Факт, шт", "num"),
        ],
    },
    "weld": {
        "title": "Потребность сварки",
        "prefix": "W",
        "file_hint": "02_Потребность_сварки.xlsx",
        "columns": [
            ("record_id", "Запись", "text"),
            ("date", "Дата", "date"),
            ("batch_id", "Партия", "text"),
            ("need_qty", "Потребность, шт", "num"),
        ],
    },
    "docs": {
        "title": "Документация",
        "prefix": "D",
        "file_hint": "03_Документация.xlsx",
        "columns": [
            ("record_id", "Запись", "text"),
            ("date", "Дата", "date"),
            ("employee", "Сотрудник", "text"),
            ("plan_docs", "План, док.", "num"),
            ("fact_docs", "Факт, док.", "num"),
        ],
    },
}

_SQL_TYPES = {"text": "TEXT", "date": "TEXT", "num": "REAL"}


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
        con.execute(
            "CREATE TABLE IF NOT EXISTS change_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, section TEXT, rid INTEGER,"
            " record_id TEXT, field TEXT, old TEXT, new TEXT, author TEXT)"
        )
        for key, cfg in SECTIONS.items():
            cols = ", ".join(f"{c} {_SQL_TYPES[k]}" for c, _, k in cfg["columns"])
            # rid - внутренний номер строки. Нужен, потому что record_id в данных
            # может повторяться (дубликат), а повтор нельзя молча потерять.
            con.execute(
                f"CREATE TABLE IF NOT EXISTS {key} ("
                f"rid INTEGER PRIMARY KEY AUTOINCREMENT, {cols}, edited INTEGER DEFAULT 0)"
            )


def get_meta(key: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(key: str, value: str) -> None:
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, value))


def is_loaded() -> bool:
    return get_meta("loaded_at") is not None


# ----------------------------------------------------------------- нормализация
def _to_iso_date(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) > 10 and text[4] == "-" and text[10] in "T ":
        text = text[:10]  # «2026-10-05T00:00:00» -> «2026-10-05»
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return dt.datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    return text  # оставляем как есть: валидатор покажет, что дата не распознана


def _to_num(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", ".").replace(" ", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_text(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


_CONVERT = {"text": _to_text, "date": _to_iso_date, "num": _to_num}


def normalize(section: str, field: str, value):
    kind = next(k for c, _, k in SECTIONS[section]["columns"] if c == field)
    return _CONVERT[kind](value)


# ------------------------------------------------------------------- загрузка
def read_excel_rows(section: str, source) -> list[dict]:
    """Читает лист «Данные» из Excel. source - путь или файловый объект."""
    import openpyxl

    wb = openpyxl.load_workbook(source, data_only=True)
    ws = wb["Данные"] if "Данные" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    wanted = [c for c, _, _ in SECTIONS[section]["columns"]]
    missing = [c for c in wanted if c not in header]
    if missing:
        raise ValueError(
            f"В файле участка «{SECTIONS[section]['title']}» нет колонок: {', '.join(missing)}"
        )
    idx = {c: header.index(c) for c in wanted}
    out = []
    for raw in rows[1:]:
        if raw is None or all(v is None for v in raw):
            continue
        out.append({c: normalize(section, c, raw[i] if i < len(raw) else None) for c, i in idx.items()})
    return out


def import_section(section: str, rows: list[dict]) -> int:
    cols = [c for c, _, _ in SECTIONS[section]["columns"]]
    with connect() as con:
        for r in rows:
            con.execute(
                f"INSERT INTO {section} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                [r.get(c) for c in cols],
            )
    return len(rows)


def import_all(sources: dict) -> dict[str, int]:
    """Первичная загрузка: sources = {section: путь или файловый объект}.
    Загрузка выполняется один раз; повторная запрещена, чтобы не затереть правки."""
    init_db()
    if is_loaded():
        raise RuntimeError("Данные уже загружены. Повторная загрузка отключена.")
    parsed = {s: read_excel_rows(s, src) for s, src in sources.items()}  # сначала всё читаем
    counts = {s: import_section(s, rows) for s, rows in parsed.items()}
    set_meta("loaded_at", dt.datetime.now().isoformat(timespec="seconds"))
    return counts


def find_import_files() -> dict[str, Path]:
    found = {}
    for section, cfg in SECTIONS.items():
        path = IMPORT_DIR / cfg["file_hint"]
        if path.exists():
            found[section] = path
    return found


def reset_all() -> None:
    """Очистка демо-данных (для повторной демонстрации)."""
    with connect() as con:
        for key in SECTIONS:
            con.execute(f"DROP TABLE IF EXISTS {key}")
        con.execute("DROP TABLE IF EXISTS change_log")
        con.execute("DROP TABLE IF EXISTS meta")
    init_db()


# --------------------------------------------------------------------- чтение
def load_df(section: str) -> pd.DataFrame:
    cols = [c for c, _, _ in SECTIONS[section]["columns"]]
    with connect() as con:
        df = pd.read_sql_query(
            f"SELECT rid, {', '.join(cols)}, edited FROM {section} ORDER BY rid", con
        )
    return df


# ------------------------------------------------------------------ сохранение
def _next_record_id(section: str, con) -> str:
    prefix = SECTIONS[section]["prefix"]
    best = 0
    for (rid,) in con.execute(f"SELECT record_id FROM {section}").fetchall():
        if rid and rid.startswith(prefix + "-") and rid[len(prefix) + 1 :].isdigit():
            best = max(best, int(rid[len(prefix) + 1 :]))
    return f"{prefix}-{best + 1:03d}"


def _log(con, section, rid, record_id, field, old, new, author):
    con.execute(
        "INSERT INTO change_log (ts, section, rid, record_id, field, old, new, author)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            dt.datetime.now().isoformat(timespec="seconds"),
            section,
            rid,
            record_id,
            field,
            None if old is None else str(old),
            None if new is None else str(new),
            author,
        ),
    )


def apply_changes(
    section: str,
    rids: list[int],
    edited: dict,
    added: list[dict],
    deleted: list[int],
    author: str,
) -> dict[str, int]:
    """Применяет правки из таблицы редактирования.

    rids - внутренние номера строк в том порядке, в котором они показаны;
    edited - {позиция: {поле: значение}}, added - новые строки, deleted - позиции.
    Затрагивается только таблица своего участка.
    """
    cfg = SECTIONS[section]
    by_label = {label: (col, kind) for col, label, kind in cfg["columns"]}
    stats = {"changed": 0, "added": 0, "deleted": 0}
    with connect() as con:
        for pos, changes in (edited or {}).items():
            rid = rids[int(pos)]
            row = con.execute(f"SELECT * FROM {section} WHERE rid=?", (rid,)).fetchone()
            if row is None:
                continue
            for label, value in changes.items():
                if label not in by_label:
                    continue
                col, kind = by_label[label]
                new = _CONVERT[kind](value)
                old = row[col]
                if new == old:
                    continue
                con.execute(f"UPDATE {section} SET {col}=?, edited=1 WHERE rid=?", (new, rid))
                _log(con, section, rid, row["record_id"], col, old, new, author)
                stats["changed"] += 1
        for pos in deleted or []:
            rid = rids[int(pos)]
            row = con.execute(f"SELECT * FROM {section} WHERE rid=?", (rid,)).fetchone()
            if row is None:
                continue
            con.execute(f"DELETE FROM {section} WHERE rid=?", (rid,))
            _log(con, section, rid, row["record_id"], "(строка)", "была", "удалена", author)
            stats["deleted"] += 1
        for new_row in added or []:
            values = {}
            for label, (col, kind) in by_label.items():
                values[col] = _CONVERT[kind](new_row.get(label))
            if all(v is None for v in values.values()):
                continue
            if not values.get("record_id"):
                values["record_id"] = _next_record_id(section, con)
            cols = list(values)
            cur = con.execute(
                f"INSERT INTO {section} ({', '.join(cols)}, edited) VALUES ({', '.join('?' * len(cols))}, 1)",
                [values[c] for c in cols],
            )
            _log(con, section, cur.lastrowid, values["record_id"], "(строка)", None, "добавлена", author)
            stats["added"] += 1
    return stats


def recent_changes(section: str, limit: int = 15) -> pd.DataFrame:
    with connect() as con:
        return pd.read_sql_query(
            "SELECT ts AS 'Когда', record_id AS 'Запись', field AS 'Поле', old AS 'Было',"
            " new AS 'Стало', author AS 'Кто' FROM change_log WHERE section=? ORDER BY id DESC LIMIT ?",
            con,
            params=(section, limit),
        )
