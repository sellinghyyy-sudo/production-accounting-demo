"""Расчёт показателей для панели директора.

Главные принципы:
  * в расчёт идут только строки без ошибок (status != bad);
  * процент выполнения = сумма факта / сумма плана (не среднее процентов);
  * единственная настоящая связь между участками - номер партии (batch_id)
    между «Заготовкой» и «Потребностью сварки». У «Документации» номера партии нет,
    поэтому её показатели считаются отдельно и ни от кого не зависят;
  * связанный показатель помечается недостоверным, если данные другого участка
    по этой партии ошибочны или отсутствуют.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import pandas as pd

import db
from validation import OUTLIER_FACTOR, validate

RELIABILITY = {
    "ok": "🟢 достоверно",
    "warn": "🟡 есть записи на проверку",
    "partial": "🟠 частично: часть данных исключена",
    "none": "🔴 нет достоверных данных",
}

BATCH_STATUS = {
    "ok": "🟢 достоверно",
    "warn": "🟡 проверить",
    "unreliable": "🔴 недостоверно",
    "no_data": "⚪ нет данных",
}

SECTION_TITLE = {k: v["title"] for k, v in db.SECTIONS.items()}


@dataclass
class Snapshot:
    start: dt.date
    end: dt.date
    v: dict[str, pd.DataFrame]  # проверенные таблицы целиком
    sub: dict[str, pd.DataFrame]  # то же, но только строки выбранного периода
    kpi: dict[str, dict] = field(default_factory=dict)
    link: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    by_employee: pd.DataFrame = field(default_factory=pd.DataFrame)
    issues: pd.DataFrame = field(default_factory=pd.DataFrame)


# --------------------------------------------------------------------- периоды
def _dates(df: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(df["date"], errors="coerce").dt.date


def _in_period(df: pd.DataFrame, start: dt.date, end: dt.date) -> pd.Series:
    d = _dates(df)
    return d.notna() & (d >= start) & (d <= end)


def date_bounds(data: dict[str, pd.DataFrame]) -> tuple[dt.date, dt.date] | None:
    all_dates = pd.concat([_dates(df) for df in data.values() if len(df)], ignore_index=True)
    all_dates = all_dates.dropna()
    if all_dates.empty:
        return None
    return min(all_dates), max(all_dates)


# ------------------------------------------------------------------ показатели
def _reliability(n_total: int, n_bad: int, n_warn: int, n_used: int) -> str:
    if n_total == 0 or n_used == 0:
        return "none"
    if n_bad:
        return "partial"
    if n_warn:
        return "warn"
    return "ok"


def _plan_fact(sub: pd.DataFrame, plan_col: str, fact_col: str) -> dict:
    used = sub[sub["include"]]
    plan = float(used[plan_col].sum())
    fact = float(used[fact_col].sum())
    n_bad = int((sub["status"] == "bad").sum())
    n_warn = int((sub["status"] == "warn").sum())
    return {
        "plan": plan,
        "fact": fact,
        "pct": (fact / plan) if plan > 0 else None,
        "n_total": len(sub),
        "n_used": len(used),
        "n_bad": n_bad,
        "n_warn": n_warn,
        "reliability": _reliability(len(sub), n_bad, n_warn, len(used)),
    }


def _weld(sub: pd.DataFrame) -> dict:
    used = sub[sub["include"]]
    n_bad = int((sub["status"] == "bad").sum())
    n_warn = int((sub["status"] == "warn").sum())
    return {
        "need": float(used["need_qty"].sum()),
        "n_total": len(sub),
        "n_used": len(used),
        "n_bad": n_bad,
        "n_warn": n_warn,
        "reliability": _reliability(len(sub), n_bad, n_warn, len(used)),
    }


# ---------------------------------------------------------------------- связка
def link_by_batch(blanks: pd.DataFrame, weld: pd.DataFrame, start: dt.date, end: dt.date) -> pd.DataFrame:
    """Для каждой партии: потребность сварки против достоверного факта заготовки."""
    rows = []
    weld_batches = weld[weld["batch_id"].notna()]
    for batch, wgrp in weld_batches.groupby("batch_id", sort=True):
        wdates = _dates(wgrp).dropna()
        if wdates.empty:
            continue  # без даты партию нельзя отнести к периоду; ошибка видна в списке проблем
        bdate = min(wdates)
        if not (start <= bdate <= end):
            continue
        w_used = wgrp[wgrp["include"]]
        b_all = blanks[blanks["batch_id"] == batch]
        b_used = b_all[b_all["include"]]
        need = float(w_used["need_qty"].sum()) if len(w_used) else None
        fact = None
        reasons: list[str] = []

        if w_used.empty:
            status = "unreliable"
            for _, r in wgrp.iterrows():
                reasons.append(f"сварка {r['record_id']}: {r['issues']}")
        elif b_all.empty:
            status = "no_data"
            reasons.append("по этой партии нет записей заготовки")
        elif b_used.empty:
            status = "unreliable"
            for _, r in b_all.iterrows():
                reasons.append(f"заготовка {r['record_id']}: {r['issues']}")
        else:
            fact = float(b_used["fact_qty"].sum())
            status = "ok"
            for _, r in b_used[b_used["status"] == "warn"].iterrows():
                status = "warn"
                reasons.append(f"заготовка {r['record_id']}: {r['issues']}")
            for _, r in w_used[w_used["status"] == "warn"].iterrows():
                status = "warn"
                reasons.append(f"сварка {r['record_id']}: {r['issues']}")
            for _, r in b_all[b_all["status"] == "bad"].iterrows():
                if r["codes"] == ["dup"]:
                    reasons.append(f"заготовка {r['record_id']}: {r['issues']}")  # только заметка
                else:
                    status = "warn"
                    reasons.append(f"заготовка {r['record_id']} отброшена: {r['issues']}")

        coverage = (fact / need) if (fact is not None and need) else None
        gap = (need - fact) if (fact is not None and need is not None) else None
        rows.append(
            {
                "batch_id": batch,
                "date": bdate,
                "need": need,
                "fact": fact,
                "coverage": coverage,
                "gap": gap,
                "status": status,
                "reason": " | ".join(reasons),
            }
        )
    cols = ["batch_id", "date", "need", "fact", "coverage", "gap", "status", "reason"]
    return pd.DataFrame(rows, columns=cols)


def _link_kpi(link: pd.DataFrame) -> dict:
    reliable = link[link["status"].isin(["ok", "warn"])]
    need = float(reliable["need"].sum()) if len(reliable) else 0.0
    fact = float(reliable["fact"].sum()) if len(reliable) else 0.0
    n_total, n_rel = len(link), len(reliable)
    if n_total == 0 or n_rel == 0:
        rel = "none"
    elif n_rel < n_total:
        rel = "partial"
    elif (link["status"] == "warn").any():
        rel = "warn"
    else:
        rel = "ok"
    return {
        "need": need,
        "fact": fact,
        "pct": (fact / need) if need > 0 else None,
        "deficit": float(reliable["gap"].clip(lower=0).sum()) if len(reliable) else 0.0,
        "n_total": n_total,
        "n_reliable": n_rel,
        "n_unreliable": int((link["status"] == "unreliable").sum()),
        "n_no_data": int((link["status"] == "no_data").sum()),
        "n_warn": int((link["status"] == "warn").sum()),
        "reliability": rel,
    }


# ------------------------------------------------------------------- проблемы
def _issues(v: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for section, df in v.items():
        for _, r in df[df["status"] != "ok"].iterrows():
            if section == "docs":
                touched = "Документация (независимый участок, другие показатели не затрагивает)"
            elif section == "blanks":
                touched = "Заготовка" + (f"; связка по партии {r['batch_id']}" if pd.notna(r["batch_id"]) else "")
            else:
                touched = "Потребность сварки" + (
                    f"; связка по партии {r['batch_id']}" if pd.notna(r["batch_id"]) else ""
                )
            rows.append(
                {
                    "section": section,
                    "Участок": SECTION_TITLE[section],
                    "Запись": r["record_id"],
                    "Дата": r["date"],
                    "Статус": r["status"],
                    "Проблема": r["issues"],
                    "Что затронуто": touched,
                }
            )
    cols = ["section", "Участок", "Запись", "Дата", "Статус", "Проблема", "Что затронуто"]
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------------- сводные
def _pct(v) -> str:
    return "н/д" if v is None else f"{v * 100:.1f} %"


def _num(v) -> str:
    return f"{v:,.0f}".replace(",", " ")


def _summary(kpi: dict) -> pd.DataFrame:
    b, w, d, k = kpi["blanks"], kpi["weld"], kpi["docs"], kpi["link"]
    rows = [
        ("Заготовка", "Выполнение плана", _pct(b["pct"]), f"{_num(b['fact'])} из {_num(b['plan'])} шт",
         b["n_used"], b["n_bad"], b["n_warn"], RELIABILITY[b["reliability"]]),
        ("Потребность сварки", "Потребность за период", f"{_num(w['need'])} шт", "сумма достоверных записей",
         w["n_used"], w["n_bad"], w["n_warn"], RELIABILITY[w["reliability"]]),
        ("Связка: сварка ↔ заготовка", "Обеспеченность потребности заготовкой", _pct(k["pct"]),
         f"{_num(k['fact'])} из {_num(k['need'])} шт; оценено партий {k['n_reliable']} из {k['n_total']}",
         k["n_reliable"], k["n_unreliable"] + k["n_no_data"], k["n_warn"], RELIABILITY[k["reliability"]]),
        ("Документация", "Выполнение плана", _pct(d["pct"]), f"{_num(d['fact'])} из {_num(d['plan'])} док.",
         d["n_used"], d["n_bad"], d["n_warn"], RELIABILITY[d["reliability"]]),
    ]
    return pd.DataFrame(
        rows,
        columns=["Участок", "Показатель", "Значение", "Расчёт", "Учтено", "Исключено (ошибки)",
                 "На проверку", "Достоверность"],
    )


def _by_employee(sub: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for section, plan_col, fact_col, unit in (
        ("blanks", "plan_qty", "fact_qty", "шт"),
        ("docs", "plan_docs", "fact_docs", "док."),
    ):
        df = sub[section]
        for emp, grp in df.groupby(df["employee"].fillna("(не указан)")):
            used = grp[grp["include"]]
            plan, fact = float(used[plan_col].sum()), float(used[fact_col].sum())
            rows.append(
                {
                    "Участок": SECTION_TITLE[section],
                    "Сотрудник": emp,
                    "План": plan,
                    "Факт": fact,
                    "Выполнение": (fact / plan) if plan > 0 else None,
                    "Исключено записей": int((grp["status"] == "bad").sum()),
                    "Единица": unit,
                }
            )
    return pd.DataFrame(rows, columns=["Участок", "Сотрудник", "План", "Факт", "Выполнение",
                                       "Исключено записей", "Единица"])


# ----------------------------------------------------------------- точка входа
def validated(data: dict[str, pd.DataFrame] | None = None, outlier_factor: float = OUTLIER_FACTOR):
    data = data if data is not None else {s: db.load_df(s) for s in db.SECTIONS}
    known = set(data["blanks"]["batch_id"].dropna())
    return {
        "blanks": validate("blanks", data["blanks"], outlier_factor=outlier_factor),
        "weld": validate("weld", data["weld"], known_batches=known, outlier_factor=outlier_factor),
        "docs": validate("docs", data["docs"], outlier_factor=outlier_factor),
    }


def snapshot(
    start: dt.date,
    end: dt.date,
    data: dict[str, pd.DataFrame] | None = None,
    outlier_factor: float = OUTLIER_FACTOR,
) -> Snapshot:
    v = validated(data, outlier_factor)
    sub = {s: df[_in_period(df, start, end)] for s, df in v.items()}
    snap = Snapshot(start=start, end=end, v=v, sub=sub)
    snap.kpi["blanks"] = _plan_fact(sub["blanks"], "plan_qty", "fact_qty")
    snap.kpi["weld"] = _weld(sub["weld"])
    snap.kpi["docs"] = _plan_fact(sub["docs"], "plan_docs", "fact_docs")
    snap.link = link_by_batch(v["blanks"], v["weld"], start, end)
    snap.kpi["link"] = _link_kpi(snap.link)
    snap.summary = _summary(snap.kpi)
    snap.by_employee = _by_employee(sub)
    snap.issues = _issues(v)
    return snap


# ------------------------------------------------------------------- для графиков
def daily(sub: pd.DataFrame, plan_col: str, fact_col: str) -> pd.DataFrame:
    """План и достоверный факт по дням; has_bad - были ли в этот день исключённые записи."""
    if sub.empty:
        return pd.DataFrame(columns=["date", "plan", "fact", "has_bad"])
    df = sub.copy()
    df["day"] = _dates(df)
    rows = []
    for day, grp in df.groupby("day"):
        used = grp[grp["include"]]
        rows.append(
            {
                "date": day,
                "plan": float(used[plan_col].sum()),
                "fact": float(used[fact_col].sum()),
                "has_bad": bool((grp["status"] == "bad").any()),
            }
        )
    return pd.DataFrame(rows)


def drill_records(snap: Snapshot, section: str) -> pd.DataFrame:
    """Записи, из которых получился итог по участку (с признаком «учтено»)."""
    sub = snap.sub[section].copy()
    cols = [c for c, _, _ in db.SECTIONS[section]["columns"]]
    sub["Учтено в итоге"] = sub["include"].map({True: "да", False: "нет"})
    sub["Статус"] = sub["status"]
    sub["Проблема"] = sub["issues"]
    return sub[cols + ["Учтено в итоге", "Статус", "Проблема"]]
