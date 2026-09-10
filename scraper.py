"""
Monitor de estoque - Maersk Container Sales
Faz login no site, le o estoque (Brasil / tipos configurados),
compara com a ultima leitura salva e manda alertas no Telegram.
"""

import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import requests
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Configuracao
# ---------------------------------------------------------------------------

MAERSK_EMAIL = os.environ["MAERSK_EMAIL"]
MAERSK_PASSWORD = os.environ["MAERSK_PASSWORD"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_IDS = [c.strip() for c in os.environ["TELEGRAM_CHAT_ID"].split(",") if c.strip()]

SIGNIN_URL = "https://www.maerskcontainersales.com/signin"

# Tipos de container monitorados (nomes exatamente como aparecem no filtro do site)
CONTAINER_TYPES = ["20' Dry Standard", "40' Dry High"]

# Combinações tipo + condição que queremos de fato (filtro fino, pós-scraping)
ALLOWED_TYPE_CONDITION = {
    "20' Dry Standard": {"DAMAGE"},
    "40' Dry High": {"AS IS", "DAMAGE"},
}

# Regiao monitorada
COUNTRY = "BR"

STATE_FILE = Path(__file__).parent / "state" / "snapshot.json"

IS_SUMMARY_RUN = "--summary" in sys.argv


def build_products_url(container_type: str) -> str:
    types_param = quote(container_type)
    return (
        f"https://www.maerskcontainersales.com/products"
        f"?countries={COUNTRY}&types={types_param}"
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for chat_id in TELEGRAM_CHAT_IDS:
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        if not resp.ok:
            print(f"[AVISO] Falha ao enviar mensagem no Telegram para {chat_id}: {resp.status_code} {resp.text}")


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------

def login_and_scrape() -> dict:
    """Faz login e retorna um dict {chave_do_item: dados_do_item}."""
    items = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # --- login ---
        page.goto(SIGNIN_URL, wait_until="networkidle")

        # aceitar cookies, se o banner aparecer (texto pode variar por idioma)
        for label in ["Essential only", "Accept all", "Aceitar", "Aceitar todos", "Somente essenciais"]:
            try:
                page.get_by_text(label, exact=False).click(timeout=2000)
                break
            except Exception:
                continue

        # garantia extra: remove qualquer overlay de cookie que ainda esteja
        # bloqueando cliques na página, mesmo que o botão acima não tenha sido encontrado
        try:
            page.evaluate(
                "document.querySelectorAll('[id*=\"coi\" i], [class*=\"cookie\" i]')"
                ".forEach(el => el.remove())"
            )
        except Exception:
            pass

        page.fill('input[type="email"]', MAERSK_EMAIL)
        page.fill('input[type="password"]', MAERSK_PASSWORD)
        page.click('button:has-text("Sign in")')
        page.wait_for_load_state("networkidle")

        # --- pagina de estoque, uma vez para cada tipo ---
        for container_type in CONTAINER_TYPES:
            page.goto(build_products_url(container_type), wait_until="networkidle")
            try:
                page.wait_for_selector(".item", timeout=15000)
            except Exception:
                continue  # esse tipo pode não ter itens disponíveis agora

            cards = page.query_selector_all(".item")
            for card in cards:
                def text_of(selector):
                    el = card.query_selector(selector)
                    return el.inner_text().strip() if el else ""

                card_type = text_of(".name")
                condition = text_of(".badge")
                site = text_of(".site")
                place = text_of(".place")
                price = text_of(".total")
                stock_raw = text_of(".stock")  # formato "x10"

                try:
                    quantity = int(stock_raw.replace("x", "").strip())
                except ValueError:
                    quantity = None

                allowed_conditions = ALLOWED_TYPE_CONDITION.get(card_type)
                if not allowed_conditions or condition.strip().upper() not in allowed_conditions:
                    continue

                key = f"{card_type} | {condition} | {site} | {place}"
                items[key] = {
                    "type": card_type,
                    "condition": condition,
                    "site": site,
                    "place": place,
                    "price": price,
                    "quantity": quantity,
                }

        browser.close()

    return items


# ---------------------------------------------------------------------------
# Comparacao e alertas
# ---------------------------------------------------------------------------

def load_previous_snapshot() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_snapshot(items: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def compare_and_alert(previous: dict, current: dict) -> None:
    """Alerta apenas ENTRADAS de estoque: itens novos ou aumento de quantidade.
    Saídas, reduções e esgotamentos não geram mensagem."""
    alerts = []

    for key, curr in current.items():
        prev = previous.get(key)

        if prev is None:
            alerts.append(
                f"🆕 <b>Novo estoque disponível</b>\n"
                f"{curr['type']} ({curr['condition']})\n"
                f"📍 {curr['site']} - {curr['place']}\n"
                f"📦 Quantidade: {curr['quantity']}\n"
                f"💰 {curr['price']}"
            )
            continue

        prev_qty = prev.get("quantity")
        curr_qty = curr.get("quantity")

        if prev_qty is None or curr_qty is None or curr_qty <= prev_qty:
            continue

        alerts.append(
            f"📈 <b>Chegada de unidades</b>\n"
            f"{curr['type']} ({curr['condition']})\n"
            f"📍 {curr['site']} - {curr['place']}\n"
            f"{prev_qty} → {curr_qty} unidades"
        )

    if not alerts:
        print("Nenhuma entrada nova detectada.")
        return

    # Telegram tem limite de tamanho por mensagem; agrupamos em blocos
    chunk = []
    chunk_len = 0
    for alert in alerts:
        if chunk_len + len(alert) > 3500:
            send_telegram("\n\n".join(chunk))
            chunk, chunk_len = [], 0
        chunk.append(alert)
        chunk_len += len(alert)
    if chunk:
        send_telegram("\n\n".join(chunk))


def send_summary(current: dict) -> None:
    if not current:
        send_telegram("📋 <b>Resumo de estoque</b>\nNenhum item encontrado com os filtros atuais.")
        return

    by_type = {}
    for item in current.values():
        label = f"{item['type']} ({item['condition']})"
        by_type.setdefault(label, 0)
        by_type[label] += item["quantity"] or 0

    lines = ["📋 <b>Resumo de estoque - Brasil</b>"]
    for label, total in sorted(by_type.items()):
        lines.append(f"• {label}: {total} unidades")

    send_telegram("\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    previous = load_previous_snapshot()
    current = login_and_scrape()

    if IS_SUMMARY_RUN:
        send_summary(current)
    else:
        compare_and_alert(previous, current)

    save_snapshot(current)


if __name__ == "__main__":
    main()
