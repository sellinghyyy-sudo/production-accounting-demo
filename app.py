"""Демо-система: рабочие экраны участков и панель генерального директора.

Запуск:  streamlit run app.py   (или run.bat)
"""
from __future__ import annotations

import datetime as dt

import altair as alt
import pandas as pd
import streamlit as st

import db
import metrics
import report
from validation import OUTLIER_FACTOR, STATUS_ICON, STATUS_TEXT

st.set_page_config(page_title="Панель руководителя", page_icon="📊", layout="wide")
db.init_db()

SCREENS = ["Панель директора", "Заготовка", "Потребность сварки", "Документация", "Администрирование"]
SECTION_BY_SCREEN = {"Заготовка": "blanks", "Потребность сварки": "weld", "Документация": "docs"}
DEFAULT_AUTHOR = {"blanks": "Сотрудник заготовки", "weld": "Сотрудник сварки", "docs": "Сотрудник документации"}


# ------------------------------------------------------------------ вспомогательное
def outlier_factor() -> float:
    raw = db.get_meta("outlier_factor")
    try:
        return float(raw) if raw else OUTLIER_FACTOR
    except ValueError:
        return OUTLIER_FACTOR


def pct(v) -> str:
    return "н/д" if v is None else f"{v * 100:.1f} %"


def num(v) -> str:
    return f"{v:,.0f}".replace(",", " ")


def with_icons(df: pd.DataFrame, status_col: str) -> pd.DataFrame:
    out = df.copy()
    out[status_col] = out[status_col].map(lambda s: f"{STATUS_ICON.get(s, '')} {STATUS_TEXT.get(s, s)}")
    return out


def short_date(d) -> str:
    return d.strftime("%d.%m")


# ------------------------------------------------------------------- первичная загрузка
def loader_screen() -> None:
    st.title("Первичная загрузка данных")
    st.write(
        "Система пуста. Данные из трёх Excel-файлов загружаются **один раз**. "
        "После этого файлы можно удалить: дальше система работает только со своей базой."
    )
    found = db.find_import_files()
    if len(found) == len(db.SECTIONS):
        st.success("В папке `import/` найдены все три файла: " + ", ".join(p.name for p in found.values()))
        if st.button("Загрузить из папки import/", type="primary"):
            try:
                counts = db.import_all(found)
            except Exception as exc:  # показываем причину человеческим языком
                st.error(f"Не удалось загрузить: {exc}")
            else:
                st.session_state["flash"] = "Загружено: " + ", ".join(
                    f"{db.SECTIONS[s]['title']} - {n} зап." for s, n in counts.items()
                )
                st.rerun()
        st.divider()
        st.caption("Или загрузите файлы вручную:")
    else:
        st.info(
            "Файлы не найдены в папке `import/`. Загрузите три файла вручную "
            "или положите их в папку `import/` и обновите страницу."
        )
    uploads = {}
    cols = st.columns(3)
    for col, (section, cfg) in zip(cols, db.SECTIONS.items()):
        with col:
            uploads[section] = st.file_uploader(cfg["title"], type=["xlsx"], key=f"up_{section}")
    if st.button("Загрузить выбранные файлы", disabled=not all(uploads.values())):
        try:
            counts = db.import_all(uploads)
        except Exception as exc:
            st.error(f"Не удалось загрузить: {exc}")
        else:
            st.session_state["flash"] = "Загружено: " + ", ".join(
                f"{db.SECTIONS[s]['title']} - {n} зап." for s, n in counts.items()
            )
            st.rerun()


# --------------------------------------------------------------------------- графики
PLAN_COLOR, FACT_COLOR, NEED_COLOR = "#9aa5b1", "#2f80ed", "#9aa5b1"


def chart_plan_fact(daily: pd.DataFrame, title: str) -> None:
    st.markdown(f"**{title}**")
    if daily.empty:
        st.info("В выбранном периоде нет записей.")
        return
    d = daily.copy()
    d["Дата"] = d["date"].map(short_date)
    order = list(d["Дата"])
    long = d.melt(id_vars=["Дата", "has_bad"], value_vars=["plan", "fact"], var_name="k", value_name="Значение")
    long["Показатель"] = long["k"].map({"plan": "План", "fact": "Факт (только достоверные)"})
    base = alt.Chart(long).encode(x=alt.X("Дата:N", sort=order, axis=alt.Axis(labelAngle=0)))
    bars = base.mark_bar().encode(
        y=alt.Y("Значение:Q", title=None),
        xOffset="Показатель:N",
        color=alt.Color(
            "Показатель:N",
            scale=alt.Scale(domain=["План", "Факт (только достоверные)"], range=[PLAN_COLOR, FACT_COLOR]),
            legend=alt.Legend(orient="bottom", title=None),
        ),
        tooltip=["Дата", "Показатель", "Значение"],
    )
    layers = bars
    flagged = d[d["has_bad"]]
    if not flagged.empty:
        top = (
            alt.Chart(flagged)
            .mark_text(text="⚠", fontSize=20, dy=-12, color="#d93025")
            .encode(x=alt.X("Дата:N", sort=order), y="plan:Q")
        )
        layers = bars + top
    st.altair_chart(layers.properties(height=280), width="stretch")
    if not flagged.empty:
        st.caption("⚠ - в этот день есть исключённые записи с ошибками; на графике их значений нет.")


def chart_link(link: pd.DataFrame) -> None:
    st.markdown("**Потребность сварки и достоверный факт заготовки по партиям**")
    if link.empty:
        st.info("В выбранном периоде нет партий.")
        return
    d = link.copy()
    order = list(d["batch_id"])
    long = d.melt(id_vars=["batch_id", "status"], value_vars=["need", "fact"], var_name="k", value_name="Значение")
    long["Показатель"] = long["k"].map({"need": "Потребность сварки", "fact": "Факт заготовки (достоверный)"})
    long = long.dropna(subset=["Значение"])
    base = alt.Chart(long).encode(x=alt.X("batch_id:N", title="Партия", sort=order, axis=alt.Axis(labelAngle=0)))
    bars = base.mark_bar().encode(
        y=alt.Y("Значение:Q", title=None),
        xOffset="Показатель:N",
        color=alt.Color(
            "Показатель:N",
            scale=alt.Scale(domain=["Потребность сварки", "Факт заготовки (достоверный)"],
                            range=[NEED_COLOR, "#27ae60"]),
            legend=alt.Legend(orient="bottom", title=None),
        ),
        tooltip=["batch_id", "Показатель", "Значение"],
    )
    bad = d[d["status"].isin(["unreliable", "no_data"])].copy()
    layers = bars
    if not bad.empty:
        bad["y"] = bad["need"].fillna(0)
        flag = (
            alt.Chart(bad)
            .mark_text(text="⚠ нет достоверного факта", fontSize=11, dy=-10, color="#d93025", align="center")
            .encode(x=alt.X("batch_id:N", sort=order), y="y:Q")
        )
        layers = bars + flag
    st.altair_chart(layers.properties(height=300), width="stretch")
    if not bad.empty:
        st.caption("⚠ - у партии нет достоверных данных, обеспеченность по ней не считается (а не «0 %»).")


# ------------------------------------------------------------------ панель директора
def kpi_card(col, title: str, value: str, subtitle: str, reliability: str, note: str = "") -> None:
    with col:
        with st.container(border=True):
            st.caption(title)
            st.markdown(f"<div style='font-size:2rem;font-weight:700;line-height:1.1'>{value}</div>",
                        unsafe_allow_html=True)
            st.caption(subtitle)
            st.markdown(f"<span style='font-size:0.85rem'>{metrics.RELIABILITY[reliability]}</span>",
                        unsafe_allow_html=True)
            if note:
                st.caption(note)


def director_screen() -> None:
    st.title("📊 Панель генерального директора")
    data = {s: db.load_df(s) for s in db.SECTIONS}
    bounds = metrics.date_bounds(data)
    if bounds is None:
        st.info("Данных пока нет.")
        return
    lo, hi = bounds

    top_l, top_r = st.columns([3, 1])
    with top_l:
        if "period" not in st.session_state:
            st.session_state["period"] = (lo, hi)
        st.date_input(
            "Период", key="period", format="DD.MM.YYYY",
            min_value=lo - dt.timedelta(days=90), max_value=hi + dt.timedelta(days=90),
        )
    with top_r:
        st.write("")
        st.button("Весь период", on_click=lambda: st.session_state.update(period=(lo, hi)), width="stretch")
    period = st.session_state["period"]
    if not (isinstance(period, tuple) and len(period) == 2):
        st.info("Выберите дату окончания периода.")
        return
    start, end = period

    snap = metrics.snapshot(start, end, data, outlier_factor())
    k = snap.kpi

    # --- проблемы данных: директор узнаёт о них сразу, а не из мелкого шрифта
    n_bad = int((snap.issues["Статус"] == "bad").sum()) if len(snap.issues) else 0
    n_warn = int((snap.issues["Статус"] == "warn").sum()) if len(snap.issues) else 0
    if n_bad:
        by_section = snap.issues[snap.issues["Статус"] == "bad"].groupby("Участок").size().to_dict()
        parts = ", ".join(f"{s}: {n}" for s, n in by_section.items())
        st.error(
            f"⚠ В данных найдено ошибок: **{n_bad}** ({parts}); на проверку: {n_warn}. "
            "Ошибочные записи **не входят** в итоги, а связанные показатели помечены как недостоверные. "
            "Исправить их могут сотрудники участков на своих экранах."
        )
    elif n_warn:
        st.warning(f"В данных есть записи на проверку: {n_warn}. Они входят в итоги, но помечены.")
    else:
        st.success("Ошибок в данных не найдено.")

    # --- карточки показателей
    c1, c2, c3, c4 = st.columns(4)
    kb, kw, kd, kl = k["blanks"], k["weld"], k["docs"], k["link"]
    kpi_card(c1, "Заготовка: выполнение плана", pct(kb["pct"]),
             f"факт {num(kb['fact'])} из плана {num(kb['plan'])} шт", kb["reliability"],
             f"исключено записей: {kb['n_bad']}" if kb["n_bad"] else "")
    kpi_card(c2, "Потребность сварки", f"{num(kw['need'])} шт",
             f"учтено записей: {kw['n_used']} из {kw['n_total']}", kw["reliability"],
             f"исключено записей: {kw['n_bad']}" if kw["n_bad"] else "")
    note = ""
    if kl["n_total"] and kl["n_reliable"] < kl["n_total"]:
        note = f"оценено партий {kl['n_reliable']} из {kl['n_total']}; без достоверного факта: {kl['n_total'] - kl['n_reliable']}"
    kpi_card(c3, "Сварка ↔ заготовка: обеспеченность", pct(kl["pct"]),
             f"дефицит заготовки: {num(kl['deficit'])} шт", kl["reliability"], note)
    kpi_card(c4, "Документация: выполнение плана", pct(kd["pct"]),
             f"факт {num(kd['fact'])} из плана {num(kd['plan'])} док.", kd["reliability"],
             "независимый участок: связи с другими нет")

    # --- сводная таблица
    st.subheader("Сводная таблица")
    st.dataframe(snap.summary, hide_index=True, width="stretch")
    with st.expander("Разрез по сотрудникам"):
        emp = snap.by_employee.copy()
        emp["Выполнение"] = emp["Выполнение"].map(pct)
        st.dataframe(emp.drop(columns=["Единица"]), hide_index=True, width="stretch")

    # --- графики
    st.subheader("Графики")
    g1, g2 = st.columns(2)
    with g1:
        chart_plan_fact(metrics.daily(snap.sub["blanks"], "plan_qty", "fact_qty"), "Заготовка: план и факт по дням, шт")
    with g2:
        chart_link(snap.link)
    g3, _ = st.columns(2)
    with g3:
        chart_plan_fact(metrics.daily(snap.sub["docs"], "plan_docs", "fact_docs"), "Документация: план и факт по дням, док.")

    # --- детализация
    st.subheader("Из чего получился итог")
    choice = st.selectbox(
        "Показатель",
        ["Заготовка", "Потребность сварки", "Сварка ↔ заготовка (по партиям)", "Документация"],
    )
    if choice == "Сварка ↔ заготовка (по партиям)":
        link = snap.link.copy()
        view = with_icons(link, "status").rename(columns={
            "batch_id": "Партия", "date": "Дата", "need": "Потребность, шт", "fact": "Достоверный факт, шт",
            "coverage": "Обеспеченность", "gap": "Дефицит (+) / излишек (−)", "status": "Достоверность",
            "reason": "Пояснение"})
        view["Обеспеченность"] = view["Обеспеченность"].map(lambda v: "н/д" if pd.isna(v) else pct(v))
        st.dataframe(view, hide_index=True, width="stretch")
        if len(link):
            batch = st.selectbox("Записи партии", list(link["batch_id"]))
            b1, b2 = st.columns(2)
            with b1:
                st.caption("Заготовка")
                rec = snap.v["blanks"][snap.v["blanks"]["batch_id"] == batch]
                st.dataframe(with_icons(rec[["record_id", "date", "employee", "plan_qty", "fact_qty", "status", "issues"]],
                                        "status"), hide_index=True, width="stretch")
            with b2:
                st.caption("Потребность сварки")
                rec = snap.v["weld"][snap.v["weld"]["batch_id"] == batch]
                st.dataframe(with_icons(rec[["record_id", "date", "need_qty", "status", "issues"]], "status"),
                             hide_index=True, width="stretch")
    else:
        section = {"Заготовка": "blanks", "Потребность сварки": "weld", "Документация": "docs"}[choice]
        st.dataframe(with_icons(metrics.drill_records(snap, section), "Статус"), hide_index=True, width="stretch")
        st.caption("«Учтено в итоге = нет» - запись с ошибкой, в сумму она не вошла.")

    # --- отчёт
    st.subheader("Отчёт")
    d1, d2, _ = st.columns([1, 1, 2])
    stamp = f"{start:%Y%m%d}_{end:%Y%m%d}"
    with d1:
        st.download_button(
            "⬇ Скачать отчёт (Excel)", report.build_excel(snap), file_name=f"otchet_{stamp}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
            width="stretch",
        )
    with d2:
        st.download_button(
            "⬇ Сводка (CSV)", report.build_csv(snap), file_name=f"svodka_{stamp}.csv", mime="text/csv",
            width="stretch",
        )

    with st.expander(f"Все проблемы данных ({len(snap.issues)})", expanded=bool(n_bad)):
        if snap.issues.empty:
            st.write("Проблем нет.")
        else:
            st.dataframe(with_icons(snap.issues.drop(columns=["section"]), "Статус"), hide_index=True, width="stretch")


# ------------------------------------------------------------------ экраны участков
def section_screen(section: str) -> None:
    cfg = db.SECTIONS[section]
    st.title(f"Участок: {cfg['title']}")
    st.caption(
        "Здесь видны и редактируются только данные этого участка. "
        "Исправление пересчитывает панель директора автоматически."
    )
    author = st.text_input("Кто вносит правки", value=DEFAULT_AUTHOR[section], key=f"author_{section}")

    data = {s: db.load_df(s) for s in db.SECTIONS}
    v = metrics.validated(data, outlier_factor())
    df = v[section].reset_index(drop=True)

    n_bad = int((df["status"] == "bad").sum())
    n_warn = int((df["status"] == "warn").sum())
    if n_bad:
        st.error(f"🔴 Строк с ошибками: {n_bad}. Они не входят в итоги. Исправьте значения в таблице и нажмите «Сохранить».")
    if n_warn:
        st.warning(f"🟡 Строк на проверку: {n_warn}. В итоги входят, но подтвердите значения.")
    if not n_bad and not n_warn:
        st.success("Все строки в порядке.")

    flash = st.session_state.pop(f"flash_{section}", None)
    if flash:
        st.success(flash)

    # таблица для редактирования
    disp = pd.DataFrame()
    config = {}
    for col, label, kind in cfg["columns"]:
        if kind == "date":
            disp[label] = pd.to_datetime(df[col], errors="coerce").dt.date
            config[label] = st.column_config.DateColumn(label, format="DD.MM.YYYY")
        elif kind == "num":
            disp[label] = df[col].astype(float)
            config[label] = st.column_config.NumberColumn(label, step=1)
        else:
            disp[label] = df[col]
            config[label] = st.column_config.TextColumn(label)
    disp["Статус"] = df["status"].map(lambda s: f"{STATUS_ICON[s]} {STATUS_TEXT[s]}")
    disp["Проблема"] = df["issues"]
    config["Статус"] = st.column_config.TextColumn("Статус", width="small")
    config["Проблема"] = st.column_config.TextColumn("Проблема", width="large")

    ver = st.session_state.get(f"ver_{section}", 0)
    key = f"editor_{section}_{ver}"
    st.data_editor(
        disp, key=key, hide_index=True, num_rows="dynamic", width="stretch",
        column_config=config, disabled=["Статус", "Проблема"],
    )
    st.caption(
        "Чтобы добавить строку - нижняя пустая строка таблицы. Чтобы удалить - отметьте строку слева и нажмите "
        "значок корзины. Пустое значение = «нет данных», такая строка будет помечена ошибкой."
    )

    if st.button("💾 Сохранить изменения", type="primary", key=f"save_{section}"):
        state = st.session_state.get(key, {})
        stats = db.apply_changes(
            section, df["rid"].tolist(), state.get("edited_rows"), state.get("added_rows"),
            state.get("deleted_rows"), author.strip() or DEFAULT_AUTHOR[section],
        )
        st.session_state[f"ver_{section}"] = ver + 1
        st.session_state[f"flash_{section}"] = (
            f"Сохранено: изменено значений - {stats['changed']}, добавлено строк - {stats['added']}, "
            f"удалено строк - {stats['deleted']}."
        )
        st.rerun()

    if section == "blanks":
        st.info("Факт заготовки влияет на показатель «Сварка ↔ заготовка» по номеру партии.")
    elif section == "weld":
        st.info("Потребность сварки сопоставляется с заготовкой по номеру партии.")
    else:
        st.info("У «Документации» нет номера партии: связи с другими участками нет, её цифры от них не зависят.")

    with st.expander("Журнал изменений участка"):
        log = db.recent_changes(section)
        st.dataframe(log, hide_index=True, width="stretch") if len(log) else st.write("Правок пока не было.")


# ----------------------------------------------------------------------- администрирование
def admin_screen() -> None:
    st.title("Администрирование")
    st.write(f"Данные загружены: **{db.get_meta('loaded_at')}**. Исходные Excel-файлы системе больше не нужны.")
    cols = st.columns(3)
    for col, (section, cfg) in zip(cols, db.SECTIONS.items()):
        with col:
            df = db.load_df(section)
            col.metric(cfg["title"], f"{len(df)} зап.", f"вручную изменено: {int(df['edited'].sum())}")

    st.subheader("Порог подозрительного факта")
    st.write(
        "Факт больше плана более чем в N раз считается ошибкой (например, опечатка «1200» вместо «100»)."
    )
    new = st.number_input("N (раз)", min_value=1.5, max_value=50.0, value=float(outlier_factor()), step=0.5)
    if st.button("Применить порог"):
        db.set_meta("outlier_factor", str(new))
        st.rerun()

    st.subheader("Правила проверки данных")
    st.markdown(
        "- 🔴 **Ошибка** (запись исключается из итогов): нет даты, нет плана или факта, отрицательное значение, "
        "факт в N раз больше плана, повтор номера записи (учитывается первая).\n"
        "- 🟡 **На проверку** (запись входит в итоги): нулевой факт при ненулевом плане, нет номера партии, "
        "партии нет в данных заготовки.\n"
        "- Процент выполнения = сумма факта / сумма плана (не среднее процентов).\n"
        "- Связь между участками - только по номеру партии: «Заготовка» ↔ «Потребность сварки». "
        "«Документация» номера партии не содержит, поэтому от остальных не зависит."
    )

    st.subheader("Сброс демо-данных")
    st.warning("Удаляет все данные и правки. После сброса потребуется новая первичная загрузка.")
    sure = st.checkbox("Понимаю, что все правки будут потеряны")
    if st.button("Сбросить данные", disabled=not sure):
        db.reset_all()
        for k in list(st.session_state.keys()):
            del st.session_state[k]
        st.rerun()


# --------------------------------------------------------------------------- маршрутизация
def main() -> None:
    if not db.is_loaded():
        flash = st.session_state.pop("flash", None)
        if flash:
            st.success(flash)
        loader_screen()
        return

    with st.sidebar:
        st.header("Разделы")
        screen = st.radio("Экран", SCREENS, label_visibility="collapsed")
        st.caption("Демо без авторизации: экраны участков разделены, данные хранятся в файле data/app.db.")
    flash = st.session_state.pop("flash", None)
    if flash:
        st.success(flash)

    if screen == "Панель директора":
        director_screen()
    elif screen == "Администрирование":
        admin_screen()
    else:
        section_screen(SECTION_BY_SCREEN[screen])


main()
