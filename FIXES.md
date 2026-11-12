# FIXES.md — список правок CheckStock

Актуально на 12.11.2026. Делать по порядку, после каждой — `docker compose up -d --build` + проверка.

---

## FIX 1. Таблица ломается при 4+ складах — нет горизонтального скролла

**Проблема:** на скриншоте при 4+ складах колонки сжимаются, таблица не влезает в экран, нет прокрутки.

**Файлы:** `app/templates/index.html`, `app/templates/base.html`.

**Что делать:**

1. В `index.html`, в карточке «Остатки по товарам и складам», таблица должна быть **обёрнута** в `<div class="table-responsive">`:

```html
<div class="table-responsive">
  <table class="table table-vcenter" id="stockTable">
    <thead id="stockHead"></thead>
    <tbody id="stockBody"></tbody>
  </table>
</div>


FIX 2. Перемещение больше остатка — ошибка 409 не показывается
Проблема: при попытке переместить 999 шт. с Севера (где 9) на Юг — ничего не происходит, ошибка не видна. Бэкенд возвращает 409, но JS не показывает.

Причина: в index.html нет <div id="opsError"> — а app.js пишет ошибку именно туда (showError('opsError', e.message) в mkOpBtn).

Файлы: app/templates/index.html.

Что делать:

В карточке «Операции» найди <div class="card-body"> и добавь <div id="opsError"> перед <div id="opsList">:

html
<div class="card-body">
  <div id="opsError" class="alert alert-danger error"></div>
  <div id="opsList"><div class="loading">Загрузка…</div></div>
</div>
Готово когда: при перемещении 999 → появляется красная плашка «На складе доступно X, нужно 999. Хватает на: Юг».

FIX 3. дост. дублируется при нулевом резерве (визуальный шум)
Проблема: при reserved = 0 в ячейке видно 9 и дост. 9 — дублируется, выглядит как ошибка.

Решение: показывать дост. только если reserved > 0 (то есть on_hand ≠ available).

Файлы: app/static/js/app.js, функция loadStock.

Что делать:

Найди блок:

javascript
td.appendChild(el('div', { text: String(onHand) }));
if (reserved > 0) {
  td.appendChild(el('small', { class: 'text-secondary', text: 'рез. ' + reserved }));
}
td.appendChild(el('div', {},
  el('small', {
    class: available > 0 ? 'text-success' : 'text-secondary',
    text: 'дост. ' + available,
  })));
Замени на:

javascript
td.appendChild(el('div', { text: String(onHand) }));
if (reserved > 0) {
  td.appendChild(el('small', { class: 'text-secondary', text: 'рез. ' + reserved }));
  td.appendChild(el('div', {},
    el('small', {
      class: available > 0 ? 'text-success' : 'text-secondary',
      text: 'дост. ' + available,
    })));
}
То есть: дост. — внутри if (reserved > 0), а не снаружи.

Готово когда: при reserved = 0 в ячейке только число (например, 9), без дост.. При резерве 3 — 9 / рез. 3 / дост. 6.

⚠️ Если хочешь оставить дост. всегда — не делай FIX 3. Это не баг, а стиль.

FIX 4. Проверка: бэкенд возвращает 409 при перемещении больше остатка?
Проблема: на скриншоте не видно, что бэкенд возвращает 409. Возможно, POST /transfers/{id}/send падает с 500 или пропускает.

Что делать:

Открой F12 → Network

Создай перемещение 999 (создание — POST /transfers)

Нажми «Отправить» — POST /transfers/{id}/send

Статус:

201 — создалось, но send вернул 409 — ок, FIX 2 покажет ошибку.

500 — баг в бэкенде (SQL), пришли docker compose logs app --tail 20.

201 и на send — бэкенд не проверяет остаток — критичный баг в app/transfers.py, надо добавить проверку available.

Пришли: скриншот Network с этим запросом.

FIX 5. Тесты после правок
Что делать:

powershell
docker compose run --rm app pytest -q
Готово когда: 13 passed.

FIX 6. Коммит и чистый клон
Что делать:

powershell
git add -A
git status   # нет .venv/.idea/__pycache__
git commit -m "Fix: скролл таблицы, opsError, компактные ячейки"
git push

# Чистый клон
cd C:\temp
Remove-Item -Recurse -Force checkstock-final -ErrorAction SilentlyContinue
git clone https://github.com/leniksergey/checkstock-test checkstock-final
cd checkstock-final
docker compose up --build
docker compose run --rm app pytest -q
Готово когда: чистый клон → 13 passed, / открывается, таблица матрицей.

FIX 7. Видео 3–5 минут
После всех фиксов — записать. Сценарий:

0:00–0:30 — задача и стек.

0:30–2:00 — сквозной сценарий:

приход → резерв FBS → перемещение → отправка/приёмка

покажи матрицу «товар × склад»

2:00–3:30 — нехватка (409 с подсказкой), удаление ФФ (архивация), повторное подтверждение.

3:30–4:30 — решения: журнал, виртуальные места, блокировки, Jinja2. Где помог ИИ, как проверил.

4:30–5:00 — что не сделано (доставка FBS клиенту, авторизация).

С голосом, 3–5 минут.

FIX 8. Отправка в компанию
После видео:

text
Здравствуйте!

Отправляю выполненное тестовое задание CheckStock.

Репозиторий: https://github.com/leniksergey/checkstock-test
Видео (3–5 мин, с голосом): [ссылка]

Запуск: docker compose up --build → http://localhost:8000
Тесты: docker compose run --rm app pytest -q → 13 passed

Стек: Python, FastAPI, PostgreSQL. Учёт на журнале движений.
Реализованы: приход, резерв FBS, перемещения, отправки FBS и FBO,
нехватка (409), мягкое удаление, идемпотентность.

ИИ: [2–3 предложения — где помог, как проверил].

Готов обсудить детали и проект Raketa.
До 17 ноября — сейчас 12-е, у тебя 5 дней.

Чек-лист
□ FIX 1 — скролл таблицы
□ FIX 2 — opsError в index.html
□ FIX 3 — дост. только при резерве (или не делать, если хочешь оставить всегда)
□ FIX 4 — проверка, что бэкенд отдаёт 409
□ FIX 5 — 13 passed
□ FIX 6 — коммит + чистый клон
□ FIX 7 — видео
□ FIX 8 — отправка
text

---

## Что делать **сейчас**

1. **Создай `FIXES.md`** (команда выше) и вставь текст.
2. **Отдай агенту** (Opencode / Cursor):
   > «Прочитай `FIXES.md`. Выполни FIX 1, 2, 3. После каждого — покажи diff. После всех — `docker compose up -d --build` и скриншот. Не трогай бэкенд.»
3. **Проверь FIX 4** сам — F12 → Network → скриншот запроса `POST /transfers/{id}/send` при 999.
4. **Пришли** скриншот **Network** и **результат** после фиксов.

---

## Приоритет

| # | Что | Срочность |
|---|---|---|
| **FIX 2** | `opsError` | 🔴 **Критично** — 25% оценки (нехватка) |
| **FIX 1** | скролл | 🔴 **Критично** — при 20 складах ломается |
| **FIX 4** | проверка 409 | 🔴 **Критично** — если бэкенд не отдаёт 409, задание не выполнено |
| **FIX 3** | `дост.` | 🟡 UX — не блокер, но улучшает вид |
| FIX 5–8 | тесты, клон, видео, письмо | 🟢 Финал |

**Начинай с FIX 2 и FIX 1** — это **критично**. Потом **FIX 4** (проверка). **FIX 3** — по желанию.

Пришли **скриншот Network** после попытки перемещения 999 — и я скажу, **отдаёт ли бэкенд 409**. Если **да** — FIX 2 покажет ошибку. Если **нет** — **критичный баг** в `transfers.py`, надо чинить.
