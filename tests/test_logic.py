"""Проверки расчётов и сохранения. Запуск:  python -m unittest discover -s tests -v"""
import datetime as dt
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import db  # noqa: E402
import metrics  # noqa: E402
import report  # noqa: E402

D = dt.date

BLANKS = [
    ("P-001", "2026-10-05", "LOT-01", "Анна Соколова", 100, 90),
    ("P-002", "2026-10-06", "LOT-02", "Анна Соколова", 100, 100),
    ("P-003", "2026-10-07", "LOT-03", "Иван Морозов", 100, 1200),
    ("P-004", "2026-10-08", "LOT-04", "Иван Морозов", 100, -5),
    ("P-005", "2026-10-09", "LOT-05", "Анна Соколова", 100, 80),
    ("P-005", "2026-10-09", "LOT-05", "Анна Соколова", 100, 80),
    ("P-006", "2026-10-12", "LOT-06", "Иван Морозов", 100, 0),
    ("P-007", "2026-10-13", "LOT-07", "Анна Соколова", 100, None),
    ("P-008", "2026-10-14", "LOT-08", "Иван Морозов", 100, 100),
]
WELD = [
    ("W-001", "2026-10-05", "LOT-01", 110),
    ("W-002", "2026-10-06", "LOT-02", 90),
    ("W-003", "2026-10-07", "LOT-03", 130),
    ("W-004", "2026-10-08", "LOT-04", 100),
    ("W-005", "2026-10-09", "LOT-05", 100),
    ("W-006", "2026-10-12", "LOT-06", 20),
    ("W-007", "2026-10-13", "LOT-07", 100),
    ("W-008", "2026-10-14", "LOT-08", 100),
]
DOCS = [
    ("D-001", "2026-10-05", "Мария Лебедева", 5, 4),
    ("D-002", "2026-10-06", "Олег Волков", 4, 4),
    ("D-003", "2026-10-07", "Мария Лебедева", 5, 3),
    ("D-004", "2026-10-09", "Олег Волков", 3, 2),
    ("D-005", "2026-10-12", "Мария Лебедева", 4, 0),
    ("D-006", "2026-10-14", "Олег Волков", 5, 5),
]
FULL = (D(2026, 10, 5), D(2026, 10, 14))


def _rows(section, tuples):
    cols = [c for c, _, _ in db.SECTIONS[section]["columns"]]
    return [dict(zip(cols, t)) for t in tuples]


class LogicTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        db.DB_PATH = Path(self.tmp) / "test.db"
        db.init_db()
        db.import_section("blanks", _rows("blanks", BLANKS))
        db.import_section("weld", _rows("weld", WELD))
        db.import_section("docs", _rows("docs", DOCS))
        db.set_meta("loaded_at", "test")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, start=FULL[0], end=FULL[1]):
        return metrics.snapshot(start, end)

    def rid_of(self, section, record_id):
        df = db.load_df(section)
        return df[df["record_id"] == record_id]["rid"].iloc[0]

    def fix_fact(self, record_id, value, author="тест"):
        df = db.load_df("blanks")
        rids = df["rid"].tolist()
        pos = rids.index(self.rid_of("blanks", record_id))
        db.apply_changes("blanks", rids, {pos: {"Факт, шт": value}}, [], [], author)

    # --- контрольный пример из листа «Пример_формул» файла «Заготовка»
    def test_control_example_from_excel(self):
        k = self.snap(D(2026, 10, 5), D(2026, 10, 6)).kpi["blanks"]
        self.assertEqual((k["plan"], k["fact"]), (200, 190))
        self.assertAlmostEqual(k["pct"], 0.95)
        self.assertEqual(k["reliability"], "ok")

    def test_full_period_excludes_errors(self):
        s = self.snap()
        k = s.kpi["blanks"]
        # исключены: P-003 (выброс), P-004 (минус), второй P-005 (дубль), P-007 (нет факта)
        self.assertEqual(k["n_bad"], 4)
        self.assertEqual(k["n_warn"], 1)  # P-006: нулевой факт
        self.assertEqual((k["plan"], k["fact"]), (500, 370))
        self.assertAlmostEqual(k["pct"], 0.74)
        self.assertEqual(k["reliability"], "partial")

    def test_percent_is_ratio_of_sums_not_mean_of_percents(self):
        k = self.snap(D(2026, 10, 5), D(2026, 10, 12)).kpi["docs"]
        # 4+4+3+2+0 = 13 из 5+4+5+3+4 = 21; среднее процентов дало бы другое число
        self.assertAlmostEqual(k["pct"], 13 / 21)

    def test_link_statuses(self):
        link = self.snap().link.set_index("batch_id")
        for lot in ("LOT-03", "LOT-04", "LOT-07"):
            self.assertEqual(link.loc[lot, "status"], "unreliable", lot)
            self.assertTrue(link.loc[lot, "coverage"] != link.loc[lot, "coverage"])  # NaN
        self.assertEqual(link.loc["LOT-06", "status"], "warn")
        for lot in ("LOT-01", "LOT-02", "LOT-05", "LOT-08"):
            self.assertEqual(link.loc[lot, "status"], "ok", lot)
        self.assertIn("дубликат", link.loc["LOT-05", "reason"])

    def test_link_kpi_is_partial_not_reliable(self):
        k = self.snap().kpi["link"]
        self.assertEqual((k["n_total"], k["n_reliable"], k["n_unreliable"]), (8, 5, 3))
        self.assertEqual(k["reliability"], "partial")  # ошибка заготовки не делает связку «достоверной»
        self.assertAlmostEqual(k["pct"], 370 / 420)

    # --- главный сценарий: исправили факт - связанный расчёт меняется, независимый - нет
    def test_fix_changes_linked_but_not_independent(self):
        before = self.snap()
        docs_before = before.kpi["docs"].copy()
        weld_before = before.kpi["weld"].copy()

        self.fix_fact("P-003", 100)  # 1200 -> 100: опечатка исправлена
        after = self.snap()

        self.assertEqual(after.link.set_index("batch_id").loc["LOT-03", "status"], "ok")
        self.assertEqual(after.kpi["link"]["n_reliable"], 6)
        self.assertAlmostEqual(after.kpi["link"]["pct"], 470 / 550)
        self.assertEqual(after.kpi["blanks"]["fact"], 470)
        self.assertEqual(after.kpi["blanks"]["n_bad"], 3)
        self.assertEqual(after.kpi["docs"], docs_before)  # независимый участок не изменился
        self.assertEqual(after.kpi["weld"], weld_before)

    def test_fixing_all_errors_makes_link_reliable(self):
        self.fix_fact("P-003", 100)
        self.fix_fact("P-004", 95)
        self.fix_fact("P-007", 100)
        df = db.load_df("blanks")
        rids = df["rid"].tolist()
        dup_rid = df[df["record_id"] == "P-005"]["rid"].tolist()[1]
        db.apply_changes("blanks", rids, {}, [], [rids.index(dup_rid)], "тест")  # удалить дубль
        self.fix_fact("P-006", 10)
        k = self.snap().kpi["link"]
        self.assertEqual(k["n_reliable"], 8)
        self.assertEqual(k["reliability"], "ok")

    def test_welding_error_does_not_make_link_reliable(self):
        df = db.load_df("weld")
        rids = df["rid"].tolist()
        db.apply_changes("weld", rids, {0: {"Потребность, шт": None}}, [], [], "тест")
        link = self.snap().link.set_index("batch_id")
        self.assertEqual(link.loc["LOT-01", "status"], "unreliable")
        self.assertEqual(self.snap().kpi["link"]["reliability"], "partial")

    def test_no_link_for_documents(self):
        issues = self.snap().issues
        docs_rows = issues[issues["section"] == "docs"]
        self.assertTrue(all("независим" in t for t in docs_rows["Что затронуто"]))
        self.assertNotIn("batch_id", db.load_df("docs").columns)

    def test_period_filter(self):
        s = self.snap(D(2026, 10, 9), D(2026, 10, 9))
        self.assertEqual(s.kpi["blanks"]["n_total"], 2)
        self.assertEqual(s.kpi["docs"]["n_total"], 1)
        self.assertEqual(list(s.link["batch_id"]), ["LOT-05"])

    # --- сохранение
    def test_changes_survive_restart_and_are_logged(self):
        self.fix_fact("P-003", 100, author="Иван")
        db_file = db.DB_PATH
        db.DB_PATH = db_file  # «перезапуск»: заново открываем тот же файл
        df = db.load_df("blanks")
        self.assertEqual(df[df["record_id"] == "P-003"]["fact_qty"].iloc[0], 100)
        log = db.recent_changes("blanks")
        self.assertEqual(log.iloc[0]["Кто"], "Иван")
        self.assertEqual(float(log.iloc[0]["Было"]), 1200)

    def test_edit_in_one_section_does_not_touch_others(self):
        w_before, d_before = db.load_df("weld"), db.load_df("docs")
        self.fix_fact("P-003", 100)
        self.assertTrue(db.load_df("weld").equals(w_before))
        self.assertTrue(db.load_df("docs").equals(d_before))

    def test_add_row_gets_next_record_id(self):
        df = db.load_df("blanks")
        db.apply_changes(
            "blanks",
            df["rid"].tolist(),
            {},
            [{"Дата": "2026-10-15", "Партия": "LOT-09", "План, шт": 100, "Факт, шт": 95}],
            [],
            "тест",
        )
        self.assertIn("P-009", set(db.load_df("blanks")["record_id"]))

    def test_reports_build(self):
        s = self.snap()
        data = report.build_excel(s)
        self.assertTrue(data.startswith(b"PK"))  # xlsx - это zip
        self.assertIn("Период", report.build_csv(s).decode("utf-8-sig"))


class ImportTest(unittest.TestCase):
    """Исходные Excel-файлы нужны только для первой загрузки."""

    SRC = Path(os.environ["USERPROFILE"]) / "Desktop" / "Тестовое задание"

    def test_import_then_remove_files(self):
        names = {
            "blanks": "01_Заготовка.xlsx",
            "weld": "02_Потребность_сварки.xlsx",
            "docs": "03_Документация.xlsx",
        }
        if not all((self.SRC / n).exists() for n in names.values()):
            self.skipTest("исходные xlsx не найдены")
        tmp = Path(tempfile.mkdtemp())
        try:
            db.DB_PATH = tmp / "app.db"
            files = {}
            for section, name in names.items():
                shutil.copy(self.SRC / name, tmp / name)
                files[section] = tmp / name
            counts = db.import_all(files)
            self.assertEqual(counts, {"blanks": 9, "weld": 8, "docs": 6})
            for path in files.values():
                path.unlink()  # убираем исходники
            s = metrics.snapshot(*FULL)
            self.assertEqual(s.kpi["blanks"]["fact"], 370)
            with self.assertRaises(RuntimeError):
                db.import_all(files)  # повторная загрузка запрещена
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
