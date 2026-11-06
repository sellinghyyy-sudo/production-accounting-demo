"""Проверка данных. Для каждой строки: статус, коды проблем, текст, «учитывать в расчёте».

Статусы:
  ok   - строка в порядке;
  warn - сомнительно, но в расчёт берём (нулевой факт, нет партии и т.п.);
  bad  - ошибка: строка в расчёт НЕ берётся, пока её не исправят.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

# Факт больше плана более чем в это число раз считается подозрительным.
OUTLIER_FACTOR = 3.0

# Какие колонки у участка плановые / фактические.
_PLAN_FACT = {
    "blanks": ("plan_qty", "fact_qty", "заготовок"),
    "docs": ("plan_docs", "fact_docs", "документов"),
}

STATUS_ICON = {"ok": "🟢", "warn": "🟡", "bad": "🔴"}
STATUS_TEXT = {"ok": "в порядке", "warn": "проверить", "bad": "ошибка"}


def _is_na(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v))


def _valid_date(value) -> bool:
    if _is_na(value):
        return False
    try:
        dt.date.fromisoformat(str(value))
        return True
    except ValueError:
        return False


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def validate(
    section: str,
    df: pd.DataFrame,
    known_batches: set[str] | None = None,
    outlier_factor: float = OUTLIER_FACTOR,
) -> pd.DataFrame:
    """Возвращает копию df с колонками status, codes, issues, include."""
    out = df.copy()
    statuses, codes_list, issues_list = [], [], []
    seen: dict[str, dict] = {}  # record_id -> значения первой записи
    value_cols = [c for c in df.columns if c not in ("rid", "record_id", "edited")]

    for _, row in df.iterrows():
        bad: list[tuple[str, str]] = []
        warn: list[tuple[str, str]] = []

        # дата
        if _is_na(row["date"]):
            bad.append(("no_date", "не указана дата"))
        elif not _valid_date(row["date"]):
            bad.append(("bad_date", f"дата не распознана: «{row['date']}»"))

        # дубликаты по номеру записи: учитываем первую, остальные - ошибка
        rec = row["record_id"]
        if not _is_na(rec):
            if rec in seen:
                same = all(
                    (_is_na(row[c]) and _is_na(seen[rec][c])) or row[c] == seen[rec][c]
                    for c in value_cols
                )
                bad.append(
                    (
                        "dup",
                        f"дубликат записи {rec} (учтена первая; значения "
                        f"{'совпадают' if same else 'различаются'})",
                    )
                )
            else:
                seen[rec] = {c: row[c] for c in value_cols}

        if section in _PLAN_FACT:
            plan_col, fact_col, unit = _PLAN_FACT[section]
            plan, fact = row[plan_col], row[fact_col]
            if _is_na(plan):
                bad.append(("no_plan", "не указан план"))
            elif plan < 0:
                bad.append(("neg_plan", f"отрицательный план ({_fmt(plan)})"))
            if _is_na(fact):
                bad.append(("no_fact", "не указан факт"))
            elif fact < 0:
                bad.append(("neg_fact", f"отрицательный факт ({_fmt(fact)})"))
            else:
                if not _is_na(plan) and plan > 0 and fact > outlier_factor * plan:
                    bad.append(
                        (
                            "outlier",
                            f"факт {_fmt(fact)} в {fact / plan:.0f} раз больше плана {_fmt(plan)} "
                            f"(порог {outlier_factor:g}×)",
                        )
                    )
                elif not _is_na(plan) and plan == 0 and fact > 0:
                    warn.append(("fact_no_plan", f"факт {_fmt(fact)} при нулевом плане"))
                if fact == 0 and not _is_na(plan) and plan > 0:
                    warn.append(("zero_fact", "нулевой факт при ненулевом плане - подтвердите"))
            if section == "blanks" and _is_na(row["batch_id"]):
                warn.append(("no_batch", "не указана партия - запись не участвует в связке со сваркой"))

        if section == "weld":
            need = row["need_qty"]
            if _is_na(need):
                bad.append(("no_need", "не указана потребность"))
            elif need < 0:
                bad.append(("neg_need", f"отрицательная потребность ({_fmt(need)})"))
            if _is_na(row["batch_id"]):
                warn.append(("no_batch", "не указана партия - связать с заготовкой нельзя"))
            elif known_batches is not None and row["batch_id"] not in known_batches:
                warn.append(("orphan_batch", f"партии {row['batch_id']} нет в данных заготовки"))

        status = "bad" if bad else ("warn" if warn else "ok")
        codes = [c for c, _ in bad + warn]
        text = "; ".join(t for _, t in bad + warn)
        statuses.append(status)
        codes_list.append(codes)
        issues_list.append(text)

    out["status"] = statuses
    out["codes"] = codes_list
    out["issues"] = issues_list
    out["include"] = [s != "bad" for s in statuses]
    return out
