# -*- coding: utf-8 -*-
"""
Позиции ПИФов РФ в акциях — дашборд на Streamlit.

Режимы:
  • «Анализ по фондам» — состав портфеля выбранного фонда;
  • «Анализ по акциям (Кто держит бумагу?)» — какие фонды держат выбранную акцию.

Источник данных: страницы фондов на Investfunds.ru (блок «Структура фонда», ТОП-активы
и СЧА). Если сайт недоступен (403/таймаут), работает встроенная база.

Запуск локально:  streamlit run app.py
"""
from __future__ import annotations

import io
import random
import re
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup

# =============================================================================
# 1. КОНСТАНТЫ И СПРАВОЧНИКИ
# =============================================================================

# --- Названия колонок базы данных (они же — колонки CSV-выгрузки) ---
C_FUND = "Фонд"
C_UK = "Управляющая компания"
C_TYPE = "Тип фонда"
C_NAV = "СЧА, млн руб."
C_DATE = "Дата структуры"
C_NAV_DATE = "Дата СЧА"
C_URL = "Ссылка"
C_TICKER = "Тикер"
C_COMPANY = "Компания"
C_SECTOR = "Сектор"
C_ASSET = "Тип актива"
C_WEIGHT = "Доля в фонде, %"
C_VALUE = "Оценочная стоимость позиции, млн руб."
C_SOURCE = "Источник данных"

ALL_COLS = [
    C_FUND, C_UK, C_TYPE, C_NAV, C_DATE, C_NAV_DATE, C_URL,
    C_TICKER, C_COMPANY, C_SECTOR, C_ASSET, C_WEIGHT, C_VALUE, C_SOURCE,
]

SRC_BUILTIN = "Встроенная база"
SRC_SITE = "Investfunds.ru"
SRC_USER = "Файл пользователя"

NO_TICKER = "—"
ASSET_EQUITY = "Акции"
ASSET_BONDS = "Облигации"
ASSET_OTHER = "Прочее"

BASE_URL = "https://investfunds.ru"
CATALOG_URL = "https://investfunds.ru/funds/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5",
}

# --- Реестр фондов с акциями на Investfunds.ru ---
# Для трёх фондов точная страница не указана (стоит общий адрес сайта):
# их данные берутся из встроенной базы. Чтобы включить парсинг, замените
# адрес на вид https://investfunds.ru/funds/<номер>/
FUNDS_URLS: dict[str, str] = {
    # УК «Первая»
    "Первая — Фонд Топ Российских акций (SBMX)": "https://investfunds.ru/funds/5247/",
    "Первая — Фонд российских акций": "https://investfunds.ru",
    "Первая — Фонд акций с выплатой дохода": "https://investfunds.ru",
    "Первая — Акции средней и малой капитализации (SBSC)": "https://investfunds.ru/funds/9469/",
    # УК «ВИМ Инвестиции»
    "ВИМ — Индекс МосБиржи (EQMX)": "https://investfunds.ru/funds/6073/",
    "ВИМ — Индекс МосБиржи (ОПИФ)": "https://investfunds.ru/funds/268/",
    "ВИМ — Акции": "https://investfunds.ru/funds/1003/",
    "ВИМ — Акции. Российские эмитенты": "https://investfunds.ru/funds/7493/",
    "ВИМ — Сбалансированный": "https://investfunds.ru/funds/113/",
    # УК «Альфа-Капитал»
    "Альфа-Капитал — Управляемые акции (AKME)": "https://investfunds.ru/funds/6575/",
    "Альфа-Капитал — Управляемые акции с выплатой дохода (AKIE)": "https://investfunds.ru/funds/11109/",
    "Альфа-Капитал — Ликвидные акции": "https://investfunds.ru/funds/432/",
    "Альфа-Капитал — Акции компаний роста": "https://investfunds.ru/funds/9635/",
    "Альфа-Капитал — Акции с выплатой дохода": "https://investfunds.ru/funds/9423/",
    "Альфа-Капитал (ИПИФ)": "https://investfunds.ru/funds/105/",
    # УК «Т-Капитал»
    "Т-Капитал — Индекс МосБиржи (TMOS)": "https://investfunds.ru/funds/6333/",
    "Т-Капитал — Акции роста (TITR)": "https://investfunds.ru/funds/10421/",
    "Т-Капитал — Компании второго эшелона": "https://investfunds.ru",
    # Прочие крупные УК
    "АТОН — Российские акции + (AMRE)": "https://investfunds.ru/funds/6809/",
    "Райффайзен — Акции": "https://investfunds.ru/funds/281/",
    "Промсвязь — Просто. Акции": "https://investfunds.ru/funds/374/",
}

# --- Секторы ---
S_FIN = "Финансы"
S_OIL = "Нефть и газ"
S_MET = "Металлы и добыча"
S_IT = "IT и технологии"
S_CONS = "Потребительский сектор"
S_TEL = "Телекоммуникации"
S_ENE = "Электроэнергетика"
S_CHEM = "Химия и удобрения"
S_TRN = "Транспорт и логистика"
S_DEV = "Девелопмент"
S_MED = "Здравоохранение"
S_HOLD = "Холдинги"
S_IND = "Промышленность"
S_BONDS = "Облигации"
S_CASH = "Денежные средства и прочее"
S_OTHER = "Прочее"

# --- Справочник эмитентов: тикер МосБиржи → (название на русском, сектор) ---
COMPANIES: dict[str, tuple[str, str]] = {
    "SBER": ("Сбербанк", S_FIN), "SBERP": ("Сбербанк (прив.)", S_FIN),
    "VTBR": ("ВТБ", S_FIN), "T": ("Т-Технологии", S_FIN),
    "MOEX": ("Московская биржа", S_FIN), "CBOM": ("МКБ", S_FIN),
    "SVCB": ("Совкомбанк", S_FIN), "BSPB": ("Банк Санкт-Петербург", S_FIN),
    "RENI": ("Ренессанс Страхование", S_FIN), "SFIN": ("ЭсЭфАй", S_FIN),
    "MBNK": ("МТС-Банк", S_FIN), "SPBE": ("СПБ Биржа", S_FIN),
    "LEAS": ("Европлан", S_FIN),
    "LKOH": ("ЛУКОЙЛ", S_OIL), "GAZP": ("Газпром", S_OIL),
    "NVTK": ("НОВАТЭК", S_OIL), "ROSN": ("Роснефть", S_OIL),
    "TATN": ("Татнефть", S_OIL), "TATNP": ("Татнефть (прив.)", S_OIL),
    "SNGS": ("Сургутнефтегаз", S_OIL), "SNGSP": ("Сургутнефтегаз (прив.)", S_OIL),
    "SIBN": ("Газпром нефть", S_OIL), "TRNFP": ("Транснефть (прив.)", S_OIL),
    "BANEP": ("Башнефть (прив.)", S_OIL),
    "GMKN": ("Норникель", S_MET), "PLZL": ("Полюс", S_MET),
    "CHMF": ("Северсталь", S_MET), "NLMK": ("НЛМК", S_MET),
    "MAGN": ("ММК", S_MET), "RUAL": ("РУСАЛ", S_MET),
    "ALRS": ("АЛРОСА", S_MET), "MTLR": ("Мечел", S_MET),
    "MTLRP": ("Мечел (прив.)", S_MET), "RASP": ("Распадская", S_MET),
    "SELG": ("Селигдар", S_MET), "UGLD": ("ЮГК", S_MET),
    "VSMO": ("ВСМПО-АВИСМА", S_MET), "TRMK": ("ТМК", S_MET),
    "ENPG": ("ЭН+ Груп", S_MET),
    "PHOR": ("ФосАгро", S_CHEM), "AKRN": ("Акрон", S_CHEM),
    "NKNC": ("Нижнекамскнефтехим", S_CHEM), "KZOS": ("Казаньоргсинтез", S_CHEM),
    "YDEX": ("Яндекс", S_IT), "OZON": ("Озон", S_IT), "HEAD": ("Хэдхантер", S_IT),
    "POSI": ("Позитив Текнолоджиз", S_IT), "ASTR": ("Группа Астра", S_IT),
    "VKCO": ("ВК", S_IT), "DATA": ("Аренадата", S_IT), "DIAS": ("Диасофт", S_IT),
    "SOFL": ("Софтлайн", S_IT), "CNRU": ("Циан", S_IT),
    "IVAT": ("ИВА Технологии", S_IT), "ELMT": ("Элемент", S_IT),
    "MTSS": ("МТС", S_TEL), "RTKM": ("Ростелеком", S_TEL),
    "X5": ("Икс 5", S_CONS), "MGNT": ("Магнит", S_CONS),
    "LENT": ("Лента", S_CONS), "FIXP": ("Фикс Прайс", S_CONS),
    "AQUA": ("Инарктика", S_CONS), "RAGR": ("Русагро", S_CONS),
    "BELU": ("Новабев", S_CONS), "MVID": ("М.Видео", S_CONS),
    "HNFG": ("Хэндерсон", S_CONS),
    "IRAO": ("Интер РАО", S_ENE), "FEES": ("Россети", S_ENE),
    "HYDR": ("РусГидро", S_ENE), "UPRO": ("Юнипро", S_ENE),
    "MSRS": ("Россети Московский регион", S_ENE),
    "MRKP": ("Россети Центр и Приволжье", S_ENE),
    "ELFV": ("ЭЛ5-Энерго", S_ENE), "OGKB": ("ОГК-2", S_ENE),
    "TGKA": ("ТГК-1", S_ENE),
    "AFLT": ("Аэрофлот", S_TRN), "FLOT": ("Совкомфлот", S_TRN),
    "NMTP": ("НМТП", S_TRN), "EUTR": ("ЕвроТранс", S_TRN),
    "FESH": ("ДВМП", S_TRN), "WUSH": ("ВУШ Холдинг", S_TRN),
    "DELI": ("Каршеринг Руссия", S_TRN),
    "PIKK": ("ПИК", S_DEV), "SMLT": ("Самолёт", S_DEV),
    "LSRG": ("ЛСР", S_DEV), "ETLN": ("Эталон", S_DEV),
    "MDMG": ("Мать и дитя", S_MED), "GEMC": ("ЮМГ", S_MED),
    "OZPH": ("Озон Фармацевтика", S_MED), "ABIO": ("Артген", S_MED),
    "AFKS": ("АФК Система", S_HOLD),
    "SGZH": ("Сегежа", S_IND), "KMAZ": ("КАМАЗ", S_IND),
}

# Специальные «псевдотикеры» для неакционных строк встроенной базы
SPECIAL_ROWS: dict[str, tuple[str, str, str]] = {
    "#OFZ": ("ОФЗ (государственные облигации)", S_BONDS, ASSET_BONDS),
    "#CORP": ("Корпоративные облигации", S_BONDS, ASSET_BONDS),
}
CASH_NAME = "Денежные средства и прочие активы"
REST_NAME = "Прочие активы (вне ТОП-10)"

# --- Синонимы названий эмитентов на Investfunds.ru → тикер ---
ALIASES: dict[str, str] = {
    "сбербанк россии": "SBER", "сбербанк": "SBER", "сбер банк": "SBER",
    "лукойл": "LKOH", "газпром": "GAZP", "газпром нефть": "SIBN",
    "т-технологии": "T", "т технологии": "T", "тинькофф": "T", "ткс холдинг": "T",
    "новатэк": "NVTK", "норильский никель": "GMKN", "норникель": "GMKN",
    "яндекс": "YDEX", "полюс": "PLZL", "татнефть": "TATN",
    "икс 5": "X5", "икс5": "X5", "x5": "X5", "корпоративный центр икс 5": "X5",
    "роснефть": "ROSN", "сургутнефтегаз": "SNGS", "транснефть": "TRNFP",
    "башнефть": "BANEP", "магнит": "MGNT", "северсталь": "CHMF",
    "нлмк": "NLMK", "новолипецкий металлургический комбинат": "NLMK",
    "ммк": "MAGN", "магнитогорский металлургический комбинат": "MAGN",
    "русал": "RUAL", "объединенная компания русал": "RUAL",
    "алроса": "ALRS", "мечел": "MTLR", "распадская": "RASP",
    "селигдар": "SELG", "южуралзолото": "UGLD", "югк": "UGLD",
    "всмпо-ависма": "VSMO", "корпорация всмпо-ависма": "VSMO",
    "трубная металлургическая компания": "TRMK", "тмк": "TRMK",
    "эн+ груп": "ENPG", "эн+": "ENPG", "фосагро": "PHOR", "акрон": "AKRN",
    "нижнекамскнефтехим": "NKNC", "казаньоргсинтез": "KZOS",
    "банк втб": "VTBR", "втб": "VTBR",
    "московская биржа": "MOEX", "московская биржа ммвб-ртс": "MOEX",
    "московский кредитный банк": "CBOM", "мкб": "CBOM",
    "совкомбанк": "SVCB", "банк санкт-петербург": "BSPB",
    "ренессанс страхование": "RENI", "группа ренессанс страхование": "RENI",
    "эсэфай": "SFIN", "мтс-банк": "MBNK", "мтс банк": "MBNK",
    "спб биржа": "SPBE", "европлан": "LEAS", "лк европлан": "LEAS",
    "мобильные телесистемы": "MTSS", "мтс": "MTSS", "ростелеком": "RTKM",
    "озон": "OZON", "интернет решения": "OZON", "хэдхантер": "HEAD", "headhunter": "HEAD",
    "позитив текнолоджиз": "POSI", "positive technologies": "POSI",
    "группа астра": "ASTR", "международная компания вк": "VKCO", "вк": "VKCO",
    "аренадата": "DATA", "группа аренадата": "DATA", "диасофт": "DIAS",
    "софтлайн": "SOFL", "циан": "CNRU", "ива технологии": "IVAT",
    "элемент": "ELMT", "вуш холдинг": "WUSH", "вуш": "WUSH",
    "каршеринг руссия": "DELI", "аэрофлот": "AFLT", "совкомфлот": "FLOT",
    "новороссийский морской торговый порт": "NMTP", "нмтп": "NMTP",
    "евротранс": "EUTR", "дальневосточное морское пароходство": "FESH",
    "интер рао": "IRAO", "интер рао еэс": "IRAO", "россети": "FEES",
    "федеральная сетевая компания": "FEES", "русгидро": "HYDR", "юнипро": "UPRO",
    "россети московский регион": "MSRS", "россети центр и приволжье": "MRKP",
    "эл5-энерго": "ELFV", "энел россия": "ELFV", "огк-2": "OGKB", "тгк-1": "TGKA",
    "пик": "PIKK", "пик-специализированный застройщик": "PIKK",
    "самолет": "SMLT", "группа компаний самолет": "SMLT", "лср": "LSRG",
    "группа лср": "LSRG", "эталон": "ETLN",
    "мать и дитя": "MDMG", "мд медикал груп": "MDMG", "юмг": "GEMC",
    "озон фармацевтика": "OZPH", "артген": "ABIO", "афк система": "AFKS",
    "сегежа": "SGZH", "сегежа групп": "SGZH", "камаз": "KMAZ",
    "инарктика": "AQUA", "русагро": "RAGR", "новабев": "BELU",
    "новабев групп": "BELU", "белуга": "BELU", "лента": "LENT",
    "фикс прайс": "FIXP", "м.видео": "MVID", "хэндерсон": "HNFG",
}
# Привилегированные акции: обыкновенный тикер → тикер «префов»
PREF_MAP = {"SBER": "SBERP", "TATN": "TATNP", "SNGS": "SNGSP", "MTLR": "MTLRP"}

# --- Метаданные фондов: УК, тип, ориентировочная СЧА (млн руб.) ---
FUND_META: dict[str, dict] = {
    "Первая — Фонд Топ Российских акций (SBMX)": dict(uk="Первая", type="БПИФ", nav=10450.0),
    "Первая — Фонд российских акций": dict(uk="Первая", type="ОПИФ", nav=21300.0),
    "Первая — Фонд акций с выплатой дохода": dict(uk="Первая", type="ОПИФ", nav=3650.0),
    "Первая — Акции средней и малой капитализации (SBSC)": dict(uk="Первая", type="БПИФ", nav=1250.0),
    "ВИМ — Индекс МосБиржи (EQMX)": dict(uk="ВИМ Инвестиции", type="БПИФ", nav=13500.7, nav_date="16.11.2026"),
    "ВИМ — Индекс МосБиржи (ОПИФ)": dict(uk="ВИМ Инвестиции", type="ОПИФ", nav=1380.0),
    "ВИМ — Акции": dict(uk="ВИМ Инвестиции", type="ОПИФ", nav=2640.0),
    "ВИМ — Акции. Российские эмитенты": dict(uk="ВИМ Инвестиции", type="ОПИФ", nav=980.0),
    "ВИМ — Сбалансированный": dict(uk="ВИМ Инвестиции", type="ОПИФ", nav=1520.0),
    "Альфа-Капитал — Управляемые акции (AKME)": dict(uk="Альфа-Капитал", type="БПИФ", nav=4620.0),
    "Альфа-Капитал — Управляемые акции с выплатой дохода (AKIE)": dict(uk="Альфа-Капитал", type="БПИФ", nav=1840.0),
    "Альфа-Капитал — Ликвидные акции": dict(uk="Альфа-Капитал", type="ОПИФ", nav=7150.0),
    "Альфа-Капитал — Акции компаний роста": dict(uk="Альфа-Капитал", type="ОПИФ", nav=2070.0),
    "Альфа-Капитал — Акции с выплатой дохода": dict(uk="Альфа-Капитал", type="ОПИФ", nav=4080.0),
    "Альфа-Капитал (ИПИФ)": dict(uk="Альфа-Капитал", type="ИПИФ", nav=620.0),
    "Т-Капитал — Индекс МосБиржи (TMOS)": dict(uk="Т-Капитал", type="БПИФ", nav=12300.0),
    "Т-Капитал — Акции роста (TITR)": dict(uk="Т-Капитал", type="БПИФ", nav=860.0),
    "Т-Капитал — Компании второго эшелона": dict(uk="Т-Капитал", type="ОПИФ", nav=740.0),
    "АТОН — Российские акции + (AMRE)": dict(uk="АТОН", type="БПИФ", nav=540.0),
    "Райффайзен — Акции": dict(uk="Райффайзен Капитал", type="ОПИФ", nav=9150.0),
    "Промсвязь — Просто. Акции": dict(uk="ПСБ Управление активами", type="ОПИФ", nav=3060.0),
}
DEFAULT_STRUCT_DATE = "30.09.2026"

# --- Веса индекса МосБиржи (ориентир для индексных фондов), % ---
INDEX_BASE: dict[str, float] = {
    "LKOH": 15.56, "SBER": 12.51, "GAZP": 9.37, "T": 5.62, "NVTK": 5.35, "YDEX": 5.18,
    "GMKN": 4.96, "PLZL": 4.71, "TATN": 3.58, "X5": 3.32, "ROSN": 2.86, "OZON": 2.02,
    "SNGSP": 1.98, "VTBR": 1.71, "MGNT": 1.52, "CHMF": 1.44, "MOEX": 1.38, "PHOR": 1.26,
    "SBERP": 1.12, "IRAO": 1.08, "NLMK": 1.02, "TRNFP": 0.98, "SNGS": 0.88, "MTSS": 0.86,
    "AFLT": 0.79, "ALRS": 0.74, "RUAL": 0.72, "HEAD": 0.71, "TATNP": 0.62, "SVCB": 0.55,
    "PIKK": 0.52, "ENPG": 0.49, "MAGN": 0.48, "CBOM": 0.41, "AFKS": 0.40, "RTKM": 0.38,
    "HYDR": 0.37, "POSI": 0.33, "VKCO": 0.30, "FEES": 0.28, "BSPB": 0.27, "UPRO": 0.21,
    "LEAS": 0.15, "SMLT": 0.12,
}

# --- Составы активных фондов (тикер → доля, %); остаток — денежные средства ---
ACTIVE_HOLDINGS: dict[str, dict[str, float]] = {
    "Первая — Фонд российских акций": {
        "SBER": 11.5, "LKOH": 10.8, "GAZP": 6.2, "T": 5.8, "YDEX": 5.6, "NVTK": 5.5,
        "PLZL": 5.1, "GMKN": 4.9, "TATN": 4.2, "X5": 4.0, "HEAD": 3.1, "ROSN": 3.0,
        "OZON": 2.9, "SNGSP": 2.8, "MGNT": 1.9, "PHOR": 1.8, "MOEX": 1.7, "CHMF": 1.6,
        "TRNFP": 1.6, "IRAO": 1.5, "VTBR": 1.3, "MTSS": 1.2, "AFKS": 1.0, "SVCB": 1.0,
        "POSI": 0.9, "ALRS": 0.8,
    },
    "Первая — Фонд акций с выплатой дохода": {
        "SBERP": 10.5, "LKOH": 10.2, "TATNP": 7.4, "SNGSP": 6.8, "X5": 6.5, "MTSS": 5.2,
        "PHOR": 5.0, "HEAD": 4.8, "TRNFP": 4.6, "NVTK": 4.1, "MOEX": 3.9, "GMKN": 3.5,
        "CHMF": 3.2, "IRAO": 3.0, "ROSN": 2.9, "MGNT": 2.7, "BSPB": 2.4, "UPRO": 1.9,
        "NLMK": 1.8, "MAGN": 1.5,
    },
    "Первая — Акции средней и малой капитализации (SBSC)": {
        "HEAD": 8.7, "CBOM": 7.9, "AFKS": 6.1, "AFLT": 5.6, "PIKK": 5.4, "RUAL": 5.2,
        "ENPG": 4.8, "SVCB": 4.6, "HYDR": 4.4, "BSPB": 4.3, "POSI": 4.0, "FEES": 3.9,
        "UPRO": 3.5, "FLOT": 3.3, "VKCO": 3.1, "SMLT": 3.0, "ASTR": 2.6, "NMTP": 2.4,
        "MDMG": 2.2, "LSRG": 2.1, "RENI": 1.8, "TGKA": 1.8, "AQUA": 1.6, "GEMC": 1.5,
        "MTLR": 1.5, "SGZH": 1.0, "ELFV": 0.4, "OGKB": 0.4, "EUTR": 0.4, "WUSH": 0.3,
        "SFIN": 0.3,
    },
    "ВИМ — Акции": {
        "SBER": 12.8, "LKOH": 12.1, "NVTK": 6.2, "YDEX": 6.0, "GAZP": 5.9, "PLZL": 5.5,
        "T": 5.4, "GMKN": 4.6, "TATN": 4.4, "X5": 4.3, "OZON": 3.6, "HEAD": 2.9,
        "ROSN": 2.7, "SNGSP": 2.5, "VTBR": 2.4, "MOEX": 2.1, "PHOR": 1.9, "CHMF": 1.7,
        "MTSS": 1.4, "AFLT": 1.3, "POSI": 1.1, "PIKK": 1.0,
    },
    "ВИМ — Акции. Российские эмитенты": {
        "LKOH": 13.2, "SBER": 12.2, "GAZP": 7.1, "NVTK": 6.4, "T": 5.9, "YDEX": 5.7,
        "GMKN": 5.2, "PLZL": 4.9, "TATN": 4.1, "X5": 3.9, "ROSN": 3.1, "SNGSP": 2.6,
        "OZON": 2.5, "VTBR": 2.0, "MOEX": 1.9, "MGNT": 1.8, "CHMF": 1.5, "PHOR": 1.4,
        "IRAO": 1.3, "HEAD": 1.2, "ALRS": 0.9, "AFLT": 0.9,
    },
    "ВИМ — Сбалансированный": {
        "#OFZ": 28.0, "#CORP": 17.5,
        "SBER": 7.2, "LKOH": 6.8, "GAZP": 3.9, "NVTK": 3.4, "T": 3.1, "YDEX": 3.0,
        "PLZL": 2.9, "GMKN": 2.6, "TATN": 2.4, "X5": 2.2, "ROSN": 1.8, "SNGSP": 1.6,
        "MOEX": 1.4, "OZON": 1.3, "HEAD": 1.2,
    },
    "Альфа-Капитал — Управляемые акции (AKME)": {
        "SBER": 11.9, "LKOH": 10.4, "T": 7.2, "YDEX": 7.0, "OZON": 5.6, "PLZL": 5.3,
        "NVTK": 5.1, "X5": 4.8, "GAZP": 4.2, "GMKN": 3.9, "HEAD": 3.6, "TATN": 3.1,
        "SVCB": 2.6, "CBOM": 2.4, "MOEX": 2.2, "POSI": 1.9, "AFKS": 1.8, "MDMG": 1.5,
        "ASTR": 1.4, "PIKK": 1.3, "VKCO": 1.2, "RENI": 1.1, "SMLT": 0.8,
    },
    "Альфа-Капитал — Управляемые акции с выплатой дохода (AKIE)": {
        "SBER": 9.8, "LKOH": 9.5, "SNGSP": 7.1, "X5": 6.6, "TATN": 6.14, "MTSS": 5.2,
        "PHOR": 4.8, "HEAD": 4.5, "NVTK": 4.3, "MOEX": 3.9, "TRNFP": 3.58, "AFKS": 3.51,
        "IRAO": 3.2, "CHMF": 2.9, "BSPB": 2.7, "GMKN": 2.6, "ROSN": 2.5, "MGNT": 2.2,
        "UPRO": 1.6, "NLMK": 1.5,
    },
    "Альфа-Капитал — Ликвидные акции": {
        "SBER": 12.6, "LKOH": 11.1, "T": 6.6, "YDEX": 6.4, "GAZP": 5.6, "NVTK": 5.4,
        "PLZL": 5.2, "OZON": 4.7, "X5": 4.5, "GMKN": 4.3, "TATN": 3.4, "HEAD": 3.0,
        "ROSN": 2.6, "MOEX": 2.4, "SVCB": 2.1, "VTBR": 1.9, "SNGSP": 1.8, "CBOM": 1.6,
        "PHOR": 1.5, "AFKS": 1.4, "CHMF": 1.2, "POSI": 1.1,
    },
    "Альфа-Капитал — Акции компаний роста": {
        "OZON": 8.4, "YDEX": 8.1, "T": 7.6, "HEAD": 6.2, "POSI": 5.5, "ASTR": 4.7,
        "X5": 4.4, "CBOM": 4.2, "SVCB": 4.0, "MDMG": 3.8, "MOEX": 3.6, "AFKS": 3.4,
        "VKCO": 3.3, "PIKK": 3.0, "RENI": 2.4, "DATA": 2.2, "SMLT": 2.1, "CNRU": 1.9,
        "WUSH": 1.7, "DIAS": 1.6, "HNFG": 1.5, "SOFL": 1.4, "DELI": 1.3, "ELMT": 1.1,
    },
    "Альфа-Капитал — Акции с выплатой дохода": {
        "LKOH": 10.6, "SBERP": 10.1, "TATNP": 6.9, "SNGSP": 6.7, "X5": 6.2, "MTSS": 5.6,
        "PHOR": 5.1, "HEAD": 4.9, "TRNFP": 4.4, "MOEX": 4.2, "NVTK": 3.8, "GMKN": 3.4,
        "IRAO": 3.3, "CHMF": 3.0, "ROSN": 2.8, "BSPB": 2.6, "MGNT": 2.5, "AFKS": 2.2,
        "UPRO": 1.8, "MAGN": 1.6, "NLMK": 1.5,
    },
    "Альфа-Капитал (ИПИФ)": {
        "SBER": 14.0, "LKOH": 11.2, "YDEX": 9.5, "T": 8.8, "OZON": 7.1, "PLZL": 6.4,
        "NVTK": 6.0, "X5": 5.6, "HEAD": 4.9, "GMKN": 4.2, "CBOM": 3.6, "SVCB": 3.1,
        "MDMG": 2.5,
    },
    "Т-Капитал — Акции роста (TITR)": {
        "YDEX": 9.2, "T": 8.6, "OZON": 8.1, "HEAD": 7.4, "POSI": 7.0, "ASTR": 6.5,
        "MTSS": 5.8, "RTKM": 5.6, "DATA": 5.1, "WUSH": 4.9, "ELMT": 4.6, "DIAS": 4.3,
        "VKCO": 4.1, "DELI": 3.8, "CNRU": 3.7, "SOFL": 3.2, "IVAT": 2.8,
    },
    "Т-Капитал — Компании второго эшелона": {
        "CBOM": 6.8, "AFKS": 6.2, "HEAD": 5.9, "PIKK": 5.4, "SVCB": 5.1, "ENPG": 4.9,
        "AFLT": 4.8, "BSPB": 4.6, "FLOT": 4.1, "HYDR": 4.0, "POSI": 3.9, "MDMG": 3.6,
        "UPRO": 3.4, "RUAL": 3.3, "SMLT": 3.0, "LSRG": 2.9, "FEES": 2.8, "ASTR": 2.6,
        "RENI": 2.5, "NMTP": 2.2, "LEAS": 2.1, "AQUA": 1.9, "GEMC": 1.8, "ETLN": 1.4,
        "SELG": 1.2, "TGKA": 1.1,
    },
    "АТОН — Российские акции + (AMRE)": {
        "SBER": 13.4, "LKOH": 10.9, "T": 6.8, "YDEX": 6.3, "PLZL": 5.6, "NVTK": 5.2,
        "X5": 5.0, "OZON": 4.6, "GAZP": 4.1, "HEAD": 3.8, "GMKN": 3.5, "TATN": 3.3,
        "MOEX": 2.9, "SVCB": 2.4, "CBOM": 2.1, "ENPG": 2.0, "AFKS": 1.9, "PIKK": 1.6,
    },
    "Райффайзен — Акции": {
        "SBER": 12.1, "LKOH": 11.4, "GAZP": 6.8, "NVTK": 5.9, "YDEX": 5.7, "T": 5.3,
        "GMKN": 5.0, "PLZL": 4.6, "TATN": 3.9, "X5": 3.7, "ROSN": 3.2, "SNGSP": 2.6,
        "OZON": 2.5, "HEAD": 2.4, "MGNT": 1.9, "CHMF": 1.8, "PHOR": 1.7, "MOEX": 1.6,
        "VTBR": 1.4, "MTSS": 1.3, "IRAO": 1.2, "NLMK": 1.1, "AFLT": 1.0, "ALRS": 0.8,
    },
    "Промсвязь — Просто. Акции": {
        "SBER": 9.9, "LKOH": 9.1, "NVTK": 6.0, "TATN": 5.6, "GAZP": 5.3, "HEAD": 5.13,
        "GMKN": 4.88, "X5": 4.37, "T": 4.2, "YDEX": 4.0, "PLZL": 3.9, "TRNFP": 3.4,
        "SNGSP": 3.1, "MOEX": 2.8, "PHOR": 2.5, "ROSN": 2.3, "CHMF": 2.0, "MTSS": 1.9,
        "OZON": 1.8, "MRKP": 1.6, "VTBR": 1.5,
    },
}
INDEX_FUNDS = [
    "Первая — Фонд Топ Российских акций (SBMX)",
    "ВИМ — Индекс МосБиржи (EQMX)",
    "ВИМ — Индекс МосБиржи (ОПИФ)",
    "Т-Капитал — Индекс МосБиржи (TMOS)",
]


# =============================================================================
# 2. ФОРМАТИРОВАНИЕ ЧИСЕЛ (российский формат)
# =============================================================================

NBSP = " "


def fmt_ru(x, decimals: int = 1) -> str:
    """1234567.891 → «1 234 567,9» (пробел — разделитель тысяч, запятая — дробной части)."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    try:
        s = f"{float(x):,.{decimals}f}"
    except (TypeError, ValueError):
        return str(x)
    return s.replace(",", NBSP).replace(".", ",")


def fmt_pct(x, decimals: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{fmt_ru(x, decimals)}{NBSP}%"


def fmt_mln(x, decimals: int = 1) -> str:
    return "—" if x is None or pd.isna(x) else f"{fmt_ru(x, decimals)}{NBSP}млн{NBSP}руб."


def parse_number(text) -> float | None:
    """«13 500 732 695.99» / «12,51» / «1 234,5 %» → float."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return None if pd.isna(text) else float(text)
    s = str(text).replace(NBSP, "").replace(" ", "").replace(" ", "").replace("%", "")
    s = s.replace("−", "-")
    if s.count(",") == 1 and s.count(".") == 0:
        s = s.replace(",", ".")
    elif s.count(",") >= 1 and s.count(".") == 1:
        s = s.replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


# =============================================================================
# 3. РАСПОЗНАВАНИЕ ЭМИТЕНТОВ
# =============================================================================

_LEGAL_FORMS = re.compile(
    r"\b(мкпао|пао|оао|зао|ао|нк|гмк|ооо|мк|plc|ltd|ипао|международная компания публичное акционерное общество)\b"
)


def normalize_issuer(name: str) -> str:
    s = str(name).lower().replace("ё", "е")
    s = re.sub(r"[«»\"'“”„]", " ", s)
    s = re.sub(r"\([^)]*\)", " ", s)
    s = _LEGAL_FORMS.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip(" ,.-")


_ALIAS_KEYS_LONG = sorted((k for k in ALIASES if len(k) >= 5), key=len, reverse=True)


def resolve_ticker(issuer: str) -> str | None:
    """Ищет тикер по названию эмитента: сначала точное совпадение, затем по вхождению."""
    norm = normalize_issuer(issuer)
    if not norm:
        return None
    if norm in ALIASES:
        return ALIASES[norm]
    up = norm.upper()
    if up in COMPANIES:
        return up
    for key in _ALIAS_KEYS_LONG:
        if key in norm:
            return ALIASES[key]
    return None


_ASSET_KIND = re.compile(
    r"^(?P<issuer>.*?)\s*,\s*(?P<kind>акци[^,\[]*|облигаци[^,\[]*|депозитарн[^,\[]*|"
    r"инвестиционн[^,\[]*пай[^,\[]*|пай[^,\[]*|расписк[^,\[]*)",
    re.IGNORECASE,
)


def classify_asset(raw_name: str) -> tuple[str, str, str, str]:
    """
    «Лукойл, акция, [1-01-00077-A]»      → ("LKOH", "ЛУКОЙЛ", "Нефть и газ", "Акции")
    «Транснефть, акция прив. [2-01-...]»  → ("TRNFP", "Транснефть (прив.)", ...)
    «Россия, облигация, [26254RMFS]»      → ("—", "Россия (облигации)", "Облигации", "Облигации")
    Возвращает (тикер, компания, сектор, тип актива).
    """
    raw = re.sub(r"\s+", " ", str(raw_name)).strip()
    clean = re.sub(r"\[[^\]]*\]", "", raw).strip(" ,")
    m = _ASSET_KIND.match(clean)
    issuer, kind = (m.group("issuer"), m.group("kind").lower()) if m else (clean, "")

    if "облигац" in kind:
        return NO_TICKER, f"{issuer.strip()} (облигации)", S_BONDS, ASSET_BONDS
    if kind.startswith("пай") or "инвестиционн" in kind:
        return NO_TICKER, f"{issuer.strip()} (паи фонда)", S_OTHER, ASSET_OTHER

    ticker = resolve_ticker(issuer)
    if ticker:
        if "прив" in kind:
            ticker = PREF_MAP.get(ticker, ticker)
        company, sector = COMPANIES.get(ticker, (issuer.strip(), S_OTHER))
        return ticker, company, sector, ASSET_EQUITY
    # Неизвестный эмитент: акция / расписка — считаем акцией, иное — прочим
    is_equity = kind.startswith("акци") or "депозитарн" in kind or "расписк" in kind
    suffix = " (прив.)" if "прив" in kind else ""
    return (NO_TICKER, issuer.strip() + suffix, S_OTHER,
            ASSET_EQUITY if is_equity else ASSET_OTHER)


# =============================================================================
# 4. ВСТРОЕННАЯ БАЗА ДАННЫХ
# =============================================================================

def _rows_for_fund(fund: str, holdings: dict[str, float], cash_min: float) -> list[dict]:
    meta = FUND_META[fund]
    total = sum(holdings.values())
    max_total = 100.0 - cash_min
    scale = max_total / total if total > max_total else 1.0
    nav = meta["nav"]
    common = {
        C_FUND: fund, C_UK: meta["uk"], C_TYPE: meta["type"], C_NAV: nav,
        C_DATE: DEFAULT_STRUCT_DATE, C_NAV_DATE: meta.get("nav_date", DEFAULT_STRUCT_DATE),
        C_URL: FUNDS_URLS.get(fund, BASE_URL), C_SOURCE: SRC_BUILTIN,
    }
    rows = []
    for tk, w in holdings.items():
        w = round(w * scale, 2)
        if tk in SPECIAL_ROWS:
            company, sector, asset = SPECIAL_ROWS[tk]
            ticker = NO_TICKER
        else:
            company, sector = COMPANIES[tk]
            ticker, asset = tk, ASSET_EQUITY
        rows.append({**common, C_TICKER: ticker, C_COMPANY: company, C_SECTOR: sector,
                     C_ASSET: asset, C_WEIGHT: w})
    cash = round(100.0 - sum(r[C_WEIGHT] for r in rows), 2)
    if cash > 0.005:
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: CASH_NAME, C_SECTOR: S_CASH,
                     C_ASSET: ASSET_OTHER, C_WEIGHT: cash})
    for r in rows:
        r[C_VALUE] = round(nav * r[C_WEIGHT] / 100.0, 2)
    return rows


@st.cache_data(show_spinner=False)
def build_default_db() -> pd.DataFrame:
    """Встроенная база: ориентировочные составы всех фондов из FUNDS_URLS."""
    rows: list[dict] = []
    for fund in FUNDS_URLS:
        if fund in INDEX_FUNDS:
            if "EQMX" in fund:  # ориентир по раскрытию фонда, без «шума»
                holdings = dict(INDEX_BASE)
            else:  # небольшое отклонение от индекса (трекинг, дата ребалансировки)
                rng = random.Random(fund)
                holdings = {t: w * (1 + rng.uniform(-0.035, 0.035)) for t, w in INDEX_BASE.items()}
            rows += _rows_for_fund(fund, holdings, cash_min=0.4)
        else:
            rows += _rows_for_fund(fund, ACTIVE_HOLDINGS[fund], cash_min=1.5)
    return pd.DataFrame(rows, columns=ALL_COLS)


# =============================================================================
# 5. ПАРСИНГ INVESTFUNDS.RU
# =============================================================================

_FUND_PAGE_RE = re.compile(r"^https?://(www\.)?investfunds\.ru/funds/\d+/?", re.IGNORECASE)
_DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
_TITLE_RE = re.compile(r"(БПИФ|ОПИФ|ИПИФ|ЗПИФ)\s*:\s*([^\n]+?)\s*\(([^()\n]+)\)")


def is_fund_page(url: str) -> bool:
    return bool(_FUND_PAGE_RE.match(str(url or "")))


def _fetch_html(url: str, timeout: int = 20) -> str:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.Timeout:
        raise RuntimeError("превышено время ожидания ответа сайта")
    except requests.ConnectionError:
        raise RuntimeError("не удалось подключиться к сайту")
    if resp.status_code == 403:
        raise RuntimeError("сайт ограничил автоматические запросы (ошибка 403)")
    if resp.status_code == 429:
        raise RuntimeError("слишком много запросов (ошибка 429), попробуйте позже")
    if resp.status_code >= 400:
        raise RuntimeError(f"сайт вернул ошибку {resp.status_code}")
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def _parse_top_assets_from_tables(soup: BeautifulSoup) -> list[tuple[str, float]]:
    """Ищет таблицу с колонками «Основные активы» / «Доля в портфеле, %»."""
    for table in soup.find_all("table"):
        head = table.get_text(" ", strip=True)[:400].lower()
        if "доля в портфеле" not in head and "основные активы" not in head:
            continue
        items = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name = tds[0].get_text(" ", strip=True)
            weight = parse_number(tds[-1].get_text(" ", strip=True))
            if name and weight is not None and 0 < weight <= 100:
                items.append((name, weight))
        if items:
            return items
    return []


def _parse_top_assets_from_text(lines: list[str]) -> list[tuple[str, float]]:
    """Запасной вариант: разбор текстового блока «Структура фонда … ТОП-10»."""
    start = next((i for i, l in enumerate(lines) if "доля в портфеле" in l.lower()), None)
    if start is None:
        return []
    items, pending = [], None
    for line in lines[start + 1: start + 80]:
        low = line.lower()
        if "полная структура" in low or "управляющий фондом" in low or "инфраструктура" in low:
            break
        m = re.match(r"^(.*\S)\s+(\d{1,3}[.,]\d{1,4})$", line)
        if m and ("," in m.group(1) or "[" in m.group(1)):
            items.append((m.group(1), parse_number(m.group(2))))
            pending = None
        elif re.fullmatch(r"\d{1,3}[.,]\d{1,4}", line) and pending:
            items.append((pending, parse_number(line)))
            pending = None
        elif re.search(r",\s*(акци|облигаци|депозитарн|пай)", low):
            pending = line
    return [(n, w) for n, w in items if w is not None and 0 < w <= 100]


def parse_fund_html(html: str) -> dict:
    """Извлекает со страницы фонда: тип, УК, СЧА, даты и ТОП-активы."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    info = dict(type=None, uk=None, title=None, nav_mln=None, nav_date=None,
                struct_date=None, items=[])

    m = _TITLE_RE.search(text)
    if m:
        info["type"], info["title"], info["uk"] = m.group(1), m.group(2).strip(), m.group(3).strip()

    # --- СЧА: блок «Динамика стоимости пая и СЧА» → дата → «СЧА» → число ---
    start = next((i for i, l in enumerate(lines) if "динамика стоимости" in l.lower()), 0)
    for i in range(start, min(start + 60, len(lines))):
        if info["nav_date"] is None and _DATE_RE.fullmatch(lines[i]):
            info["nav_date"] = lines[i]
        if lines[i].upper() == "СЧА" and i + 1 < len(lines):
            val = parse_number(lines[i + 1])
            if val and val > 1e5:  # значения на сайте — в рублях
                info["nav_mln"] = round(val / 1e6, 2)
                break
    if info["nav_mln"] is None:
        m = re.search(r"СЧА\s*[:\n]?\s*([\d  ]{5,}[.,]\d{1,2})", text)
        if m:
            val = parse_number(m.group(1))
            if val and val > 1e5:
                info["nav_mln"] = round(val / 1e6, 2)

    m = re.search(r"Структура фонда на\s*(\d{2}\.\d{2}\.\d{4})", text)
    if m:
        info["struct_date"] = m.group(1)

    items = _parse_top_assets_from_tables(soup) or _parse_top_assets_from_text(lines)
    info["items"] = items
    return info


def _rows_from_parsed(fund: str, url: str, info: dict) -> list[dict]:
    meta = FUND_META.get(fund, {})
    nav = info["nav_mln"] if info["nav_mln"] else meta.get("nav")
    common = {
        C_FUND: fund,
        # для фондов из реестра оставляем единое написание УК
        C_UK: meta.get("uk") or info["uk"] or "Не указана",
        C_TYPE: info["type"] or meta.get("type") or "ОПИФ",
        C_NAV: nav,
        C_DATE: info["struct_date"] or info["nav_date"] or datetime.now().strftime("%d.%m.%Y"),
        C_NAV_DATE: info["nav_date"] or info["struct_date"] or "—",
        C_URL: url, C_SOURCE: SRC_SITE,
    }
    merged: dict[tuple, dict] = {}
    for raw, w in info["items"]:
        ticker, company, sector, asset = classify_asset(raw)
        key = (ticker, company)
        if key in merged:
            merged[key][C_WEIGHT] += w
        else:
            merged[key] = {**common, C_TICKER: ticker, C_COMPANY: company,
                           C_SECTOR: sector, C_ASSET: asset, C_WEIGHT: w}
    rows = list(merged.values())
    rest = round(100.0 - sum(r[C_WEIGHT] for r in rows), 2)
    if rest > 0.05:
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: REST_NAME, C_SECTOR: S_OTHER,
                     C_ASSET: ASSET_OTHER, C_WEIGHT: rest})
    for r in rows:
        r[C_WEIGHT] = round(r[C_WEIGHT], 2)
        r[C_VALUE] = round(nav * r[C_WEIGHT] / 100.0, 2) if nav else None
    return rows


@st.cache_data(ttl=86400, show_spinner=False)
def parse_investfunds(urls_dict: dict[str, str]) -> tuple[pd.DataFrame, dict[str, str]]:
    """
    Проходит по словарю {название фонда: URL}, загружает страницы Investfunds.ru,
    извлекает ТОП-активы (эмитент и вес, %) и СЧА, рассчитывает стоимость позиции
    (СЧА × вес / 100). Результат кэшируется на сутки.
    Возвращает (DataFrame с позициями, {фонд: текст ошибки}).
    """
    rows: list[dict] = []
    errors: dict[str, str] = {}
    for i, (fund, url) in enumerate(urls_dict.items()):
        if not is_fund_page(url):
            errors[fund] = "в реестре указан общий адрес сайта, а не страница фонда"
            continue
        if i:
            time.sleep(0.5)  # бережная нагрузка на сайт
        try:
            info = parse_fund_html(_fetch_html(url))
        except Exception as exc:  # noqa: BLE001
            errors[fund] = str(exc)
            continue
        if not info["items"]:
            errors[fund] = "на странице не найдена таблица «ТОП-активы»"
            continue
        if not info["nav_mln"] and fund not in FUND_META:
            errors[fund] = "не удалось определить СЧА фонда"
            continue
        name = fund
        if fund.startswith("#auto:"):
            name = info["title"] or fund.replace("#auto:", "")
        rows += _rows_from_parsed(name, url, info)
    return pd.DataFrame(rows, columns=ALL_COLS), errors


@st.cache_data(ttl=86400, show_spinner=False)
def discover_all_equity_funds(max_pages: int = 3) -> tuple[dict[str, str], list[str]]:
    """
    Парсит каталог https://investfunds.ru/funds/ и находит фонды категории «акции»
    или «смешанные инвестиции». Возвращает ({название: URL}, [ошибки]).
    """
    found: dict[str, str] = {}
    errors: list[str] = []
    for page in range(1, max_pages + 1):
        url = CATALOG_URL if page == 1 else f"{CATALOG_URL}?page={page}"
        try:
            html = _fetch_html(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Страница каталога {page}: {exc}")
            break
        soup = BeautifulSoup(html, "html.parser")
        before = len(found)
        for a in soup.find_all("a", href=True):
            m = re.match(r"^(?:https?://(?:www\.)?investfunds\.ru)?/funds/(\d+)/?$", a["href"])
            if not m:
                continue
            name = a.get_text(" ", strip=True)
            if len(name) < 3:
                continue
            box = a.find_parent("tr") or a.find_parent("li") or a.parent
            ctx = (box.get_text(" ", strip=True) if box else name).lower()
            if "ликвидир" in ctx or "квалифицированн" in ctx:
                continue
            if any(k in ctx for k in ("акци", "смешан")):
                found.setdefault(name, f"{BASE_URL}/funds/{m.group(1)}/")
        if len(found) == before:
            break  # новых фондов на странице нет — дальше не идём
        time.sleep(0.5)
    return found, errors


# =============================================================================
# 6. ЗАГРУЗКА ПОЛЬЗОВАТЕЛЬСКОГО ФАЙЛА
# =============================================================================

UPLOAD_COLUMNS = {
    "фонд": C_FUND, "название фонда": C_FUND, "наименование фонда": C_FUND,
    "управляющая компания": C_UK, "ук": C_UK,
    "тип фонда": C_TYPE, "тип": C_TYPE,
    "сча, млн руб.": C_NAV, "сча": C_NAV, "сча (млн руб.)": C_NAV, "сча, млн руб": C_NAV,
    "дата структуры": C_DATE, "дата": C_DATE, "дата данных": C_DATE,
    "дата сча": C_NAV_DATE,
    "ссылка": C_URL, "ссылка на фонд": C_URL, "url": C_URL,
    "тикер": C_TICKER,
    "компания": C_COMPANY, "эмитент": C_COMPANY, "актив": C_COMPANY, "наименование": C_COMPANY,
    "сектор": C_SECTOR, "тип актива": C_ASSET,
    "доля в фонде, %": C_WEIGHT, "доля, %": C_WEIGHT, "доля": C_WEIGHT,
    "доля в портфеле, %": C_WEIGHT, "вес, %": C_WEIGHT, "вес": C_WEIGHT,
    "оценочная стоимость позиции, млн руб.": C_VALUE,
    "стоимость позиции, млн руб.": C_VALUE, "стоимость, млн руб.": C_VALUE,
    "источник данных": C_SOURCE,
}


def _to_numeric(series: pd.Series) -> pd.Series:
    return series.map(parse_number).astype("float64")


def read_user_file(uploaded) -> pd.DataFrame:
    """Читает CSV/Excel пользователя и приводит к структуре базы."""
    name = uploaded.name.lower()
    raw = uploaded.getvalue()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(raw))
    else:
        text = None
        for enc in ("utf-8-sig", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError("не удалось определить кодировку файла (ожидается UTF-8 или Windows-1251)")
        header = text.lstrip().split("\n", 1)[0]
        sep = ";" if ";" in header else ("\t" if "\t" in header else ",")
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str)

    df = df.rename(columns={c: UPLOAD_COLUMNS.get(str(c).strip().lower(), str(c).strip())
                            for c in df.columns})
    if C_FUND not in df.columns or C_WEIGHT not in df.columns:
        raise ValueError("в файле должны быть как минимум колонки «Фонд» и «Доля в фонде, %»")
    if C_COMPANY not in df.columns and C_TICKER not in df.columns:
        raise ValueError("в файле нужна колонка «Компания» или «Тикер»")
    return finalize_db(df, default_source=SRC_USER)


def finalize_db(df: pd.DataFrame, default_source: str = SRC_USER) -> pd.DataFrame:
    """Дополняет недостающие колонки, тикеры, секторы и стоимость позиций."""
    df = df.copy()
    for col in ALL_COLS:
        if col not in df.columns:
            df[col] = None
    df = df[ALL_COLS].copy()

    for col in (C_NAV, C_WEIGHT, C_VALUE):
        df[col] = _to_numeric(df[col])
    text_cols = [c for c in ALL_COLS if c not in (C_NAV, C_WEIGHT, C_VALUE)]
    for col in text_cols:
        df[col] = df[col].astype("object").where(df[col].notna(), None)
        df[col] = df[col].map(lambda v: None if v is None or str(v).strip() in ("", "nan") else str(v).strip())

    df = df[df[C_FUND].notna() & df[C_WEIGHT].notna()].copy()

    def fill_row(r: dict) -> dict:
        for c in text_cols:
            v = r.get(c)
            r[c] = None if v is None or (isinstance(v, float) and pd.isna(v)) else v
        tk = (r[C_TICKER] or "").upper()
        if tk in ("", "-", "—", "NAN"):
            tk = resolve_ticker(r[C_COMPANY]) if r[C_COMPANY] else None
        if tk and tk in COMPANIES:
            r[C_TICKER] = tk
            r[C_COMPANY] = r[C_COMPANY] or COMPANIES[tk][0]
            r[C_SECTOR] = r[C_SECTOR] or COMPANIES[tk][1]
            r[C_ASSET] = r[C_ASSET] or ASSET_EQUITY
        else:
            r[C_TICKER] = tk or NO_TICKER
            r[C_COMPANY] = r[C_COMPANY] or r[C_TICKER]
            r[C_SECTOR] = r[C_SECTOR] or S_OTHER
            if not r[C_ASSET]:
                r[C_ASSET] = ASSET_BONDS if r[C_SECTOR] == S_BONDS else (
                    ASSET_EQUITY if r[C_TICKER] != NO_TICKER else ASSET_OTHER)
        meta = FUND_META.get(r[C_FUND], {})
        r[C_UK] = r[C_UK] or meta.get("uk") or "Не указана"
        r[C_TYPE] = (r[C_TYPE] or meta.get("type") or "ОПИФ").upper()
        r[C_URL] = r[C_URL] or FUNDS_URLS.get(r[C_FUND], BASE_URL)
        r[C_DATE] = r[C_DATE] or datetime.now().strftime("%d.%m.%Y")
        r[C_NAV_DATE] = r[C_NAV_DATE] or r[C_DATE]
        r[C_SOURCE] = r[C_SOURCE] or default_source
        return r

    if not df.empty:
        df = pd.DataFrame([fill_row(r) for r in df.to_dict("records")], columns=ALL_COLS)
        for col in (C_NAV, C_WEIGHT, C_VALUE):
            df[col] = df[col].astype("float64")
        # СЧА: если не указана — восстанавливаем по стоимости позиций или из справочника
        nav_from_values = (df[C_VALUE] * 100 / df[C_WEIGHT]).where(df[C_WEIGHT] > 0)
        df[C_NAV] = df[C_NAV].fillna(nav_from_values)
        df[C_NAV] = df[C_NAV].fillna(df[C_FUND].map(lambda f: FUND_META.get(f, {}).get("nav")))
        df[C_NAV] = df.groupby(C_FUND)[C_NAV].transform(lambda s: s.fillna(s.median()))
        df[C_VALUE] = df[C_VALUE].fillna(df[C_NAV] * df[C_WEIGHT] / 100.0)
    return df.reset_index(drop=True)


def replace_funds(base: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Заменяет в базе строки фондов, присутствующих в new, и добавляет новые фонды."""
    if new is None or new.empty:
        return base
    kept = base[~base[C_FUND].isin(new[C_FUND].unique())]
    return pd.concat([kept, new], ignore_index=True)[ALL_COLS]


def db_to_csv(df: pd.DataFrame) -> bytes:
    """CSV в «русском» формате: разделитель «;», десятичная запятая, UTF-8 с BOM (для Excel)."""
    return df.to_csv(sep=";", decimal=",", index=False).encode("utf-8-sig")


def db_to_excel(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Позиции фондов")
    return buf.getvalue()


# =============================================================================
# 7. ВСПОМОГАТЕЛЬНОЕ ДЛЯ ИНТЕРФЕЙСА
# =============================================================================

PLOTLY_CONFIG = {"displayModeBar": False, "locale": "ru"}
PALETTE = ["#2E5BFF", "#00A389", "#F2A900", "#E5484D", "#8E4EC6", "#0091FF",
           "#F76B15", "#30A46C", "#D6409F", "#6E56CF", "#12A594", "#978365",
           "#5B6B7F", "#B5A642", "#3E63DD", "#A0A9B4"]

CUSTOM_CSS = """
<style>
/* Скрываем англоязычные служебные элементы Streamlit */
#MainMenu, footer, [data-testid="stDeployButton"], [data-testid="stAppDeployButton"] {visibility: hidden;}
[data-testid="InputInstructions"] {display: none;}
[data-testid="stElementToolbar"] {display: none;}
/* Русификация загрузчика файлов */
[data-testid="stFileUploaderDropzoneInstructions"] > div > span {display: none;}
[data-testid="stFileUploaderDropzoneInstructions"] > div > small {display: none;}
[data-testid="stFileUploaderDropzoneInstructions"] > div::before {
    content: "Перетащите файл сюда"; display: block;}
[data-testid="stFileUploaderDropzoneInstructions"] > div::after {
    content: "CSV или Excel, до 200 МБ"; display: block; font-size: 0.8em; opacity: 0.7;}
[data-testid="stFileUploaderDropzone"] button {font-size: 0 !important;}
[data-testid="stFileUploaderDropzone"] button::after {content: "Выбрать файл"; font-size: 14px;}
/* Карточки показателей */
[data-testid="stMetric"] {
    background: rgba(46, 91, 255, 0.05); border: 1px solid rgba(46, 91, 255, 0.15);
    border-radius: 10px; padding: 10px 14px;}
[data-testid="stMetricValue"] {font-size: 1.35rem;}
</style>
"""


def styled(df: pd.DataFrame, pct_cols=(), mln_cols=()):
    fmt = {c: (lambda v: fmt_pct(v)) for c in pct_cols}
    fmt.update({c: (lambda v: fmt_ru(v, 1)) for c in mln_cols})
    return df.style.format(fmt, na_rep="—").hide(axis="index")


def search_filter(df: pd.DataFrame, query: str, cols: list[str]) -> pd.DataFrame:
    q = (query or "").strip().lower()
    if not q:
        return df
    mask = pd.Series(False, index=df.index)
    for c in cols:
        mask |= df[c].astype(str).str.lower().str.contains(q, regex=False, na=False)
    return df[mask]


def run_update(urls: dict[str, str], force: bool) -> tuple[pd.DataFrame, dict[str, str]]:
    """Загружает фонды по одному с индикатором прогресса (каждый фонд кэшируется отдельно)."""
    if force:
        parse_investfunds.clear()
    parts, errors = [], {}
    n = len(urls)
    bar = st.progress(0.0, text="Подготовка к загрузке…")
    for i, (fund, url) in enumerate(urls.items(), start=1):
        label = fund.replace("#auto:", "")
        bar.progress((i - 1) / n, text=f"Загрузка {i} из {n}: {label[:40]}")
        df_part, errs = parse_investfunds({fund: url})
        if not df_part.empty:
            parts.append(df_part)
        errors.update({k.replace("#auto:", ""): v for k, v in errs.items()})
    bar.progress(1.0, text="Загрузка завершена")
    time.sleep(0.4)
    bar.empty()
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=ALL_COLS)
    return new, errors


# =============================================================================
# 8. ИНТЕРФЕЙС
# =============================================================================

def sidebar(db: pd.DataFrame) -> tuple[list[str], list[str]]:
    sb = st.sidebar
    sb.header("⚙️ Фильтры")
    uk_all = sorted(db[C_UK].dropna().unique())
    types_all = sorted(db[C_TYPE].dropna().unique(),
                       key=lambda t: {"БПИФ": 0, "ОПИФ": 1, "ИПИФ": 2}.get(t, 9))
    sel_uk = sb.multiselect("Управляющие компании", uk_all, default=uk_all,
                            placeholder="Выберите управляющие компании")
    sel_types = sb.multiselect("Тип фонда", types_all, default=types_all,
                               placeholder="Выберите тип фонда")

    sb.divider()
    sb.header("🔄 Обновление данных")
    force = sb.checkbox("Не использовать кэш (загрузить заново)", value=False,
                        help="Данные с сайта кэшируются на 24 часа.")
    if sb.button("Обновить данные с Investfunds.ru", type="primary", use_container_width=True):
        with sb:
            new, errors = run_update(FUNDS_URLS, force)
        st.session_state.db = replace_funds(st.session_state.db, new)
        st.session_state.update_log = dict(
            time=datetime.now().strftime("%d.%m.%Y %H:%M"),
            ok=sorted(new[C_FUND].unique()) if not new.empty else [],
            errors=errors,
        )
        st.rerun()

    with sb.expander("🧭 Поиск всех фондов акций в каталоге"):
        st.caption("Сканирует каталог Investfunds.ru, находит фонды категорий «акции» и "
                   "«смешанные инвестиции» и добавляет их к анализу.")
        max_new = st.number_input("Максимум новых фондов", min_value=5, max_value=200, value=30, step=5)
        if st.button("Найти и загрузить фонды", use_container_width=True):
            with st.spinner("Сканирование каталога…"):
                found, cat_errors = discover_all_equity_funds()
            known = set(FUNDS_URLS.values()) | set(st.session_state.db[C_URL].dropna())
            fresh = {f"#auto:{n}": u for n, u in found.items() if u not in known}
            fresh = dict(list(fresh.items())[: int(max_new)])
            if not fresh:
                st.session_state.update_log = dict(
                    time=datetime.now().strftime("%d.%m.%Y %H:%M"), ok=[],
                    errors={"Каталог": "; ".join(cat_errors) or "новых фондов не найдено"})
            else:
                new, errors = run_update(fresh, force=False)
                st.session_state.db = replace_funds(st.session_state.db, new)
                st.session_state.update_log = dict(
                    time=datetime.now().strftime("%d.%m.%Y %H:%M"),
                    ok=sorted(new[C_FUND].unique()) if not new.empty else [],
                    errors={**errors, **({"Каталог": "; ".join(cat_errors)} if cat_errors else {})},
                )
            st.rerun()

    log = st.session_state.get("update_log")
    if log:
        sb.success(f"Обновлено {log['time']}: загружено фондов — {len(log['ok'])}, "
                   f"с ошибками — {len(log['errors'])}.")
        if log["errors"]:
            with sb.expander("Подробности ошибок"):
                for fund, err in log["errors"].items():
                    st.markdown(f"• **{fund}** — {err}")
                st.caption("Для фондов с ошибками сохраняются данные встроенной базы.")

    sb.divider()
    sb.header("📁 Файлы")
    up = sb.file_uploader("Загрузить свой CSV/Excel отчет", type=["csv", "xlsx", "xls"],
                          help="Обязательные колонки: «Фонд», «Компания» или «Тикер», "
                               "«Доля в фонде, %». Остальные колонки — как в выгрузке базы.")
    if up is not None:
        mode = sb.radio("Как применить файл", ["Добавить к базе (заменить совпадающие фонды)",
                                               "Полностью заменить базу"])
        if sb.button("Применить файл", use_container_width=True):
            try:
                user_df = read_user_file(up)
                if user_df.empty:
                    raise ValueError("в файле нет строк с заполненными «Фонд» и «Доля»")
                if mode.startswith("Полностью"):
                    st.session_state.db = user_df
                else:
                    st.session_state.db = replace_funds(st.session_state.db, user_df)
                st.session_state.upload_msg = ("success",
                                               f"Файл «{up.name}» загружен: фондов — "
                                               f"{user_df[C_FUND].nunique()}, позиций — {len(user_df)}.")
            except Exception as exc:  # noqa: BLE001
                st.session_state.upload_msg = ("error", f"Не удалось прочитать файл: {exc}")
            st.rerun()
    msg = st.session_state.pop("upload_msg", None)
    if msg:
        (sb.success if msg[0] == "success" else sb.error)(msg[1])

    db_now = st.session_state.db
    sb.download_button("Скачать текущую базу (CSV)", data=db_to_csv(db_now),
                       file_name=f"pif_akcii_{datetime.now():%Y%m%d}.csv", mime="text/csv",
                       use_container_width=True)
    sb.download_button("Скачать текущую базу (Excel)", data=db_to_excel(db_now),
                       file_name=f"pif_akcii_{datetime.now():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       use_container_width=True)
    if sb.button("Сбросить к встроенной базе", use_container_width=True):
        st.session_state.db = build_default_db().copy()
        st.session_state.pop("update_log", None)
        st.rerun()
    return sel_uk, sel_types


def tab_funds(fdb: pd.DataFrame) -> None:
    nav_by_fund = fdb.groupby(C_FUND)[C_NAV].first().sort_values(ascending=False)
    fund = st.selectbox("Фонд", nav_by_fund.index.tolist(), index=0,
                        placeholder="Начните вводить название фонда",
                        help="Список отсортирован по СЧА. Можно начать вводить название для поиска.")
    fd = fdb[fdb[C_FUND] == fund].sort_values(C_WEIGHT, ascending=False).reset_index(drop=True)
    head = fd.iloc[0]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Управляющая компания", head[C_UK])
    c2.metric("Тип фонда", head[C_TYPE])
    c3.metric("СЧА", fmt_mln(head[C_NAV], 0), help=f"Стоимость чистых активов на {head[C_NAV_DATE]}")
    c4.metric("Дата актуальности данных", head[C_DATE],
              help=f"Дата структуры портфеля. СЧА — на {head[C_NAV_DATE]}.")

    equities = fd[fd[C_ASSET] == ASSET_EQUITY]
    top10 = fd[~fd[C_COMPANY].isin([CASH_NAME, REST_NAME])].head(10)[C_WEIGHT].sum()
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Позиций в портфеле", f"{len(fd[~fd[C_COMPANY].isin([CASH_NAME, REST_NAME])])}")
    k2.metric("Доля ТОП-10", fmt_pct(top10, 1))
    k3.metric("Доля акций", fmt_pct(equities[C_WEIGHT].sum(), 1))
    if not equities.empty:
        big = equities.iloc[0]
        k4.metric(f"Крупнейшая позиция · {fmt_pct(big[C_WEIGHT], 1)}", big[C_COMPANY],
                  help=f"{big[C_TICKER]} — {fmt_mln(big[C_VALUE])}")

    lc, rc = st.columns([3, 2])
    with lc:
        if is_fund_page(head[C_URL]):
            st.link_button("🔗 Открыть страницу фонда на Investfunds.ru", head[C_URL])
        else:
            st.link_button("🔗 Открыть Investfunds.ru", head[C_URL] or BASE_URL)
            st.caption("Точный адрес страницы этого фонда не указан в реестре.")
    with rc:
        src = head[C_SOURCE]
        note = ("ТОП-10 позиций с сайта, остальное — «Прочие активы»."
                if src == SRC_SITE else
                "Ориентировочный состав. Для актуальных данных нажмите «Обновить данные» в боковой панели."
                if src == SRC_BUILTIN else "Данные из загруженного файла.")
        st.caption(f"Источник: **{src}**. {note}")

    st.markdown("#### Структура портфеля")
    g1, g2 = st.columns([3, 2])
    with g1:
        top_n = st.slider("Сколько крупнейших позиций показать на диаграмме", 5, 20, 10)
        pie_src = fd[fd[C_WEIGHT] > 0]
        top = pie_src.head(top_n)
        rest_w = pie_src[C_WEIGHT].sum() - top[C_WEIGHT].sum()
        labels = top[C_COMPANY].tolist()
        values = top[C_WEIGHT].tolist()
        money = top[C_VALUE].tolist()
        if rest_w > 0.01:
            labels.append("Прочие активы")
            values.append(rest_w)
            money.append(pie_src[C_VALUE].sum() - top[C_VALUE].sum())
        fig = go.Figure(go.Pie(
            labels=labels, values=values, customdata=money, hole=0.55, sort=False,
            direction="clockwise", marker=dict(colors=PALETTE, line=dict(color="white", width=1)),
            texttemplate="%{value:.1f} %", textposition="inside",
            hovertemplate="<b>%{label}</b><br>Доля в фонде: %{value:.2f} %"
                          "<br>Стоимость позиции: %{customdata:,.1f} млн руб.<extra></extra>",
        ))
        fig.update_layout(
            separators=", ", height=470, margin=dict(t=30, b=10, l=10, r=10),
            title=dict(text=f"ТОП-{top_n} активов фонда", x=0, font=dict(size=15)),
            legend=dict(title=dict(text="Эмитенты"), font=dict(size=12)),
            annotations=[dict(text=f"СЧА<br><b>{fmt_ru(head[C_NAV], 0)}</b><br>млн руб.",
                              showarrow=False, font=dict(size=14))],
        )
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
    with g2:
        sec = fd.groupby(C_SECTOR, as_index=False)[[C_WEIGHT, C_VALUE]].sum().sort_values(C_WEIGHT)
        fig_s = px.bar(sec, x=C_WEIGHT, y=C_SECTOR, orientation="h", text=C_WEIGHT,
                       custom_data=[C_VALUE], color_discrete_sequence=["#2E5BFF"])
        fig_s.update_traces(
            texttemplate="%{x:.1f} %", textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>Доля: %{x:.2f} %<br>Стоимость: %{customdata[0]:,.1f} млн руб.<extra></extra>")
        fig_s.update_layout(
            separators=", ", height=470 + 20, margin=dict(t=60, b=10, l=10, r=40),
            title=dict(text="Распределение по секторам", x=0, font=dict(size=15)),
            xaxis_title="Доля в фонде, %", yaxis_title=None, showlegend=False,
        )
        st.plotly_chart(fig_s, use_container_width=True, config=PLOTLY_CONFIG)

    st.markdown("#### Все позиции фонда")
    q = st.text_input("Поиск по позициям", placeholder="Тикер, компания или сектор…", key="q_fund")
    table = fd[[C_TICKER, C_COMPANY, C_SECTOR, C_WEIGHT, C_VALUE]]
    table = search_filter(table, q, [C_TICKER, C_COMPANY, C_SECTOR])
    st.caption(f"Показано позиций: {len(table)} из {len(fd)}. "
               "Сортировка — щелчком по заголовку колонки.")
    st.dataframe(
        styled(table, pct_cols=[C_WEIGHT], mln_cols=[C_VALUE]),
        use_container_width=True, hide_index=True, height=min(38 + 35 * max(len(table), 1), 640),
    )


def tab_stocks(fdb: pd.DataFrame) -> None:
    eq = fdb[fdb[C_ASSET] == ASSET_EQUITY].copy()
    if eq.empty:
        st.info("В отфильтрованных фондах нет позиций в акциях.")
        return
    eq["_key"] = eq[C_TICKER].where(eq[C_TICKER] != NO_TICKER, eq[C_COMPANY])
    agg = (eq.groupby("_key")
             .agg(ticker=(C_TICKER, "first"), company=(C_COMPANY, "first"),
                  sector=(C_SECTOR, "first"), total=(C_VALUE, "sum"),
                  funds=(C_FUND, "nunique"), avg_w=(C_WEIGHT, "mean"))
             .sort_values("total", ascending=False))
    label = {k: (f"{r.ticker} — {r.company}" if r.ticker != NO_TICKER else r.company)
             for k, r in agg.iterrows()}

    key = st.selectbox("Акция", agg.index.tolist(), index=0, format_func=lambda k: label[k],
                       placeholder="Введите тикер или название компании",
                       help="Список отсортирован по суммарной стоимости пакетов во всех фондах. "
                            "Начните вводить тикер (например, SBER) или название.")
    info = agg.loc[key]
    holders = (eq[eq["_key"] == key]
               .groupby(C_FUND, as_index=False)
               .agg(**{C_UK: (C_UK, "first"), C_TYPE: (C_TYPE, "first"),
                       C_WEIGHT: (C_WEIGHT, "sum"), C_VALUE: (C_VALUE, "sum"),
                       C_URL: (C_URL, "first")}))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Тикер: {info.ticker}", info.company, help=f"Сектор: {info.sector}")
    c2.metric("Фондов-держателей", f"{len(holders)}")
    c3.metric("Суммарная стоимость пакетов", fmt_mln(holders[C_VALUE].sum(), 1))
    c4.metric("Средняя доля в портфелях", fmt_pct(holders[C_WEIGHT].mean()))

    sort_by = st.radio("Сортировать фонды", ["По доле в портфеле", "По стоимости пакета"],
                       horizontal=True)
    metric_col = C_WEIGHT if sort_by.startswith("По доле") else C_VALUE
    holders = holders.sort_values(metric_col, ascending=False).reset_index(drop=True)

    x_title = "Доля в портфеле, %" if metric_col == C_WEIGHT else "Оценочная стоимость пакета, млн руб."
    text_tpl = "%{x:.2f} %" if metric_col == C_WEIGHT else "%{x:,.1f}"
    fig = px.bar(holders, x=metric_col, y=C_FUND, color=C_UK, orientation="h",
                 custom_data=[C_TYPE, C_WEIGHT, C_VALUE, C_UK],
                 color_discrete_sequence=PALETTE,
                 category_orders={C_FUND: holders[C_FUND].tolist()})
    fig.update_traces(
        texttemplate=text_tpl, textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>УК: %{customdata[3]} · %{customdata[0]}"
                      "<br>Доля в портфеле: %{customdata[1]:.2f} %"
                      "<br>Стоимость пакета: %{customdata[2]:,.1f} млн руб.<extra></extra>")
    fig.update_layout(
        separators=", ", height=max(320, 42 * len(holders) + 140),
        margin=dict(t=50, b=10, l=10, r=60),
        title=dict(text=f"Кто держит {info.company} ({info.ticker})", x=0, font=dict(size=15)),
        xaxis_title=x_title, yaxis_title=None,
        legend=dict(title=dict(text="Управляющая компания"), orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)

    st.markdown("#### Фонды-держатели")
    table = holders.rename(columns={
        C_FUND: "Название фонда", C_WEIGHT: "Доля в портфеле, %",
        C_VALUE: "Оценочная стоимость пакета, млн руб.", C_URL: "Ссылка на фонд",
    })[["Название фонда", C_UK, C_TYPE, "Доля в портфеле, %",
        "Оценочная стоимость пакета, млн руб.", "Ссылка на фонд"]]
    q = st.text_input("Поиск по фондам", placeholder="Название фонда или УК…", key="q_stock")
    table = search_filter(table, q, ["Название фонда", C_UK, C_TYPE])
    st.dataframe(
        styled(table, pct_cols=["Доля в портфеле, %"], mln_cols=["Оценочная стоимость пакета, млн руб."]),
        use_container_width=True, hide_index=True,
        height=min(38 + 35 * max(len(table), 1), 640),
        column_config={"Ссылка на фонд": st.column_config.LinkColumn(
            "Ссылка на фонд", display_text="Открыть на Investfunds.ru")},
    )

    with st.expander("📊 Крупнейшие бумаги во всех отфильтрованных фондах"):
        top = agg.head(20).reset_index(drop=True)
        top["Бумага"] = [f"{t} · {c}" if t != NO_TICKER else c for t, c in zip(top.ticker, top.company)]
        fig_t = px.bar(top.iloc[::-1], x="total", y="Бумага", orientation="h",
                       custom_data=["funds", "avg_w"], color_discrete_sequence=["#00A389"])
        fig_t.update_traces(
            texttemplate="%{x:,.0f}", textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>Суммарная стоимость: %{x:,.1f} млн руб."
                          "<br>Фондов-держателей: %{customdata[0]}"
                          "<br>Средняя доля: %{customdata[1]:.2f} %<extra></extra>")
        fig_t.update_layout(separators=", ", height=640, margin=dict(t=20, b=10, l=10, r=60),
                            xaxis_title="Суммарная стоимость пакетов, млн руб.", yaxis_title=None)
        st.plotly_chart(fig_t, use_container_width=True, config=PLOTLY_CONFIG)


def main() -> None:
    st.set_page_config(page_title="Позиции ПИФов РФ в акциях", layout="wide", page_icon="📈")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    if "db" not in st.session_state:
        st.session_state.db = build_default_db().copy()
    db: pd.DataFrame = st.session_state.db

    sel_uk, sel_types = sidebar(db)
    db = st.session_state.db

    st.title("📈 Позиции российских ПИФов в акциях")
    n_site = db.loc[db[C_SOURCE] == SRC_SITE, C_FUND].nunique()
    n_base = db.loc[db[C_SOURCE] == SRC_BUILTIN, C_FUND].nunique()
    n_user = db.loc[db[C_SOURCE] == SRC_USER, C_FUND].nunique()
    parts = [f"с Investfunds.ru — {n_site}", f"из встроенной базы — {n_base}"]
    if n_user:
        parts.append(f"из файла — {n_user}")
    st.caption(f"БПИФ, ОПИФ и ИПИФ, инвестирующие в российские акции. Фондов в базе: "
               f"{db[C_FUND].nunique()} ({', '.join(parts)}). "
               "Стоимость позиции = СЧА × доля / 100.")

    fdb = db[db[C_UK].isin(sel_uk) & db[C_TYPE].isin(sel_types)]
    if fdb.empty:
        st.warning("Нет фондов, подходящих под выбранные фильтры. Измените фильтры в боковой панели.")
        st.stop()

    t1, t2 = st.tabs(["📊 Анализ по фондам", "🔎 Анализ по акциям (Кто держит бумагу?)"])
    with t1:
        tab_funds(fdb)
    with t2:
        tab_stocks(fdb)

    st.divider()
    st.caption("Данные носят информационный характер и не являются индивидуальной инвестиционной "
               "рекомендацией. Встроенная база содержит ориентировочные составы портфелей; "
               "точные данные — на сайтах управляющих компаний и Investfunds.ru.")


if __name__ == "__main__":
    main()
