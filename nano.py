"""
CyberShoke Player Monitor v5.1
===============================
Мониторит серверы CyberShoke Duels.
- Прокручивает страницу до конца, чтобы найти ВСЕ серверы.
- Использует один браузер для скорости.
- Ищет карточки по тексту (надежнее).
- Защита от падений Chrome (JSON сериализация).

Зависимости:
    pip install selenium requests webdriver-manager

Запуск:
    python nano.py
"""

from __future__ import annotations

import os
# Принудительно UTF-8 на Windows ДО любых других импортов
os.environ.setdefault("PYTHONUTF8", "1")

import time
import json
import sys
import threading
import traceback
import requests
import telebot
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
)

# ── UTF-8 stdout (fix UnicodeEncodeError на Windows) ────────
import io
try:
    if hasattr(sys.stdout, 'buffer'):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer'):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except Exception:
    pass

# ── ANSI на Windows ─────────────────────────────────────────
if sys.platform == "win32":
    try:
        from colorama import init as _ci
        _ci()
    except ImportError:
        import ctypes
        _k = ctypes.windll.kernel32
        _k.SetConsoleMode(_k.GetStdHandle(-11), 7)

# ============================================================
#   НАСТРОЙКИ
# ============================================================

FACEIT_API_KEY = "40cff481-5d7f-4dba-8930-3dfca35bbc89"
CYBERSHOKE_URL = "https://cybershoke.net/ru/cs2/servers/duels"

# Настройки Telegram
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8012263704:AAFw3ryC0NuNsGcCRbftZG_k5wL7AdHG_nA")
SUBSCRIBERS_FILE = os.environ.get("SUBSCRIBERS_FILE", "subscribers.json")
os.makedirs(os.path.dirname(SUBSCRIBERS_FILE), exist_ok=True) if os.path.dirname(SUBSCRIBERS_FILE) else None

# Дефолтные настройки (если пользователь не задал свои)
DEFAULT_MIN_ELO   = 3000
DEFAULT_MIN_LEVEL = 10
DEFAULT_CATEGORY  = "ONLY MIRAGE"

AVAILABLE_CATEGORIES = ["ONLY MIRAGE", "ONLY DUST2", "ARENA MAPS", "ALL MAPS"]

CHECK_INTERVAL = 5 * 60   # 5 минут


# ============================================================
#   WEBDRIVER
# ============================================================

def build_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    # На Linux (Docker) используем system chromedriver если есть, иначе selenium-manager
    import shutil
    chrome_bin = os.environ.get("CHROME_BIN") or shutil.which("chromium") or shutil.which("google-chrome")
    if chrome_bin:
        opts.binary_location = chrome_bin
    system_chromedriver = shutil.which("chromedriver")
    if system_chromedriver:
        drv = webdriver.Chrome(service=Service(system_chromedriver), options=opts)
    else:
        # selenium >= 4.6 имеет встроенный selenium-manager — сам скачает chromedriver
        drv = webdriver.Chrome(options=opts)

    drv.set_page_load_timeout(30)
    drv.set_script_timeout(15)
    drv.execute_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
    )
    return drv


# ============================================================
#   ШАГ 1: Прокрутка и сбор серверов
# ============================================================

def select_category(driver: webdriver.Chrome, category: str = "ALL MAPS") -> None:
    """Выбирает нужную категорию в фильтрах CyberShoke.
    Учитывает, что UI работает как мультиселект с чекбоксами —
    сначала снимает все активные галочки, потом ставит нужную."""
    print(f"    [~] Выбираем категорию '{category}' в фильтрах...")
    try:
        # Открываем дропдаун «Категория»
        cat_dropdown = driver.find_element(
            By.XPATH,
            "//div[contains(@class,'filter-section') and .//span[contains(text(),'Категория')]] | "
            "//*[contains(@class,'select') and .//*[contains(text(),'Категория')]] | "
            "//span[contains(text(),'Категория')]/ancestor::*[contains(@class,'filter')][1]"
        )
        cat_dropdown.click()
        time.sleep(1.5)

        # --- Снимаем все активные чекбоксы ---
        unchecked = driver.execute_script("""
            let count = 0;
            const inputs = document.querySelectorAll(
                '.filter-section input[type=checkbox]:checked, ' +
                '[class*="select"] input[type=checkbox]:checked, ' +
                '[class*="dropdown"] input[type=checkbox]:checked'
            );
            inputs.forEach(el => { el.click(); count++; });
            return count;
        """)
        if unchecked > 0:
            time.sleep(0.5)

        # --- Кликаем нужную опцию ---
        if category == "ALL MAPS":
            # ALL MAPS = ничего не выбрано → сайт показывает все серверы.
            # Просто закрываем дропдаун без клика на опцию.
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        else:
            option = driver.find_element(
                By.XPATH,
                f"//div[normalize-space(text())='{category}'] | "
                f"//span[normalize-space(text())='{category}'] | "
                f"//li[normalize-space(text())='{category}']"
            )
            option.click()

        time.sleep(3)
        print(f"    [+] Категория '{category}' выбрана.")
    except Exception as e:
        print(f"    [!] Не удалось выбрать категорию '{category}': {e}")

# Обратная совместимость
select_category_only_mirage = lambda d: select_category(d, "ONLY MIRAGE")


def scroll_and_collect(driver: webdriver.Chrome) -> list[dict]:
    """Прокручивает страницу вниз И одновременно собирает сервера в DOM.
    Решает проблему виртуализированного ленивого списка: карточки вне видимасти могут выгружаться из DOM."""
    print("    [~] Прокручиваем страницу и собираем сервера...")
    seen: dict[str, dict] = {}   # text -> server dict

    def _collect_visible():
        """JS: собирает все видимые в DOM карточки и возвращает список."""
        return driver.execute_script(r"""
            // Получаем категорию из класса .servers-grid родителя карточки
            // Класс выглядит как: "servers-grid servers-grid-ONLY DUST2-1"
            function getCategory(cardEl) {
                let el = cardEl;
                while (el && !el.classList.contains('home-body-servers')) {
                    el = el.parentElement;
                }
                if (!el) return 'UNKNOWN';
                const grid = el.parentElement;
                if (!grid) return 'UNKNOWN';
                const cls = grid.className || '';
                // Ищем паттерн: servers-grid-<CATEGORY>-<N>
                const m = cls.match(/servers-grid-([A-Z][A-Z0-9 ]+?)-\d+/);
                return m ? m[1].trim() : 'UNKNOWN';
            }

            // Онлайн из .block-servers-group-info внутри той же карточки .home-body-servers
            // Формат: "6/16 | duels_dust2_1x1"
            function getOnline(cardEl) {
                let el = cardEl;
                while (el && !el.classList.contains('home-body-servers')) {
                    el = el.parentElement;
                }
                if (!el) return -1;
                const info = el.querySelector('.block-servers-group-info');
                if (info) {
                    const m = info.textContent.match(/(\d+)\s*[\/|–\-]\s*(\d+)/);
                    if (m) return parseInt(m[1]);
                }
                return -1;
            }

            const result = [];
            const cards = document.querySelectorAll('.block-servers-name');
            for (let c of cards) {
                result.push({
                    text: c.innerText.trim(),
                    online: getOnline(c),
                    category: getCategory(c)
                });
            }
            return result;
        """)

    # Первичная отрисовка
    time.sleep(3.0)

    # Скроллим все .servers-grid контейнеры (родители карточек)
    # а не .home-body-servers (это сами карточки)
    STEP = 400          # px за шаг
    STEP_PAUSE = 0.25   # пауза между шагами
    BOTTOM_PAUSE = 2.0  # пауза когда достигли конца (ждём подгрузки)
    stale_count = 0

    while True:
        # Текущая позиция и максимум - по всем grid-контейнерам или window
        pos_info = driver.execute_script("""
            // Ищем .servers-grid с карточками
            const grids = [...document.querySelectorAll('[class*="servers-grid-"]')];
            // Берём самый высокий (обычно один при фильтре по категории)
            let maxGrid = null, maxSH = 0;
            for (const g of grids) {
                if (g.scrollHeight > maxSH) { maxSH = g.scrollHeight; maxGrid = g; }
            }
            if (maxGrid && maxGrid.scrollHeight > maxGrid.clientHeight + 50) {
                return {
                    top: maxGrid.scrollTop,
                    max: maxGrid.scrollHeight - maxGrid.clientHeight,
                    cards: document.querySelectorAll('.block-servers-name').length,
                    useGrid: true
                };
            }
            return {
                top: window.scrollY,
                max: document.body.scrollHeight - window.innerHeight,
                cards: document.querySelectorAll('.block-servers-name').length,
                useGrid: false
            };
        """)

        current_top = pos_info["top"]
        max_scroll   = pos_info["max"]
        card_count   = pos_info["cards"]

        # Собираем видимые карточки
        for item in (_collect_visible() or []):
            key = item["text"]
            if key and key not in seen:
                seen[key] = item

        if current_top >= max_scroll - 10:
            time.sleep(BOTTOM_PAUSE)
            new_cards = driver.execute_script(
                "return document.querySelectorAll('.block-servers-name').length"
            )
            if new_cards <= card_count:
                stale_count += 1
                if stale_count >= 3:
                    break
            else:
                stale_count = 0
        else:
            stale_count = 0

        # Шаг вниз по grid-контейнеру или window
        driver.execute_script(f"""
            const grids = [...document.querySelectorAll('[class*="servers-grid-"]')];
            let moved = false;
            for (const g of grids) {{
                if (g.scrollHeight > g.clientHeight + 50) {{
                    g.scrollTop += {STEP};
                    moved = true;
                }}
            }}
            if (!moved) window.scrollBy(0, {STEP});
        """)
        time.sleep(STEP_PAUSE)

    # Финальный сбор: гарантированно берём всё, что осталось в DOM
    for item in (_collect_visible() or []):
        key = item["text"]
        if key and key not in seen:
            seen[key] = item

    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)

    result = list(seen.values())
    # Фильтруем: онлайн > 0 ИЛИ онлайн неизвестен (-1) —
    # некоторые категории (напр. ARENA MAPS) имеют другую HTML-структуру,
    # и онлайн просто не парсится. В этом случае пускаем сервер пройти до модалки.
    result_to_check = [d for d in result if d["online"] > 0 or d["online"] == -1]
    result_zero     = [d for d in result if d["online"] == 0]
    print(f"    [+] Собрано серверов: {len(result)} (проверяем: {len(result_to_check)}, пустых online=0: {len(result_zero)})")
    return result_to_check


def scroll_to_bottom(driver: webdriver.Chrome) -> None:
    """[deprecated] Используйте scroll_and_collect. Оставлено для обратной совместимости."""
    pass


def collect_server_list(driver: webdriver.Chrome) -> list[dict]:
    """[deprecated] Используйте scroll_and_collect. Оставлено для обратной совместимости."""
    return scroll_and_collect(driver)


# ============================================================
#   ШАГ 2: Клик и парсинг модалки
# ============================================================

def fetch_server_players(driver: webdriver.Chrome, card_text: str) -> tuple[list[dict], str, str]:
    """Ищет карточку по тексту, кликает, парсит модалку, закрывает модалку."""
    
    # 1. Ищем карточку и кликаем
    card_el = driver.execute_script("""
        // Ищем карточку по всем категориям
        const cards = document.querySelectorAll('.block-servers-name');
        for (let c of cards) {
            if (c.innerText.trim() === arguments[0]) {
                c.scrollIntoView({block: 'center'});
                return c;
            }
        }
        return null;
    """, card_text)

    if not card_el:
        print("    [!] Карточка не найдена на странице (возможно, сервер исчез)")
        return [], "", ""

    time.sleep(0.5)
    ActionChains(driver).move_to_element(card_el).click().perform()

    # 2. Ждем модалку
    try:
        WebDriverWait(driver, 6).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".server-modal__table, [class*='modalShowing_true']"))
        )
    except TimeoutException:
        # Если не открылась, жмем ESC на всякий случай
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        return [], "", ""

    time.sleep(0.5)

    # 3. Парсим данные (возвращаем JSON строку, чтобы избежать ошибки десериализации Chrome)
    result_json = driver.execute_script(r"""
        const nameEl = document.querySelector('.server-modal__name');
        const serverName = nameEl ? nameEl.innerText.trim() : '';

        const ipEl = document.querySelector('.server-modal__ip');
        let serverIp = '';
        if (ipEl) {
            const m = ipEl.innerText.match(/(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5})/);
            if (m) serverIp = m[1];
        }

        const table = document.querySelector('.server-modal__table');
        const players = [];
        if (table) {
            const rows = table.querySelectorAll('tr');
            for (const row of rows) {
                const cells = row.querySelectorAll('th');
                if (cells.length < 4) continue;

                const rowText = row.innerText.trim();
                if (/Игрок|Player|Убийства/.test(rowText) && !/\d+:\d+:\d+/.test(rowText))
                    continue;

                const nameCell = row.querySelector('.server-modal__name');
                let nickname = nameCell ? nameCell.innerText.trim() : null;

                if (!nickname) {
                    for (const cell of cells) {
                        const ct = cell.innerText.trim();
                        if (ct && ct.length > 1 && ct.length < 40 && !/^[\d.,:/%\s\-]+$/.test(ct)) {
                            nickname = ct;
                            break;
                        }
                    }
                }

                let steamId = null;
                for (const a of row.querySelectorAll('a[href]')) {
                    const m = a.href.match(/(765\d{14})/);
                    if (m) { steamId = m[1]; break; }
                }

                let faceitLvl = 0;
                const lvlImg = row.querySelector('img.server-modal__lvl');
                if (lvlImg) {
                    const src = lvlImg.src || '';
                    const m2 = src.match(/faceit\/(\d{1,2})\.png/i);
                    if (m2) faceitLvl = parseInt(m2[1]);
                }

                if (nickname) {
                    players.push({nickname: nickname, faceit_level: faceitLvl, steam_id: steamId});
                }
            }
        }

        return JSON.stringify({players: players, serverIp: serverIp, serverName: serverName});
    """)

    # 4. Закрываем модалку
    try:
        driver.execute_script("""
            const closeBtn = document.querySelector('.modal-close');
            if (closeBtn) closeBtn.click();
            const unauthClose = document.querySelector('.server-modal__unauth-close');
            if (unauthClose) unauthClose.click();
        """)
    except:
        pass
    ActionChains(driver).send_keys(Keys.ESCAPE).perform()
    time.sleep(0.3)

    if not result_json:
        return [], "", ""

    try:
        res = json.loads(result_json)
        return res.get("players", []), res.get("serverIp", ""), res.get("serverName", "")
    except json.JSONDecodeError:
        return [], "", ""


# ============================================================
#   FACEIT API
# ============================================================

def check_faceit_by_nickname(nickname: str) -> dict | None:
    url = "https://open.faceit.com/data/v4/players"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, params={"nickname": nickname, "game": "cs2"}, timeout=10)
        if r.status_code != 200:
            return None
        return _parse_faceit(r.json())
    except Exception:
        return None


def check_faceit_by_steam(steam_id: str) -> dict | None:
    url = "https://open.faceit.com/data/v4/players"
    headers = {"Authorization": f"Bearer {FACEIT_API_KEY}"}
    try:
        r = requests.get(url, headers=headers, params={"game": "cs2", "game_player_id": steam_id}, timeout=10)
        if r.status_code != 200:
            return None
        return _parse_faceit(r.json())
    except Exception:
        return None


def _parse_faceit(data: dict) -> dict | None:
    games = data.get("games", {}).get("cs2", {})
    return {
        "nickname":   data.get("nickname", "???"),
        "faceit_url": data.get("faceit_url", "").replace("{lang}", "en"),
        "elo":        int(games.get("faceit_elo", 0)),
        "level":      int(games.get("skill_level", 0)),
    }


# ============================================================
#   TELEGRAM БОТ (ПОДПИСКИ И ФИЛЬТРЫ)
# ============================================================

bot = telebot.TeleBot(TG_BOT_TOKEN)

def load_subscribers() -> dict:
    if os.path.exists(SUBSCRIBERS_FILE):
        try:
            with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Миграция со старого формата (list) на новый (dict)
                if isinstance(data, list):
                    new_data = {}
                    for chat_id in data:
                        new_data[str(chat_id)] = {
                            "min_elo": DEFAULT_MIN_ELO,
                            "min_level": DEFAULT_MIN_LEVEL,
                            "category": DEFAULT_CATEGORY,
                        }
                    save_subscribers(new_data)
                    return new_data
                # Миграция: добавляем поле category если его нет
                changed = False
                for settings in data.values():
                    if "category" not in settings:
                        settings["category"] = DEFAULT_CATEGORY
                        changed = True
                if changed:
                    save_subscribers(data)
                return data
        except Exception:
            return {}
    return {}

def save_subscribers(subs: dict) -> None:
    with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
        json.dump(subs, f, indent=4)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    text = (
        "👋 <b>Привет! Я бот для мониторинга CyberShoke.</b>\n\n"
        "Я ищу сильных игроков по выбранной категории серверов.\n\n"
        "Команды:\n"
        "✅ /subscribe — подписаться на уведомления\n"
        "❌ /unsubscribe — отписаться от уведомлений\n"
        "⚙️ /settings — посмотреть текущие настройки\n"
        "🗺 /set_category [название] — выбрать категорию (ONLY MIRAGE, ONLY DUST2...)\n"
        "🔧 /set_elo [число] — минимальное ELO (например: /set_elo 3200)\n"
        "🔧 /set_level [число] — минимальный уровень (например: /set_level 10)"
    )
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['subscribe'])
def subscribe(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id not in subs:
        subs[chat_id] = {"min_elo": DEFAULT_MIN_ELO, "min_level": DEFAULT_MIN_LEVEL, "category": DEFAULT_CATEGORY}
        save_subscribers(subs)
        bot.reply_to(message, (
            f"✅ <b>Вы успешно подписались!</b>\n\nТекущие фильтры:\n"
            f"🗺 Категория: {DEFAULT_CATEGORY}\n"
            f"📈 ELO: {DEFAULT_MIN_ELO}+\n"
            f"⭐ Уровень: {DEFAULT_MIN_LEVEL}+"
        ), parse_mode="HTML")
    else:
        bot.reply_to(message, "ℹ️ Вы уже подписаны на уведомления.")

@bot.message_handler(commands=['unsubscribe'])
def unsubscribe(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id in subs:
        del subs[chat_id]
        save_subscribers(subs)
        bot.reply_to(message, "❌ <b>Вы отписались.</b> Уведомления больше приходить не будут.", parse_mode="HTML")
    else:
        bot.reply_to(message, "ℹ️ Вы не были подписаны.")

@bot.message_handler(commands=['settings'])
def show_settings(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id in subs:
        elo  = subs[chat_id].get("min_elo", DEFAULT_MIN_ELO)
        lvl  = subs[chat_id].get("min_level", DEFAULT_MIN_LEVEL)
        cat  = subs[chat_id].get("category", DEFAULT_CATEGORY)
        bot.reply_to(message, (
            f"⚙️ <b>Ваши текущие фильтры:</b>\n\n"
            f"🗺 Категория: {cat}\n"
            f"📈 Минимальное ELO: {elo}\n"
            f"⭐ Минимальный уровень: {lvl}"
        ), parse_mode="HTML")
    else:
        bot.reply_to(message, "ℹ️ Вы не подписаны. Напишите /subscribe, чтобы подписаться.")

@bot.message_handler(commands=['set_category'])
def set_category_cmd(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id not in subs:
        bot.reply_to(message, "ℹ️ Сначала подпишитесь с помощью /subscribe.")
        return

    cats_list = "\n".join(f"  • <code>{c}</code>" for c in AVAILABLE_CATEGORIES)
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        bot.reply_to(message, (
            f"⚠️ Укажите название категории.\n\nДоступные категории:\n{cats_list}\n\n"
            f"Пример: <code>/set_category ONLY DUST2</code>"
        ), parse_mode="HTML")
        return

    chosen = args[1].strip().upper()
    # Ищем совпадение (без учёта регистра)
    matched = next((c for c in AVAILABLE_CATEGORIES if c.upper() == chosen), None)
    if not matched:
        bot.reply_to(message, (
            f"⚠️ Неизвестная категория: <b>{chosen}</b>\n\nДоступные категории:\n{cats_list}"
        ), parse_mode="HTML")
        return

    subs[chat_id]["category"] = matched
    save_subscribers(subs)
    bot.reply_to(message, f"✅ <b>Категория установлена: {matched}</b>", parse_mode="HTML")

@bot.message_handler(commands=['set_elo'])
def set_elo(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id not in subs:
        bot.reply_to(message, "ℹ️ Сначала подпишитесь с помощью /subscribe.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Укажите значение ELO. Пример: /set_elo 3200")
            return
        
        new_elo = int(args[1])
        if new_elo < 0 or new_elo > 6000:
            bot.reply_to(message, "⚠️ ELO должно быть от 0 до 6000.")
            return
            
        subs[chat_id]["min_elo"] = new_elo
        save_subscribers(subs)
        bot.reply_to(message, f"✅ <b>Минимальное ELO установлено на {new_elo}.</b>", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "⚠️ Пожалуйста, введите корректное число.")

@bot.message_handler(commands=['set_level'])
def set_level(message):
    subs = load_subscribers()
    chat_id = str(message.chat.id)
    if chat_id not in subs:
        bot.reply_to(message, "ℹ️ Сначала подпишитесь с помощью /subscribe.")
        return
    
    try:
        args = message.text.split()
        if len(args) < 2:
            bot.reply_to(message, "⚠️ Укажите значение уровня. Пример: /set_level 8")
            return
        
        new_lvl = int(args[1])
        if new_lvl < 1 or new_lvl > 10:
            bot.reply_to(message, "⚠️ Уровень должен быть от 1 до 10.")
            return
            
        subs[chat_id]["min_level"] = new_lvl
        save_subscribers(subs)
        bot.reply_to(message, f"✅ <b>Минимальный уровень установлен на {new_lvl}.</b>", parse_mode="HTML")
    except ValueError:
        bot.reply_to(message, "⚠️ Пожалуйста, введите корректное число.")

def bot_polling():
    """Запускает опрос Telegram серверов в отдельном потоке.
    Использует polling(none_stop=False) вместо infinity_polling, чтобы
    исключения (409 Conflict) выбрасывались наружу и мы могли их поймать."""
    while True:
        try:
            bot.stop_polling()  # сбросить старое состояние если было
        except Exception:
            pass
        try:
            # none_stop=False — при ошибке сразу бросает исключение (не глотает внутри)
            bot.polling(none_stop=False, timeout=10, long_polling_timeout=5, skip_pending=True)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 409:
                # Другой контейнер (старый Railway) ещё жив. Ждём 60 сек пока умрёт.
                print("[!] 409 Conflict: старый экземпляр ещё работает. Жду 60 сек...")
                time.sleep(60)
            else:
                print(f"[!] Ошибка Telegram API: {e}")
                time.sleep(10)
        except Exception as e:
            print(f"[!] Ошибка Telegram бота: {e}")
            time.sleep(10)

# ============================================================
#   УВЕДОМЛЕНИЕ
# ============================================================

def send_telegram_message(player: dict, server_name: str, server_ip: str) -> None:
    if not TG_BOT_TOKEN:
        return
    subs = load_subscribers()

    cat_text = f"\n🗺 <b>Категория:</b> {player['server_category']}" if player.get('server_category') else ""
    ip_text  = f"\n🔌 <b>IP:</b> <code>connect {server_ip}</code>" if server_ip else ""
    tg_text = (
        f"🚨 <b>ОБНАРУЖЕН ТОПОВЫЙ ИГРОК</b> 🚨\n\n"
        f"👤 <b>Ник:</b> <code>{player['nickname']}</code>\n"
        f"📈 <b>ELO:</b> {player['elo']}\n"
        f"⭐ <b>Уровень:</b> {player['level']}\n"
        f"🔗 <b>Faceit:</b> <a href='{player['faceit_url']}'>Ссылка</a>\n"
        f"🎮 <b>Сервер:</b> <code>{server_name}</code>{cat_text}{ip_text}"
    )
    
    # Собираем список chat_id, которым нужно отправить сообщение
    server_category = player.get("server_category", "")
    target_chats = []
    for chat_id, settings in subs.items():
        min_lvl = settings.get("min_level", DEFAULT_MIN_LEVEL)
        min_elo = settings.get("min_elo", DEFAULT_MIN_ELO)
        user_cat = settings.get("category", DEFAULT_CATEGORY)

        # «ALL MAPS» у пользователя = не фильтруем по категории
        cat_ok = (user_cat == "ALL MAPS") or (not server_category) or (server_category == user_cat)

        if cat_ok and player['level'] >= min_lvl and player['elo'] >= min_elo:
            target_chats.append(chat_id)
            
    # Отправляем сообщения в отдельном потоке, чтобы не тормозить парсер
    if target_chats:
        threading.Thread(target=_bulk_send_telegram, args=(target_chats, tg_text), daemon=True).start()

def _bulk_send_telegram(chat_ids: list[str], text: str) -> None:
    """Отправляет сообщения списку пользователей с учетом лимитов Telegram API."""
    for i, chat_id in enumerate(chat_ids):
        try:
            bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
        except telebot.apihelper.ApiTelegramException as e:
            if e.error_code == 429:
                # Too Many Requests: ждем указанное время
                retry_after = int(e.result_json.get('parameters', {}).get('retry_after', 5))
                print(f"    [!] Telegram Rate Limit. Ждем {retry_after} сек...")
                time.sleep(retry_after)
                try:
                    bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
                except Exception:
                    pass
            elif e.error_code in (403, 400):
                # Пользователь заблокировал бота или удалил чат
                print(f"    [!] Пользователь {chat_id} заблокировал бота. Удаляем из базы.")
                _remove_subscriber(chat_id)
        except Exception as e:
            print(f"    [!] Ошибка отправки в Telegram для {chat_id}: {e}")
            
        # Telegram разрешает отправлять не более 30 сообщений в секунду
        # Делаем небольшую паузу, чтобы не словить бан
        if (i + 1) % 25 == 0:
            time.sleep(1)

def _remove_subscriber(chat_id: str) -> None:
    """Удаляет пользователя из базы (например, если он заблокировал бота)."""
    try:
        subs = load_subscribers()
        if chat_id in subs:
            del subs[chat_id]
            save_subscribers(subs)
    except Exception:
        pass

def notify(player: dict, server_name: str, server_ip: str) -> None:
    b = "=" * 60
    print(f"\033[91m{b}\033[0m")
    print(f"\033[93m  !!! ОБНАРУЖЕН ТОПОВЫЙ ИГРОК !!!\033[0m")
    try:
        print(f"\033[96m  Ник:      {player['nickname']}\033[0m")
    except UnicodeEncodeError:
        print(f"\033[96m  Ник:      {player['nickname'].encode('utf-8', 'replace').decode('utf-8', 'ignore')}\033[0m")
    print(f"\033[96m  ELO:      {player['elo']}\033[0m")
    print(f"\033[96m  Уровень:  {player['level']}\033[0m")
    print(f"\033[96m  Faceit:   {player['faceit_url']}\033[0m")
    print(f"\033[96m  Сервер:   {server_name}\033[0m")
    if server_ip:
        print(f"\033[96m  IP:       {server_ip}\033[0m")
    print(f"\033[91m{b}\033[0m\n")
    
    # Отправка в Telegram
    send_telegram_message(player, server_name, server_ip)


# ============================================================
#   ОСНОВНОЙ ЦИКЛ
# ============================================================

def scan_servers() -> None:
    """Один полный цикл обхода серверов."""
    print(f"\n[*] Запускаю браузер и открываю {CYBERSHOKE_URL} ...")
    driver = build_driver()
    
    try:
        driver.get(CYBERSHOKE_URL)
        time.sleep(8)
        
        select_category(driver, "ALL MAPS")

        servers = scroll_and_collect(driver)

        if not servers:
            print("[!] Серверы не найдены или нет онлайн-игроков.")
            return

        total = len(servers)
        print(f"[*] Найдено серверов с игроками: {total}")

        checked_nicks: set[str] = set()
        top_found = 0

        for seq, srv in enumerate(servers):
            card_text = srv["text"]
            online = srv["online"]

            srv_category = srv.get("category", "")
            try:
                print(f"\n  [{seq+1}/{total}] [{srv_category}] {card_text} (online: {online})")
            except UnicodeEncodeError:
                print(f"\n  [{seq+1}/{total}] [{srv_category}] {card_text.encode('utf-8', 'replace').decode('utf-8', 'ignore')} (online: {online})")

            try:
                players, server_ip, server_name = fetch_server_players(driver, card_text)
            except WebDriverException as e:
                print(f"    [!] Ошибка браузера. Перезапускаю Chrome...")
                driver.quit()
                driver = build_driver()
                driver.get(CYBERSHOKE_URL)
                time.sleep(8)
                select_category(driver, "ALL MAPS")
                servers = scroll_and_collect(driver)
                total = len(servers)
                continue
            except Exception as e:
                print(f"    [!] Ошибка: {e}")
                continue

            info = server_name or card_text
            if server_ip:
                info += f"  |  IP {server_ip}"

            if not players:
                print(f"    -> 0 игроков (модалка не открылась или пуста)")
                continue

            # Разделяем игроков по уровню иконки
            candidates = []   # level >= 1 или 0 (неизвестен) → чекаем API
            skipped    = []   # level 0 → пропуск (если точно знаем, что 0)

            # Находим минимальные требования среди всех подписчиков, 
            # чтобы не делать лишние запросы к Faceit API
            subs = load_subscribers()
            global_min_lvl = DEFAULT_MIN_LEVEL
            if subs:
                global_min_lvl = min(s.get("min_level", DEFAULT_MIN_LEVEL) for s in subs.values())

            for p in players:
                nick = p["nickname"]
                if nick in checked_nicks:
                    continue
                fl = p.get("faceit_level", 0)
                
                # Если уровень иконки меньше самого минимального требуемого уровня всех юзеров,
                # и это не 0 (0 значит уровень неизвестен, надо чекать), то пропускаем
                if 0 < fl < global_min_lvl:
                    skipped.append((nick, fl))
                else:
                    candidates.append(p)

            print(f"    -> {len(players)} игроков  |  "
                  f"чекаю {len(candidates)}  |  "
                  f"пропущено {len(skipped)} (lvl < {global_min_lvl})")

            if skipped:
                skip_str = ", ".join(f"{n}(Lv{l})" for n, l in skipped[:8])
                if len(skipped) > 8:
                    skip_str += f" ...+{len(skipped)-8}"
                try:
                    print(f"    \033[90m  skip: {skip_str}\033[0m")
                except UnicodeEncodeError:
                    print(f"    \033[90m  skip: {skip_str.encode('utf-8', 'replace').decode('utf-8', 'ignore')}\033[0m")

            for p in candidates:
                nick = p["nickname"]
                checked_nicks.add(nick)
                fl = p.get("faceit_level", 0)

                faceit = None
                if p.get("steam_id"):
                    faceit = check_faceit_by_steam(p["steam_id"])
                if not faceit:
                    faceit = check_faceit_by_nickname(nick)

                if faceit:
                    elo = faceit['elo']
                    lvl = faceit['level']
                    faceit["server_category"] = srv_category

                    # Для консоли используем дефолтные настройки
                    is_top = (lvl >= DEFAULT_MIN_LEVEL and elo >= DEFAULT_MIN_ELO)
                    
                    if is_top:
                        color = "\033[91m"  # красный — ТОПОВЫЙ
                    else:
                        color = "\033[37m"  # серый — обычный
                    try:
                        print(f"    {color}{nick} — ELO {elo}, Lvl {lvl}\033[0m")
                    except UnicodeEncodeError:
                        print(f"    {color}{nick.encode('utf-8', 'replace').decode('utf-8', 'ignore')} — ELO {elo}, Lvl {lvl}\033[0m")

                    # Уведомляем всех подписчиков, чьи фильтры подходят
                    # В консоль выводим notify только если игрок подходит под дефолтные настройки
                    if is_top:
                        notify(faceit, server_name or card_text, server_ip)
                        top_found += 1
                    else:
                        # Отправляем в телеграм без вывода в консоль
                        send_telegram_message(faceit, server_name or card_text, server_ip)
                else:
                    try:
                        print(f"    \033[90m{nick} — нет Faceit (icon Lv{fl})\033[0m")
                    except UnicodeEncodeError:
                        print(f"    \033[90m{nick.encode('utf-8', 'replace').decode('utf-8', 'ignore')} — нет Faceit (icon Lv{fl})\033[0m")

                time.sleep(0.8)  # Faceit rate-limit

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    print(f"\n{'=' * 40}")
    print(f"  Проверено ников:   {len(checked_nicks)}")
    print(f"  Топовых игроков:   {top_found}")
    print(f"{'=' * 40}")


def run_monitor() -> None:
    print("=" * 60)
    print("  CyberShoke Monitor v6.0")
    print(f"  Дефолтный порог: Level >= {DEFAULT_MIN_LEVEL}, ELO >= {DEFAULT_MIN_ELO}")
    print(f"  Дефолтная категория: {DEFAULT_CATEGORY}")
    print(f"  Интервал: {CHECK_INTERVAL // 60} мин")
    print(f"  URL: {CYBERSHOKE_URL}")
    print("=" * 60)

    # Запускаем Telegram бота в фоновом потоке
    if TG_BOT_TOKEN:
        threading.Thread(target=bot_polling, daemon=True).start()
        print("[*] Telegram бот запущен. Жду подписчиков...")
        print("    (Напишите боту /start и /subscribe в Telegram)")
    else:
        print("[!] Токен Telegram бота не указан. Уведомления отключены.")

    while True:
        try:
            scan_servers()
        except Exception as e:
            print(f"[!] Критическая ошибка: {e}")
            traceback.print_exc()

        print(f"\n[*] Следующая проверка через {CHECK_INTERVAL // 60} мин...\n")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_monitor()
