"""Скачиваемый отчёт за выбранный период: Excel (несколько листов) и CSV (сводка)."""
from __future__ import annotations

import datetime as dt
import io

import pandas as pd

import db
import metrics
from validation import STATUS_TEXT


def _records_sheet(snap: metrics.Snapshot, section: str) -> pd.DataFrame:
    sub = snap.sub[section]
    cols = db.SECTIONS[section]["columns"]
    out = sub[[c for c, _, _ in cols]].copy()
    out.columns = [label for _, label, _ in cols]
    out["Статус"] = sub["status"].map(STATUS_TEXT)
    out["Учтено в итоге"] = sub["include"].map({True: "да", False: "нет"})
    out["Проблема"] = sub["issues"]
    out["Исправлено вручную"] = sub["edited"].map({1: "да", 0: ""})
    return out


def _link_sheet(snap: metrics.Snapshot) -> pd.DataFrame:
    link = snap.link.copy()
    if link.empty:
        return link
    link["status"] = link["status"].map(metrics.BATCH_STATUS)
    link["coverage"] = link["coverage"].map(lambda v: None if v is None or pd.isna(v) else round(v, 4))
    return link.rename(
        columns={
            "batch_id": "Партия",
            "date": "Дата",
            "need": "Потребность сварки, шт",
            "fact": "Достоверный факт заготовки, шт",
            "coverage": "Обеспеченность (доля)",
            "gap": "Дефицит (+) / излишек (-), шт",
            "status": "Достоверность",
            "reason": "Пояснение",
        }
    )


def _issues_sheet(snap: metrics.Snapshot) -> pd.DataFrame:
    issues = snap.issues.drop(columns=["section"]).copy()
    issues["Статус"] = issues["Статус"].map(STATUS_TEXT)
    return issues


def summary_frame(snap: metrics.Snapshot) -> pd.DataFrame:
    return snap.summary


def build_csv(snap: metrics.Snapshot) -> bytes:
    """CSV со сводкой. utf-8-sig и «;» - чтобы Excel в русской локали открыл без правок."""
    df = snap.summary.copy()
    df.insert(0, "Период", f"{snap.start:%d.%m.%Y} - {snap.end:%d.%m.%Y}")
    return df.to_csv(index=False, sep=";").encode("utf-8-sig")


def build_excel(snap: metrics.Snapshot) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        info = pd.DataFrame(
            {
                "Параметр": ["Период", "Сформирован", "Правило ошибок", "Связи между участками"],
                "Значение": [
                    f"{snap.start:%d.%m.%Y} - {snap.end:%d.%m.%Y}",
                    dt.datetime.now().strftime("%d.%m.%Y %H:%M"),
                    "Записи со статусом «ошибка» в итоги не входят; сомнительные входят, но помечены.",
                    "Только по номеру партии: Заготовка ↔ Потребность сварки. Документация независима.",
                ],
            }
        )
        info.to_excel(writer, sheet_name="Сводка", index=False, startrow=0)
        snap.summary.to_excel(writer, sheet_name="Сводка", index=False, startrow=len(info) + 2)
        snap.by_employee.drop(columns=["Единица"]).to_excel(
            writer, sheet_name="По сотрудникам", index=False
        )
        _link_sheet(snap).to_excel(writer, sheet_name="Связка по партиям", index=False)
        for section in db.SECTIONS:
            _records_sheet(snap, section).to_excel(
                writer, sheet_name=db.SECTIONS[section]["title"][:31], index=False
            )
        _issues_sheet(snap).to_excel(writer, sheet_name="Проблемы данных", index=False)

        # ширина колонок, чтобы отчёт читался без ручной подгонки
        for ws in writer.book.worksheets:
            for col in ws.columns:
                width = max(len(str(c.value)) if c.value is not None else 0 for c in col)
                ws.column_dimensions[col[0].column_letter].width = min(max(width + 2, 10), 70)
    return buf.getvalue()
