import logging
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import io
import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile, BufferedInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError
import calendar
import os
import asyncio
import aiohttp
import time
import random

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Настройка сессии с таймаутами
session = AiohttpSession()
session.api_timeout = 60
session.api_retries = 5

# Инициализация бота и диспетчера
API_TOKEN = 'ВАШ_ТОКЕН_API'  # Замените на свой токен от BotFather
bot = Bot(token=API_TOKEN, session=session)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Класс для хранения состояний при формировании отчета
class ReportStates(StatesGroup):
    waiting_for_report_type = State()
    waiting_for_period = State()
    waiting_for_date_range = State()

# Класс для хранения состояний при просмотре статистики
class StatsStates(StatesGroup):
    waiting_for_stats_type = State()
    waiting_for_period = State()
    waiting_for_date_range = State()

# Инициализация базы данных
def init_db():
    """
    Инициализирует базу данных SQLite и создает необходимые таблицы,
    если они еще не существуют.
    """
    conn = sqlite3.connect('analytics.db')
    cursor = conn.cursor()
    
    # Создание таблицы продаж
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        product_name TEXT,
        amount REAL,
        date TEXT,
        user_id INTEGER
    )
    ''')
    
    # Создание таблицы пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        user_id INTEGER UNIQUE,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        registration_date TEXT,
        last_activity TEXT
    )
    ''')
    
    # Создание таблицы действий пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_activity (
        id INTEGER PRIMARY KEY,
        user_id INTEGER,
        action_type TEXT,
        action_date TEXT,
        additional_data TEXT
    )
    ''')
    
    conn.commit()
    conn.close()
    
    logging.info("База данных инициализирована")

# Функция для добавления нового пользователя или обновления данных существующего
def register_user(user_id, username, first_name, last_name):
    """
    Регистрирует нового пользователя в базе данных или обновляет информацию
    о существующем пользователе.
    
    Args:
        user_id (int): ID пользователя в Telegram
        username (str): Имя пользователя
        first_name (str): Имя
        last_name (str): Фамилия
    """
    conn = sqlite3.connect('analytics.db')
    cursor = conn.cursor()
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute(
        "SELECT * FROM users WHERE user_id = ?", 
        (user_id,)
    )
    user = cursor.fetchone()
    
    if user is None:
        # Добавляем нового пользователя
        cursor.execute(
            "INSERT INTO users (user_id, username, first_name, last_name, registration_date, last_activity) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, first_name, last_name, now, now)
        )
    else:
        # Обновляем информацию о существующем пользователе
        cursor.execute(
            "UPDATE users SET username = ?, first_name = ?, last_name = ?, last_activity = ? WHERE user_id = ?",
            (username, first_name, last_name, now, user_id)
        )
    
    conn.commit()
    conn.close()

# Функция для логирования действий пользователя
def log_user_activity(user_id, action_type, additional_data=None):
    """
    Записывает действие пользователя в журнал активности.
    
    Args:
        user_id (int): ID пользователя в Telegram
        action_type (str): Тип действия (например, 'start', 'report', 'stats')
        additional_data (str, optional): Дополнительные данные о действии
    """
    conn = sqlite3.connect('analytics.db')
    cursor = conn.cursor()
    
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute(
        "INSERT INTO user_activity (user_id, action_type, action_date, additional_data) VALUES (?, ?, ?, ?)",
        (user_id, action_type, now, additional_data)
    )
    
    # Обновляем время последней активности пользователя
    cursor.execute(
        "UPDATE users SET last_activity = ? WHERE user_id = ?",
        (now, user_id)
    )
    
    conn.commit()
    conn.close()

# Функция для получения данных продаж за период
def get_sales_data(start_date, end_date):
    """
    Получает данные о продажах из базы данных за указанный период.
    
    Args:
        start_date (str): Начальная дата в формате 'YYYY-MM-DD'
        end_date (str): Конечная дата в формате 'YYYY-MM-DD'
        
    Returns:
        pandas.DataFrame: DataFrame с данными о продажах
    """
    conn = sqlite3.connect('analytics.db')
    
    query = f"""
    SELECT 
        product_name,
        SUM(amount) as total_amount,
        date
    FROM 
        sales
    WHERE 
        date BETWEEN '{start_date}' AND '{end_date}'
    GROUP BY 
        product_name, date
    ORDER BY 
        date
    """
    # Примечание: сумма (total_amount) уже в гривнах
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    return df

# Функция для получения данных об активности пользователей за период
def get_user_activity_data(start_date, end_date):
    """
    Получает данные об активности пользователей из базы данных за указанный период.
    
    Args:
        start_date (str): Начальная дата в формате 'YYYY-MM-DD'
        end_date (str): Конечная дата в формате 'YYYY-MM-DD'
        
    Returns:
        pandas.DataFrame: DataFrame с данными об активности пользователей
    """
    conn = sqlite3.connect('analytics.db')
    
    query = f"""
    SELECT 
        ua.user_id,
        u.username,
        ua.action_type,
        COUNT(*) as action_count,
        ua.action_date
    FROM 
        user_activity ua
    JOIN 
        users u ON ua.user_id = u.user_id
    WHERE 
        ua.action_date BETWEEN '{start_date} 00:00:00' AND '{end_date} 23:59:59'
    GROUP BY 
        ua.user_id, ua.action_type, SUBSTR(ua.action_date, 1, 10)
    ORDER BY 
        ua.action_date
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()
    
    return df

# Функция для генерации графика продаж
def generate_sales_chart(df, period_name, temp_dir='temp_charts'):
    """
    Генерирует график продаж на основе данных DataFrame и сохраняет его во временный файл.
    
    Args:
        df (pandas.DataFrame): DataFrame с данными о продажах
        period_name (str): Название периода для заголовка графика
        temp_dir (str): Директория для временных файлов
        
    Returns:
        str: Путь к сохраненному файлу графика
    """
    # Создаем временную директорию, если она не существует
    os.makedirs(temp_dir, exist_ok=True)
    
    # Генерируем уникальное имя файла
    timestamp = int(time.time())
    random_suffix = random.randint(1000, 9999)
    filename = f"{temp_dir}/sales_chart_{timestamp}_{random_suffix}.png"
    
    plt.figure(figsize=(10, 6))
    
    # Преобразуем дату в формат datetime
    df['date'] = pd.to_datetime(df['date'])
    
    # Агрегируем данные по дате
    daily_sales = df.groupby(['date', 'product_name'])['total_amount'].sum().unstack()
    
    # Строим график
    ax = daily_sales.plot(kind='line', marker='o')
    plt.title(f'Продажи по товарам за {period_name}')
    plt.xlabel('Дата')
    plt.ylabel('Сумма продаж (грн)')
    plt.grid(True)
    plt.tight_layout()
    
    # Сохраняем график в файл
    plt.savefig(filename, format='png', dpi=100)
    plt.close()
    
    return filename

# Функция для генерации графика активности пользователей
def generate_activity_chart(df, period_name, temp_dir='temp_charts'):
    """
    Генерирует график активности пользователей на основе данных DataFrame и сохраняет его во временный файл.
    
    Args:
        df (pandas.DataFrame): DataFrame с данными об активности пользователей
        period_name (str): Название периода для заголовка графика
        temp_dir (str): Директория для временных файлов
        
    Returns:
        str: Путь к сохраненному файлу графика
    """
    # Создаем временную директорию, если она не существует
    os.makedirs(temp_dir, exist_ok=True)
    
    # Генерируем уникальное имя файла
    timestamp = int(time.time())
    random_suffix = random.randint(1000, 9999)
    filename = f"{temp_dir}/activity_chart_{timestamp}_{random_suffix}.png"
    
    plt.figure(figsize=(10, 6))
    
    # Преобразуем дату в формат datetime и извлекаем только дату
    df['action_date'] = pd.to_datetime(df['action_date']).dt.date
    
    # Агрегируем данные по дате и типу действия
    activity_by_date = df.groupby(['action_date', 'action_type'])['action_count'].sum().unstack()
    
    # Строим график
    ax = activity_by_date.plot(kind='line', marker='o')
    plt.title(f'Активность пользователей за {period_name}')
    plt.xlabel('Дата')
    plt.ylabel('Количество действий')
    plt.grid(True)
    plt.tight_layout()
    
    # Сохраняем график в файл
    plt.savefig(filename, format='png', dpi=100)
    plt.close()
    
    return filename

# Функция для экспорта данных в CSV
def export_to_csv(df, filename):
    """
    Экспортирует данные из DataFrame в файл CSV.
    
    Args:
        df (pandas.DataFrame): DataFrame с данными для экспорта
        filename (str): Имя файла CSV
        
    Returns:
        str: Путь к созданному файлу CSV
    """
    file_path = f"{filename}.csv"
    df.to_csv(file_path, index=False, encoding='utf-8')
    return file_path

# Функция для вычисления дат начала и конца периода
def get_date_range(period_type):
    """
    Вычисляет даты начала и конца периода на основе типа периода.
    
    Args:
        period_type (str): Тип периода ('day', 'week', 'month', 'year')
        
    Returns:
        tuple: Кортеж с начальной и конечной датами в формате 'YYYY-MM-DD'
    """
    today = datetime.date.today()
    
    if period_type == 'day':
        start_date = today
        end_date = today
    elif period_type == 'week':
        start_date = today - datetime.timedelta(days=today.weekday())
        end_date = start_date + datetime.timedelta(days=6)
    elif period_type == 'month':
        start_date = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end_date = today.replace(day=last_day)
    elif period_type == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(month=12, day=31)
    else:
        # По умолчанию возвращаем текущий месяц
        start_date = today.replace(day=1)
        last_day = calendar.monthrange(today.year, today.month)[1]
        end_date = today.replace(day=last_day)
    
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

# Функция для генерации тестовых данных (для демонстрации)
def generate_test_data():
    """
    Генерирует тестовые данные продаж для демонстрации возможностей бота.
    """
    conn = sqlite3.connect('analytics.db')
    cursor = conn.cursor()
    
    # Очищаем таблицу продаж
    cursor.execute("DELETE FROM sales")
    
    # Продукты для тестовых данных (цены в гривнах)
    products = [
        {"id": 1, "name": "Смартфон", "price_range": (15000, 40000)},
        {"id": 2, "name": "Ноутбук", "price_range": (25000, 85000)},
        {"id": 3, "name": "Наушники", "price_range": (1500, 9000)},
        {"id": 4, "name": "Планшет", "price_range": (8000, 30000)},
    ]
    
    # Генерируем данные за последние 30 дней
    import random
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=30)
    
    date_range = [start_date + datetime.timedelta(days=x) for x in range(31)]
    
    for date in date_range:
        # Генерируем от 5 до 15 продаж в день
        for _ in range(random.randint(5, 15)):
            product = random.choice(products)
            price = random.uniform(product["price_range"][0], product["price_range"][1])
            user_id = random.randint(100000, 999999)
            
            cursor.execute(
                "INSERT INTO sales (product_id, product_name, amount, date, user_id) VALUES (?, ?, ?, ?, ?)",
                (product["id"], product["name"], round(price, 2), date.strftime("%Y-%m-%d"), user_id)
            )
    
    conn.commit()
    conn.close()
    
    logging.info("Тестовые данные сгенерированы")

# Создаем клавиатуры для меню
def get_main_keyboard():
    """
    Создает основную клавиатуру с кнопками команд бота.
    
    Returns:
        ReplyKeyboardMarkup: Объект клавиатуры с кнопками команд
    """
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/report"), KeyboardButton(text="/stats")]
        ],
        resize_keyboard=True
    )
    return keyboard

def get_report_type_keyboard():
    """
    Создает клавиатуру для выбора типа отчета.
    
    Returns:
        InlineKeyboardMarkup: Объект инлайн-клавиатуры с типами отчетов
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Продажи", callback_data="report_sales")],
            [InlineKeyboardButton(text="Активность пользователей", callback_data="report_activity")]
        ]
    )
    return keyboard

def get_period_keyboard():
    """
    Создает клавиатуру для выбора периода отчета/статистики.
    
    Returns:
        InlineKeyboardMarkup: Объект инлайн-клавиатуры с периодами
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="День", callback_data="period_day")],
            [InlineKeyboardButton(text="Неделя", callback_data="period_week")],
            [InlineKeyboardButton(text="Месяц", callback_data="period_month")],
            [InlineKeyboardButton(text="Год", callback_data="period_year")],
            [InlineKeyboardButton(text="Указать диапазон", callback_data="period_custom")]
        ]
    )
    return keyboard

# Обработчики команд

@dp.message(Command("start"))
async def cmd_start(message: Message):
    """
    Обработчик команды /start.
    Отправляет приветственное сообщение и регистрирует пользователя.
    """
    user = message.from_user
    register_user(user.id, user.username, user.first_name, user.last_name)
    log_user_activity(user.id, 'start')
    
    await message.answer(
        f"Привет, {user.first_name}! Я бот для аналитики данных.\n\n"
        "Я могу помочь тебе собирать и анализировать данные, генерировать отчеты и экспортировать их в CSV.\n\n"
        "Доступные команды:\n"
        "/report - создать отчет\n"
        "/stats - просмотреть статистику",
        reply_markup=get_main_keyboard()
    )

@dp.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext):
    """
    Обработчик команды /report.
    Инициирует процесс создания отчета.
    """
    user = message.from_user
    log_user_activity(user.id, 'report')
    
    await state.set_state(ReportStates.waiting_for_report_type)
    await message.answer(
        "Какой тип отчета вы хотите создать?",
        reply_markup=get_report_type_keyboard()
    )

@dp.callback_query(F.data.startswith("report_"), ReportStates.waiting_for_report_type)
async def process_report_type(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора типа отчета.
    Сохраняет выбранный тип и запрашивает период.
    """
    await callback.answer()
    report_type = callback.data.split('_')[1]
    
    await state.update_data(report_type=report_type)
    await state.set_state(ReportStates.waiting_for_period)
    
    await callback.message.answer(
        f"Выбран тип отчета: {report_type}\n\nВыберите период:",
        reply_markup=get_period_keyboard()
    )

@dp.callback_query(F.data.startswith("period_"), ReportStates.waiting_for_period)
async def process_report_period(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора периода для отчета.
    Обрабатывает выбор и генерирует отчет или запрашивает диапазон дат.
    """
    await callback.answer()
    period_type = callback.data.split('_')[1]
    
    if period_type == 'custom':
        await state.set_state(ReportStates.waiting_for_date_range)
        await callback.message.answer(
            "Введите диапазон дат в формате YYYY-MM-DD - YYYY-MM-DD"
        )
    else:
        start_date, end_date = get_date_range(period_type)
        await generate_report(callback.from_user.id, state, start_date, end_date, period_type)

@dp.message(ReportStates.waiting_for_date_range)
async def process_report_date_range(message: Message, state: FSMContext):
    """
    Обработчик ввода диапазона дат для отчета.
    Парсит введенный диапазон и генерирует отчет.
    """
    try:
        date_range = message.text.strip().split(' - ')
        start_date = date_range[0].strip()
        end_date = date_range[1].strip()
        
        # Проверяем корректность формата дат
        datetime.datetime.strptime(start_date, "%Y-%m-%d")
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        
        await generate_report(message.from_user.id, state, start_date, end_date, 'custom')
    except (ValueError, IndexError):
        await message.answer(
            "Неверный формат дат. Пожалуйста, введите диапазон в формате YYYY-MM-DD - YYYY-MM-DD"
        )

async def generate_report(user_id, state, start_date, end_date, period_type):
    """
    Генерирует отчет на основе выбранных параметров.
    
    Args:
        user_id (int): ID пользователя в Telegram
        state (FSMContext): Контекст состояния FSM
        start_date (str): Начальная дата в формате 'YYYY-MM-DD'
        end_date (str): Конечная дата в формате 'YYYY-MM-DD'
        period_type (str): Тип периода ('day', 'week', 'month', 'year', 'custom')
    """
    period_names = {
        'day': 'день',
        'week': 'неделю',
        'month': 'месяц',
        'year': 'год',
        'custom': f'период {start_date} - {end_date}'
    }
    
    period_name = period_names[period_type]
    
    data = await state.get_data()
    report_type = data.get('report_type')
    
    try:
        await send_message_with_retry(
            user_id,
            f"Генерирую отчет типа '{report_type}' за {period_name}..."
        )
        
        if report_type == 'sales':
            # Получаем данные о продажах
            df = get_sales_data(start_date, end_date)
            
            if df.empty:
                await send_message_with_retry(
                    user_id,
                    f"Нет данных о продажах за {period_name}."
                )
                await state.clear()
                return
            
            # Генерируем график
            chart_path = generate_sales_chart(df, period_name)
            
            # Отправляем график с повторными попытками
            await send_photo_with_retry(
                user_id,
                FSInputFile(chart_path),
                caption=f"График продаж за {period_name}"
            )
            
            # Экспортируем в CSV
            csv_filename = f"sales_report_{start_date}_to_{end_date}"
            csv_path = export_to_csv(df, csv_filename)
            
            # Отправляем CSV файл с повторными попытками
            await send_document_with_retry(
                user_id,
                FSInputFile(csv_path),
                caption=f"Отчет о продажах за {period_name} в формате CSV"
            )
            
            # Удаляем временные файлы
            try:
                os.remove(csv_path)
                os.remove(chart_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении временных файлов: {e}")
            
        elif report_type == 'activity':
            # Получаем данные об активности пользователей
            df = get_user_activity_data(start_date, end_date)
            
            if df.empty:
                await send_message_with_retry(
                    user_id,
                    f"Нет данных об активности пользователей за {period_name}."
                )
                await state.clear()
                return
            
            # Генерируем график
            chart_path = generate_activity_chart(df, period_name)
            
            # Отправляем график с повторными попытками
            await send_photo_with_retry(
                user_id,
                FSInputFile(chart_path),
                caption=f"График активности пользователей за {period_name}"
            )
            
            # Экспортируем в CSV
            csv_filename = f"activity_report_{start_date}_to_{end_date}"
            csv_path = export_to_csv(df, csv_filename)
            
            # Отправляем CSV файл с повторными попытками
            await send_document_with_retry(
                user_id,
                FSInputFile(csv_path),
                caption=f"Отчет об активности пользователей за {period_name} в формате CSV"
            )
            
            # Удаляем временные файлы
            try:
                os.remove(csv_path)
                os.remove(chart_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении временных файлов: {e}")
    
    except Exception as e:
        logging.error(f"Ошибка при генерации отчета: {e}")
        try:
            await send_message_with_retry(
                user_id,
                f"Произошла ошибка при генерации отчета: {str(e)}"
            )
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
    
    await state.clear()
    try:
        await send_message_with_retry(
            user_id,
            "Отчет сгенерирован успешно!",
            reply_markup=get_main_keyboard()
        )
    except Exception as send_error:
        logging.error(f"Не удалось отправить финальное сообщение: {send_error}")

@dp.message(Command("stats"))
async def cmd_stats(message: Message, state: FSMContext):
    """
    Обработчик команды /stats.
    Инициирует процесс просмотра статистики.
    """
    user = message.from_user
    log_user_activity(user.id, 'stats')
    
    await state.set_state(StatsStates.waiting_for_stats_type)
    await message.answer(
        "Какую статистику вы хотите посмотреть?",
        reply_markup=get_report_type_keyboard()
    )

@dp.callback_query(F.data.startswith("report_"), StatsStates.waiting_for_stats_type)
async def process_stats_type(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора типа статистики.
    Сохраняет выбранный тип и запрашивает период.
    """
    await callback.answer()
    stats_type = callback.data.split('_')[1]
    
    await state.update_data(stats_type=stats_type)
    await state.set_state(StatsStates.waiting_for_period)
    
    await callback.message.answer(
        f"Выбран тип статистики: {stats_type}\n\nВыберите период:",
        reply_markup=get_period_keyboard()
    )

@dp.callback_query(F.data.startswith("period_"), StatsStates.waiting_for_period)
async def process_stats_period(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора периода для статистики.
    Обрабатывает выбор и генерирует статистику или запрашивает диапазон дат.
    """
    await callback.answer()
    period_type = callback.data.split('_')[1]
    
    if period_type == 'custom':
        await state.set_state(StatsStates.waiting_for_date_range)
        await callback.message.answer(
            "Введите диапазон дат в формате YYYY-MM-DD - YYYY-MM-DD"
        )
    else:
        start_date, end_date = get_date_range(period_type)
        await show_statistics(callback.from_user.id, state, start_date, end_date, period_type)

@dp.message(StatsStates.waiting_for_date_range)
async def process_stats_date_range(message: Message, state: FSMContext):
    """
    Обработчик ввода диапазона дат для статистики.
    Парсит введенный диапазон и показывает статистику.
    """
    try:
        date_range = message.text.strip().split(' - ')
        start_date = date_range[0].strip()
        end_date = date_range[1].strip()
        
        # Проверяем корректность формата дат
        datetime.datetime.strptime(start_date, "%Y-%m-%d")
        datetime.datetime.strptime(end_date, "%Y-%m-%d")
        
        await show_statistics(message.from_user.id, state, start_date, end_date, 'custom')
    except (ValueError, IndexError):
        await message.answer(
            "Неверный формат дат. Пожалуйста, введите диапазон в формате YYYY-MM-DD - YYYY-MM-DD"
        )

async def show_statistics(user_id, state, start_date, end_date, period_type):
    """
    Показывает статистику на основе выбранных параметров.
    
    Args:
        user_id (int): ID пользователя в Telegram
        state (FSMContext): Контекст состояния FSM
        start_date (str): Начальная дата в формате 'YYYY-MM-DD'
        end_date (str): Конечная дата в формате 'YYYY-MM-DD'
        period_type (str): Тип периода ('day', 'week', 'month', 'year', 'custom')
    """
    period_names = {
        'day': 'день',
        'week': 'неделю',
        'month': 'месяц',
        'year': 'год',
        'custom': f'период {start_date} - {end_date}'
    }
    
    period_name = period_names[period_type]
    
    data = await state.get_data()
    stats_type = data.get('stats_type')
    
    try:
        await send_message_with_retry(
            user_id,
            f"Загружаю статистику типа '{stats_type}' за {period_name}..."
        )
        
        if stats_type == 'sales':
            # Получаем данные о продажах
            df = get_sales_data(start_date, end_date)
            
            if df.empty:
                await send_message_with_retry(
                    user_id,
                    f"Нет данных о продажах за {period_name}."
                )
                await state.clear()
                return
            
            # Анализируем данные
            total_sales = df['total_amount'].sum()
            product_sales = df.groupby('product_name')['total_amount'].sum().sort_values(ascending=False)
            
            # Формируем текстовый отчет
            stats_text = f"📊 Статистика продаж за {period_name}:\n\n"
            stats_text += f"📈 Общая сумма продаж: {total_sales:.2f} грн\n\n"
            stats_text += "🏆 Продажи по товарам:\n"
            
            for product, amount in product_sales.items():
                stats_text += f"- {product}: {amount:.2f} грн ({(amount/total_sales*100):.1f}%)\n"
            
            await send_message_with_retry(user_id, stats_text)
            
            # Генерируем график
            chart_path = generate_sales_chart(df, period_name)
            
            # Отправляем график с повторными попытками
            await send_photo_with_retry(
                user_id,
                FSInputFile(chart_path),
                caption=f"График продаж за {period_name}"
            )
            
            # Удаляем временный файл
            try:
                os.remove(chart_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении временного файла: {e}")
            
        elif stats_type == 'activity':
            # Получаем данные об активности пользователей
            df = get_user_activity_data(start_date, end_date)
            
            if df.empty:
                await send_message_with_retry(
                    user_id,
                    f"Нет данных об активности пользователей за {period_name}."
                )
                await state.clear()
                return
            
            # Анализируем данные
            total_actions = df['action_count'].sum()
            action_types = df.groupby('action_type')['action_count'].sum().sort_values(ascending=False)
            active_users = df.groupby('username')['action_count'].sum().sort_values(ascending=False).head(5)
            
            # Формируем текстовый отчет
            stats_text = f"📊 Статистика активности за {period_name}:\n\n"
            stats_text += f"📈 Общее количество действий: {total_actions}\n\n"
            stats_text += "🔍 Распределение по типам действий:\n"
            
            for action, count in action_types.items():
                stats_text += f"- {action}: {count} ({(count/total_actions*100):.1f}%)\n"
            
            stats_text += "\n👥 Самые активные пользователи:\n"
            
            for user, count in active_users.items():
                stats_text += f"- {user}: {count} действий\n"
            
            await send_message_with_retry(user_id, stats_text)
            
            # Генерируем график
            chart_path = generate_activity_chart(df, period_name)
            
            # Отправляем график с повторными попытками
            await send_photo_with_retry(
                user_id,
                FSInputFile(chart_path),
                caption=f"График активности пользователей за {period_name}"
            )
            
            # Удаляем временный файл
            try:
                os.remove(chart_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении временного файла: {e}")
    
    except Exception as e:
        logging.error(f"Ошибка при показе статистики: {e}")
        try:
            await send_message_with_retry(
                user_id,
                f"Произошла ошибка при загрузке статистики: {str(e)}"
            )
        except Exception as send_error:
            logging.error(f"Не удалось отправить сообщение об ошибке: {send_error}")
    
    await state.clear()
    try:
        await send_message_with_retry(
            user_id,
            "Статистика загружена успешно!",
            reply_markup=get_main_keyboard()
        )
    except Exception as send_error:
        logging.error(f"Не удалось отправить финальное сообщение: {send_error}")

# Вспомогательная функция для отправки сообщений с повторными попытками
async def send_message_with_retry(chat_id, text, reply_markup=None, max_retries=5, initial_delay=1):
    """
    Отправляет сообщение с механизмом повторных попыток в случае ошибок соединения.
    
    Args:
        chat_id (int): ID чата назначения
        text (str): Текст сообщения
        reply_markup: Опциональная клавиатура
        max_retries (int): Максимальное количество повторных попыток
        initial_delay (float): Начальная задержка между попытками в секундах
    
    Returns:
        Message: Объект сообщения в случае успеха
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await bot.send_message(chat_id, text, reply_markup=reply_markup)
        except TelegramRetryAfter as e:
            # Если Telegram просит подождать
            logging.warning(f"Telegram просит подождать {e.retry_after} секунд. Ждем...")
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, aiohttp.ClientError) as e:
            last_exception = e
            logging.error(f"Ошибка соединения при отправке сообщения (попытка {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)
            delay *= 2  # Экспоненциальное увеличение задержки
        except Exception as e:
            last_exception = e
            logging.error(f"Неизвестная ошибка при отправке сообщения: {e}")
            await asyncio.sleep(delay)
            delay *= 2
    
    # Если все попытки исчерпаны
    raise last_exception if last_exception else Exception("Не удалось отправить сообщение после нескольких попыток")

# Вспомогательная функция для отправки фото с повторными попытками
async def send_photo_with_retry(chat_id, photo, caption=None, max_retries=5, initial_delay=1):
    """
    Отправляет фото с механизмом повторных попыток в случае ошибок соединения.
    
    Args:
        chat_id (int): ID чата назначения
        photo: Файл или ID фото
        caption (str): Подпись к фото
        max_retries (int): Максимальное количество повторных попыток
        initial_delay (float): Начальная задержка между попытками в секундах
    
    Returns:
        Message: Объект сообщения в случае успеха
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await bot.send_photo(chat_id, photo, caption=caption)
        except TelegramRetryAfter as e:
            # Если Telegram просит подождать
            logging.warning(f"Telegram просит подождать {e.retry_after} секунд. Ждем...")
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, aiohttp.ClientError) as e:
            last_exception = e
            logging.error(f"Ошибка соединения при отправке фото (попытка {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)
            delay *= 2  # Экспоненциальное увеличение задержки
        except Exception as e:
            last_exception = e
            logging.error(f"Неизвестная ошибка при отправке фото: {e}")
            await asyncio.sleep(delay)
            delay *= 2
    
    # Если все попытки исчерпаны
    raise last_exception if last_exception else Exception("Не удалось отправить фото после нескольких попыток")

# Вспомогательная функция для отправки документа с повторными попытками
async def send_document_with_retry(chat_id, document, caption=None, max_retries=5, initial_delay=1):
    """
    Отправляет документ с механизмом повторных попыток в случае ошибок соединения.
    
    Args:
        chat_id (int): ID чата назначения
        document: Файл или ID документа
        caption (str): Подпись к документу
        max_retries (int): Максимальное количество повторных попыток
        initial_delay (float): Начальная задержка между попытками в секундах
    
    Returns:
        Message: Объект сообщения в случае успеха
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return await bot.send_document(chat_id, document, caption=caption)
        except TelegramRetryAfter as e:
            # Если Telegram просит подождать
            logging.warning(f"Telegram просит подождать {e.retry_after} секунд. Ждем...")
            await asyncio.sleep(e.retry_after)
        except (TelegramNetworkError, aiohttp.ClientError) as e:
            last_exception = e
            logging.error(f"Ошибка соединения при отправке документа (попытка {attempt+1}/{max_retries}): {e}")
            await asyncio.sleep(delay)
            delay *= 2  # Экспоненциальное увеличение задержки
        except Exception as e:
            last_exception = e
            logging.error(f"Неизвестная ошибка при отправке документа: {e}")
            await asyncio.sleep(delay)
            delay *= 2
    
    # Если все попытки исчерпаны
    raise last_exception if last_exception else Exception("Не удалось отправить документ после нескольких попыток")

# Очистка временных файлов
def cleanup_temp_files(directory='temp_charts'):
    """
    Удаляет временные файлы из указанной директории.
    
    Args:
        directory (str): Путь к директории с временными файлами
    """
    if os.path.exists(directory):
        for file in os.listdir(directory):
            file_path = os.path.join(directory, file)
            try:
                if os.path.isfile(file_path):
                    os.unlink(file_path)
            except Exception as e:
                logging.error(f"Ошибка при удалении файла {file_path}: {e}")

# Запуск бота
async def main():
    try:
        # Инициализация базы данных
        init_db()
        # Для тестирования генерируем тестовые данные
        generate_test_data()
        
        # Очистка временных файлов перед запуском
        cleanup_temp_files()
        
        # Запуск бота
        await dp.start_polling(bot)
    except Exception as e:
        logging.error(f"Критическая ошибка при запуске бота: {e}")
    finally:
        # Очистка временных файлов при завершении
        cleanup_temp_files()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен")
    except Exception as e:
        logging.critical(f"Необработанное исключение: {e}")