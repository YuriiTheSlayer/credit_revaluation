"""Корпоративная палитра Comfy — единые токены для UI и Excel-экспорта.

Значения взяты из корпоративного фронтенда ``promo-self-service``
(``theme.js``/``styles.css``, адаптация брендбука): зелёный — основные
действия, оранжевый — акцент/CTA и фирменная «оранжевая точка», жёлтый
зарезервирован под предупреждения (никогда как фон), заголовки ключевых
поверхностей — UPPERCASE с трекингом. Шрифт — Inter с системным фолбэком
(приложение офлайн, Google Fonts не подгружаем).

Модуль без зависимостей: используется и в UI (CSS-переменные), и в экспорте
(xlsxwriter).
"""

# основная гамма (брендбук → promo-self-service/theme.js)
GREEN = "#2ABD13"          # фирменный зелёный: primary-действия, серия Comfy
GREEN_DARK = "#1F8E0E"     # ховеры, «выигрываем»
GREEN_TINT = "#E8F6E5"     # greenSoft: подложки выделения
GREEN_ZEBRA = "#F2F2F2"    # зебра строк в Excel (корп. таблицы — нейтральные)

GRAPHITE = "#2D2D2D"       # charcoal: основной текст
DARK_GRAY = "#4A4A4A"      # вторичный текст, шапки таблиц
MUTED = "#9B9B9B"          # midGray: подписи, eyebrow-заголовки
NEAR_BLACK = "#1A1A1A"     # тёмные поверхности (сайдер в корп. приложениях)
BG = "#F5F5F5"             # offWhite: фон приложения
SURFACE = "#FFFFFF"        # карточки/поверхности
OUTLINE = "#E8E8E8"        # lightGray: рамки

# акценты
ORANGE = "#FC5E0F"         # CTA-акцент и фирменная «оранжевая точка»
ORANGE_TINT = "#FFE9DD"    # orangeSoft
RED = "#E02424"            # ошибки/«проигрываем»
RED_TINT = "#FBE3E3"       # redSoft
BLUE_SOFT = "#E1EBFD"

#: серии графиков: Comfy всегда первым — фирменным зелёным
SERIES = [
    GREEN,        # Comfy
    "#4A4A4A",    # графит
    ORANGE,       # оранжевый акцент
    "#3B82F6",    # синий
    "#8E44AD",    # фиолетовый
    "#0E9F9F",    # бирюзовый
    "#B7791F",    # охра
    RED,          # красный
]

#: фирменный шрифтовой стек (Inter, если установлен; офлайн-фолбэки)
FONT_STACK = ("'Inter', 'Segoe UI', Arial, system-ui, -apple-system, "
              "Roboto, sans-serif")

APP_TITLE = "Payment Terms Dashboard"
APP_SUBTITLE = "Сроки рассрочки: Comfy vs конкуренты"
