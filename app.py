# -*- coding: utf-8 -*-
"""
Позиции ПИФов РФ в акциях — дашборд на Streamlit.

Режимы:
  • «Анализ по фондам» — состав портфеля выбранного фонда или сводный портфель (ИТОГО);
  • «Анализ по акциям (Кто держит бумагу?)» — какие фонды держат выбранную акцию.

Источники данных:
  • список фондов — каталог Investfunds.ru (фонды акций и смешанных инвестиций);
  • состав фонда, по убыванию полноты:
      1) «Справка о стоимости чистых активов» (форма ОКУД 0420502) с сайта УК — полный список акций;
      2) блок структуры портфеля на странице фонда на сайте УК;
      3) ТОП-10 со страницы фонда на Investfunds.ru;
      4) встроенная ориентировочная база (работает без интернета).
  • справку о СЧА можно также загрузить вручную (PDF или ссылка), а базу — из CSV/Excel.

Запуск локально:  streamlit run app.py
"""
from __future__ import annotations

import io
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
C_DOC = "Ссылка на источник"

ALL_COLS = [
    C_FUND, C_UK, C_TYPE, C_NAV, C_DATE, C_NAV_DATE, C_URL,
    C_TICKER, C_COMPANY, C_SECTOR, C_ASSET, C_WEIGHT, C_VALUE, C_SOURCE, C_DOC,
]

SRC_BUILTIN = "Встроенная база"
SRC_SITE = "Investfunds.ru"
SRC_USER = "Файл пользователя"

NO_TICKER = "—"
ASSET_EQUITY = "Акции"
ASSET_BONDS = "Облигации"
ASSET_CASH = "Денежные средства"
ASSET_OTHER = "Прочие активы"

BASE_URL = "https://investfunds.ru"

# Автозагрузка при открытии дашборда
MIN_NAV_MLN = 1000        # в список попадают фонды акций и смешанные фонды с СЧА от этой величины (млн руб.)
MIN_EQUITY_SHARE = 20.0   # фонды с меньшей долей акций исключаются из выборки, %
AUTO_CATALOG_PAGES = 20   # сколько страниц каталога Investfunds.ru (по 50 фондов) просматривать
AUTO_MAX_FUNDS = 300      # предел числа фондов, составы которых загружаются автоматически
LOAD_WORKERS = 4          # параллельных загрузок (больше — быстрее, но выше риск блокировки сайтом)
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
FUNDS_URLS: dict[str, str] = {
    # УК «Первая»
    "Первая — Фонд Топ Российских акций (SBMX)": "https://investfunds.ru/funds/5247/",
    "Первая — Фонд российских акций": "https://investfunds.ru/funds/43/",
    "Первая — Фонд акций с выплатой дохода": "https://investfunds.ru/funds/8123/",
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
    "Т-Капитал — Компании второго эшелона": "https://investfunds.ru/funds/10151/",
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
S_CASH = "Денежные средства"
S_OTHER_ASSETS = "Прочие активы"
S_OTHER = "Прочие отрасли"

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
    "TGKA": ("ТГК-1", S_ENE), "MSNG": ("Мосэнерго", S_ENE),
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
CASH_NAME = "Денежные средства"
OTHER_NAME = "Прочие активы"
REST_NAME = OTHER_NAME

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
    "мосэнерго": "MSNG", "эл5-энерго": "ELFV", "энел россия": "ELFV", "огк-2": "OGKB", "тгк-1": "TGKA",
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
    "Первая — Фонд Топ Российских акций (SBMX)": dict(uk="Первая", type="БПИФ", nav=20540.0),
    "Первая — Фонд российских акций": dict(uk="Первая", type="ОПИФ", nav=12790.0),
    "Первая — Фонд акций с выплатой дохода": dict(uk="Первая", type="ОПИФ", nav=22400.0),
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
    "Т-Капитал — Индекс МосБиржи (TMOS)": dict(uk="Т-Капитал", type="БПИФ", nav=12450.0),
    "Т-Капитал — Акции роста (TITR)": dict(uk="Т-Капитал", type="БПИФ", nav=860.0),
    "Т-Капитал — Компании второго эшелона": dict(uk="Т-Капитал", type="ОПИФ", nav=1360.0),
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
                     C_ASSET: ASSET_CASH, C_WEIGHT: cash})
    for r in rows:
        r[C_VALUE] = round(nav * r[C_WEIGHT] / 100.0, 2)
    return rows


# Строки, которые не могут быть позицией портфеля (показатели доходности, отрасли, итоги)
_JUNK_RE = re.compile(r"динамик|доходност|изменени|стоимост\w*\s+пая|\bсча\b|\bпа[яйиюе]\b|индекс|"
                      r"отрасл|промышленност|\bитого\b|\bвсего\b|структур|за\s+(?:месяц|год)", re.I)
_CASH_RE = re.compile(r"^\s*(?:денежн|депозит)", re.I)


def normalize_positions(df: pd.DataFrame) -> pd.DataFrame:
    """
    Приводит состав каждого фонда к виду: акции + одна строка «Денежные средства»
    + одна строка «Прочие активы» (облигации, паи, нераскрытые позиции и т. п.).
    Строки-показатели («Динамика пая», отрасли и т. п.) отбрасываются.
    """
    if df is None or df.empty:
        return df
    out = []
    for _, g in df.groupby(C_FUND, sort=False):
        base = g.iloc[0].to_dict()
        nav = base.get(C_NAV)
        nav = None if nav is None or pd.isna(nav) else float(nav)
        comp = g[C_COMPANY].astype(str)
        is_eq = g[C_ASSET] == ASSET_EQUITY
        junk = comp.str.contains(_JUNK_RE) & (g[C_TICKER].fillna(NO_TICKER) == NO_TICKER)
        eq = g[is_eq & ~junk].copy()
        # повторы одной и той же строки (дубли блоков на сайте) — убрать; части одной бумаги — сложить
        eq = eq.drop_duplicates(subset=[C_TICKER, C_COMPANY, C_WEIGHT])
        if eq.duplicated(subset=[C_TICKER, C_COMPANY]).any():
            first = eq.drop_duplicates(subset=[C_TICKER, C_COMPANY]).set_index([C_TICKER, C_COMPANY])
            sums = eq.groupby([C_TICKER, C_COMPANY])[[C_WEIGHT, C_VALUE]].sum(min_count=1)
            first[[C_WEIGHT, C_VALUE]] = sums
            eq = first.reset_index()[g.columns]
        non = g[~is_eq & ~junk]
        cash_w = float(non.loc[(non[C_ASSET] == ASSET_CASH) | comp[non.index].str.contains(_CASH_RE),
                               C_WEIGHT].sum())
        eq_w = float(eq[C_WEIGHT].sum())
        if eq_w + cash_w > 100.5:  # доли не могут в сумме превышать 100 %
            k = 100.0 / (eq_w + cash_w)
            eq[C_WEIGHT] = eq[C_WEIGHT] * k
            cash_w *= k
            eq_w *= k
            if nav:
                eq[C_VALUE] = (nav * eq[C_WEIGHT] / 100).round(2)
        other_w = max(0.0, 100.0 - eq_w - cash_w)
        out.append(eq)
        common = {c: base.get(c) for c in (C_FUND, C_UK, C_TYPE, C_NAV, C_DATE, C_NAV_DATE, C_URL,
                                           C_SOURCE, C_DOC)}
        for name, sector, asset, w in ((CASH_NAME, S_CASH, ASSET_CASH, cash_w),
                                       (OTHER_NAME, S_OTHER_ASSETS, ASSET_OTHER, other_w)):
            if w > 0.005:
                out.append(pd.DataFrame([{**common, C_TICKER: NO_TICKER, C_COMPANY: name, C_SECTOR: sector,
                                          C_ASSET: asset, C_WEIGHT: round(w, 4),
                                          C_VALUE: round(nav * w / 100, 2) if nav else None}]))
    res = pd.concat(out, ignore_index=True)
    return res.reindex(columns=ALL_COLS)


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
    return normalize_positions(pd.DataFrame(rows, columns=ALL_COLS))


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


# Строка ТОП-активов на Investfunds.ru всегда описывает бумагу:
# «Лукойл, акция, [1-01-00077-A]», «Транснефть, акция прив. [...]», «Россия, облигация, [26254RMFS]»
_ASSET_ROW_RE = re.compile(
    r",\s*(акци|облигаци|депозитарн|инвестиционн|пай|ипотечн)|акция\s+(об|прив)|\[[0-9A-Z][^\]]{3,}\]",
    re.IGNORECASE)


def _is_asset_name(name: str) -> bool:
    return bool(_ASSET_ROW_RE.search(name or ""))


def _dedupe_items(items: list[tuple[str, float]]) -> list[tuple[str, float]]:
    """Убирает повторы одной и той же строки (сайт дублирует блоки для мобильной вёрстки)."""
    seen, out = set(), []
    for name, w in items:
        key = (re.sub(r"\s+", " ", name).strip().lower(), round(w, 4))
        if key not in seen:
            seen.add(key)
            out.append((name, w))
    return out


def _parse_top_assets_from_tables(soup: BeautifulSoup) -> list[tuple[str, float]]:
    """
    Ищет таблицу ТОП-активов: только «листовые» таблицы (без вложенных) после заголовка
    «Структура фонда»; в расчёт идут только строки с бумагами (акция/облигация/пай…).
    """
    anchor = soup.find(string=re.compile(r"Структура\s+фонда", re.I))
    tables = anchor.find_all_next("table") if anchor else soup.find_all("table")
    for table in tables:
        if table.find("table") is not None:
            continue
        items = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            name = tds[0].get_text(" ", strip=True)
            weight = parse_number(tds[-1].get_text(" ", strip=True))
            if _is_asset_name(name) and weight is not None and 0 < weight <= 100:
                items.append((name, weight))
        items = _dedupe_items(items)
        if items:
            return items
    return []


def _parse_top_assets_from_text(lines: list[str]) -> list[tuple[str, float]]:
    """Запасной вариант: разбор текстового блока «Структура фонда … ТОП-10»."""
    start = next((i for i, l in enumerate(lines) if "структура фонда" in l.lower()), None)
    if start is None:
        start = next((i for i, l in enumerate(lines) if "доля в портфеле" in l.lower()), None)
    if start is None:
        return []
    items, pending = [], None
    for line in lines[start + 1: start + 80]:
        low = line.lower()
        if "полная структура" in low or "управляющий фондом" in low or "инфраструктура" in low:
            break
        m = re.match(r"^(.*\S)\s+(\d{1,3}[.,]\d{1,4})$", line)
        if m and _is_asset_name(m.group(1)):
            items.append((m.group(1), parse_number(m.group(2))))
            pending = None
        elif re.fullmatch(r"\d{1,3}[.,]\d{1,4}", line) and pending:
            items.append((pending, parse_number(line)))
            pending = None
        elif _is_asset_name(line):
            pending = line
        else:
            pending = None
    return _dedupe_items([(n, w) for n, w in items if w is not None and 0 < w <= 100])


def _parse_other_funds(soup: BeautifulSoup) -> list[dict]:
    """Таблица «Другие фонды этой УК»: [{name, url, nav_rub}] (все фонды УК с их СЧА)."""
    fund_href = re.compile(r"^(?:https?://(?:www\.)?investfunds\.ru)?/funds/(\d+)/?$")
    anchor = soup.find(string=re.compile(r"Другие\s+фонды\s+этой\s+УК", re.I))
    tables = [anchor.find_next("table")] if anchor and anchor.find_next("table") else []
    tables += [t for t in soup.find_all("table")
               if "сча" in t.get_text(" ", strip=True)[:200].lower()
               and "название фонда" in t.get_text(" ", strip=True)[:200].lower()]
    out, seen = [], set()
    for t in tables:
        for tr in t.find_all("tr"):
            a = tr.find("a", href=True)
            tds = tr.find_all("td")
            if not a or not fund_href.match(a["href"]) or len(tds) < 2:
                continue
            fid = fund_href.match(a["href"]).group(1)
            if fid in seen:
                continue
            seen.add(fid)
            out.append(dict(name=a.get_text(" ", strip=True), url=f"{BASE_URL}/funds/{fid}/",
                            nav_rub=parse_number(tds[-1].get_text(" ", strip=True))))
    return out


def parse_fund_html(html: str) -> dict:
    """Извлекает со страницы фонда: тип, УК, СЧА, даты и ТОП-активы."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    info = dict(type=None, uk=None, title=None, nav_mln=None, nav_date=None,
                struct_date=None, items=[], uk_link=None, kind=None, status=None, object=None,
                qualified="квалифицированных инвесторов" in text.lower(), other_funds=[])
    low = [l.lower() for l in lines]
    for label, key in (("тип", "kind"), ("статус", "status"), ("объект инвестирования", "object")):
        if label in low:
            i = low.index(label)
            if i + 1 < len(lines):
                info[key] = lines[i + 1].strip()
    info["other_funds"] = _parse_other_funds(soup)
    for a in soup.find_all("a", href=True):
        if "сайте ук" in a.get_text(" ", strip=True).lower() and a["href"].startswith("http"):
            info["uk_link"] = a["href"]
            break

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

    m = re.search(r"Структура\s+фонда\s+на\s*(\d{2}\.\d{2}\.\d{4})", text)
    if not m:  # дата может стоять в соседнем элементе
        m = re.search(r"Структура\s+фонда[^\d]{0,120}?(\d{2}\.\d{2}\.\d{4})", text)
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
        C_DATE: info["struct_date"] or "—",
        C_NAV_DATE: info["nav_date"] or "—",
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
    total = sum(r[C_WEIGHT] for r in rows)
    if total > 100.5:  # защита: доли не могут в сумме превышать 100 %
        for r in rows:
            r[C_WEIGHT] *= 100.0 / total
    rest = round(100.0 - sum(r[C_WEIGHT] for r in rows), 2)
    if rest > 0.05:
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: OTHER_NAME, C_SECTOR: S_OTHER_ASSETS,
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


# =============================================================================
# 5а. КАТАЛОГ ФОНДОВ INVESTFUNDS.RU
# =============================================================================

C_OBJECT = "Объект инвестирования"
CATALOG_COLS = [C_FUND, C_UK, C_TYPE, C_NAV, C_NAV_DATE, C_URL, C_OBJECT]

# Названия УК на Investfunds.ru → единое написание в приложении
UK_NORMALIZE = {
    "уk райффайзен": "Райффайзен Капитал", "ук райффайзен": "Райффайзен Капитал",
    "райффайзен капитал": "Райффайзен Капитал", "райффайзен": "Райффайзен Капитал",
    "промсвязь": "ПСБ Управление активами", "псб": "ПСБ Управление активами",
    "атон-менеджмент": "АТОН", "атон": "АТОН",
    "втб капитал управление активами": "ВИМ Инвестиции",
}
FUND_KIND_TO_TYPE = {"открытый": "ОПИФ", "биржевой": "БПИФ", "интервальный": "ИПИФ", "закрытый": "ЗПИФ"}


def normalize_uk(name: str | None) -> str:
    if not name:
        return "Не указана"
    key = re.sub(r"\s+", " ", str(name)).strip().lower()
    return UK_NORMALIZE.get(key, re.sub(r"\s+", " ", str(name)).strip())


def _header_index(headers: list[str], *keys: str) -> int | None:
    for i, h in enumerate(headers):
        hl = h.lower()
        if all(k in hl for k in keys):
            return i
    return None


def parse_catalog_page(html: str) -> list[dict]:
    """
    Разбирает одну страницу каталога https://investfunds.ru/funds/?limit=50&page=N.
    Названия фондов и колонки с данными на сайте могут быть в разных таблицах
    (закреплённая первая колонка) — строки сопоставляются по порядку.
    """
    soup = BeautifulSoup(html, "html.parser")
    fund_href = re.compile(r"^(?:https?://(?:www\.)?investfunds\.ru)?/funds/(\d+)/?$")

    data_tbl = None
    for t in soup.find_all("table"):
        head_txt = " ".join(th.get_text(" ", strip=True) for th in t.find_all("th"))
        if "объект инвестирования" in head_txt.lower():
            data_tbl = t
            break
    if data_tbl is None:
        return []

    head_row = next((tr for tr in data_tbl.find_all("tr") if tr.find_all("th")), None)
    headers = [th.get_text(" ", strip=True) for th in head_row.find_all("th")] if head_row else []
    data_rows = [tr for tr in data_tbl.find_all("tr") if tr.find_all("td")]

    # Список фондов по порядку (если ссылок нет внутри строк данных)
    name_links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        m = fund_href.match(a["href"])
        if not m or data_tbl in a.parents:
            continue
        name = a.get_text(" ", strip=True)
        if len(name) < 2 or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        cell = a.find_parent(["td", "th", "li", "div"])
        cell_txt = cell.get_text(" ", strip=True).lower() if cell else ""
        name_links.append((name, m.group(1), cell_txt))

    idx = {
        "uk": _header_index(headers, "управляющая"),
        "date": _header_index(headers, "дата расчета"),
        "nav": _header_index(headers, "сча"),
        "status": _header_index(headers, "статус"),
        "object": _header_index(headers, "объект инвестирования"),
    }
    out = []
    for i, tr in enumerate(data_rows):
        tds = tr.find_all("td")
        texts = [td.get_text(" ", strip=True) for td in tds]
        shift = len(texts) - len(headers) if headers else 0

        def cell(key):
            j = idx.get(key)
            if j is None:
                return ""
            j += shift
            return texts[j] if 0 <= j < len(texts) else ""

        link = next((a for a in tr.find_all("a", href=True) if fund_href.match(a["href"])), None)
        if link is not None:
            fid = fund_href.match(link["href"]).group(1)
            name = link.get_text(" ", strip=True)
            kind_txt = link.find_parent("td").get_text(" ", strip=True).lower() if link.find_parent("td") else ""
        elif i < len(name_links):
            name, fid, kind_txt = name_links[i]
        else:
            continue
        uk = cell("uk")
        if not uk:
            uk_img = tr.find("img", alt=True)
            uk = uk_img["alt"] if uk_img else ""
        kind = next((v for k, v in FUND_KIND_TO_TYPE.items() if k in kind_txt), "")
        out.append({
            C_FUND: name, C_UK: normalize_uk(uk), C_TYPE: kind or "ОПИФ",
            C_NAV: parse_number(cell("nav")), C_NAV_DATE: cell("date") or None,
            C_URL: f"{BASE_URL}/funds/{fid}/", C_OBJECT: cell("object"),
            "_status": cell("status").lower(), "_kind": kind,
        })
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_catalog_page(page: int) -> list[dict]:
    url = f"{CATALOG_URL}?limit=50&page={page}"
    return parse_catalog_page(_fetch_html(url))


def is_equity_fund(row: dict) -> bool:
    obj = (row.get(C_OBJECT) or "").lower()
    status = row.get("_status") or ""
    if row.get("_kind") == "ЗПИФ":
        return False
    if status and "сформирован" not in status:
        return False
    if obj:
        return "акци" in obj or "смешан" in obj
    return "акци" in row[C_FUND].lower()


def discover_all_equity_funds(max_pages: int = 100, progress=None) -> tuple[pd.DataFrame, list[str]]:
    """
    Загружает каталог https://investfunds.ru/funds/ постранично и оставляет
    сформированные открытые, биржевые и интервальные фонды категорий
    «акции» и «смешанные инвестиции». Каждая страница кэшируется на сутки.
    """
    rows, errors = [], []
    for page in range(1, max_pages + 1):
        if progress:
            progress(page, max_pages)
        try:
            part = fetch_catalog_page(page)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"страница {page}: {exc}")
            break
        if not part:
            if page == 1:
                errors.append("не удалось распознать таблицу каталога (возможно, изменилась вёрстка сайта)")
            break
        new = [r for r in part if r[C_URL] not in {x[C_URL] for x in rows}]
        if not new:
            break
        rows += new
        time.sleep(0.3)
    df = pd.DataFrame([r for r in rows if is_equity_fund(r)])
    if df.empty:
        return pd.DataFrame(columns=CATALOG_COLS), errors
    return df[CATALOG_COLS].drop_duplicates(C_URL).reset_index(drop=True), errors


# =============================================================================
# 5б. ПОЛНЫЙ СОСТАВ С САЙТОВ УПРАВЛЯЮЩИХ КОМПАНИЙ
# =============================================================================
# Источник №1 — «Справка о стоимости чистых активов» (форма ОКУД 0420502). Каждая УК
# ежемесячно публикует её на своём сайте; в разделе «2.7. Акции российских акционерных
# обществ» перечислены ВСЕ акции фонда (ISIN, категория, количество, стоимость).
# Источник №2 — блок «Структура активов» / «Портфель фонда» на странице фонда на сайте УК.

SRC_UK_REPORT = "Сайт УК: справка о СЧА"
SRC_UK_PAGE = "Сайт УК: страница фонда"

# Известные страницы фондов на сайтах УК (для остальных ссылка берётся с Investfunds.ru —
# блок «Страница ПИФ на сайте УК»)
UK_PAGES: dict[str, str] = {
    "ВИМ — Индекс МосБиржи (ОПИФ)": "https://www.wealthim.ru/products/pif/opif/wimfimb/",
    "ВИМ — Акции": "https://www.wealthim.ru/products/pif/opif/wimfa/",
    "Первая — Фонд акций с выплатой дохода": "https://first-am.ru/individuals/fund/stock-fund-with-income-payment",
    "Альфа-Капитал — Ликвидные акции": "https://www.alfacapital.ru/individual/pifs/opifa_akliq",
    "Альфа-Капитал — Акции компаний роста": "https://www.alfacapital.ru/individual/pifs/opif_ak_growth",
    "Альфа-Капитал — Акции с выплатой дохода": "https://www.alfacapital.ru/individual/pifs/opif_akstockip",
}
# Т-Капитал выкладывает справки на CDN с предсказуемыми именами файлов
TCAP_CDN = "https://cdn.t-capital-funds.ru/static/documents"
TCAP_REPORTS: dict[str, tuple[str, str]] = {
    "Т-Капитал — Индекс МосБиржи (TMOS)": ("tmos", "4100"),
    "Т-Капитал — Акции роста (TITR)": ("titr", "6126"),
    "Т-Капитал — Компании второго эшелона": ("smcp", "5991"),
}

# ISIN → тикер для крупнейших бумаг (остальные — по названию эмитента или через ISS МосБиржи)
ISIN_MAP: dict[str, str] = {
    "RU0009029540": "SBER", "RU0009029557": "SBERP", "RU0009024277": "LKOH",
    "RU0007661625": "GAZP", "RU000A0DKVS5": "NVTK", "RU0009062285": "AFLT", "RU0008958863": "MSNG",
    "RU0009046452": "NLMK", "RU0008943394": "RTKM", "RU0007288411": "GMKN",
    "RU000A0J2Q06": "ROSN", "RU0009033591": "TATN", "RU0006944147": "TATNP",
    "RU0008926258": "SNGS", "RU0009029524": "SNGSP", "RU0009046510": "CHMF",
    "RU000A0JKQU8": "MGNT", "RU000A0JNAA8": "PLZL", "RU0007252813": "ALRS",
    "RU0007775219": "MTSS", "RU000A0JP5V6": "VTBR", "RU000A0JR4A1": "MOEX",
    "RU000A0JPNM1": "IRAO", "RU000A0JRKT8": "PHOR", "RU0009084396": "MAGN",
    "RU000A0JPKH7": "HYDR", "RU000A0JP7J7": "PIKK", "RU000A0DQZE3": "AFKS",
    "RU0009091573": "TRNFP", "RU000A0JUG31": "CBOM", "RU000A0JNUD0": "UPRO",
    "RU000A100K72": "ENPG", "RU000A1025V3": "RUAL", "RU000A107T19": "YDEX",
    "RU000A107UL4": "T", "RU000A108X38": "X5", "RU000A103X66": "POSI",
    "RU000A0ZZG02": "SMLT", "RU000A106T36": "ASTR",
}

RU_MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
             "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
_ISIN_RE = re.compile(r"^[A-Z]{2}[0-9A-Z]{9}[0-9]$")


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_text(url: str) -> str:
    return _fetch_html(url)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_bytes(url: str) -> bytes:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=40)
    except requests.RequestException as exc:
        raise RuntimeError(f"не удалось скачать файл ({exc.__class__.__name__})")
    if resp.status_code >= 400:
        raise RuntimeError(f"файл недоступен (ошибка {resp.status_code})")
    return resp.content


def _url_exists(url: str) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15, stream=True)
        ok = r.status_code == 200 and "html" not in r.headers.get("Content-Type", "").lower()
        r.close()
        return ok
    except requests.RequestException:
        return False


_ISS_STATE = {"down": False}


@st.cache_data(ttl=7 * 86400, show_spinner=False)
def isin_to_ticker_moex(isin: str) -> tuple[str, str] | None:
    """Поиск тикера по ISIN через открытый API Московской биржи (ISS)."""
    if _ISS_STATE["down"]:
        return None
    url = ("https://iss.moex.com/iss/securities.json?iss.meta=off&q=" + isin +
           "&securities.columns=secid,isin,shortname,primary_boardid")
    try:
        data = requests.get(url, headers=HEADERS, timeout=6).json()["securities"]["data"]
    except Exception:  # noqa: BLE001
        _ISS_STATE["down"] = True  # API недоступен — больше не ждём его в этой сессии
        return None
    exact = [r for r in data if r[1] == isin]
    best = next((r for r in exact if r[3] in ("TQBR", "TQTF", "TQPI")), exact[0] if exact else None)
    return (best[0], best[2]) if best else None


def _extract_date(text: str) -> datetime | None:
    s = str(text)
    for pat, fmt in ((r"(\d{2}\.\d{2}\.\d{4})", "%d.%m.%Y"), (r"(\d{2}-\d{2}-\d{4})", "%d-%m-%Y"),
                     (r"(\d{4}-\d{2}-\d{2})", "%Y-%m-%d"), (r"(?<!\d)(\d{8})(?!\d)", "%d%m%Y"),
                     (r"(?<!\d)(\d{4}_\d{2}_\d{2})(?!\d)", "%Y_%m_%d")):
        for m in re.finditer(pat, s):
            try:
                d = datetime.strptime(m.group(1), fmt)
                if 2015 <= d.year <= 2100:
                    return d
            except ValueError:
                continue
    m = re.search(r"(\d{1,2})\s+(" + "|".join(RU_MONTHS) + r")\s+(\d{4})", s.lower())
    if m:
        return datetime(int(m.group(3)), RU_MONTHS[m.group(2)], int(m.group(1)))
    return None


_REPORT_TEXT_RE = re.compile(r"справк\w*\s+о\s+стоимости\s+чистых\s+активов|\bсча\b", re.I)
_REPORT_HREF_RE = re.compile(r"scha|spravka|0420502", re.I)
_REPORT_EXCLUDE = re.compile(r"правил|прирост|изменени|расч[её]т\w*\s+сча|kid|ключев", re.I)


def find_report_links(html: str, base_url: str) -> list[tuple[datetime | None, str]]:
    """Ссылки на справки о СЧА на странице, от свежих к старым."""
    from urllib.parse import urljoin
    soup = BeautifulSoup(html, "html.parser")
    found: dict[str, datetime | None] = {}
    for a in soup.find_all("a", href=True):
        text = f"{a.get_text(' ', strip=True)} {a.get('title', '')}".strip()
        href = urljoin(base_url, a["href"])
        if not (_REPORT_TEXT_RE.search(text) or _REPORT_HREF_RE.search(href)):
            continue
        if _REPORT_EXCLUDE.search(text) or href.lower().endswith((".jpg", ".png")):
            continue
        found.setdefault(href, _extract_date(f"{text} {href}"))
    return sorted(((d, u) for u, d in found.items()),
                  key=lambda x: x[0] or datetime(1900, 1, 1), reverse=True)


def discover_reports(page_url: str) -> list[str]:
    """Ищет справки о СЧА на странице фонда УК и, при необходимости, в разделах раскрытия информации."""
    from urllib.parse import urljoin, urlparse
    html = fetch_text(page_url)
    links = find_report_links(html, page_url)
    if links:
        return [u for _, u in links]
    soup = BeautifulSoup(html, "html.parser")
    host = urlparse(page_url).netloc
    hops = []
    for a in soup.find_all("a", href=True):
        t = a.get_text(" ", strip=True).lower()
        if any(k in t for k in ("раскрытие", "отчетност", "отчётност", "документы фонда", "документы")):
            u = urljoin(page_url, a["href"])
            if urlparse(u).netloc == host and u not in hops:
                hops.append(u)
    for u in hops[:4]:
        try:
            links = find_report_links(fetch_text(u), u)
        except Exception:  # noqa: BLE001
            continue
        if links:
            return [x for _, x in links]
    return []


def _last_business_day(year: int, month: int) -> datetime:
    nxt = datetime(year + (month == 12), month % 12 + 1, 1)
    d = nxt - pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def tcap_report_url(slug: str, regno: str) -> str | None:
    """Подбирает адрес последней справки о СЧА фонда Т-Капитала на их CDN."""
    today = datetime.now()
    for k in range(0, 4):
        y, m = today.year, today.month - k
        while m <= 0:
            m += 12
            y -= 1
        d = _last_business_day(y, m)
        if d > today:
            continue
        ds = d.strftime("%d-%m-%Y")
        for name in (f"otchet-scha-{slug}-{ds}.pdf", f"otchet-scha-{slug}-{ds}.PDF",
                     f"{regno}-{slug}-scha-{ds}.pdf", f"{slug}-scha-{ds}.pdf"):
            url = f"{TCAP_CDN}/{name}"
            if _url_exists(url):
                return url
    return None


def _num_cell(c) -> float | None:
    s = re.sub(r"\s+", "", str(c or ""))
    if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", s):
        return None
    return float(s.replace(",", "."))


def _table_kind(header_txt: str) -> str | None:
    h = header_txt.lower()
    if "категория акций" in h or "акции российских" in h or "акции иностранных" in h:
        return "equity"
    if "депозитарн" in h:
        return "equity"
    if "облигац" in h:
        return "bond"
    if "инвестиционн" in h and "па" in h:
        return "units"
    return None


def parse_scha_pdf(data: bytes) -> dict:
    """
    Разбирает «Справку о стоимости чистых активов» (форма 0420502) в PDF.
    Возвращает: nav (руб.), date (дд.мм.гггг), title (название фонда),
    items — список акций {issuer, isin, kind ('ord'/'pref'/''), value (руб.)}.
    """
    import pdfplumber  # импорт здесь, чтобы приложение запускалось и без этой библиотеки

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        flat = re.sub(r"\s+", " ", text)
        result = None
        # 1) таблицы с линиями сетки; 2) «визуализация XBRL» без линий — по выравниванию текста
        for settings in (None, {"vertical_strategy": "text", "horizontal_strategy": "text"}):
            tables = []
            for page in pdf.pages:
                for tb in (page.extract_tables(settings) if settings else page.extract_tables()) or []:
                    tables.append([[re.sub(r"\s+", " ", str(c or "")).strip() for c in row] for row in tb])
            result = _parse_scha_tables(tables, flat)
            if len(result["items"]) >= 3:
                break
        if len(result["items"]) < 3:  # 3) разбор по координатам слов (таблицы без сетки)
            geo = _parse_scha_geometry(pdf)
            if len(geo) > len(result["items"]):
                result["items"] = geo
    return result


def _line_segments(words: list[dict]) -> list[dict]:
    """Склеивает слова одной строки в «ячейки» по расстоянию между ними."""
    segs = []
    for w in sorted(words, key=lambda w: w["x0"]):
        gap_limit = max(2.0, 0.45 * (w["bottom"] - w["top"]))
        if segs and w["x0"] - segs[-1]["x1"] <= gap_limit:
            segs[-1]["text"] += " " + w["text"]
            segs[-1]["x1"] = w["x1"]
        else:
            segs.append(dict(text=w["text"], x0=w["x0"], x1=w["x1"]))
    return segs


def _parse_scha_geometry(pdf) -> list[dict]:
    """
    Разбор раздела «Акции российских акционерных обществ» по положению слов на странице
    (для справок без линий сетки). Строка таблицы — блок строк текста от одного ISIN
    до следующего; колонки внутри блока определяются по горизонтальной координате.
    """
    isin_start = re.compile(r"[A-Z]{2}[0-9A-Z]{4,10}")
    items, in_equity = [], False
    for page in pdf.pages:
        words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
        lines: list[list[dict]] = []
        for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
            if lines and abs(lines[-1][0]["top"] - w["top"]) <= 2:
                lines[-1].append(w)
            else:
                lines.append([w])
        segs = [_line_segments(l) for l in lines]

        # отмечаем строки-«якоря» (начало ISIN) и границы разделов
        anchors, section = [], []
        for i, ls in enumerate(segs):
            txt = " ".join(sg["text"] for sg in ls).lower()
            if "акции российских" in txt or "категория акций" in txt or "депозитарн" in txt:
                in_equity = True
            elif re.search(r"\bоблигаци|инвестиционные паи|раздел 4|раздел iv|обязательств", txt) \
                    and "акци" not in txt:
                in_equity = False
            section.append(in_equity)
            isg = next((sg for sg in ls if isin_start.fullmatch(sg["text"].replace(" ", ""))), None)
            if isg is not None:
                anchors.append((i, isg))
        for k, (i, isg) in enumerate(anchors):
            if not section[i]:
                continue
            end = min(anchors[k + 1][0] if k + 1 < len(anchors) else len(segs), i + 6)
            for j in range(i + 1, end):  # блок не заходит в следующий раздел
                if re.match(r"(?i)(раздел|подраздел|\d\.\d+\.)\s", " ".join(sg["text"] for sg in segs[j])):
                    end = j
                    break
            block = [sg for ls in segs[i:end] for sg in ls]
            # ISIN (может быть перенесён)
            isin = "".join(sg["text"].replace(" ", "") for sg in block
                           if abs(sg["x0"] - isg["x0"]) <= 3 and re.fullmatch(r"[0-9A-Z ]+", sg["text"]))
            isin = isin[:12]
            if not _ISIN_RE.match(isin):
                continue
            # колонки правее ISIN
            cols: list[dict] = []
            for sg in sorted((sg for sg in block if sg["x0"] > isg["x1"] + 1), key=lambda z: z["x0"]):
                col = next((c for c in cols if abs(c["x0"] - sg["x0"]) <= 4), None)
                if col is None:
                    cols.append(dict(x0=sg["x0"], parts=[sg["text"]]))
                else:
                    col["parts"].append(sg["text"])
            cols.sort(key=lambda c: c["x0"])
            num_vals, cat_txt = [], ""
            for c in cols:
                joined = ""
                for part in c["parts"]:
                    joined += part if part.startswith(",") or not joined else " " + part
                v = _num_cell(joined)
                if v is not None:
                    num_vals.append(v)
                else:
                    cat_txt += "".join(c["parts"]).lower()
            if not num_vals:
                continue
            value = num_vals[1] if len(num_vals) >= 2 else num_vals[0]
            left = [sg for sg in block if sg["x1"] < isg["x0"] - 1 and re.search(r"[А-Яа-яA-Za-z]{2}", sg["text"])]
            first_x = min((sg["x0"] for sg in left), default=0)
            name = " ".join(sg["text"] for sg in left if abs(sg["x0"] - first_x) <= 4)
            items.append(dict(issuer=name, isin=isin, value=value,
                              kind="pref" if "привилег" in cat_txt else ("ord" if "обыкнов" in cat_txt else "")))
    return items


def _parse_scha_tables(tables: list, flat: str) -> dict:

    items, nav, current, cash = [], None, None, None
    for tb in tables:
        head = " ".join(" ".join(r) for r in tb[:3])
        kind = _table_kind(head)
        if kind:
            current = kind
        for cells in tb:
            joined = " ".join(cells).lower()
            if "стоимость чистых активов" in joined and "справка" not in joined:
                nums = [n for n in (_num_cell(c) for c in cells) if n and n > 1000]
                if nums:
                    nav = nums[-1]
            if cash is None and re.search(r"денежные средства\s*[–-]\s*всего", joined):
                nums = [n for n in (_num_cell(c) for c in cells) if n is not None and n >= 0]
                if nums:
                    cash = nums[-1]
            isin_idx = next((i for i, c in enumerate(cells) if _ISIN_RE.match(c.replace(" ", ""))), None)
            if isin_idx is None:
                continue
            cat = next((c.lower() for c in cells if "обыкнов" in c.lower() or "привилег" in c.lower()), "")
            if not cat and current != "equity":
                continue
            nums = [n for n in (_num_cell(c) for c in cells[isin_idx + 1:]) if n is not None]
            if not nums:
                continue
            value = nums[1] if len(nums) >= 2 else nums[0]
            name = next((c for c in cells[:isin_idx] if re.search(r"[А-Яа-яA-Za-z]{3}", c)), "")
            items.append(dict(issuer=name, isin=cells[isin_idx].replace(" ", ""),
                              kind="pref" if "привилег" in cat else ("ord" if cat else ""),
                              value=value))

    if nav is None:  # запасной вариант — поиск по тексту
        for m in reversed(list(re.finditer(r"Стоимость чистых активов", flat))):
            window = flat[m.end(): m.end() + 250]
            n = (re.search(r"\b\d{2,3}\s+(\d{1,3}(?: \d{3})+,\d{1,2})", window)
                 or re.search(r"(\d{1,3}(?: \d{3})+,\d{1,2})", window))
            if n:
                nav = parse_number(n.group(1))
                break

    if cash is None:
        m = re.search(r"Денежные средства\s*[–-]\s*всего\s*(?:\b\d{2}\s+)?(\d{1,3}(?: \d{3})*(?:,\d{1,2})?)", flat)
        if m:
            cash = parse_number(m.group(1))

    date = None
    m = re.search(r"по\s+состоянию\s+на\s+(\d{1,2})\s+([а-я]+)\s+(\d{4})", flat, re.I)
    if m and m.group(2).lower() in RU_MONTHS:
        date = f"{int(m.group(1)):02d}.{RU_MONTHS[m.group(2).lower()]:02d}.{m.group(3)}"
    else:
        m = re.search(r"Параметры справки.{0,400}?(\d{2}\.\d{2}\.\d{4})", flat)
        date = m.group(1) if m else None

    title = None
    m = re.search(r"(?:фонд\w*|фондом)\s+рыночных\s+финансовых\s+инструментов\s*[«\"“]([^»\"”]{3,120})[»\"”]",
                  flat, re.I)
    if m:
        title = m.group(1).strip()
    return dict(nav=nav, date=date, title=title, items=items, cash=cash, text_len=len(flat))


def pretty_issuer(name: str) -> str:
    s = re.sub(r"(?i)публичное акционерное общество|акционерное общество|международная компания|"
               r"\bмкпао\b|\bпао\b|\bао\b|\bоао\b|\bнк\b", " ", str(name))
    s = re.sub(r"[«»\"“”]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" ,") or str(name)


def _resolve_report_item(it: dict) -> tuple[str, str, str]:
    """(тикер, компания, сектор) для строки справки."""
    tk = ISIN_MAP.get(it["isin"]) or resolve_ticker(it["issuer"])
    if tk and it["kind"] == "pref":
        tk = PREF_MAP.get(tk, tk)
    if not tk:
        found = isin_to_ticker_moex(it["isin"])
        if found:
            tk = found[0]
    if tk and tk in COMPANIES:
        return tk, COMPANIES[tk][0], COMPANIES[tk][1]
    suffix = " (прив.)" if it["kind"] == "pref" else ""
    return (tk or NO_TICKER), pretty_issuer(it["issuer"]) + suffix, S_OTHER


def _fund_common(fund: str, meta: dict, nav_mln, struct_date, nav_date, source, doc_url) -> dict:
    fm = FUND_META.get(fund, {})
    return {
        C_FUND: fund, C_UK: meta.get(C_UK) or fm.get("uk") or "Не указана",
        C_TYPE: meta.get(C_TYPE) or fm.get("type") or "ОПИФ", C_NAV: nav_mln,
        C_DATE: struct_date or nav_date or datetime.now().strftime("%d.%m.%Y"),
        C_NAV_DATE: nav_date or struct_date or "—",
        C_URL: meta.get(C_URL) or FUNDS_URLS.get(fund, BASE_URL), C_SOURCE: source, C_DOC: doc_url,
    }


def rows_from_report(fund: str, meta: dict, rep: dict, doc_url: str) -> list[dict]:
    nav_rub = rep["nav"]
    common = _fund_common(fund, meta, round(nav_rub / 1e6, 2), rep["date"], rep["date"],
                          SRC_UK_REPORT, doc_url)
    agg: dict[tuple, dict] = {}
    for it in rep["items"]:
        tk, company, sector = _resolve_report_item(it)
        key = (tk, company)
        if key not in agg:
            agg[key] = {**common, C_TICKER: tk, C_COMPANY: company, C_SECTOR: sector,
                        C_ASSET: ASSET_EQUITY, "_rub": 0.0}
        agg[key]["_rub"] += it["value"]
    rows = list(agg.values())
    for r in rows:
        r[C_WEIGHT] = round(r["_rub"] / nav_rub * 100, 4)
        r[C_VALUE] = round(r.pop("_rub") / 1e6, 2)
    cash = rep.get("cash")
    if cash and nav_rub and 0 < cash < nav_rub:
        w = round(cash / nav_rub * 100, 4)
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: CASH_NAME, C_SECTOR: S_CASH,
                     C_ASSET: ASSET_CASH, C_WEIGHT: w, C_VALUE: round(cash / 1e6, 2)})
    return rows


_STRUCT_HEADINGS = ("структура активов", "портфель фонда", "состав фонда", "структура портфеля",
                    "топ-10 эмитентов", "состав портфеля", "крупнейшие позиции", "крупных позиций",
                    "крупнейших позиций", "крупнейшие эмитенты", "основные позиции", "топ-10")
_ASSET_CLASS_WORDS = {"акции", "облигации", "денежные средства", "прочее", "прочие активы",
                      "депозиты", "паи", "иное", "дебиторская задолженность", "валюта"}


def _structure_from_scripts(html: str) -> dict:
    """
    Многие сайты УК рисуют диаграммы скриптами, а данные передают в JSON внутри страницы
    (например, {"name":"Лукойл","value":9.9}). Ищем такие пары и оставляем только эмитентов.
    """
    raw = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), html)
    name_k = r"(?:name|title|label|issuer|emitent|text|company)"
    val_k = r"(?:value|share|percent|percentage|weight|y|part|proportion|dolya)"
    pats = [
        re.compile(r"[\"']?" + name_k + r"[\"']?\s*:\s*[\"']([^\"'<>{}\\]{2,60})[\"'][^{}]{0,200}?[\"']?"
                   + val_k + r"[\"']?\s*:\s*[\"']?(\d{1,3}(?:[.,]\d{1,6})?)", re.I),
        re.compile(r"[\"']?" + val_k + r"[\"']?\s*:\s*[\"']?(\d{1,3}(?:[.,]\d{1,6})?)[\"']?[^{}]{0,200}?[\"']?"
                   + name_k + r"[\"']?\s*:\s*[\"']([^\"'<>{}\\]{2,60})[\"']", re.I),
    ]
    found, first_pos = [], None
    for k, pat in enumerate(pats):
        for m in pat.finditer(raw):
            name, val = (m.group(1), m.group(2)) if k == 0 else (m.group(2), m.group(1))
            w = parse_number(val)
            if w is None:
                continue
            found.append((m.start(), name.strip(), w))
    found.sort()
    items, cash, seen = [], None, set()
    for pos, name, w in found:
        if _CASH_RE.search(name):
            cash = w if cash is None else cash
            continue
        clean = re.sub(r"(?i)\s+(ао|ап|обыкн\.?|прив\.?)$", "", name)
        if _JUNK_RE.search(name) or not resolve_ticker(clean) or name.lower() in seen:
            continue
        seen.add(name.lower())
        items.append((name, w))
        first_pos = pos if first_pos is None else first_pos
    if items and all(w <= 1 for _, w in items) and sum(w for _, w in items) <= 1.05:
        items = [(n, w * 100) for n, w in items]  # доли переданы в долях единицы
        cash = cash * 100 if cash is not None and cash <= 1 else cash
    date = None
    if first_pos is not None:
        window = raw[max(0, first_pos - 4000): first_pos + 4000]
        dates = [d for d in (_extract_date(m.group(0)) for m in
                             re.finditer(r"\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2}", window))
                 if d and d <= datetime.now()]
        if dates:
            date = max(dates).strftime("%d.%m.%Y")
    items = [(n, w) for n, w in items if 0 < w <= 100]
    return dict(items=items if len(items) >= 3 else [], date=date, cash=cash)


def parse_uk_structure_html(html: str) -> dict:
    """Извлекает пары «эмитент — доля, %» из блока структуры портфеля на странице фонда УК."""
    from_scripts = _structure_from_scripts(html)
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    lines = [l.strip() for l in soup.get_text("\n").split("\n") if l.strip()]
    starts = [i for i, l in enumerate(lines) if any(h in l.lower() for h in _STRUCT_HEADINGS)]
    best = dict(items=[], date=None, cash=None)
    for st_i in starts:
        items, prev, cash = [], None, None
        date = None
        for k, line in enumerate(lines[st_i: st_i + 400]):
            if k and re.search(r"(?i)^(динамика|доходность|документы|стоимость пая|история|графики?)\b", line):
                break
            if date is None:
                m = _DATE_RE.search(line)
                if m:
                    date = m.group(1)
            m_pct = re.fullmatch(r"(\d{1,3}(?:[.,]\d{1,4})?)\s*%", line)
            m_pair = re.fullmatch(r"(.{2,80}?)[\s:–-]+(\d{1,3}(?:[.,]\d{1,4})?)\s*%", line)
            if m_pct and prev:
                name, w = prev, parse_number(m_pct.group(1))
            elif m_pair:
                name, w = m_pair.group(1), parse_number(m_pair.group(2))
            else:
                if re.search(r"[А-Яа-яA-Za-z]{2}", line) and len(line) <= 80:
                    prev = line
                continue
            prev = None
            if w is None or not 0 < w <= 100:
                continue
            if _CASH_RE.search(name):
                cash = w if cash is None else cash
                continue
            clean = re.sub(r"(?i)\s+(ао|ап|обыкн\.?|прив\.?)$", "", name.strip())
            if name.strip().lower() in _ASSET_CLASS_WORDS or _JUNK_RE.search(name) or not resolve_ticker(clean):
                continue  # не эмитент (класс активов, показатель) или не удалось распознать
            items.append((name.strip(), w))
        if len(items) >= 3 and len(items) > len(best["items"]):
            best = dict(items=items, date=date, cash=cash)
    if len(from_scripts["items"]) > len(best["items"]):
        best = from_scripts
    return best


def rows_from_uk_page(fund: str, meta: dict, st_: dict, nav_mln, nav_date, page_url) -> list[dict]:
    common = _fund_common(fund, meta, nav_mln, st_["date"], nav_date, SRC_UK_PAGE, page_url)
    agg: dict[tuple, dict] = {}
    for name, w in st_["items"]:
        clean = re.sub(r"(?i)\s+(ао|ап|ао\.|ап\.|обыкн\.?|прив\.?)$", "", name).strip()
        tk = resolve_ticker(clean)
        if tk and re.search(r"(?i)\s(ап|прив\.?)$", name):
            tk = PREF_MAP.get(tk, tk)
        company, sector = COMPANIES.get(tk, (clean, S_OTHER)) if tk else (clean, S_OTHER)
        key = (tk or NO_TICKER, company)
        if key in agg:
            agg[key][C_WEIGHT] += w
        else:
            agg[key] = {**common, C_TICKER: tk or NO_TICKER, C_COMPANY: company, C_SECTOR: sector,
                        C_ASSET: ASSET_EQUITY, C_WEIGHT: w}
    rows = list(agg.values())
    if st_.get("cash"):
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: CASH_NAME, C_SECTOR: S_CASH,
                     C_ASSET: ASSET_CASH, C_WEIGHT: st_["cash"]})
    total = sum(r[C_WEIGHT] for r in rows)
    if total > 100.5:
        for r in rows:
            r[C_WEIGHT] *= 100.0 / total
    rest = round(100 - sum(r[C_WEIGHT] for r in rows), 2)
    if rest > 0.05:
        rows.append({**common, C_TICKER: NO_TICKER, C_COMPANY: OTHER_NAME, C_SECTOR: S_OTHER_ASSETS,
                     C_ASSET: ASSET_OTHER, C_WEIGHT: rest})
    for r in rows:
        r[C_WEIGHT] = round(r[C_WEIGHT], 4)
        r[C_VALUE] = round(nav_mln * r[C_WEIGHT] / 100, 2) if nav_mln else None
    return rows


# «Точки входа» на сайтах УК: страницы, где есть ссылки на страницы отдельных фондов
UK_SEED_PAGES: dict[str, list[str]] = {
    "Первая": ["https://first-am.ru/individuals/fund/stock-fund-with-income-payment"],
    "ВИМ Инвестиции": ["https://www.wealthim.ru/products/pif/opif/", "https://www.wealthim.ru/products/pif/opif/wimfimb/"],
    "Альфа-Капитал": ["https://www.alfacapital.ru/individual/pifs", "https://www.alfacapital.ru/individual/bpifs"],
    "Т-Капитал": ["https://t-capital-funds.ru/"],
    "АТОН": ["https://www.am-aton.ru/"],
    "ПСБ Управление активами": ["https://www.upravlyaem.ru/"],
}


def _strip_uk_prefix(fund: str) -> str:
    s = re.sub(r"\([A-Z]{3,5}\)", " ", fund)
    return re.sub(r"^[^—–]+[—–]\s*", "", s).strip() if re.search(r"[—–]", s) else s


@st.cache_data(ttl=86400, show_spinner=False)
def _uk_links(seed_url: str) -> list[tuple[str, str]]:
    """Ссылки (текст, адрес) со страницы УК на её же домене."""
    from urllib.parse import urljoin, urlparse
    soup = BeautifulSoup(fetch_text(seed_url), "html.parser")
    host = urlparse(seed_url).netloc.replace("www.", "")
    out = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        url = urljoin(seed_url, a["href"]).split("#")[0]
        if len(text) >= 4 and urlparse(url).netloc.replace("www.", "") == host and urlparse(url).path.count("/") >= 2:
            out.append((text, url))
    return out


def resolve_uk_page(fund: str, uk: str | None) -> str | None:
    """Находит страницу фонда на сайте УК по ссылкам с её «точек входа»."""
    target = _strip_uk_prefix(fund)
    best, best_score = None, 0.0
    for seed in UK_SEED_PAGES.get(uk or "", []):
        try:
            links = _uk_links(seed)
        except Exception:  # noqa: BLE001
            continue
        if links:
            i, score = match_fund_by_title(target, [t for t, _ in links], with_score=True)
            if score > best_score:
                best, best_score = links[i][1], score
    return best if best_score >= 0.75 else None


def _catalog_url_for(fund: str) -> str | None:
    """Ищет страницу фонда на Investfunds.ru в загруженном каталоге по похожему названию."""
    try:
        cat = st.session_state.get("catalog")
    except Exception:  # noqa: BLE001
        return None
    if cat is None or cat.empty:
        return None
    names = cat[C_FUND].tolist()
    i, score = match_fund_by_title(fund, names, with_score=True)
    return cat.iloc[i][C_URL] if score >= 0.5 else None


def _date_key(d: str | None) -> datetime:
    try:
        return datetime.strptime(d, "%d.%m.%Y")
    except (TypeError, ValueError):
        return datetime(1900, 1, 1)


def load_fund_composition(fund: str, meta: dict) -> tuple[list[dict], str, list[str]]:
    """
    Собирает состав фонда из всех доступных источников и выбирает САМЫЙ СВЕЖИЙ по дате состава;
    при одинаковой дате — более полный:
      1) справка о СЧА с сайта УК (полный состав);
      2) структура портфеля на странице фонда на сайте УК;
      3) ТОП-10 с Investfunds.ru.
    Возвращает (строки базы, описание источника, журнал попыток).
    """
    log: list[str] = []
    inv_url = meta.get(C_URL)
    if not is_fund_page(inv_url):
        inv_url = FUNDS_URLS.get(fund) if is_fund_page(FUNDS_URLS.get(fund)) else _catalog_url_for(fund)
        if inv_url:
            meta = {**meta, C_URL: inv_url}
    info = None
    if is_fund_page(inv_url):
        try:
            info = parse_fund_html(fetch_text(inv_url))
        except Exception as exc:  # noqa: BLE001
            log.append(f"Investfunds.ru: {exc}")
    else:
        log.append("Investfunds.ru: адрес страницы фонда не указан и не найден в каталоге "
                   "(загрузите список фондов или впишите адрес в FUNDS_URLS)")
    nav_mln = (info or {}).get("nav_mln") or meta.get(C_NAV) or FUND_META.get(fund, {}).get("nav")
    nav_date = (info or {}).get("nav_date") or meta.get(C_NAV_DATE)
    uk_name = meta.get(C_UK) or FUND_META.get(fund, {}).get("uk")
    uk_url = UK_PAGES.get(fund) or (info or {}).get("uk_link") or resolve_uk_page(fund, uk_name)

    # (дата состава, полнота, строки, описание)
    candidates: list[tuple[datetime, int, list[dict], str]] = []

    # 1. Справка о СЧА
    report_urls: list[str] = []
    if fund in TCAP_REPORTS:
        u = tcap_report_url(*TCAP_REPORTS[fund])
        if u:
            report_urls.append(u)
    uk_failed = False
    if uk_url:
        try:
            report_urls += [u for u in discover_reports(uk_url) if u not in report_urls]
        except Exception as exc:  # noqa: BLE001
            uk_failed = True
            log.append(f"Сайт УК ({uk_url}): {exc}")
    else:
        log.append("Сайт УК: страница фонда не найдена")
    for u in report_urls[:3]:
        try:
            rep = parse_scha_pdf(fetch_bytes(u))
        except Exception as exc:  # noqa: BLE001
            log.append(f"Справка {u.rsplit('/', 1)[-1]}: {exc}")
            continue
        if rep["nav"] and len(rep["items"]) >= 3:
            candidates.append((_date_key(rep["date"]), 3, rows_from_report(fund, meta, rep, u),
                               f"{SRC_UK_REPORT} на {rep['date'] or '—'}"))
            break
        log.append(f"Справка {u.rsplit('/', 1)[-1]}: не удалось разобрать таблицу акций")
    if uk_url and not report_urls and not uk_failed:
        log.append("Сайт УК: ссылки на справки о СЧА не найдены")

    # 2. Структура на странице фонда УК
    if uk_url and not uk_failed:
        try:
            st_ = parse_uk_structure_html(fetch_text(uk_url))
            if st_["items"]:
                candidates.append((_date_key(st_["date"]), 2,
                                   rows_from_uk_page(fund, meta, st_, nav_mln, nav_date, uk_url),
                                   f"{SRC_UK_PAGE} на {st_['date'] or '—'}"))
            else:
                log.append("Сайт УК: структура портфеля на странице не найдена")
        except Exception as exc:  # noqa: BLE001
            log.append(f"Сайт УК: {exc}")

    # 3. ТОП-10 Investfunds.ru
    if info and info["items"]:
        rows = _rows_from_parsed(fund, inv_url, info)
        for r in rows:
            r[C_UK] = meta.get(C_UK) or r[C_UK]
            r[C_TYPE] = meta.get(C_TYPE) or r[C_TYPE]
            r[C_DOC] = inv_url
        candidates.append((_date_key(info["struct_date"]), 1, rows,
                           f"{SRC_SITE} (ТОП-10) на {info['struct_date'] or '—'}"))
    elif info is not None:
        log.append("Investfunds.ru: таблица ТОП-активов не найдена")

    if not candidates:
        return [], "", log
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)
    best = candidates[0]
    if len(candidates) > 1:
        log.append("Выбран самый свежий источник. Другие: " + "; ".join(c[3] for c in candidates[1:]))
    rows = best[2]
    for r in rows:  # СЧА и её дата — самые свежие (с Investfunds.ru), доли — из выбранного источника
        if nav_mln and r.get(C_NAV) != nav_mln and best[1] != 3:
            r[C_NAV] = nav_mln
            r[C_VALUE] = round(nav_mln * r[C_WEIGHT] / 100, 2) if r.get(C_WEIGHT) is not None else None
        if nav_date and best[1] != 3:
            r[C_NAV_DATE] = nav_date
    return rows, best[3], log


def match_fund_by_title(title: str | None, candidates: list[str], with_score: bool = False):
    """Индекс фонда, лучше всего совпадающего с названием (и, по запросу, степень совпадения 0…1)."""
    if not title or not candidates:
        return (0, 0.0) if with_score else 0
    stop = {"фонд", "фонда", "паевой", "инвестиционный", "открытый", "биржевой", "интервальный",
            "рыночных", "финансовых", "инструментов", "и", "с", "—", "-", "пиф", "бпиф", "опиф", "ипиф"}

    def toks(s):
        return {t for t in re.findall(r"[а-яa-z0-9]+", s.lower().replace("ё", "е")) if t not in stop}
    t0 = toks(title)
    scores = [len(t0 & toks(c)) / (len(t0 | toks(c)) or 1) for c in candidates]
    best = max(range(len(candidates)), key=lambda i: scores[i])
    return (best, scores[best]) if with_score else best


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
    "источник данных": C_SOURCE, "ссылка на источник": C_DOC,
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
    new = normalize_positions(new)
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

# Совместимость с разными версиями Streamlit: в новых версиях параметр
# use_container_width устарел и заменён на width="stretch".
try:
    _ST_VER = tuple(int(x) for x in re.findall(r"\d+", st.__version__)[:2])
except Exception:  # noqa: BLE001
    _ST_VER = (1, 99)
WIDE = {"width": "stretch"} if _ST_VER >= (1, 50) else {"use_container_width": True}
CHART_W = {} if _ST_VER >= (1, 50) else {"use_container_width": True}
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
/* Компактная шапка и минимальные отступы */
[data-testid="stHeader"] {height: 2.2rem; min-height: 2.2rem; background: transparent;}
.block-container, [data-testid="stMainBlockContainer"] {
    padding-top: 2.4rem !important; padding-bottom: 1rem !important;}
[data-testid="stSidebarUserContent"] {padding-top: 0.5rem;}
[data-testid="stSidebar"] h2 {font-size: 1.05rem; padding: 0.2rem 0;}
[data-testid="stVerticalBlock"] {gap: 0.55rem;}
.app-title {font-size: 1.45rem; font-weight: 700; line-height: 1.2; margin: 0;}
.app-sub {font-size: 0.8rem; opacity: 0.7; margin: 0.1rem 0 0.2rem 0;}
.sec-title {font-size: 1rem; font-weight: 600; margin: 0.3rem 0 0 0;}
/* Карточки показателей */
[data-testid="stMetric"] {
    background: rgba(46, 91, 255, 0.05); border: 1px solid rgba(46, 91, 255, 0.15);
    border-radius: 8px; padding: 6px 10px;}
[data-testid="stMetricValue"] {font-size: 1.12rem; line-height: 1.3;}
[data-testid="stMetricValue"] > div {white-space: nowrap; overflow: hidden; text-overflow: ellipsis;}
[data-testid="stMetricLabel"] p {font-size: 0.78rem;}
[data-testid="stTabs"] [data-baseweb="tab"] {padding-top: 0.2rem; padding-bottom: 0.2rem;}
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


# =============================================================================
# 8. ИНТЕРФЕЙС
# =============================================================================

SRC_USER_REPORT = "Справка о СЧА (загружена вручную)"
SOURCE_NOTES = {
    SRC_UK_REPORT: "Полный состав из «Справки о стоимости чистых активов» (форма 0420502) с сайта управляющей компании.",
    SRC_USER_REPORT: "Полный состав из загруженной вами справки о стоимости чистых активов.",
    SRC_UK_PAGE: "Структура портфеля со страницы фонда на сайте управляющей компании.",
    SRC_SITE: "ТОП-10 позиций с Investfunds.ru, остальное — «Прочие активы».",
    SRC_BUILTIN: "Ориентировочный состав из встроенной базы. Загрузите актуальный состав кнопкой ниже.",
    SRC_USER: "Данные из загруженного файла.",
}


def fund_universe(db: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    """Все фонды приложения: с загруженным составом (из базы) и из каталога Investfunds.ru."""
    loaded = (db.groupby(C_FUND, as_index=False)
                .agg(**{C_UK: (C_UK, "first"), C_TYPE: (C_TYPE, "first"), C_NAV: (C_NAV, "first"),
                        C_NAV_DATE: (C_NAV_DATE, "first"), C_URL: (C_URL, "first"),
                        C_SOURCE: (C_SOURCE, "first")}))
    loaded["loaded"] = True
    if catalog is None or catalog.empty:
        return loaded
    cat = catalog[~catalog[C_FUND].isin(loaded[C_FUND]) & ~catalog[C_URL].isin(loaded[C_URL])].copy()
    cat[C_SOURCE] = "—"
    cat["loaded"] = False
    cols = [C_FUND, C_UK, C_TYPE, C_NAV, C_NAV_DATE, C_URL, C_SOURCE, "loaded"]
    return pd.concat([loaded[cols], cat[cols]], ignore_index=True)


def _thread_ctx_initializer():
    """Передаёт контекст Streamlit в рабочие потоки (чтобы кэш работал без предупреждений)."""
    try:
        from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
        ctx = get_script_run_ctx()
    except Exception:  # noqa: BLE001
        return None
    if ctx is None:
        return None

    def init():
        try:
            add_script_run_ctx(threading.current_thread(), ctx)
        except Exception:  # noqa: BLE001
            pass
    return init


class _NoComposition(Exception):
    """Состав не получен — исключение, чтобы неудача не попала в кэш."""
    def __init__(self, log):
        super().__init__("состав не получен")
        self.log = log


@st.cache_data(ttl=86400, show_spinner=False)
def composition_cached(fund: str, inv_url: str | None, uk: str | None, ftype: str | None,
                       nav, nav_date) -> tuple[list[dict], str, list[str]]:
    """Состав фонда с кэшем на сутки (общим для всех посетителей). Неудачи не кэшируются."""
    meta = {C_URL: inv_url, C_UK: uk, C_TYPE: ftype, C_NAV: nav, C_NAV_DATE: nav_date}
    rows, src, log = load_fund_composition(fund, meta)
    if not rows:
        raise _NoComposition(log)
    return rows, src, log


def _resolve_inv_url(fund: str, meta: dict) -> str | None:
    for u in (meta.get(C_URL), FUNDS_URLS.get(fund)):
        if is_fund_page(u):
            return u
    return _catalog_url_for(fund)


def run_load(targets: list[tuple[str, dict]], force: bool, deep: bool = False) -> tuple[pd.DataFrame, dict]:
    """
    Загружает составы фондов параллельно (LOAD_WORKERS потоков) с индикатором прогресса.
    force — пересчитать составы; deep — заново скачать страницы и файлы (сбросить весь кэш).
    """
    if force or deep:
        composition_cached.clear()
    if deep:
        fetch_text.clear()
        fetch_bytes.clear()
    jobs = []
    for fund, meta in targets:
        nav = meta.get(C_NAV)
        jobs.append((fund, _resolve_inv_url(fund, meta), meta.get(C_UK), meta.get(C_TYPE),
                     None if nav is None or pd.isna(nav) else float(nav), meta.get(C_NAV_DATE)))
    n = len(jobs)
    results: dict[str, tuple] = {}
    bar = st.progress(0.0, text=f"Загрузка составов: 0 из {n}")

    def work(job):
        try:
            return job[0], composition_cached(*job)
        except _NoComposition as exc:
            return job[0], ([], "", exc.log)
        except Exception as exc:  # noqa: BLE001
            return job[0], ([], "", [f"непредвиденная ошибка: {exc}"])

    with ThreadPoolExecutor(max_workers=LOAD_WORKERS, initializer=_thread_ctx_initializer()) as ex:
        futures = [ex.submit(work, j) for j in jobs]
        for done, fut in enumerate(as_completed(futures), start=1):
            fund, res = fut.result()
            results[fund] = res
            bar.progress(done / n, text=f"Загрузка составов: {done} из {n} · {fund[:40]}")
    bar.empty()

    parts, log = [], {}
    for fund, *_ in jobs:  # исходный порядок
        rows, src, notes = results.get(fund, ([], "", ["нет результата"]))
        if rows:
            parts.append(pd.DataFrame(rows, columns=ALL_COLS))
        log[fund] = (src, list(notes))
    new = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=ALL_COLS)
    return new, log


def load_catalog_parallel(pages: int) -> tuple[pd.DataFrame, list[str]]:
    """Параллельно загружает первые `pages` страниц каталога Investfunds.ru."""
    bar = st.progress(0.0, text=f"Каталог Investfunds.ru: 0 из {pages} страниц")
    by_page: dict[int, list] = {}
    errors: list[str] = []

    def work(p):
        try:
            return p, fetch_catalog_page(p), None
        except Exception as exc:  # noqa: BLE001
            return p, [], f"страница {p}: {exc}"

    with ThreadPoolExecutor(max_workers=LOAD_WORKERS, initializer=_thread_ctx_initializer()) as ex:
        futures = [ex.submit(work, p) for p in range(1, pages + 1)]
        for done, fut in enumerate(as_completed(futures), start=1):
            p, rows, err = fut.result()
            by_page[p] = rows
            if err:
                errors.append(err)
            bar.progress(done / pages, text=f"Каталог Investfunds.ru: {done} из {pages} страниц")
    bar.empty()
    rows = [r for p in sorted(by_page) for r in by_page[p]]
    if not rows and not errors:
        errors.append("не удалось распознать таблицу каталога (возможно, изменилась вёрстка сайта)")
    df = pd.DataFrame([r for r in rows if is_equity_fund(r)])
    if df.empty:
        return pd.DataFrame(columns=CATALOG_COLS), sorted(errors)
    return df[CATALOG_COLS].drop_duplicates(C_URL).reset_index(drop=True), sorted(errors)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_uk_list() -> list[tuple[str, str]]:
    """Все управляющие компании с Investfunds.ru: [(название, адрес страницы)]."""
    soup = BeautifulSoup(_fetch_html(f"{BASE_URL}/uk/"), "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        m = re.match(r"^(?:https?://(?:www\.)?investfunds\.ru)?/uk/([^/?#]+)/?$", a["href"])
        name = a.get_text(" ", strip=True)
        if m and name and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append((name, f"{BASE_URL}/uk/{m.group(1)}/"))
    return out


def parse_uk_page(html: str) -> list[dict]:
    """Фонды на странице УК: [{name, url, kind}], kind — открытый / биржевой / интервальный / закрытый."""
    from bs4 import NavigableString, Tag
    soup = BeautifulSoup(html, "html.parser")
    kinds = {"открытый", "биржевой", "интервальный", "закрытый"}
    fund_href = re.compile(r"^(?:https?://(?:www\.)?investfunds\.ru)?/funds/(\d+)/?$")
    current, out, seen = None, [], set()
    for el in soup.descendants:
        if isinstance(el, NavigableString):
            t = el.strip().lower()
            if t in kinds:
                current = t
        elif isinstance(el, Tag) and el.name == "a" and el.get("href") and fund_href.match(el["href"]):
            fid = fund_href.match(el["href"]).group(1)
            if fid not in seen:
                seen.add(fid)
                out.append(dict(name=el.get_text(" ", strip=True), url=f"{BASE_URL}/funds/{fid}/", kind=current))
    return out


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_uk_funds(uk_url: str) -> list[dict]:
    return parse_uk_page(_fetch_html(uk_url))


@st.cache_data(ttl=86400, show_spinner=False)
def fund_page_info(url: str) -> dict:
    """Разобранная страница фонда (без ТОП-активов в кэше не нуждается — но кэшируем целиком)."""
    return parse_fund_html(fetch_text(url))


# Названия, по которым фонд заведомо не является фондом акций
_NON_EQUITY_NAME_RE = re.compile(
    r"накопительн|облигац|денежн\w*\s+рын|ликвидност|сбережен|флоатер|рантье|консерватив|"
    r"сберегательн|депозит|овернайт|\bофз\b|\bзолот|драгоцен|недвижим|валютн|юан|доллар|евро", re.I)


def _equity_share_of_items(items: list[tuple[str, float]]) -> float:
    """Доля акций среди перечисленных ТОП-активов (в % от суммы их весов)."""
    total = sum(w for _, w in items)
    if not total:
        return 0.0
    eq = sum(w for n, w in items if classify_asset(n)[3] == ASSET_EQUITY)
    return eq / total * 100


def _is_equity_info(info: dict) -> bool:
    obj = (info.get("object") or "").lower()
    kind = (info.get("kind") or "").lower()
    status = (info.get("status") or "").lower()
    title = info.get("title") or ""
    if not (("акци" in obj or "смешан" in obj) and "закрыт" not in kind and info.get("type") != "ЗПИФ"
            and (not status or "сформирован" in status) and not info.get("qualified")):
        return False
    name_flag = bool(_NON_EQUITY_NAME_RE.search(title)) and "акци" not in title.lower()
    if not (name_flag or "смешан" in obj):
        return True
    # «Смешанный» фонд или название вроде «Накопительный» / «Облигации»: решает доля акций
    # среди крупнейших позиций на Investfunds.ru
    if info.get("items"):
        return _equity_share_of_items(info["items"]) >= MIN_EQUITY_SHARE
    return not name_flag  # данных о составе нет: смешанный оставляем до проверки состава


def _parallel(func, items, label: str, bar) -> list:
    """Выполняет func(item) в LOAD_WORKERS потоках, обновляя индикатор прогресса."""
    results, n = [], len(items)
    if not n:
        return results
    with ThreadPoolExecutor(max_workers=LOAD_WORKERS, initializer=_thread_ctx_initializer()) as ex:
        futures = {ex.submit(func, it): it for it in items}
        for done, fut in enumerate(as_completed(futures), start=1):
            try:
                results.append((futures[fut], fut.result(), None))
            except Exception as exc:  # noqa: BLE001
                results.append((futures[fut], None, str(exc)))
            bar.progress(done / n, text=f"{label}: {done} из {n}")
    return results


def discover_equity_funds(min_nav_mln: float = MIN_NAV_MLN) -> tuple[pd.DataFrame, list[str]]:
    """
    Список фондов акций и смешанных фондов с СЧА ≥ min_nav_mln:
      1) все УК с Investfunds.ru → фонды на страницах УК;
      2) у каждой УК — таблица «Другие фонды этой УК» (СЧА всех её фондов) на странице одного фонда;
      3) фонды с СЧА от порога проверяются по своей странице (объект инвестирования, тип, статус).
    """
    errors: list[str] = []
    bar = st.progress(0.0, text="Список управляющих компаний…")
    try:
        uks = fetch_uk_list()
    except Exception as exc:  # noqa: BLE001
        uks = []
        errors.append(f"список УК: {exc}")

    # 1) фонды на страницах УК → по одному «опорному» фонду на УК
    seeds = set(u for u in FUNDS_URLS.values() if is_fund_page(u))
    for _, funds, err in _parallel(fetch_uk_funds, [u for _, u in uks], "Страницы управляющих компаний", bar):
        if err or not funds:
            continue
        open_funds = [f for f in funds if f["kind"] != "закрытый"]
        if open_funds:
            seeds.add(open_funds[0]["url"])
    if not uks:
        errors.append("список УК недоступен — поиск ведётся только по фондам из реестра")

    # 2) СЧА всех фондов каждой УК из таблицы «Другие фонды этой УК»
    navs: dict[str, tuple[str, float]] = {}
    for url, info, err in _parallel(fund_page_info, sorted(seeds), "СЧА фондов управляющих компаний", bar):
        if err or not info:
            continue
        for f in info["other_funds"]:
            if f["nav_rub"]:
                navs.setdefault(f["url"], (f["name"], f["nav_rub"]))
        if info.get("nav_mln"):
            navs.setdefault(url, (info.get("title") or url, info["nav_mln"] * 1e6))
    candidates = [u for u, (_, nav) in navs.items() if nav >= min_nav_mln * 1e6]

    # 3) проверка каждого кандидата по его странице
    rows = []
    for url, info, err in _parallel(fund_page_info, candidates, "Проверка фондов (СЧА от 1 млрд руб.)", bar):
        if err or not info or not _is_equity_info(info):
            continue
        rows.append({C_FUND: info.get("title") or navs[url][0], C_UK: normalize_uk(info.get("uk")),
                     C_TYPE: info.get("type") or "ОПИФ", C_NAV: info.get("nav_mln") or navs[url][1] / 1e6,
                     C_NAV_DATE: info.get("nav_date"), C_URL: url, C_OBJECT: info.get("object")})
    bar.empty()
    df = pd.DataFrame(rows, columns=CATALOG_COLS)
    return df.drop_duplicates(C_URL).sort_values(C_NAV, ascending=False).reset_index(drop=True), errors


def merge_catalog(cat: pd.DataFrame) -> None:
    if cat.empty:
        return
    cat = cat[~cat[C_URL].isin(set(FUNDS_URLS.values()))]
    old = st.session_state.get("catalog")
    st.session_state.catalog = (pd.concat([old, cat]).drop_duplicates(C_URL, keep="last")
                                if old is not None and not old.empty else cat)


def autoload() -> None:
    """При первом открытии: все фонды акций и смешанные фонды с СЧА от MIN_NAV_MLN и их составы."""
    st.session_state.autoloaded = True
    box = st.empty()
    with box.container():
        st.info("Загружаем список фондов с Investfunds.ru и их составы с сайтов управляющих компаний. "
                "При первом открытии за сутки это может занять несколько минут, дальше — секунды.")
        cat, cat_errors = discover_equity_funds()
        merge_catalog(cat)
        meta_all = fund_universe(st.session_state.db, st.session_state.catalog)
        pool = meta_all.sort_values(C_NAV, ascending=False, na_position="last").head(AUTO_MAX_FUNDS)
        targets = [(r[C_FUND], r.to_dict()) for _, r in pool.iterrows()]
        new, log = run_load(targets, force=False)
    box.empty()
    if cat_errors:
        log = {"Список фондов Investfunds.ru": ("" if cat.empty else f"найдено фондов: {len(cat)}", cat_errors), **log}
    apply_loaded(new, log)


def _store_log(log: dict) -> None:
    st.session_state.update_log = dict(time=datetime.now().strftime("%d.%m.%Y %H:%M"), funds=log)


def apply_loaded(new: pd.DataFrame, log: dict) -> None:
    if new is not None and not new.empty:
        new = normalize_positions(new)
        eq_share = new[new[C_ASSET] == ASSET_EQUITY].groupby(C_FUND)[C_WEIGHT].sum()
        own = set(FUNDS_URLS)  # фонды из реестра не исключаем
        full = set(new.loc[new[C_SOURCE].isin([SRC_UK_REPORT, SRC_USER_REPORT]), C_FUND])
        # без акций — исключаем всегда; по полному составу (справка о СЧА) — если акций меньше порога.
        # По ТОП-10 долю портфеля не оценить: остаток может состоять из тех же акций.
        drop = [f for f in new[C_FUND].unique() if f not in own and
                (eq_share.get(f, 0.0) <= 0 or (f in full and eq_share.get(f, 0.0) < MIN_EQUITY_SHARE))]
        if drop:
            new = new[~new[C_FUND].isin(drop)]
            cat = st.session_state.get("catalog")
            if cat is not None and not cat.empty:
                st.session_state.catalog = cat[~cat[C_FUND].isin(drop)]
            for f in drop:
                src, notes = log.get(f, ("", []))
                log[f] = ("", list(notes) + [f"исключён из выборки: доля акций {eq_share.get(f, 0.0):.1f} % "
                                             f"(меньше {MIN_EQUITY_SHARE:.0f} %)"])
    st.session_state.db = replace_funds(st.session_state.db, new)
    _store_log(log)


def sidebar(meta_all: pd.DataFrame) -> tuple[list[str], list[str]]:
    sb = st.sidebar
    sb.header("⚙️ Фильтры")
    uk_all = sorted(meta_all[C_UK].dropna().unique())
    types_all = sorted(meta_all[C_TYPE].dropna().unique(),
                       key=lambda t: {"БПИФ": 0, "ОПИФ": 1, "ИПИФ": 2}.get(t, 9))
    sel_uk = sb.multiselect("Управляющие компании", uk_all, default=uk_all,
                            placeholder="Выберите управляющие компании")
    sel_types = sb.multiselect("Тип фонда", types_all, default=types_all,
                               placeholder="Выберите тип фонда")

    # ---------------- Список фондов ----------------
    sb.divider()
    sb.header("📋 Список фондов")
    n_all, n_loaded = len(meta_all), int(meta_all["loaded"].sum())
    sb.caption(f"Фондов в списке: {n_all}, состав загружен: {n_loaded}.")
    with sb.expander("Обновить список фондов с Investfunds.ru"):
        st.caption(f"При открытии дашборда автоматически находятся все фонды акций и смешанные фонды "
                   f"с СЧА от {MIN_NAV_MLN:,.0f} млн руб.".replace(",", " ") +
                   " по всем управляющим компаниям на Investfunds.ru (открытые, биржевые и интервальные, "
                   "сформированные, не для квалифицированных инвесторов). Здесь можно изменить порог.")
        min_nav = st.number_input("Минимальная СЧА фонда, млн руб.", 0, 100000, int(MIN_NAV_MLN), step=500)
        if st.button("Обновить список фондов", type="primary", **WIDE):
            for f in (fetch_uk_list, fetch_uk_funds, fund_page_info):
                f.clear()
            cat, errors = discover_equity_funds(float(min_nav))
            merge_catalog(cat)
            st.session_state.catalog_msg = (
                ("success", f"Найдено фондов акций и смешанных инвестиций: {len(cat)}.")
                if not cat.empty else
                ("error", "Не удалось загрузить каталог: " + ("; ".join(errors) or "фонды не найдены")))
            st.rerun()
    msg = st.session_state.pop("catalog_msg", None)
    if msg:
        (sb.success if msg[0] == "success" else sb.error)(msg[1])

    # ---------------- Составы ----------------
    sb.divider()
    sb.header("🔄 Составы фондов")
    sb.caption("Порядок источников: справка о СЧА с сайта УК (полный состав) → структура на странице "
               "фонда на сайте УК → ТОП-10 с Investfunds.ru. Если всё недоступно, данные фонда не меняются.")
    max_n = sb.number_input("Сколько фондов загрузить за раз", 1, 300, 25, step=5,
                            help="Берутся фонды из текущих фильтров, начиная с самых крупных по СЧА.")
    only_new = sb.checkbox("Только фонды без актуального состава", value=False,
                           help="Пропустить фонды, состав которых уже загружен с сайтов УК.")
    force = sb.checkbox("Не использовать кэш (загрузить заново)", value=False,
                        help="Загруженные страницы и файлы кэшируются на 24 часа.")
    if sb.button("Обновить составы", type="primary", **WIDE):
        pool = meta_all[meta_all[C_UK].isin(sel_uk) & meta_all[C_TYPE].isin(sel_types)]
        if only_new:
            pool = pool[~pool[C_SOURCE].isin([SRC_UK_REPORT, SRC_UK_PAGE, SRC_USER_REPORT])]
        pool = pool.sort_values(C_NAV, ascending=False, na_position="last").head(int(max_n))
        targets = [(r[C_FUND], r.to_dict()) for _, r in pool.iterrows()]
        if targets:
            with sb:
                new, log = run_load(targets, force=True, deep=force)
            apply_loaded(new, log)
        else:
            st.session_state.catalog_msg = ("success", "Все фонды в выборке уже загружены с сайтов УК.")
        st.rerun()

    log = st.session_state.get("update_log")
    if log and log.get("funds"):
        funds_log = log["funds"]
        ok = {f: v for f, v in funds_log.items() if v[0]}
        full = sum(1 for v in ok.values() if v[0].startswith((SRC_UK_REPORT, SRC_UK_PAGE)))
        (sb.success if ok else sb.warning)(
            f"{log['time']}: загружено {len(ok)} из {len(funds_log)} фондов, из них с сайтов УК — {full}.")
        with sb.expander("Подробности загрузки"):
            for fund, (src, notes) in funds_log.items():
                if src:
                    st.markdown(f"✅ **{fund}** — {src}")
                else:
                    st.markdown(f"⚠️ **{fund}** — состав не получен, оставлены прежние данные")
                for n in notes:
                    st.caption(f"• {n}")

    # ---------------- Файлы ----------------
    sb.divider()
    sb.header("📁 Файлы")
    with sb.expander("📄 Загрузить справку о СЧА (PDF)"):
        st.caption("Ежемесячная «Справка о стоимости чистых активов» (форма 0420502) с сайта УК "
                   "содержит полный список акций фонда.")
        pdf = st.file_uploader("Файл справки", type=["pdf"], key="pdf_up")
        pdf_url = st.text_input("…или ссылка на PDF", placeholder="https://…/scha-….pdf")
        if st.button("Разобрать справку", **WIDE, disabled=not (pdf or pdf_url.strip())):
            try:
                data = pdf.getvalue() if pdf else fetch_bytes(pdf_url.strip())
                rep = parse_scha_pdf(data)
                if not rep["nav"] or len(rep["items"]) < 1:
                    raise ValueError("в файле не найдены СЧА или таблица «Акции российских акционерных обществ»")
                st.session_state.pending_report = dict(rep=rep, doc=pdf.name if pdf else pdf_url.strip(),
                                                       manual=bool(pdf))
            except Exception as exc:  # noqa: BLE001
                st.session_state.pending_report = None
                st.error(f"Не удалось разобрать справку: {exc}")
        pend = st.session_state.get("pending_report")
        if pend:
            rep = pend["rep"]
            st.info(f"Справка на {rep['date'] or '—'}: СЧА {fmt_mln(rep['nav'] / 1e6)}, акций — "
                    f"{len(rep['items'])}." + (f" Фонд: «{rep['title']}»." if rep["title"] else ""))
            names = meta_all.sort_values(C_FUND)[C_FUND].tolist()
            target = st.selectbox("К какому фонду относится справка", names,
                                  index=match_fund_by_title(rep["title"], names))
            if st.button("Применить справку к фонду", type="primary", **WIDE):
                meta_row = meta_all[meta_all[C_FUND] == target].iloc[0].to_dict()
                rows = rows_from_report(target, meta_row, rep, pend["doc"])
                if pend["manual"]:
                    for r in rows:
                        r[C_SOURCE] = SRC_USER_REPORT
                apply_loaded(pd.DataFrame(rows, columns=ALL_COLS),
                             {target: (f"{SRC_USER_REPORT if pend['manual'] else SRC_UK_REPORT} "
                                       f"на {rep['date'] or '—'}", [])})
                st.session_state.pending_report = None
                st.rerun()

    up = sb.file_uploader("Загрузить свой CSV/Excel отчет", type=["csv", "xlsx", "xls"],
                          help="Обязательные колонки: «Фонд», «Компания» или «Тикер», "
                               "«Доля в фонде, %». Остальные колонки — как в выгрузке базы.")
    if up is not None:
        mode = sb.radio("Как применить файл", ["Добавить к базе (заменить совпадающие фонды)",
                                               "Полностью заменить базу"])
        if sb.button("Применить файл", **WIDE):
            try:
                user_df = read_user_file(up)
                if user_df.empty:
                    raise ValueError("в файле нет строк с заполненными «Фонд» и «Доля»")
                if mode.startswith("Полностью"):
                    st.session_state.db = normalize_positions(user_df)
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
                       file_name=f"pif_akcii_{datetime.now():%Y%m%d}.csv", mime="text/csv", **WIDE)
    sb.download_button("Скачать текущую базу (Excel)", data=db_to_excel(db_now),
                       file_name=f"pif_akcii_{datetime.now():%Y%m%d}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", **WIDE)
    if sb.button("Сбросить составы к встроенной базе", **WIDE):
        st.session_state.db = build_default_db().copy()
        st.session_state.pop("update_log", None)
        st.rerun()
    return sel_uk, sel_types


TOTAL_FUND = "📊 ИТОГО — все выбранные фонды"
TOTAL_STOCK = "__TOTAL__"
C_HOLDERS = "Фондов-держателей"


def _date_range(values) -> str:
    """Список дат «дд.мм.гггг» → «30.09.2026» или «30.06.2026 – 30.09.2026»."""
    d = pd.to_datetime(pd.Series(list(values)), format="%d.%m.%Y", errors="coerce").dropna()
    if d.empty:
        return "—"
    lo, hi = d.min().strftime("%d.%m.%Y"), d.max().strftime("%d.%m.%Y")
    return lo if lo == hi else f"{lo} – {hi}"


def aggregate_total(fdb: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Сводный портфель (ИТОГО) всех фондов в выборке: позиции суммируются по стоимости,
    доля = стоимость позиции / суммарная СЧА × 100.
    """
    navs = fdb.groupby(C_FUND)[C_NAV].first()
    nav_total = float(navs.sum(skipna=True))
    df = fdb.copy()
    df["_key"] = df[C_TICKER].where(df[C_TICKER] != NO_TICKER, df[C_COMPANY])
    agg = (df.groupby("_key", as_index=False)
             .agg(**{C_TICKER: (C_TICKER, "first"), C_COMPANY: (C_COMPANY, "first"),
                     C_SECTOR: (C_SECTOR, "first"), C_ASSET: (C_ASSET, "first"),
                     C_VALUE: (C_VALUE, "sum"), C_HOLDERS: (C_FUND, "nunique")})
             .drop(columns="_key"))
    agg[C_WEIGHT] = agg[C_VALUE] / nav_total * 100 if nav_total > 0 else float("nan")
    agg = agg.sort_values(C_VALUE, ascending=False).reset_index(drop=True)
    sources = fdb.groupby(C_FUND)[C_SOURCE].first().value_counts()
    summary = dict(
        nav=nav_total, n_funds=int(navs.size), n_uk=int(fdb[C_UK].nunique()),
        n_types=", ".join(sorted(fdb[C_TYPE].dropna().unique())),
        struct_dates=_date_range(fdb.groupby(C_FUND)[C_DATE].first()),
        nav_dates=_date_range(fdb.groupby(C_FUND)[C_NAV_DATE].first()),
        sources="; ".join(f"{k} — {v}" for k, v in sources.items()),
    )
    return agg, summary


def aggregate_stocks(fdb: pd.DataFrame) -> pd.DataFrame:
    """Сводка по всем акциям в выборке фондов."""
    eq = fdb[fdb[C_ASSET] == ASSET_EQUITY].copy()
    eq["_key"] = eq[C_TICKER].where(eq[C_TICKER] != NO_TICKER, eq[C_COMPANY])
    agg = (eq.groupby("_key")
             .agg(ticker=(C_TICKER, "first"), company=(C_COMPANY, "first"),
                  sector=(C_SECTOR, "first"), total=(C_VALUE, "sum"),
                  funds=(C_FUND, "nunique"), avg_w=(C_WEIGHT, "mean"))
             .sort_values("total", ascending=False))
    all_eq = agg["total"].sum()
    agg["share"] = agg["total"] / all_eq * 100 if all_eq > 0 else float("nan")
    return agg


def _fund_label(meta: pd.DataFrame):
    loaded = dict(zip(meta[C_FUND], meta["loaded"]))
    return lambda f: f if f == TOTAL_FUND or loaded.get(f, True) else f"{f} · состав не загружен"


def _not_loaded_view(fund: str, meta_row: dict) -> None:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Управляющая компания", meta_row.get(C_UK) or "—")
    c2.metric("Тип фонда", meta_row.get(C_TYPE) or "—")
    c3.metric("СЧА", fmt_mln(meta_row.get(C_NAV), 0))
    c4.metric("Дата СЧА", meta_row.get(C_NAV_DATE) or "—")
    st.info("Состав этого фонда ещё не загружен. Будут опробованы справка о СЧА и страница фонда на "
            "сайте управляющей компании, затем ТОП-10 с Investfunds.ru.")
    b1, b2 = st.columns([1, 2])
    with b1:
        if st.button("📥 Загрузить состав этого фонда", type="primary"):
            new, log = run_load([(fund, meta_row)], force=False)
            apply_loaded(new, log)
            st.rerun()
    with b2:
        if is_fund_page(meta_row.get(C_URL)):
            st.link_button("🔗 Страница фонда на Investfunds.ru", meta_row[C_URL])


def tab_funds(fdb: pd.DataFrame, meta: pd.DataFrame) -> None:
    order = meta.sort_values(["loaded", C_NAV], ascending=[False, False], na_position="last")
    options = [TOTAL_FUND] + order[C_FUND].tolist()
    sc1, sc2 = st.columns([5, 1])
    top_n = sc2.number_input("Позиций на диаграмме", 5, 25, 10, step=1)
    fund = sc1.selectbox("Фонд", options, index=1 if len(options) > 1 else 0,
                        format_func=_fund_label(meta),
                        placeholder="Начните вводить название фонда",
                        help="Первый пункт «ИТОГО» — сводный портфель всех фондов с загруженным составом. "
                             "Остальные фонды отсортированы по СЧА; фонды из каталога без состава — в конце.")
    is_total = fund == TOTAL_FUND
    if is_total and fdb.empty:
        st.info("Ни у одного фонда в выборке ещё нет загруженного состава.")
        return
    if not is_total and fund not in set(fdb[C_FUND]):
        _not_loaded_view(fund, meta[meta[C_FUND] == fund].iloc[0].to_dict())
        return
    pf_word = "сводном портфеле" if is_total else "фонде"

    if is_total:
        fd, sm = aggregate_total(fdb)
        nav = sm["nav"]
    else:
        fd = fdb[fdb[C_FUND] == fund].sort_values(C_WEIGHT, ascending=False).reset_index(drop=True)
        head = fd.iloc[0]
        nav = head[C_NAV]
    equities = fd[fd[C_ASSET] == ASSET_EQUITY].sort_values(C_WEIGHT, ascending=False)
    eq_share = equities[C_WEIGHT].sum()
    m = st.columns([1.5, 1.25, 1.05, 0.75, 0.85, 0.85, 1.5])
    if is_total:
        m[0].metric("Фондов / управляющих компаний", f"{sm['n_funds']} / {sm['n_uk']}",
                    help=f"Типы фондов: {sm['n_types']}")
        m[1].metric("Суммарная СЧА", fmt_mln(nav, 0), help=f"Даты СЧА: {sm['nav_dates']}")
        m[2].metric("Состав на", sm["struct_dates"], help=f"Даты структуры портфелей. СЧА — {sm['nav_dates']}.")
    else:
        m[0].metric(f"Управляющая компания · {head[C_TYPE]}", head[C_UK])
        m[1].metric(f"СЧА на {head[C_NAV_DATE]}", fmt_mln(nav, 0), help="Стоимость чистых активов фонда")
        m[2].metric("Состав на", head[C_DATE] if head[C_DATE] not in (None, "") else "—",
                    help="Дата, на которую раскрыта структура портфеля в источнике данных")
    m[3].metric("Акций", f"{len(equities)}")
    m[4].metric("Доля ТОП-10", fmt_pct(equities.head(10)[C_WEIGHT].sum(), 1),
                help="Суммарная доля десяти крупнейших акций")
    m[5].metric("Доля акций", fmt_pct(min(eq_share, 100.0), 1))
    if not equities.empty:
        big = equities.iloc[0]
        m[6].metric(f"Крупнейшая · {fmt_pct(big[C_WEIGHT], 1)}", big[C_COMPANY],
                    help=f"{big[C_TICKER]} — {fmt_mln(big[C_VALUE])}")
    if is_total and int((~meta["loaded"]).sum()):
        st.caption(f"В «ИТОГО» учтены только фонды с загруженным составом: {sm['n_funds']} из {len(meta)}.")

    if is_total:
        st.caption(f"Сводный портфель: позиции всех фондов сложены по оценочной стоимости, "
                   f"доля считается от суммарной СЧА. Источники данных (число фондов): {sm['sources']}.")
    else:
        l1, l2, l3, rc = st.columns([1.3, 1.3, 1.1, 2.3])
        with l1:
            if is_fund_page(head[C_URL]):
                st.link_button("🔗 Investfunds.ru", head[C_URL])
            else:
                st.link_button("🔗 Investfunds.ru", head[C_URL] or BASE_URL,
                               help="Точный адрес страницы этого фонда не указан в реестре.")
        with l2:
            doc = head.get(C_DOC)
            if isinstance(doc, str) and doc.startswith("http"):
                st.link_button("📄 Документ-источник", doc)
        with l3:
            if st.button("🔄 Обновить состав", help="Загрузить состав этого фонда заново"):
                meta_row = meta[meta[C_FUND] == fund].iloc[0].to_dict()
                new, log = run_load([(fund, meta_row)], force=True)
                apply_loaded(new, log)
                st.rerun()
        with rc:
            src = head[C_SOURCE]
            st.caption(f"Источник: **{src}**. {SOURCE_NOTES.get(src, '')}")

    g1, g2 = st.columns([3, 2])
    with g1:
        top = equities[equities[C_WEIGHT] > 0].head(int(top_n))
        rest_eq = equities.iloc[len(top):]
        other = fd[fd[C_ASSET] != ASSET_EQUITY]
        labels = top[C_COMPANY].tolist()
        values = top[C_WEIGHT].tolist()
        money = top[C_VALUE].tolist()
        if rest_eq[C_WEIGHT].sum() > 0.01:
            labels.append(f"Остальные акции ({len(rest_eq)})")
            values.append(rest_eq[C_WEIGHT].sum())
            money.append(rest_eq[C_VALUE].sum())
        for label, part in ((CASH_NAME, other[other[C_ASSET] == ASSET_CASH]),
                            (OTHER_NAME, other[other[C_ASSET] != ASSET_CASH])):
            if part[C_WEIGHT].sum() > 0.01:
                labels.append(label)
                values.append(part[C_WEIGHT].sum())
                money.append(part[C_VALUE].sum())
        fig = go.Figure(go.Pie(
            labels=labels, values=values, customdata=money, hole=0.55, sort=False,
            direction="clockwise", marker=dict(colors=PALETTE, line=dict(color="white", width=1)),
            texttemplate="%{value:.1f} %", textposition="inside",
            hovertemplate=f"<b>%{{label}}</b><br>Доля в {pf_word}: %{{value:.2f}} %"
                          "<br>Стоимость позиции: %{customdata:,.1f} млн руб.<extra></extra>",
        ))
        nav_label = "Суммарная СЧА" if is_total else "СЧА"
        fig.update_layout(
            separators=", ", height=380, margin=dict(t=28, b=0, l=0, r=0),
            title=dict(text=f"ТОП-{int(top_n)} акций " + ("всех фондов" if is_total else "фонда"),
                       x=0, font=dict(size=14)),
            legend=dict(title=dict(text="Эмитенты"), font=dict(size=11)),
            annotations=[dict(text=f"{nav_label}<br><b>{fmt_ru(nav, 0)}</b><br>млн руб.",
                              showarrow=False, font=dict(size=13))],
        )
        st.plotly_chart(fig, config=PLOTLY_CONFIG, **CHART_W)
    with g2:
        non_eq_label = fd[C_ASSET].where(fd[C_ASSET] == ASSET_CASH, OTHER_NAME)
        sec = (fd.assign(**{C_SECTOR: fd[C_SECTOR].where(fd[C_ASSET] == ASSET_EQUITY, non_eq_label)})
                 .groupby(C_SECTOR, as_index=False)[[C_WEIGHT, C_VALUE]].sum().sort_values(C_WEIGHT))
        fig_s = px.bar(sec, x=C_WEIGHT, y=C_SECTOR, orientation="h", text=C_WEIGHT,
                       custom_data=[C_VALUE], color_discrete_sequence=["#2E5BFF"])
        fig_s.update_traces(
            texttemplate="%{x:.1f} %", textposition="outside", cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>Доля: %{x:.2f} %<br>Стоимость: %{customdata[0]:,.1f} млн руб.<extra></extra>")
        fig_s.update_layout(
            separators=", ", height=380, margin=dict(t=28, b=0, l=0, r=40),
            title=dict(text="Распределение по секторам", x=0, font=dict(size=14)),
            xaxis_title=f"Доля в {pf_word}, %", yaxis_title=None, showlegend=False,
        )
        st.plotly_chart(fig_s, config=PLOTLY_CONFIG, **CHART_W)

    t1c, t2c = st.columns([2, 3])
    t1c.markdown('<p class="sec-title">' + ("Все позиции сводного портфеля" if is_total else "Все позиции фонда")
                 + "</p>", unsafe_allow_html=True)
    q = t2c.text_input("Поиск по позициям", placeholder="🔎 Поиск: тикер, компания или сектор…", key="q_fund",
                       label_visibility="collapsed")
    # акции по убыванию доли, затем «Денежные средства» и «Прочие активы»
    order = fd[C_ASSET].map({ASSET_EQUITY: 0, ASSET_CASH: 1}).fillna(2)
    view = fd.assign(_o=order).sort_values(["_o", C_WEIGHT], ascending=[True, False])
    view = search_filter(view, q, [C_TICKER, C_COMPANY, C_SECTOR])  # поиск и по тикеру, и по сектору
    cols = [C_COMPANY, C_VALUE, C_WEIGHT] + ([C_HOLDERS] if is_total else [])
    table = view[cols]
    st.caption(f"Показано позиций: {len(table)} из {len(fd)}. Сортировка — щелчком по заголовку колонки.")
    st.dataframe(
        styled(table, pct_cols=[C_WEIGHT], mln_cols=[C_VALUE]),
        **WIDE, hide_index=True, height=min(38 + 35 * max(len(table), 1), 640),
        column_config={
            C_COMPANY: st.column_config.Column("Компания", width="medium"),
            C_VALUE: st.column_config.Column(
                "Стоимость, млн ₽", width="small",
                help="Суммарная стоимость позиции во всех фондах, млн руб." if is_total
                else "Оценочная стоимость позиции, млн руб. (СЧА × доля / 100)"),
            C_WEIGHT: st.column_config.Column(
                "Доля, %", width="small",
                help="Доля в сводном портфеле, %" if is_total else "Доля в фонде, %"),
            C_HOLDERS: st.column_config.Column("Фондов", width="small", help="Число фондов-держателей"),
        },
    )


def _stocks_total_view(agg: pd.DataFrame, fdb: pd.DataFrame) -> None:
    """ИТОГО по всем акциям: сводка, диаграмма крупнейших бумаг и полная таблица."""
    n_funds_eq = fdb.loc[fdb[C_ASSET] == ASSET_EQUITY, C_FUND].nunique()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Бумаг в портфелях фондов", f"{len(agg)}")
    c2.metric("Фондов с акциями", f"{n_funds_eq}")
    c3.metric("Суммарная стоимость акций", fmt_mln(agg["total"].sum(), 0))
    c4.metric("Держателей на одну бумагу", fmt_ru(agg["funds"].mean(), 1),
              help="Среднее число фондов, владеющих одной бумагой.")

    top_n = st.slider("Сколько бумаг показать на диаграмме", 10, 50, 20, step=5, key="top_stocks")
    top = agg.head(top_n).reset_index(drop=True)
    top["Бумага"] = [f"{t} · {c}" if t != NO_TICKER else c for t, c in zip(top.ticker, top.company)]
    fig_t = px.bar(top.iloc[::-1], x="total", y="Бумага", orientation="h",
                   custom_data=["funds", "avg_w", "share"], color_discrete_sequence=["#00A389"])
    fig_t.update_traces(
        texttemplate="%{x:,.0f}", textposition="outside", cliponaxis=False,
        hovertemplate="<b>%{y}</b><br>Суммарная стоимость: %{x:,.1f} млн руб."
                      "<br>Доля от всех акций в фондах: %{customdata[2]:.2f} %"
                      "<br>Фондов-держателей: %{customdata[0]}"
                      "<br>Средняя доля в портфелях: %{customdata[1]:.2f} %<extra></extra>")
    fig_t.update_layout(separators=", ", height=max(320, 24 * len(top) + 90),
                        margin=dict(t=30, b=0, l=0, r=60),
                        title=dict(text=f"ТОП-{top_n} бумаг по суммарной стоимости пакетов",
                                   x=0, font=dict(size=15)),
                        xaxis_title="Суммарная стоимость пакетов, млн руб.", yaxis_title=None)
    st.plotly_chart(fig_t, config=PLOTLY_CONFIG, **CHART_W)

    st.markdown('<p class="sec-title">Все акции в портфелях фондов</p>', unsafe_allow_html=True)
    table = agg.reset_index(drop=True).rename(columns={
        "ticker": "Тикер", "company": "Компания", "sector": "Сектор", "funds": C_HOLDERS,
        "total": "Суммарная стоимость пакетов, млн руб.",
        "share": "Доля от всех акций в фондах, %", "avg_w": "Средняя доля в портфелях, %",
    })
    q = st.text_input("Поиск по акциям", placeholder="Тикер, компания или сектор…", key="q_all_stocks")
    table = search_filter(table, q, ["Тикер", "Компания", "Сектор"])[
        ["Компания", "Суммарная стоимость пакетов, млн руб.", "Доля от всех акций в фондах, %",
         C_HOLDERS, "Средняя доля в портфелях, %"]]
    st.caption(f"Показано бумаг: {len(table)} из {len(agg)}. Сортировка — щелчком по заголовку колонки.")
    st.dataframe(
        styled(table, pct_cols=["Доля от всех акций в фондах, %", "Средняя доля в портфелях, %"],
               mln_cols=["Суммарная стоимость пакетов, млн руб."]),
        **WIDE, hide_index=True, height=min(38 + 35 * max(len(table), 1), 640),
        column_config={
            "Компания": st.column_config.Column("Компания", width="medium"),
            "Суммарная стоимость пакетов, млн руб.": st.column_config.Column(
                "Стоимость, млн ₽", width="small", help="Суммарная стоимость пакетов во всех фондах, млн руб."),
            "Доля от всех акций в фондах, %": st.column_config.Column(
                "Доля, %", width="small", help="Доля от стоимости всех акций в выбранных фондах"),
            C_HOLDERS: st.column_config.Column("Фондов", width="small", help="Число фондов-держателей"),
            "Средняя доля в портфелях, %": st.column_config.Column(
                "Ср. доля, %", width="small", help="Средняя доля бумаги в портфелях фондов-держателей"),
        },
    )


def tab_stocks(fdb: pd.DataFrame) -> None:
    eq = fdb[fdb[C_ASSET] == ASSET_EQUITY].copy()
    if eq.empty:
        st.info("В отфильтрованных фондах нет загруженных позиций в акциях.")
        return
    eq["_key"] = eq[C_TICKER].where(eq[C_TICKER] != NO_TICKER, eq[C_COMPANY])
    agg = aggregate_stocks(fdb)
    label = {k: (f"{r.ticker} — {r.company}" if r.ticker != NO_TICKER else r.company)
             for k, r in agg.iterrows()}
    label[TOTAL_STOCK] = "📊 ИТОГО — все акции"

    key = st.selectbox("Акция", [TOTAL_STOCK] + agg.index.tolist(), index=1,
                       format_func=lambda k: label[k],
                       placeholder="Введите тикер или название компании",
                       help="Первый пункт «ИТОГО» — сводка по всем бумагам. Остальные отсортированы "
                            "по суммарной стоимости пакетов. Начните вводить тикер (например, SBER) "
                            "или название.")
    if key == TOTAL_STOCK:
        _stocks_total_view(agg, fdb)
        return

    info = agg.loc[key]
    holders = (eq[eq["_key"] == key]
               .groupby(C_FUND, as_index=False)
               .agg(**{C_UK: (C_UK, "first"), C_TYPE: (C_TYPE, "first"),
                       C_WEIGHT: (C_WEIGHT, "sum"), C_VALUE: (C_VALUE, "sum"),
                       C_URL: (C_URL, "first")}))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Тикер: {info.ticker}", info.company, help=f"Сектор: {info.sector}")
    c2.metric("Фондов-держателей", f"{len(holders)}")
    c3.metric("Суммарная стоимость пакетов", fmt_mln(holders[C_VALUE].sum(), 1),
              help=f"{fmt_pct(info.share)} от стоимости всех акций в выбранных фондах")
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
        separators=", ", height=max(300, 32 * len(holders) + 120),
        margin=dict(t=30, b=0, l=0, r=60),
        title=dict(text=f"Кто держит {info.company} ({info.ticker})", x=0, font=dict(size=15)),
        xaxis_title=x_title, yaxis_title=None,
        legend=dict(title=dict(text="Управляющая компания"), orientation="h", y=-0.15),
    )
    st.plotly_chart(fig, config=PLOTLY_CONFIG, **CHART_W)

    st.markdown('<p class="sec-title">Фонды-держатели</p>', unsafe_allow_html=True)
    table = holders.rename(columns={
        C_FUND: "Название фонда", C_WEIGHT: "Доля в портфеле, %",
        C_VALUE: "Оценочная стоимость пакета, млн руб.", C_URL: "Ссылка на фонд",
    })[["Название фонда", "Оценочная стоимость пакета, млн руб.", "Доля в портфеле, %",
        C_UK, C_TYPE, "Ссылка на фонд"]]
    q = st.text_input("Поиск по фондам", placeholder="Название фонда или УК…", key="q_stock")
    table = search_filter(table, q, ["Название фонда", C_UK, C_TYPE])
    st.dataframe(
        styled(table, pct_cols=["Доля в портфеле, %"], mln_cols=["Оценочная стоимость пакета, млн руб."]),
        **WIDE, hide_index=True,
        height=min(38 + 35 * max(len(table), 1), 640),
        column_config={
            "Название фонда": st.column_config.Column("Фонд", width="medium"),
            "Оценочная стоимость пакета, млн руб.": st.column_config.Column(
                "Стоимость, млн ₽", width="small", help="Оценочная стоимость пакета, млн руб."),
            "Доля в портфеле, %": st.column_config.Column("Доля, %", width="small", help="Доля в портфеле фонда, %"),
            C_UK: st.column_config.Column("УК", help="Управляющая компания"),
            C_TYPE: st.column_config.Column("Тип", width="small", help="Тип фонда"),
            "Ссылка на фонд": st.column_config.LinkColumn("Ссылка", display_text="Открыть"),
        },
    )


def main() -> None:
    st.set_page_config(page_title="Позиции ПИФов РФ в акциях", layout="wide", page_icon="📈")
    st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

    if "db" not in st.session_state:
        st.session_state.db = build_default_db().copy()
    if "catalog" not in st.session_state:
        st.session_state.catalog = pd.DataFrame(columns=CATALOG_COLS)
    if st.session_state.get("data_version") != 2:  # привести старые данные сессии к новому формату
        st.session_state.db = normalize_positions(st.session_state.db)
        st.session_state.data_version = 2
    if not st.session_state.get("autoloaded"):
        st.markdown('<p class="app-title">📈 Позиции российских ПИФов в акциях</p>', unsafe_allow_html=True)
        autoload()
        st.rerun()

    meta_all = fund_universe(st.session_state.db, st.session_state.catalog)
    sel_uk, sel_types = sidebar(meta_all)
    db = st.session_state.db
    meta_all = fund_universe(db, st.session_state.catalog)

    src_counts = db.groupby(C_FUND)[C_SOURCE].first().value_counts()
    parts = [f"{k} — {v}" for k, v in src_counts.items()]
    n_cat = int((~meta_all["loaded"]).sum())
    st.markdown('<p class="app-title">📈 Позиции российских ПИФов в акциях</p>'
                f'<p class="app-sub">Фондов с составом: {db[C_FUND].nunique()} ({"; ".join(parts)})'
                + (f"; в каталоге без состава — {n_cat}" if n_cat else "")
                + ". Стоимость позиции = СЧА × доля / 100.</p>", unsafe_allow_html=True)

    meta = meta_all[meta_all[C_UK].isin(sel_uk) & meta_all[C_TYPE].isin(sel_types)]
    if meta.empty:
        st.warning("Нет фондов, подходящих под выбранные фильтры. Измените фильтры в боковой панели.")
        st.stop()
    fdb = db[db[C_FUND].isin(meta[C_FUND])]

    t1, t2 = st.tabs(["📊 Анализ по фондам", "🔎 Анализ по акциям (Кто держит бумагу?)"])
    with t1:
        tab_funds(fdb, meta)
    with t2:
        tab_stocks(fdb)

    st.divider()
    st.caption("Данные носят информационный характер и не являются индивидуальной инвестиционной "
               "рекомендацией. Встроенная база содержит ориентировочные составы портфелей; "
               "точные данные — на сайтах управляющих компаний и Investfunds.ru.")


if __name__ == "__main__":
    main()
