# -*- coding: utf-8 -*-
import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = "8837143109:AAEaL3ZlBPSVmM0EtAAexHkb_lzrcjTagpc"
ADMIN_ID = 1038754614

# ВАШ ДОМЕН С RAILWAY
WEBHOOK_HOST = "crypto-bot-final.up.railway.app"
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}"

SYMBOL = "BTC-USDT"
STOP_POINTS = 100.0
TAKE_POINTS = 300.0

# ========== ИНИЦИАЛИЗАЦИЯ ==========
bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

# ========== КЛАВИАТУРА ==========
def main_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== КОМАНДЫ ==========
@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Бот работает на вебхуках.\n\nВыбери действие:",
        reply_markup=main_menu()
    )

@dp.callback_query_handler(lambda c: c.data == "status")
async def cb_status(call: types.CallbackQuery):
    await call.answer()
    await call.message.edit_text(
        "✅ Бот работает, сканер активен.\n💰 BTC/USDT\n⏱ 5 минут",
        reply_markup=main_menu()
    )

@dp.callback_query_handler(lambda c: c.data == "help")
async def cb_help(call: types.CallbackQuery):
    await call.answer()
    await call.message.edit_text(
        "ℹ️ Бот анализирует CCI и EMA.\n🟢 LONG — покупка\n🔴 SHORT — продажа",
        reply_markup=main_menu()
    )

# ========== ИНДИКАТОРЫ ==========
def calc_cci(highs, lows, closes, period=14):
    if len(closes) < period:
        return [0] * len(closes)
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    cci = []
    for i in range(len(tp)):
        if i < period - 1:
            cci.append(0.0)
            continue
        window = tp[i - period + 1:i + 1]
        sma = sum(window) / period
        mean_dev = sum(abs(x - sma) for x in window) / period
        cci.append((tp[i] - sma) / (0.015 * mean_dev) if mean_dev else 0.0)
    return cci

def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema

# ========== KUCOIN API ==========
async def get_kucoin_candles(session, limit=120):
    url = f"https://api.kucoin.com/api/v1/market/candles?type=5min&symbol={SYMBOL}&limit={limit}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                if data["code"] == "200000":
                    klines = data["data"]
                    return {
                        "highs": [float(k[3]) for k in klines],
                        "lows": [float(k[4]) for k in klines],
                        "closes": [float(k[2]) for k in klines],
                    }
    except Exception as e:
        logger.error(f"KuCoin ошибка: {e}")
    return None

async def send_signal(signal):
    price = signal["price"]
    if signal["type"] == "LONG":
        stop = round(price - STOP_POINTS, 2)
        take = round(price + TAKE_POINTS, 2)
        emoji = "🟢"
    else:
        stop = round(price + STOP_POINTS, 2)
        take = round(price - TAKE_POINTS, 2)
        emoji = "🔴"
    text = f"{emoji} <b>СИГНАЛ {signal['type']} BTC/USDT</b>\n💰 Вход: ${price:,.2f}\n🛑 Стоп: ${stop:,.2f}\n🎯 Тейк: ${take:,.2f}\n📊 CCI: {signal['cci']:.1f}"
    await bot.send_message(ADMIN_ID, text, parse_mode="HTML")

# ========== СКАНЕР ==========
async def scanner():
    logger.info("🟢 Сканер запущен!")
    last_time = 0
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                now = datetime.now(timezone.utc)
                sec_to_close = (5 - (now.minute % 5)) * 60 - now.second
                if sec_to_close > 3:
                    await asyncio.sleep(min(sec_to_close, 30))
                    continue
                await asyncio.sleep(3)
                
                m5 = await get_kucoin_candles(session)
                if not m5:
                    await asyncio.sleep(30)
                    continue
                
                logger.info(f"✅ Данные KuCoin: {len(m5['closes'])} свечей")
                
                cci = calc_cci(m5["highs"], m5["lows"], m5["closes"])
                if len(cci) < 20:
                    continue
                
                cci_now = cci[-1]
                cci_prev = cci[-2]
                price = m5["closes"][-1]
                ema6 = calc_ema(m5["closes"], 6)
                ema12 = calc_ema(m5["closes"], 12)
                ema24 = calc_ema(m5["closes"], 24)
                bull = ema6 > ema12 > ema24
                bear = ema6 < ema12 < ema24
                
                logger.info(f"📊 BTC ${price:,.0f} | CCI: {cci_now:.1f}")
                
                signal = None
                if bull and price > ema6:
                    if cci_prev < 0 <= cci_now:
                        signal = {"type": "LONG", "price": price, "pattern": "Пересечение нуля вверх", "cci": cci_now}
                    elif cci_now > 100 and cci_prev <= 100:
                        signal = {"type": "LONG", "price": price, "pattern": "Импульс выше 100", "cci": cci_now}
                if bear and price < ema6:
                    if cci_prev > 0 >= cci_now:
                        signal = {"type": "SHORT", "price": price, "pattern": "Пересечение нуля вниз", "cci": cci_now}
                    elif cci_now < -100 and cci_prev >= -100:
                        signal = {"type": "SHORT", "price": price, "pattern": "Импульс ниже -100", "cci": cci_now}
                
                if signal:
                    time_diff = datetime.now().timestamp() - last_time if last_time else 9999
                    if time_diff > 1800:
                        await send_signal(signal)
                        last_time = datetime.now().timestamp()
                        logger.info(f"✅ СИГНАЛ {signal['type']}")
                
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Ошибка: {e}")
                await asyncio.sleep(30)

# ========== ВЕБХУК ==========
async def handle_webhook(request):
    body = await request.json()
    update = types.Update(**body)
    await dp.process_update(update)
    return web.Response()

async def on_startup(app):
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"✅ Вебхук установлен: {WEBHOOK_URL}")
    asyncio.create_task(scanner())
    logger.info("✅ Бот запущен!")

async def on_shutdown(app):
    await bot.delete_webhook()
    logger.info("❌ Вебхук удалён")

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    app = web.Application()
    app.router.add_post(WEBHOOK_PATH, handle_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Сервер запущен на порту {port}")
    web.run_app(app, host="0.0.0.0", port=port)
