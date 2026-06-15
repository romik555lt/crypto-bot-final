# -*- coding: utf-8 -*-
import asyncio
import logging
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.utils import executor
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "8504074176:AAGPg8H71gLAMDK1a8lpQU48UuyLbLm0itw"
ADMIN_ID = 1038754614

SYMBOL = "BTCUSDT"
STOP_POINTS = 100.0
TAKE_POINTS = 300.0

# ========== УДАЛЕНИЕ ВЕБХУКА ==========
async def delete_webhook():
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook") as resp:
            result = await resp.json()
            logger.info(f"Удаление вебхука: {result}")

# ========== КЛАВИАТУРА ==========
def main_menu():
    keyboard = [
        [InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")]
    ]
    return InlineKeyboardMarkup(keyboard)

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)

@dp.message_handler(commands=["start"])
async def cmd_start(message: types.Message):
    await message.answer("👋 Привет! Бот работает.\n\nВыбери действие:", reply_markup=main_menu())

@dp.callback_query_handler(lambda c: c.data == "status")
async def cb_status(call: types.CallbackQuery):
    await call.answer()
    await call.message.edit_text("✅ Бот работает, сканер активен.\n💰 BTC/USDT\n⏱ 5 минут", reply_markup=main_menu())

@dp.callback_query_handler(lambda c: c.data == "help")
async def cb_help(call: types.CallbackQuery):
    await call.answer()
    await call.message.edit_text("ℹ️ Бот анализирует CCI и EMA.\n🟢 LONG — покупка\n🔴 SHORT — продажа", reply_markup=main_menu())

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

async def get_binance_candles(session, limit=120):
    url = f"https://data.binance.com/api/v3/klines?symbol={SYMBOL}&interval=5m&limit={limit}"
    try:
        async with session.get(url, timeout=10) as response:
            if response.status == 200:
                data = await response.json()
                return {
                    "highs": [float(c[2]) for c in data],
                    "lows": [float(c[3]) for c in data],
                    "closes": [float(c[4]) for c in data],
                }
    except Exception as e:
        logger.error(f"Ошибка: {e}")
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

async def scanner():
    logger.info("🟢 Сканер запущен!")
    last_time = 0
    await delete_webhook()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                now = datetime.now(timezone.utc)
                sec_to_close = (5 - (now.minute % 5)) * 60 - now.second
                if sec_to_close > 3:
                    await asyncio.sleep(min(sec_to_close, 30))
                    continue
                await asyncio.sleep(3)
                
                m5 = await get_binance_candles(session)
                if not m5:
                    await asyncio.sleep(30)
                    continue
                
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

async def on_startup(dp):
    asyncio.create_task(scanner())
    logger.info("✅ Бот запущен!")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
