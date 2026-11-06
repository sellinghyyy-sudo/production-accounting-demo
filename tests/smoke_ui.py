"""Дымовая проверка интерфейса без браузера (встроенный тестовый режим Streamlit).
Запуск:  python tests/smoke_ui.py
Использует временную базу, рабочую data/app.db не трогает."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["APP_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "smoke.db")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402


def check(at, where):
    if at.exception:
        print("FAIL", where, [e.value for e in at.exception])
        sys.exit(1)
    print("ok  ", where)


at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
check(at, "экран первичной загрузки")
at.button[0].click().run()
check(at, "первичная загрузка из import/")

for screen in ["Панель директора", "Заготовка", "Потребность сварки", "Документация", "Администрирование"]:
    at.sidebar.radio[0].set_value(screen).run()
    check(at, screen)

at.sidebar.radio[0].set_value("Панель директора").run()
texts = " ".join(m.value for m in at.markdown) + " ".join(e.value for e in at.error)
print("ошибки на панели:", [e.value[:90] for e in at.error])
print("карточек (markdown с процентами):", sum("%" in m.value for m in at.markdown))
print("ГОТОВО")
