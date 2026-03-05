from flask import Flask, request, jsonify
import requests
import os
import threading
import time
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

KAITEN_WEBHOOK_URL = os.environ.get('KAITEN_WEBHOOK_URL')
KAITEN_API_URL_TMPL = 'https://golodniyleshiy.kaiten.ru/api/latest/cards/{card_id}/checklists'
KAITEN_TOKEN = os.environ.get('KAITEN_TOKEN')


def keep_alive():
    """Пингует сервер каждые 10 минут, чтобы Render не усыплял его."""
    while True:
        time.sleep(600)
        try:
            host = os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')
            requests.get(f"https://{host}/ping", timeout=10)
        except Exception:
            pass


threading.Thread(target=keep_alive, daemon=True).start()


def format_product_line(p):
    qty = str(p.get('quantity', '')) if p.get('quantity', '') else ''
    options_str = ', '.join(
        filter(None, [op.get('variant', '') for op in p.get('options', [])])
    )
    parts = [
        p.get('name', ''),
        f"{qty}шт" if qty else '',
        options_str
    ]
    return ', '.join([part for part in parts if part])


def create_checklist_and_items(card_id, orderid, products):
    url = KAITEN_API_URL_TMPL.format(card_id=card_id)
    headers = {
        "Authorization": f"Bearer {KAITEN_TOKEN}",
        "Content-Type": "application/json"
    }
    checklist_payload = {
        "name": f"Заказ №{orderid}"
    }
    resp = requests.post(url, json=checklist_payload, headers=headers)
    resp.raise_for_status()
    checklist_id = resp.json().get('id')
    logger.info("API ответ по чек-листу: %s", resp.json())

    for idx, p in enumerate(products, 1):
        item_payload = {
            "text": format_product_line(p),
            "sort_order": idx
        }
        item_url = f"https://golodniyleshiy.kaiten.ru/api/latest/checklists/{checklist_id}/items"
        resp_item = requests.post(item_url, json=item_payload, headers=headers)
        resp_item.raise_for_status()
        logger.info("Добавлен пункт %d: %s", idx, resp_item.json())


@app.route('/', methods=['POST'])
def webhook():
    # Получаем данные от Tilda
    if request.is_json:
        data = request.json
    else:
        data = request.form.to_dict()

    logger.info("=== НОВЫЙ ЗАКАЗ ===")
    logger.info("Входящие данные от Тильды: %s", data)

    # ИЗВЛЕКАЕМ ДАТУ ИЗ ДАННЫХ TILDA
    # В Tilda поле с датой обычно называется 'Date' и имеет формат "дд-мм-гггг"
    raw_date_from_tilda = data.get('Date', '')
    logger.info("Сырая дата от Tilda: %s", raw_date_from_tilda)

    # ПРЕОБРАЗУЕМ ДАТУ В НУЖНЫЙ ФОРМАТ
    try:
        if raw_date_from_tilda:
            # Парсим строку "дд-мм-гггг" в объект datetime
            # Если формат в Tilda другой (например "дд/мм/гггг"), измените маску на '%d/%m/%Y'
            order_date_obj = datetime.strptime(raw_date_from_tilda, '%d-%m-%Y')
            
            # Форматируем для заголовка карточки (ДД.ММ.ГГГГ)
            date_for_title = order_date_obj.strftime("%d.%m.%Y")
            
            # Форматируем для поля due_date в Kaiten (ГГГГ-ММ-ДД)
            date_for_kaiten = order_date_obj.strftime("%Y-%m-%d")
            
            logger.info("Дата успешно преобразована:")
            logger.info("  - Для заголовка: %s", date_for_title)
            logger.info("  - Для due_date: %s", date_for_kaiten)
        else:
            # Если дата пустая, используем текущую московскую дату
            logger.warning("Поле Date пустое, использую текущую дату")
            moscow_time = datetime.now(timezone(timedelta(hours=3)))
            date_for_title = moscow_time.strftime("%d.%m.%Y")
            date_for_kaiten = moscow_time.strftime("%Y-%m-%d")
            logger.info("Текущая дата МСК: %s", date_for_title)

    except Exception as e:
        # Если ошибка парсинга, используем текущую дату
        logger.error("ОШИБКА парсинга даты '%s': %s", raw_date_from_tilda, e)
        moscow_time = datetime.now(timezone(timedelta(hours=3)))
        date_for_title = moscow_time.strftime("%d.%m.%Y")
        date_for_kaiten = moscow_time.strftime("%Y-%m-%d")
        logger.warning("Использую текущую дату МСК: %s", date_for_title)

    # ОСНОВНЫЕ ДАННЫЕ ЗАКАЗА
    payment = data.get('payment', {})
    orderid = payment.get('orderid', '')
    
    # Используем исправленную дату в заголовке
    title = f"Заказ #{orderid}, {date_for_title}"
    logger.info("Заголовок карточки: %s", title)

    # ФОРМИРУЕМ СПИСОК ТОВАРОВ
    products = payment.get('products', [])
    products_list = ""
    for idx, p in enumerate(products, 1):
        products_list += f"Товар {idx}: {p.get('name', '')}, Количество: {p.get('quantity', '')}, Цена: {p.get('price', '')}\n"
        options = p.get('options', [])
        for op in options:
            products_list += f"    Опция: {op.get('option', '')} — {op.get('variant', '')}\n"

    # ДАННЫЕ КЛИЕНТА
    fio = data.get('ma_name') or payment.get('delivery_fio', '')
    phone = data.get('Phone', '') or data.get('ma_phone', '')
    email = data.get('Email', '') or data.get('ma_email', '')
    comment = data.get('comment', '')

    # ФОРМИРУЕМ ОПИСАНИЕ
    description = (
        f"Номер заказа: {orderid}\n"
        f"Дата заказа: {date_for_title}\n"  # Добавил дату в описание для наглядности
        f"\n=== ТОВАРЫ ===\n"
        f"{products_list}"
        f"\n=== ДОСТАВКА И ОПЛАТА ===\n"
        f"Стоимость доставки: {payment.get('delivery_price', '')}\n"
        f"Способ доставки: {payment.get('delivery', '')}\n"
        f"Город доставки: {payment.get('delivery_city', '')}\n"
        f"Адрес пункта выдачи: {payment.get('delivery_address', '')}\n"
        f"\n=== СКИДКИ И СУММЫ ===\n"
        f"Промокод: {payment.get('promocode', '')}\n"
        f"Скидка: {payment.get('discountvalue', '')} ({payment.get('discount', '')})\n"
        f"Сумма без доставки: {payment.get('subtotal', '')}\n"
        f"Итого к оплате: {payment.get('amount', '')}\n"
        f"\n=== КЛИЕНТ ===\n"
        f"ФИО: {fio}\n"
        f"Телефон: {phone}\n"
        f"Email: {email}\n"
        f"Комментарий: {comment}\n"
    )

    # ФОРМИРУЕМ PAYLOAD ДЛЯ KAITEN
    payload = {
        "title": title,
        "description": description,
        "due_date": date_for_kaiten,  # Используем дату из заказа!
        "members": [],
        "links": []
    }

    # ОТПРАВЛЯЕМ В KAITEN
    logger.info("Отправляю в Kaiten:")
    logger.info("  URL: %s", KAITEN_WEBHOOK_URL)
    logger.info("  Payload: %s", payload)
    
    resp = requests.post(KAITEN_WEBHOOK_URL, json=payload)
    logger.info("Ответ Kaiten: %d %s", resp.status_code, resp.text)

    # СОЗДАЕМ ЧЕК-ЛИСТ (если карточка создана)
    if resp.status_code == 200:
        try:
            card_id = resp.json().get('id')
            if card_id:
                logger.info("Создаю чек-лист для карточки %s", card_id)
                create_checklist_and_items(card_id, orderid, products)
        except Exception as e:
            logger.error("Ошибка при создании чек-листа: %s", e)

    logger.info("=== ЗАКАЗ ОБРАБОТАН ===\n")
    return jsonify({"status": "ok", "kaiten_response": resp.status_code}), 200


@app.route('/ping', methods=['GET'])
def ping():
    logger.info("Converter is UP")
    return 'OK', 200


if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
