"""Премиальный HTML/CSS/JS шаблон таблицы для Telegram Mini App."""

from __future__ import annotations

import json

from services.table_markdown import normalize_table_rows
from services.table_number_parse import parse_table_number
from services.telegram_safe_text import _escape_telegram_html

_PAGE_SIZE = 15
_MONEY_HINTS = ("руб", "₽", "выруч", "доход", "сумм", "перечисл", "прибыл", "amount", "revenue")
_QTY_HINTS = ("шт", "кол", "qty", "count", "единиц", "колич")


def _detect_numeric_columns(headers: list[str], data_rows: list[list[str]]) -> list[int]:
    indices: list[int] = []
    for idx, header in enumerate(headers):
        low = (header or "").lower()
        if any(h in low for h in _MONEY_HINTS) or any(h in low for h in _QTY_HINTS):
            indices.append(idx)
            continue
        nums = 0
        for row in data_rows[: min(len(data_rows), 40)]:
            if idx < len(row) and parse_table_number(row[idx]) is not None:
                nums += 1
        if nums >= max(1, min(len(data_rows), 40) // 2):
            indices.append(idx)
    return indices


def _json_script_safe(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def build_mini_app_table_html(
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> str:
    """Полная HTML-страница: тема Telegram, sticky header, поиск и пагинация."""
    matrix = normalize_table_rows(rows)
    display_title = _escape_telegram_html((title or "Отчёт NeuroMule").strip())
    if not matrix:
        return _empty_document(display_title)

    headers = matrix[0]
    data_rows = matrix[1:]
    numeric_cols = _detect_numeric_columns(headers, data_rows)
    headers_json = _json_script_safe(headers)
    rows_json = _json_script_safe(data_rows)
    numeric_cols_json = _json_script_safe(numeric_cols)

    header_cells = "".join(
        f"<th scope=\"col\">{_escape_telegram_html(str(h))}</th>" for h in headers
    )

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>{display_title}</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    :root {{
      color-scheme: light dark;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      margin: 0;
      padding: 12px;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      font-size: 15px;
      line-height: 1.45;
      background-color: var(--tg-theme-bg-color, #17212b);
      color: var(--tg-theme-text-color, #f5f5f5);
      -webkit-font-smoothing: antialiased;
    }}
    .dashboard {{
      max-width: 960px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .toolbar {{
      display: flex;
      flex-direction: column;
      gap: 10px;
    }}
    .toolbar h1 {{
      margin: 0;
      font-size: 1.15rem;
      font-weight: 700;
      color: var(--tg-theme-text-color, #f5f5f5);
    }}
    .search-input {{
      width: 100%;
      padding: 12px 14px;
      border-radius: 12px;
      border: 1px solid var(--tg-theme-hint-color, rgba(255,255,255,0.2));
      background: var(--tg-theme-secondary-bg-color, #232e3c);
      color: var(--tg-theme-text-color, #f5f5f5);
      font-size: 15px;
      outline: none;
    }}
    .search-input::placeholder {{
      color: var(--tg-theme-hint-color, #8b9bab);
    }}
    .search-input:focus {{
      border-color: var(--tg-theme-button-color, #5288c1);
      box-shadow: 0 0 0 2px rgba(82, 136, 193, 0.25);
    }}
    .pager {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      font-size: 13px;
      color: var(--tg-theme-hint-color, #8b9bab);
    }}
    .pager-actions {{
      display: flex;
      gap: 8px;
    }}
    .pager-btn {{
      padding: 8px 14px;
      border-radius: 10px;
      border: none;
      background: var(--tg-theme-button-color, #5288c1);
      color: var(--tg-theme-button-text-color, #ffffff);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }}
    .pager-btn:disabled {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    .table-card {{
      background: var(--tg-theme-secondary-bg-color, #232e3c);
      border-radius: 16px;
      box-shadow: 0 8px 28px rgba(0, 0, 0, 0.22);
      overflow: hidden;
    }}
    .table-wrap {{
      overflow: auto;
      max-height: min(72vh, 640px);
      -webkit-overflow-scrolling: touch;
    }}
    table {{
      table-layout: auto;
      width: 100%;
      border-collapse: collapse;
    }}
    thead th {{
      position: sticky;
      top: 0;
      z-index: 2;
      padding: 12px 10px;
      text-align: left;
      font-weight: 700;
      font-size: 13px;
      letter-spacing: 0.02em;
      background: var(--tg-theme-secondary-bg-color, #232e3c);
      border-bottom: 1px solid var(--tg-theme-hint-color, rgba(255,255,255,0.12));
      white-space: nowrap;
    }}
    tbody td {{
      padding: 11px 10px;
      border-bottom: 1px solid var(--tg-theme-hint-color, rgba(255,255,255,0.08));
      vertical-align: top;
    }}
    tbody tr:last-child td {{
      border-bottom: none;
    }}
    tbody tr {{
      transition: background-color 0.15s ease;
    }}
    tbody tr:active,
    tbody tr:hover {{
      background: var(--tg-theme-bg-color, rgba(23, 33, 43, 0.55));
    }}
    td.num,
    th.num {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .empty-state {{
      padding: 24px 16px;
      text-align: center;
      color: var(--tg-theme-hint-color, #8b9bab);
    }}
  </style>
</head>
<body>
  <div class="dashboard">
    <header class="toolbar">
      <h1>{display_title}</h1>
      <input
        id="search"
        class="search-input"
        type="search"
        placeholder="🔍 Поиск по таблице…"
        autocomplete="off"
        enterkeyhint="search"
      />
      <div class="pager">
        <span id="pager-info">—</span>
        <div class="pager-actions">
          <button type="button" class="pager-btn" id="prev-btn" disabled>← Назад</button>
          <button type="button" class="pager-btn" id="next-btn" disabled>Вперёд →</button>
        </div>
      </div>
    </header>
    <div class="table-card">
      <div class="table-wrap">
        <table id="data-table">
          <thead><tr>{header_cells}</tr></thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
  <script>
  (function () {{
    const PAGE_SIZE = {_PAGE_SIZE};
    const HEADERS = {headers_json};
    const ALL_ROWS = {rows_json};
    const NUMERIC_COLS = new Set({numeric_cols_json});

    const tbody = document.getElementById("tbody");
    const searchEl = document.getElementById("search");
    const pagerInfo = document.getElementById("pager-info");
    const prevBtn = document.getElementById("prev-btn");
    const nextBtn = document.getElementById("next-btn");

    let filtered = ALL_ROWS.slice();
    let page = 1;

    HEADERS.forEach(function (_, idx) {{
      const th = document.querySelectorAll("thead th")[idx];
      if (th && NUMERIC_COLS.has(idx)) th.classList.add("num");
    }});

    function escapeHtml(text) {{
      return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}

    function rowMatches(row, query) {{
      if (!query) return true;
      const q = query.toLowerCase();
      for (let i = 0; i < row.length; i++) {{
        if (String(row[i] ?? "").toLowerCase().indexOf(q) !== -1) return true;
      }}
      return false;
    }}

    function applyFilter() {{
      const q = (searchEl.value || "").trim();
      filtered = q ? ALL_ROWS.filter(function (row) {{ return rowMatches(row, q); }}) : ALL_ROWS.slice();
      page = 1;
      render();
    }}

    function render() {{
      const total = filtered.length;
      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      if (page > pages) page = pages;
      const start = (page - 1) * PAGE_SIZE;
      const slice = filtered.slice(start, start + PAGE_SIZE);

      if (!slice.length) {{
        tbody.innerHTML =
          '<tr><td class="empty-state" colspan="' + HEADERS.length + '">Ничего не найдено</td></tr>';
      }} else {{
        tbody.innerHTML = slice.map(function (row) {{
          const cells = HEADERS.map(function (_, idx) {{
            const raw = idx < row.length ? row[idx] : "";
            const cls = NUMERIC_COLS.has(idx) ? ' class="num"' : "";
            return "<td" + cls + ">" + escapeHtml(raw) + "</td>";
          }}).join("");
          return "<tr>" + cells + "</tr>";
        }}).join("");
      }}

      const from = total ? start + 1 : 0;
      const to = Math.min(start + PAGE_SIZE, total);
      pagerInfo.textContent = total
        ? "Строки " + from + "–" + to + " из " + total + " · стр. " + page + "/" + pages
        : "Нет данных";
      prevBtn.disabled = page <= 1;
      nextBtn.disabled = page >= pages;
    }}

    let debounceTimer = null;
    searchEl.addEventListener("input", function () {{
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(applyFilter, 120);
    }});

    prevBtn.addEventListener("click", function () {{
      if (page > 1) {{ page -= 1; render(); }}
    }});
    nextBtn.addEventListener("click", function () {{
      page += 1;
      render();
    }});

    if (window.Telegram && window.Telegram.WebApp) {{
      const tg = window.Telegram.WebApp;
      tg.ready();
      tg.expand();
    }}

    render();
  }})();
  </script>
</body>
</html>"""


def _empty_document(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <script src="https://telegram.org/js/telegram-web-app.js"></script>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      font-family: sans-serif;
      background-color: var(--tg-theme-bg-color, #17212b);
      color: var(--tg-theme-text-color, #f5f5f5);
    }}
  </style>
</head>
<body><p>Нет данных для отображения.</p></body>
</html>"""


def markdown_table_to_html_document(
    rows: list[list[str]],
    *,
    title: str | None = None,
) -> str:
    """Совместимость: премиальный Mini App HTML вместо голой ``<table>``."""
    return build_mini_app_table_html(rows, title=title)
