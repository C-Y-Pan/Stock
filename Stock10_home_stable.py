import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# --- Email 設定 (請修改這裡) ---
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "cypan2000@gmail.com" # 您的 Gmail
SENDER_PASSWORD = "amds ieiu wgqk exir" # 您的應用程式密碼 (非登入密碼)
RECEIVER_EMAIL = "cypan2000@gmail.com" # 接收報告的信箱

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import pytz
import sqlite3
import hashlib
import extra_streamlit_components as stx  # [新增] 引入 Cookie 套件
from datetime import datetime, timedelta    # [新增] 用於設定過期時間

# --- 頁面設定 ---
st.set_page_config(page_title="量化投資決策系統 (Quant Pro v6.0)", layout="wide")

# [修改] 初始化 Cookie 管理器 (加入 key 以穩定運作)
# @st.cache_resource # 註：這裡建議不要用 cache，直接實例化即可，或者用 session_state 控管
def get_cookie_manager():
    return stx.CookieManager(key="invest_cookie_manager")

cookie_manager = get_cookie_manager()

import sqlite3
import hashlib

# ==========================================
# 資料庫管理模組 (SQLite)
# ==========================================
DB_NAME = "invest_pro.db"

def init_db():
    """初始化資料庫與表格"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 建立使用者表
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT)''')
    # 建立持股表 (username, ticker, shares)
    c.execute('''CREATE TABLE IF NOT EXISTS portfolios 
                 (username TEXT, ticker TEXT, shares INTEGER, 
                  FOREIGN KEY(username) REFERENCES users(username))''')
    conn.commit()
    conn.close()

def make_hashes(password):
    """密碼加密 (SHA256)"""
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    """驗證密碼"""
    if make_hashes(password) == hashed_text: return True
    return False

def add_user(username, password):
    """註冊新用戶"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO users(username, password) VALUES (?,?)', 
                  (username, make_hashes(password)))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False # 用戶名已存在
    finally:
        conn.close()

def login_user(username, password):
    """登入驗證"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT password FROM users WHERE username = ?', (username,))
    data = c.fetchall()
    conn.close()
    if data:
        return check_hashes(password, data[0][0])
    return False

def save_portfolio_to_db(username, df):
    """儲存持股至資料庫 (覆蓋舊資料)"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 先刪除該用戶舊資料
    c.execute('DELETE FROM portfolios WHERE username = ?', (username,))
    # 寫入新資料
    for idx, row in df.iterrows():
        c.execute('INSERT INTO portfolios (username, ticker, shares) VALUES (?,?,?)',
                  (username, row['代號'], int(row['持有股數'])))
    conn.commit()
    conn.close()

def load_portfolio_from_db(username):
    """從資料庫讀取持股"""
    conn = sqlite3.connect(DB_NAME)
    try:
        df = pd.read_sql_query(f"SELECT ticker as '代號', shares as '持有股數' FROM portfolios WHERE username = '{username}'", conn)
        return df
    except:
        return pd.DataFrame()
    finally:
        conn.close()

# 程式啟動時初始化 DB
init_db()

# ==========================================
# CSS 優化：手機與電腦版面響應式適配
# ==========================================
def inject_mobile_css():
    st.markdown("""
        <style>
            /* === 電腦版 (Desktop Default) === */
            /* 給予足夠的 padding-top (4rem)，避免標題被 Streamlit 頂部功能列遮擋 */
            .block-container {
                padding-top: 4rem !important;
                padding-bottom: 2rem !important;
                padding-left: 4rem !important;
                padding-right: 4rem !important;
            }

            /* === 手機版 (Mobile Override) === */
            /* 當螢幕寬度小於 768px 時，強制縮減邊距以爭取顯示空間 */
            @media (max-width: 768px) {
                .block-container {
                    padding-top: 2rem !important; /* 手機版頂部留白較小，但保留一點空間 */
                    padding-left: 0.5rem !important;
                    padding-right: 0.5rem !important;
                }
            }
            
            /* 其他通用優化設定 (維持不變) */
            [data-testid="stMetric"] {
                background-color: #1E1E1E;
                border: 1px solid #333;
                border-radius: 8px;
                padding: 10px;
                box-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }
            
            .modebar { display: none !important; }
            
            button {
                min-height: 45px !important;
            }
        </style>
    """, unsafe_allow_html=True)

# 請確保在程式最開頭呼叫此函式
inject_mobile_css()

# ==========================================
# Session State 初始化
# ==========================================
if 'analysis_history' not in st.session_state:
    st.session_state['analysis_history'] = {}
if 'scan_results_df' not in st.session_state:
    st.session_state['scan_results_df'] = None
if 'is_scanning' not in st.session_state:
    st.session_state['is_scanning'] = False
if 'last_ticker' not in st.session_state:
    st.session_state['last_ticker'] = "2330"
if 'all_stock_list' not in st.session_state:
    st.session_state['all_stock_list'] = None

# ==========================================
# 0. 核心資料庫：共用股票清單 (含基本面數據)
# ==========================================
TW_STOCK_NAMES_STATIC = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '2317': '鴻海', '2382': '廣達',
    '3008': '大立光', '3711': '日月光投控', '3034': '聯詠', '3661': '世芯-KY'
}

import urllib3

# 忽略 SSL 不安全警告，保持介面乾淨
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@st.cache_data(ttl=3600, show_spinner=False)
def get_master_stock_data():
    """
    [終極版] 從證交所與櫃買中心獲取「每日收盤行情」(STOCK_DAY_ALL)
    策略：不分股票/ETF/ETN，只要市場上有報價的商品全數抓取，確保無遺漏。
    """
    stock_map = {} # 使用字典去重 (代號為 Key)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json'
    }
    
    # ==========================================
    # 1. 上市全市場行情 (TWSE All Daily Quotes)
    # ==========================================
    # 這個 API 包含上市的所有：股票、ETF、ETN、特別股、權證...
    try:
        url_twse_all = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
        res = requests.get(url_twse_all, headers=headers, timeout=8, verify=False) 
        if res.status_code == 200:
            data = res.json()
            for row in data:
                code = row.get('Code')
                name = row.get('Name')
                if code and name:
                    stock_map[code] = {
                        "代號": code, 
                        "名稱": name, 
                        "市場": "上市",
                        # 行情表不含基本面數據，預設給 "-"，確保搜尋功能正常
                        "本益比": "-", "殖利率(%)": "-", "股價淨值比": "-"
                    }
    except Exception as e:
        print(f"TWSE All Quote Error: {e}")

    # ==========================================
    # 2. 上櫃全市場行情 (TPEx All Daily Quotes)
    # ==========================================
    # 這個 API 包含上櫃的所有商品
    try:
        url_tpex_all = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes"
        res = requests.get(url_tpex_all, headers=headers, timeout=8, verify=False)
        if res.status_code == 200:
            data = res.json()
            for row in data:
                code = row.get('SecuritiesCompanyCode')
                name = row.get('CompanyName')
                if code and name:
                    stock_map[code] = {
                        "代號": code, 
                        "名稱": name, 
                        "市場": "上櫃",
                        "本益比": "-", "殖利率(%)": "-", "股價淨值比": "-"
                    }
    except Exception as e:
        print(f"TPEx All Quote Error: {e}")

    # ==========================================
    # 3. [選擇性] 補充個股基本面數據 (Optional)
    # ==========================================
    # 為了讓一般股票仍能顯示本益比，我們嘗試抓取 PE 表來更新 stock_map
    # 如果這裡失敗也沒關係，至少 stock_map 裡已經有代號和名稱了 (這最重要)
    try:
        url_pe = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        res = requests.get(url_pe, headers=headers, timeout=3, verify=False)
        if res.status_code == 200:
            data = res.json()
            for row in data:
                c = row.get('Code')
                if c in stock_map:
                    stock_map[c]['本益比'] = row.get('PEratio', '-')
                    stock_map[c]['殖利率(%)'] = row.get('DividendYield', '-')
                    stock_map[c]['股價淨值比'] = row.get('PBratio', '-')
    except: pass

    # ==========================================
    # 4. 轉為 DataFrame 並回傳
    # ==========================================
    if not stock_map:
        # 萬一連線全掛，回傳空表
        return pd.DataFrame(columns=["代號", "名稱", "市場", "本益比", "殖利率(%)", "股價淨值比"])
    
    # 將字典轉回 List 再轉 DataFrame
    final_list = list(stock_map.values())
    return pd.DataFrame(final_list)



def get_stock_name(ticker):
    code = ticker.split('.')[0]
    if code in TW_STOCK_NAMES_STATIC: return TW_STOCK_NAMES_STATIC[code]
    try:
        if st.session_state['all_stock_list'] is not None:
            df = st.session_state['all_stock_list']
        else:
            df = get_master_stock_data()
            st.session_state['all_stock_list'] = df
            
        row = df[df['代號'] == code]
        if not row.empty: return row.iloc[0]['名稱']
    except: pass
    return code

ALL_TECH_TICKERS = "\n".join(list(TW_STOCK_NAMES_STATIC.keys()))


PRESET_LISTS = {
    "🔥 台股熱門 50 (權值)": [
        "2330", "2317", "2454", "2382", "2303", "2308", "3008", "3034", "3035", "3037", 
        "3443", "3661", "2603", "2609", "2615", "2376", "3231", "2356", "2357", "3017",
        "2059", "3324", "6669", "3529", "5269", "5274", "3045", "4966", "2344", "6274",
        "8046", "3016", "2360", "6239", "6213", "3533", "3653", "8210", "3131", "9958",
        "1513", "1519", "1503", "1504", "1605", "2881", "2882", "2891", "5871", "2886", "6781", "3211"
    ],
    "🤖 AI 伺服器與散熱": [
        "2382", "3231", "2356", "6669", "2376", "3017", "3324", "2421", "3013", "3483", 
        "3653", "6213", "8996", "2486", "3533", "5274", "8210", "2059", "3694"
    ],
    "⚡ 重電綠能與軍工": [
        "1513", "1519", "1503", "1504", "1605", "1609", "6806", "3708", "9958", "6219", 
        "2634", "8033", "2618", "2610", "1514", "5284", "2204"
    ],
    "🚢 航運與原物料": [
        "2603", "2609", "2615", "2637", "2605", "2606", "5608", "2002", "2014", "2027", 
        "1101", "1102", "1301", "1303", "1326", "6505"
    ],
    "💰 金融存股觀察": [
        "2881", "2882", "2891", "2886", "2884", "2885", "2892", "2890", "2880", "2883", 
        "2887", "5880", "5876", "2834", "2801", "2809", "2897"
    ],
    "📊 高股息 ETF": [
        "0050", "0056", "00878", "00919", "00929", "00939", "00940", "00713", "00918", "00915"
    ]
}


# ==========================================
# 1. 數據獲取 (Updated)
# ==========================================
@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(ticker, start_date, end_date):
    """
    [智慧偵測修正版]
    修正成交量怪異問題：
    在執行「斷崖偵測」時，同步檢查成交量是否出現對應的暴增。
    只有當「股價崩跌」且「成交量暴增」同時發生時，才執行成交量還原。
    避免對 Yahoo 已經還原過的成交量進行二次放大。
    """
    ticker = str(ticker).strip().upper()
    
    candidates = []
    if '.' in ticker:
        candidates.append(ticker)
        base_code = ticker.split('.')[0]
        candidates.extend([f"{base_code}.TW", f"{base_code}.TWO"])
    else:
        import re
        if ticker.startswith('00') and re.search('[A-Z]', ticker):
            candidates = [f"{ticker}.TWO", f"{ticker}.TW", ticker]
        else:
            candidates = [f"{ticker}.TW", f"{ticker}.TWO", ticker]

    for t in candidates:
        try:
            stock = yf.Ticker(t)
            df = stock.history(start=start_date - timedelta(days=700), end=end_date + timedelta(days=1), auto_adjust=False, actions=True)
            
            if df.empty or len(df) < 5: continue

            df_safe = df.copy()
            df = df.sort_index(ascending=True)

            try:
                # ==========================================
                # A. 基礎事件還原 (Metadata)
                # ==========================================
                if 'Stock Splits' in df.columns or 'Dividends' in df.columns:
                    df_rev = df.sort_index(ascending=False).copy()
                    opens, highs, lows, closes = df_rev['Open'].values, df_rev['High'].values, df_rev['Low'].values, df_rev['Close'].values
                    vols = df_rev['Volume'].values.astype(float)
                    splits = df_rev['Stock Splits'].values if 'Stock Splits' in df.columns else np.zeros(len(df))
                    divs = df_rev['Dividends'].values if 'Dividends' in df.columns else np.zeros(len(df))
                    
                    p_cum, v_cum = 1.0, 1.0
                    for i in range(len(df_rev)):
                        if splits[i] > 0:
                            p_cum *= (1.0 / splits[i])
                            v_cum *= splits[i]
                        if divs[i] > 0:
                            price_before = closes[i] + divs[i]
                            if price_before > 0: p_cum *= (1 - divs[i] / price_before)
                        
                        opens[i] *= p_cum; highs[i] *= p_cum; lows[i] *= p_cum; closes[i] *= p_cum
                        vols[i] *= v_cum 
                    
                    df['Open'], df['High'], df['Low'], df['Close'], df['Volume'] = opens[::-1], highs[::-1], lows[::-1], closes[::-1], vols[::-1]

                # ==========================================
                # B. 智慧斷崖偵測 (Smart Gap Detection)
                # ==========================================
                p_open = df['Open'].values
                p_close = df['Close'].values
                p_high, p_low = df['High'].values, df['Low'].values
                p_vol = df['Volume'].values.astype(float)
                
                p_close = np.nan_to_num(p_close, nan=0.0)
                
                for i in range(1, len(df)):
                    prev_close = p_close[i-1]
                    curr_open = p_open[i]
                    
                    # 取得前後日的成交量 (處理 0 的情況)
                    prev_vol = p_vol[i-1] if p_vol[i-1] > 0 else 1.0
                    curr_vol = p_vol[i]
                    
                    if prev_close > 5:
                        ratio = curr_open / prev_close
                        
                        # 偵測到股價斷崖 (跌幅 > 40%) -> 疑似分割
                        if ratio < 0.6:
                            curr_close = p_close[i]
                            gap_factor = curr_close / prev_close # 價格縮小因子 (如 0.14)
                            
                            # 強制修正歷史價格 (變小)
                            p_open[:i] *= gap_factor; p_high[:i] *= gap_factor
                            p_low[:i] *= gap_factor; p_close[:i] *= gap_factor
                            
                            # [關鍵修正] 智慧判斷是否需要修正成交量
                            # 理論上，若價格變 1/7，成交量應變 7倍。
                            # 我們檢查：現在的量是不是比昨天暴增了 3 倍以上？
                            # 如果是 -> 代表是原始量，需要還原歷史量 (放大歷史量)
                            # 如果否 -> 代表 Yahoo 已經還原過量了，我們不動它
                            
                            vol_jump_ratio = curr_vol / prev_vol
                            expected_jump = 1.0 / gap_factor # 理論應跳增倍數 (如 7.0)
                            
                            # 判定門檻：實際跳增幅度大於理論的一半 (例如 > 3.5倍)
                            if vol_jump_ratio > (expected_jump * 0.5):
                                # 確實暴增了，執行歷史量還原 (放大歷史量，讓它跟現在一樣高)
                                vol_correction_factor = expected_jump
                                p_vol[:i] *= vol_correction_factor
                            else:
                                # 量沒變，代表 Yahoo 已經調過了，或該 ETF 規模縮水
                                # 不做任何動作，保持原樣
                                pass
                
                df['Open'], df['High'], df['Low'], df['Close'], df['Volume'] = p_open, p_high, p_low, p_close, p_vol

            except Exception:
                df = df_safe

            # C. 輸出
            mask = (df.index >= pd.to_datetime(start_date - timedelta(days=100)).tz_localize(df.index.tz))
            df = df.loc[mask].reset_index()
            df['Date'] = df['Date'].dt.tz_localize(None).dt.normalize()
            
            cols = ['Dividends', 'Stock Splits']
            df = df.drop(columns=[c for c in cols if c in df.columns], errors='ignore')
            
            if not df.empty and 'Close' in df.columns:
                return df, t
                
        except Exception:
            continue
            
    return pd.DataFrame(), ticker



@st.cache_data(ttl=5, show_spinner=False)
def get_market_data(start_date, end_date):
    try:
        market = yf.Ticker("^TWII")
        df = market.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
        vix = yf.Ticker("^VIX") # S&P 500 VIX 作為全球恐慌指標參考
        df_vix = vix.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
        
        if not df.empty:
            df = df.reset_index()
            df['Date'] = df['Date'].dt.tz_localize(None).dt.normalize()
            
            if not df_vix.empty:
                df_vix = df_vix.reset_index()
                df_vix['Date'] = df_vix['Date'].dt.tz_localize(None).dt.normalize()
                df = pd.merge(df, df_vix[['Date', 'Close']].rename(columns={'Close': 'VIX'}), on='Date', how='left')
                df['VIX'] = df['VIX'].ffill().fillna(20)
            else:
                df['VIX'] = 0.0
                
            df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
            df['OBV_MA20'] = df['OBV'].rolling(20).mean()
            
            delta = df['Close'].diff()
            gain = (delta.where(delta>0, 0)).rolling(14).mean()
            loss = (-delta.where(delta<0, 0)).rolling(14).mean()
            df['Market_RSI'] = (100 - (100 / (1 + gain/loss))).fillna(50)
            
            df['Market_MA20'] = df['Close'].rolling(20).mean()
            df['Market_MA60'] = df['Close'].rolling(60).mean()
            
            # [修正] 這裡原本漏了 Open, High, Low, Volume，導致後續 Alpha Score 計算 KD 時找不到欄位報錯
            return df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Market_RSI', 'Market_MA20', 'Market_MA60', 'OBV', 'OBV_MA20', 'VIX']]
    except: pass
    return pd.DataFrame()

@st.cache_data(ttl=10000, show_spinner=False)
def get_margin_data(start_date_str):
    """
    獲取台股整體融資券數據 (來源: FinMind)
    """
    try:
        url = "https://api.finmindtrade.com/api/v4/data"
        params = {"dataset": "TaiwanStockTotalMarginPurchaseShortSale", "start_date": start_date_str, "token": ""}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if data.get('msg') == 'success' and data.get('data'):
            df = pd.DataFrame(data['data'])
            df['date'] = pd.to_datetime(df['date'])
            for c in ['TodayBalance', 'YesBalance', 'buy', 'sell', 'Return']:
                if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
            return df
    except: pass
    return pd.DataFrame()

# ==========================================
# 2. 指標計算 (修復數據時間差導致的 NaN 問題)
# ==========================================
def calculate_indicators(df, atr_period, multiplier, market_df):
    data = df.copy()
    
    # 合併大盤數據
    if not market_df.empty:
        data['Date'] = pd.to_datetime(data['Date']).dt.normalize()
        market_df['Date'] = pd.to_datetime(market_df['Date']).dt.normalize()
        data = pd.merge(data, market_df, on='Date', how='left', suffixes=('', '_Market'))
        
        cols_to_fill = ['Market_RSI', 'Market_MA20', 'Market_MA60', 'VIX']
        for c in cols_to_fill:
            if c in data.columns:
                data[c] = data[c].ffill()
        
        if 'Market_RSI' in data.columns: data['Market_RSI'] = data['Market_RSI'].fillna(50)
        if 'Market_MA20' in data.columns: data['Market_MA20'] = data['Market_MA20'].fillna(0)
        if 'VIX' in data.columns: data['VIX'] = data['VIX'].fillna(20)
    else:
        data['Market_RSI'] = 50
        data['Market_MA20'] = 0
        data['VIX'] = 20
    
    # --- 指標計算 ---
    data['OBV'] = (np.sign(data['Close'].diff()) * data['Volume']).fillna(0).cumsum()
    data['OBV_MA20'] = data['OBV'].rolling(20).mean()
    data['Vol_MA20'] = data['Volume'].rolling(20).mean().replace(0, 1).fillna(1)
    
    data['MA20'] = data['Close'].rolling(20).mean()
    # [新增] MA30 用於乖離判斷
    data['MA30'] = data['Close'].rolling(30).mean()
    data['MA60'] = data['Close'].rolling(60).mean()
    data['MA120'] = data['Close'].rolling(120).mean() 
    data['MA240'] = data['Close'].rolling(240, min_periods=60).mean()
    
    # [新增] 100日新高 與 週漲幅參考價
    data['High_100d'] = data['Close'].rolling(100).max()
    data['Close_Lag5'] = data['Close'].shift(5) # 5天前價格，計算週漲幅用

    high = data['High']; low = data['Low']; close = data['Close']
    
    # ATR Calculation
    data['tr0'] = abs(high - low)
    data['tr1'] = abs(high - close.shift(1))
    data['tr2'] = abs(low - close.shift(1))
    data['TR'] = data[['tr0', 'tr1', 'tr2']].max(axis=1)
    data['ATR'] = data['TR'].ewm(span=atr_period, adjust=False).mean()
    
    # SuperTrend Calculation
    data['Basic_Upper'] = (high + low) / 2 + (multiplier * data['ATR'])
    data['Basic_Lower'] = (high + low) / 2 - (multiplier * data['ATR'])
    
    final_upper = [0.0]*len(data); final_lower = [0.0]*len(data); supertrend = [0.0]*len(data); trend = [1]*len(data)
    
    if len(data)>0:
        final_upper[0]=data['Basic_Upper'].iloc[0]
        final_lower[0]=data['Basic_Lower'].iloc[0]
        supertrend[0]=final_lower[0]
        
    for i in range(1, len(data)):
        curr_close = close.iloc[i-1]
        
        if data['Basic_Upper'].iloc[i] < final_upper[i-1] or curr_close > final_upper[i-1]:
            final_upper[i] = data['Basic_Upper'].iloc[i]
        else:
            final_upper[i] = final_upper[i-1]
            
        if data['Basic_Lower'].iloc[i] > final_lower[i-1] or curr_close < final_lower[i-1]:
            final_lower[i] = data['Basic_Lower'].iloc[i]
        else:
            final_lower[i] = final_lower[i-1]
            
        if trend[i-1] == 1:
            if close.iloc[i] < final_lower[i-1]: trend[i] = -1
            else: trend[i] = 1
        else:
            if close.iloc[i] > final_upper[i-1]: trend[i] = 1
            else: trend[i] = -1
            
        supertrend[i] = final_lower[i] if trend[i] == 1 else final_upper[i]
    
    data['SuperTrend'] = supertrend
    data['Trend'] = trend
    
    # RSI (個股)
    delta = data['Close'].diff()
    gain = (delta.where(delta>0, 0)).rolling(14).mean()
    loss = (-delta.where(delta<0, 0)).rolling(14).mean()
    data['RSI'] = (100 - (100 / (1 + gain/loss))).fillna(50)
    
    # Bollinger Bands
    data['BB_Mid'] = data['Close'].rolling(20).mean()
    data['BB_Std'] = data['Close'].rolling(20).std()
    data['BB_Lower'] = data['BB_Mid'] - (2.0 * data['BB_Std'])
    data['BB_Upper'] = data['BB_Mid'] + (2.0 * data['BB_Std'])
    
    data['Is_Market_Panic'] = data['Market_RSI'] < 50 
    
    return data.dropna(subset=['SuperTrend'])



# ==========================================
# 3. 策略邏輯 & 輔助 (Modified with Confidence Score)
# ==========================================
def run_simple_strategy(data, rsi_buy_thresh, fee_rate=0.001425, tax_rate=0.003, use_chip_strategy=True, use_strict_bear_exit=True):
    """
    執行策略回測 v6 (Strict Bear Exit Toggle):
    新增 use_strict_bear_exit 參數，控制是否在「嚴格空頭且破月線」時強制賣出。
    """
    df = data.copy()
    positions = []; reasons = []; actions = []; target_prices = []
    return_labels = []; confidences = []
    
    position = 0; days_held = 0; entry_price = 0.0; trade_type = 0
    
    # 轉為 numpy array 加速迭代
    close = df['Close'].values; trend = df['Trend'].values; rsi = df['RSI'].values
    bb_lower = df['BB_Lower'].values; ma20 = df['MA20'].values; ma60 = df['MA60'].values
    ma240 = df['MA240'].fillna(method='bfill').values
    ma30 = df['MA30'].ffill().values
    high_100d = df['High_100d'].fillna(0).values
    close_lag5 = df['Close_Lag5'].fillna(close[0]).values
    
    volume = df['Volume'].values; vol_ma20 = df['Vol_MA20'].values
    obv = df['OBV'].values; obv_ma20 = df['OBV_MA20'].values
    market_panic = df['Is_Market_Panic'].values
    bb_width_vals = ((df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']).values

    for i in range(len(df)):
        signal = position; reason_str = ""; action_code = "Hold" if position == 1 else "Wait"
        this_target = entry_price * 1.15 if position == 1 else np.nan
        ret_label = ""; conf_score = 0

        # [核心判斷] 趨勢狀態
        is_ma240_down = False
        is_ma60_up = False
        
        if i > 0:
            if ma240[i] < ma240[i-1]: is_ma240_down = True
            if ma60[i] > ma60[i-1]: is_ma60_up = True
            
        is_price_weak = (close[i] < ma60[i]) and (close[i] < ma20[i])
        is_strict_bear = is_ma240_down and (not is_ma60_up) and is_price_weak

        # --- 進場邏輯 ---
        if position == 0:
            is_buy = False
            rsi_threshold_A = 60 if is_strict_bear else 55
            
            # 策略 A
            if (trend[i]==1 and (i>0 and trend[i-1]==-1) and volume[i]>vol_ma20[i] and close[i]>ma60[i] and rsi[i]>rsi_threshold_A and obv[i]>obv_ma20[i]):
                is_buy=True; trade_type=1; reason_str="動能突破"
            # 策略 B
            elif not is_strict_bear and trend[i]==1 and close[i]>ma60[i] and (df['Low'].iloc[i]<=ma20[i]*1.02) and close[i]>ma20[i] and volume[i]<vol_ma20[i] and rsi[i]>45:
                is_buy=True; trade_type=1; reason_str="均線回測"
            # 策略 C
            elif use_chip_strategy and not is_strict_bear and close[i]>ma60[i] and obv[i]>obv_ma20[i] and volume[i]<vol_ma20[i] and (close[i]<ma20[i] or rsi[i]<55) and close[i]>bb_lower[i]:
                is_buy=True; trade_type=3; reason_str="籌碼佈局"
            # 策略 D
            elif rsi[i]<rsi_buy_thresh and close[i]<bb_lower[i] and market_panic[i] and volume[i]>vol_ma20[i]*0.5:
                is_buy=True; trade_type=2; reason_str="超賣反彈"
            
            if is_buy:
                signal=1; days_held=0; entry_price=close[i]; action_code="Buy"
                
                # 計算信心值
                base_score = 60
                if is_strict_bear: base_score -= 10
                if is_ma240_down and is_ma60_up: base_score += 5
                if volume[i] > vol_ma20[i] * 1.5: base_score += 15
                elif volume[i] > vol_ma20[i]: base_score += 8
                if i > 5 and ma60[i] > ma60[i-5] and close[i] > ma60[i]: base_score += 10
                if trade_type == 1 and 60 <= rsi[i] <= 75: base_score += 10
                elif trade_type == 2 and rsi[i] <= 25: base_score += 10
                if i > 3 and bb_width_vals[i-1] < 0.15: base_score += 5
                if close[i] > ma30[i] * 1.04: base_score += 5
                
                weekly_ratio = close[i] / close_lag5[i] if close_lag5[i] > 0 else 1.0
                if close[i] >= high_100d[i] and weekly_ratio < 1.27: base_score += 15
                
                conf_score = min(base_score, 99)
        
        # --- 出場邏輯 ---
        elif position == 1:
            days_held+=1
            drawdown=(close[i]-entry_price)/entry_price
            
            if trade_type==2 and trend[i]==1: trade_type=1; reason_str="反彈轉波段"
            if trade_type==3 and volume[i]>vol_ma20[i]*1.2: trade_type=1; reason_str="佈局完成發動"
            
            is_sell = False
            stop_loss_limit = -0.10 if is_strict_bear else -0.12
            
            if drawdown < stop_loss_limit:
                is_sell=True; reason_str=f"觸發停損({stop_loss_limit*100:.0f}%)"; action_code="Sell"
            elif days_held <= (2 if is_strict_bear else 3):
                action_code="Hold"; reason_str="鎖倉觀察"
            else:
                if trade_type==1 and trend[i]==-1: 
                    if close[i] < ma20[i]:
                        is_sell=True; reason_str="趨勢轉弱且破月線"
                    else:
                        action_code="Hold"; reason_str="轉弱(守月線)"
                
                # [修改] 只有在開關開啟(True) 時，才執行「長空破月線」強制出場
                elif use_strict_bear_exit and is_strict_bear and close[i] < ma20[i]:
                    is_sell=True; reason_str="長空破月線"
                    
                elif trade_type==2 and days_held>10 and drawdown<0: is_sell=True; reason_str="逆勢操作超時"
                elif trade_type==3 and close[i]<bb_lower[i]: is_sell=True; reason_str="支撐確認失敗"
                
            if is_sell:
                signal=0; action_code="Sell"
                pnl = (close[i] - entry_price) / entry_price * 100
                sign = "+" if pnl > 0 else ""
                ret_label = f"{sign}{pnl:.1f}%"

        position=signal
        positions.append(signal); reasons.append(reason_str); actions.append(action_code)
        target_prices.append(this_target); return_labels.append(ret_label)
        confidences.append(conf_score if action_code == "Buy" else 0)
        
    df['Position']=positions; df['Reason']=reasons; df['Action']=actions
    df['Target_Price']=target_prices; df['Return_Label']=return_labels
    df['Confidence'] = confidences
    
    df['Real_Position'] = df['Position'].shift(1).fillna(0)
    df['Market_Return'] = df['Close'].pct_change().fillna(0)
    df['Strategy_Return'] = df['Real_Position'] * df['Market_Return']
    cost_series = pd.Series(0.0, index=df.index)
    cost_series[df['Action'] == 'Buy'] = fee_rate
    cost_series[df['Action'] == 'Sell'] = fee_rate + tax_rate
    df['Strategy_Return'] = df['Strategy_Return'] - cost_series
    df['Cum_Strategy']=(1+df['Strategy_Return']).cumprod()
    df['Cum_Market']=(1+df['Market_Return']).cumprod()
    return df


# 修改後：傳遞成本參數
def run_optimization(raw_df, market_df, user_start_date, fee_rate=0.001425, tax_rate=0.003, use_chip_strategy=True, use_strict_bear_exit=True):
    best_ret = -999; best_params = None; best_df = None; target_start = pd.to_datetime(user_start_date)
    
    for m in [3.0, 3.5]:
        for r in [25, 30]:
            df_ind = calculate_indicators(raw_df, 10, m, market_df)
            df_slice = df_ind[df_ind['Date'] >= target_start].copy()
            if df_slice.empty: continue
            
            # [修改] 傳遞 use_strict_bear_exit
            df_res = run_simple_strategy(df_slice, r, fee_rate, tax_rate, use_chip_strategy, use_strict_bear_exit)
            
            ret = df_res['Cum_Strategy'].iloc[-1] - 1
            if ret > best_ret:
                best_ret = ret
                best_params = {'Mult':m, 'RSI_Buy':r, 'Return':ret}
                best_df = df_res
    return best_params, best_df



def validate_strategy_robust(raw_df, market_df, split_ratio=0.7, fee_rate=0.001425, tax_rate=0.003):
    """
    執行嚴謹的樣本外測試 (Walk-Forward Analysis 簡化版)
    Split Ratio: 訓練集佔比 (預設 70%)
    """
    # 1. 資料切割
    total_len = len(raw_df)
    if total_len < 100: return None # 資料過少無法驗證
    
    split_idx = int(total_len * split_ratio)
    train_data_raw = raw_df.iloc[:split_idx].copy()
    test_data_raw = raw_df.iloc[split_idx:].copy()
    
    # 確保切分後的測試集有足夠數據
    if len(test_data_raw) < 30: return None

    # 2. 訓練階段 (In-Sample): 在過去數據找最佳參數
    # 注意：start_date 設為訓練集的第一天
    train_start_date = train_data_raw['Date'].min()
    best_params_train, train_res_df = run_optimization(train_data_raw, market_df, train_start_date, fee_rate, tax_rate)
    
    if best_params_train is None: return None

    # 3. 測試階段 (Out-of-Sample): 用訓練好的參數去跑未來的數據
    # 關鍵：這裡不能再做 run_optimization，必須固定參數
    
    # 先計算測試集的指標 (使用訓練集找出的最佳 Multiplier)
    test_ind = calculate_indicators(test_data_raw, 10, best_params_train['Mult'], market_df)
    
    # 執行策略 (使用訓練集找出的最佳 RSI 閾值)
    test_res_df = run_simple_strategy(test_ind, best_params_train['RSI_Buy'], fee_rate, tax_rate)
    
    # 4. 績效比較與指標計算
    def get_metrics(df):
        if df.empty: return 0, 0
        cum_ret = df['Cum_Strategy'].iloc[-1] - 1
        mdd = calculate_mdd(df['Cum_Strategy'])
        # 年化報酬估算
        days = (df['Date'].max() - df['Date'].min()).days
        cagr = ((1 + cum_ret) ** (365/days) - 1) if days > 0 else 0
        return cum_ret, mdd, cagr

    train_ret, train_mdd, train_cagr = get_metrics(train_res_df)
    test_ret, test_mdd, test_cagr = get_metrics(test_res_df)
    
    return {
        "params": best_params_train,
        "train": {"ret": train_ret, "mdd": train_mdd, "cagr": train_cagr, "df": train_res_df},
        "test": {"ret": test_ret, "mdd": test_mdd, "cagr": test_cagr, "df": test_res_df},
        "split_date": test_data_raw['Date'].min()
    }

def calculate_target_hit_rate(df):
    if df is None or df.empty: return "0.0%", 0, 0
    
    buy_indices = df[df['Action']=='Buy'].index
    total = len(buy_indices)
    
    # [新增] 防呆機制：如果完全沒有買進訊號，直接回傳 0，避免除以零錯誤
    if total == 0:
        return "0.0%", 0, 0
        
    hits = 0
    for idx in buy_indices:
        entry = df.loc[idx, 'Close']
        target = entry * 1.15
        future = df.loc[idx+1:]
        
        # 尋找下一次賣出點，定義持有區間
        sell_rows = future[future['Action']=='Sell']
        if not sell_rows.empty:
            period = df.loc[idx:sell_rows.index[0]]
        else:
            period = df.loc[idx:]
            
        if period['High'].max() >= target: hits += 1
        
    return f"{(hits/total)*100:.1f}%", hits, total

def calculate_realized_win_rate(df):
    """
    計算實際平倉的勝率 (基於 Action='Sell' 的紀錄)
    回傳: 勝率字串, 勝場數, 總交易數, 平均單筆報酬
    """
    if df is None or df.empty: return "0.0%", 0, 0, 0.0
    
    # 篩選出所有「賣出」的紀錄
    closed_trades = df[df['Action'] == 'Sell']
    if closed_trades.empty: return "0.0%", 0, 0, 0.0
    
    pnl_values = []
    for label in closed_trades['Return_Label']:
        try:
            # 解析字串 "+10.5%" -> 10.5
            val = float(label.replace('%', '').replace('+', ''))
            pnl_values.append(val)
        except: pass
        
    if not pnl_values: return "0.0%", 0, 0, 0.0
    
    pnl_arr = np.array(pnl_values)
    total_trades = len(pnl_arr)
    winning_trades = len(pnl_arr[pnl_arr > 0])
    
    win_rate = (winning_trades / total_trades) * 100
    avg_pnl = pnl_arr.mean()
    
    return f"{win_rate:.1f}%", winning_trades, total_trades, avg_pnl

def calculate_mdd(cum_series):
    if cum_series.empty: return 0.0
    return ((cum_series - cum_series.cummax()) / cum_series.cummax()).min() * 100

def calculate_stock_personality(df, market_df):
    if df.empty or market_df.empty: return "N/A", "N/A", "N/A"
    merged = pd.merge(df[['Date', 'Close']], market_df[['Date', 'Close']], on='Date', suffixes=('_Stock', '_Market')).dropna()
    if len(merged) < 30: return "N/A", "N/A", "數據不足"
    cov = np.cov(merged['Close_Stock'].pct_change().dropna(), merged['Close_Market'].pct_change().dropna())[0, 1]
    var = np.var(merged['Close_Market'].pct_change().dropna())
    beta = cov / var if var != 0 else 0
    vol = merged['Close_Stock'].pct_change().std() * (252**0.5) * 100
    desc = "高波動" if vol>40 else ("低波動" if vol<20 else "穩健")
    return f"{beta:.2f}", f"{vol:.1f}%", desc

def calculate_risk_metrics(df):
    if df.empty: return {}
    df['Daily_Ret'] = df['Cum_Strategy'].pct_change().fillna(0)
    rf = 0.015 / 252
    mean_ret = df['Daily_Ret'].mean()
    std_ret = df['Daily_Ret'].std()
    sharpe = (mean_ret - rf) / std_ret * np.sqrt(252) if std_ret != 0 else 0
    downside_ret = df[df['Daily_Ret'] < 0]['Daily_Ret']
    downside_std = downside_ret.std()
    sortino = (mean_ret - rf) / downside_std * np.sqrt(252) if downside_std != 0 else 0
    gross_profit = df[df['Strategy_Return'] > 0]['Strategy_Return'].sum()
    gross_loss = abs(df[df['Strategy_Return'] < 0]['Strategy_Return'].sum())
    profit_factor = gross_profit / gross_loss if gross_loss != 0 else 0
    volatility = std_ret * np.sqrt(252)
    return {'Sharpe': sharpe, 'Sortino': sortino, 'Profit_Factor': profit_factor, 'Volatility': volatility}

def run_monte_carlo_sim(last_price, vol_str, days=120, sims=50):
    try: sigma = float(vol_str.replace('%', '')) / 100
    except: sigma = 0.2
    mu = 0.05; dt = 1/252; simulation_df = pd.DataFrame()
    for x in range(sims):
        price_series = [last_price]; price = last_price
        for i in range(days):
            drift = (mu - 0.5 * sigma**2) * dt
            shock = sigma * np.sqrt(dt) * np.random.normal()
            price = price * np.exp(drift + shock)
            price_series.append(price)
        simulation_df[f'Sim_{x}'] = price_series
    final_prices = simulation_df.iloc[-1]
    var_95 = np.percentile(final_prices, 5)
    return simulation_df, var_95

def analyze_signal(final_df):
    if final_df is None or final_df.empty: return "無數據", "gray", ""
    last = final_df.iloc[-1]; act = last['Action']; reason = last['Reason'] if last['Reason'] else "持倉續抱"
    if act=="Buy": return "🚀 買進", "red", reason
    elif act=="Sell": return "⚡ 賣出", "green", reason
    elif last['Position']==1: return "✊ 續抱", "red", reason
    else: return "👀 觀望", "gray", "空手"

# ==========================================
# 5. [核心演算法] 買賣評等 (Alpha Score) - 實務嚴謹版
# ==========================================
def calculate_alpha_score(df, margin_df, short_df):
    """
    Alpha Score v5.6 (Long-Term MA Filter):
    - 加分條件維持不變。
    - [新增] 趨勢買入濾網: 若買進訊號發生時，收盤價未同時站上 MA120 與 MA240，大幅扣分 (-20)。
      這能確保只有在長線架構轉強(或至少克服壓力)時，才給予高分。
    """
    df = df.copy()

    if 'Action' not in df.columns or 'Position' not in df.columns:
        return calculate_alpha_score_technical_fallback(df)

    # 補全指標
    if 'RSI' not in df.columns: df['RSI'] = 50
    if 'MA20' not in df.columns: df['MA20'] = df['Close'].rolling(20).mean()
    if 'MA60' not in df.columns: df['MA60'] = df['Close'].rolling(60).mean()
    if 'MA120' not in df.columns: df['MA120'] = df['Close'].rolling(120).mean() # 確保有 MA120
    if 'MA240' not in df.columns: df['MA240'] = df['Close'].rolling(240, min_periods=60).mean()
    if 'MA30' not in df.columns: df['MA30'] = df['Close'].rolling(30).mean()
    if 'High_100d' not in df.columns: df['High_100d'] = df['Close'].rolling(100).max()
    if 'Close_Lag5' not in df.columns: df['Close_Lag5'] = df['Close'].shift(5)
    
    if 'Vol_MA20' not in df.columns: df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    
    action = df['Action'].values
    position = pd.Series(df['Position'].values).ffill().fillna(0).values
    close = df['Close'].values
    ma20 = df['MA20'].ffill().values
    ma60 = df['MA60'].ffill().values
    ma120 = df['MA120'].fillna(method='bfill').values
    ma240 = df['MA240'].fillna(method='bfill').values
    ma30 = df['MA30'].ffill().values
    
    high_100d = df['High_100d'].fillna(0).values
    close_lag5 = df['Close_Lag5'].fillna(close[0]).values
    
    volume = df['Volume'].fillna(0).values
    vol_ma20 = df['Vol_MA20'].replace(0, 1).fillna(1).values
    rsi = df['RSI'].fillna(50).values
    
    # 2. 計算「類比調節因子」
    bias_val = (close - ma20) / ma20 * 100
    score_bias = np.clip(bias_val * 2, -15, 15)
    score_rsi = np.clip((rsi - 50) * 0.6, -15, 15)
    
    vol_ratio = volume / vol_ma20
    score_vol = np.where(vol_ratio > 1, np.clip((vol_ratio - 1) * 5, 0, 10), 0)
    
    # 趨勢判定 (年線下彎且季線無力)
    ma240_slope_neg = np.zeros(len(df), dtype=bool)
    ma60_slope_pos = np.zeros(len(df), dtype=bool)
    if len(ma240) > 1:
        ma240_slope_neg[1:] = ma240[1:] < ma240[:-1]
        ma60_slope_pos[1:] = ma60[1:] > ma60[:-1]
    
    penalty_mask = ma240_slope_neg & (~ma60_slope_pos)
    score_trend_penalty = np.where(penalty_mask, -15, 0)
    
    # 加分條件
    cond_ma30_gap = (close > ma30 * 1.04)
    score_ma30 = np.where(cond_ma30_gap, 5, 0)
    
    weekly_ratio = np.divide(close, close_lag5, out=np.ones_like(close), where=close_lag5!=0)
    cond_not_overheated = weekly_ratio < 1.27
    cond_breakout = (close >= high_100d)
    score_breakout = np.where(cond_breakout & cond_not_overheated, 15, 0)
    
    # 綜合調節值
    analog_modulation = score_bias + score_rsi + score_vol + score_trend_penalty + score_ma30 + score_breakout

    # 3. 狀態錨定評分
    alpha_score = np.zeros(len(df))
    log_msg = np.full(len(df), "", dtype=object)

    holding_score = 60 + analog_modulation
    waiting_score = -30 + analog_modulation
    
    alpha_score = np.where(position == 1, holding_score, waiting_score)
    
    base_log_msg = np.where(position == 1, "持倉監控", "空手觀望")
    base_log_msg = np.where(penalty_mask, base_log_msg + " [⚠️年線蓋頭]", base_log_msg)
    
    rescue_mask = ma240_slope_neg & ma60_slope_pos
    base_log_msg = np.where(rescue_mask, base_log_msg + " [季線救援]", base_log_msg)
    base_log_msg = np.where(cond_ma30_gap, base_log_msg + " [📈強勢乖離]", base_log_msg)
    base_log_msg = np.where(cond_breakout & cond_not_overheated, base_log_msg + " [🚀百日突破]", base_log_msg)

    log_msg = base_log_msg

    # 4. 訊號事件
    buy_mask = (action == 'Buy')
    
    # [抄底策略檢查]
    reason_series = df['Reason'].fillna("").astype(str)
    is_panic_strat = reason_series.str.contains('反彈|超賣').values
    panic_bear_penalty_mask = buy_mask & is_panic_strat & ma240_slope_neg
    
    # [新增] 長均線濾網 (Long MA Filter)
    # 條件：未同時站上 MA120 與 MA240
    # 注意：若是抄底策略(is_panic_strat)，因為本來就是逆勢，所以不受此限制(否則永遠抄不到底)
    # 此濾網主要針對「趨勢突破」類型的策略
    not_above_long_ma = (close < ma120) | (close < ma240)
    trend_buy_penalty_mask = buy_mask & (~is_panic_strat) & not_above_long_ma

    # 基礎買進脈衝
    buy_pulse = 85 + (analog_modulation * 0.5)
    
    # 執行扣分
    # 1. 逆勢抄底扣分 (-15)
    buy_pulse = np.where(panic_bear_penalty_mask, buy_pulse - 15, buy_pulse)
    # 2. 趨勢買入但未站上長均扣分 (-20)
    buy_pulse = np.where(trend_buy_penalty_mask, buy_pulse - 20, buy_pulse)
    
    # 限制範圍
    # 如果觸發任一扣分，最高分限制在 65 (偏弱勢買點)
    any_penalty = panic_bear_penalty_mask | trend_buy_penalty_mask
    buy_pulse = np.clip(buy_pulse, 85 if not np.any(any_penalty) else 60, 99)
    
    alpha_score = np.where(buy_mask, buy_pulse, alpha_score)
    
    if 'Reason' in df.columns:
        buy_reasons = df['Reason'].fillna("")
        log_msg = np.where(buy_mask, "買進: " + buy_reasons, log_msg)
        # 評語警示
        log_msg = np.where(panic_bear_penalty_mask, log_msg + " [⚠️逆勢抄底]", log_msg)
        log_msg = np.where(trend_buy_penalty_mask, log_msg + " [⚠️未站上長均]", log_msg)

    sell_mask = (action == 'Sell')
    sell_pulse = -85 + (analog_modulation * 0.5)
    sell_pulse = np.clip(sell_pulse, -99, -85)
    alpha_score = np.where(sell_mask, sell_pulse, alpha_score)
    
    if 'Reason' in df.columns:
        sell_reasons = df['Reason'].fillna("")
        log_msg = np.where(sell_mask, "賣出: " + sell_reasons, log_msg)

    # 5. 平滑化
    final_series = pd.Series(alpha_score)
    smoothed_score = final_series.ewm(alpha=0.5, adjust=False).mean().values
    final_score = np.where(buy_mask | sell_mask, alpha_score, smoothed_score)
    
    df['Alpha_Score'] = np.clip(final_score, -100, 100)
    
    conditions = [
        (df['Alpha_Score'] >= 80),
        (df['Alpha_Score'] >= 50),
        (df['Alpha_Score'] >= 0),
        (df['Alpha_Score'] <= -80),
        (df['Alpha_Score'] <= -50)
    ]
    choices = ["🔥 極強勢", "📈 多頭攻勢", "⚖️ 偏多震盪", "⚡ 極弱勢", "📉 空頭修正"]
    
    base_log = np.select(conditions, choices, default="☁️ 盤整")
    df['Score_Log'] = np.where(buy_mask | sell_mask, log_msg, base_log)
    
    df['Score_Log'] = np.where((~buy_mask) & (~sell_mask) & penalty_mask, df['Score_Log'] + " (長空)", df['Score_Log'])
    df['Score_Log'] = np.where((~buy_mask) & (~sell_mask) & rescue_mask, df['Score_Log'] + " (轉強)", df['Score_Log'])
    
    df['Recommended_Position'] = ((df['Alpha_Score'] + 100) / 2).clip(0, 100)

    return df



def calculate_alpha_score_technical_fallback(df):
    """
    [備用] 純技術面評分 v4.1
    當 DataFrame 沒有 Action/Position 欄位時使用 (例如大盤分析頁面)
    """
    df = df.copy()
    if 'Trend' not in df.columns: df['Trend'] = 1
    if 'MA60' not in df.columns: df['MA60'] = df['Close'].rolling(60).mean()
    if 'Vol_MA20' not in df.columns: df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    if 'RSI' not in df.columns: df['RSI'] = 50
    if 'BB_Lower' not in df.columns: df['BB_Lower'] = df['Close'] * 0.9
    
    close = df['Close'].values
    trend = df['Trend'].values
    ma60 = df['MA60'].values
    volume = df['Volume'].fillna(0).values
    vol_ma20 = df['Vol_MA20'].fillna(0).values
    rsi = df['RSI'].fillna(50).values
    bb_lower = df['BB_Lower'].values
    
    base_score = np.zeros(len(df))
    base_score = np.where((close > ma60) & (trend == 1), 40, base_score)
    base_score = np.where((close < ma60) & (trend == -1), -40, base_score)
    
    strat_score = np.zeros(len(df))
    # A. 動能
    strat_score = np.where((trend == 1) & (volume > vol_ma20) & (close > ma60) & (rsi > 55), 40, strat_score)
    # B. 恐慌反彈 (Override)
    cond_D = (rsi < 30) & (close <= bb_lower * 1.01)
    
    raw_final = base_score + strat_score
    raw_final = np.where(cond_D, 80, raw_final) # 強制拉升
    
    df['Alpha_Score'] = np.clip(raw_final, -100, 100)
    df['Score_Log'] = np.where(df['Alpha_Score']>0, "多頭格局", "空頭/盤整")
    df['Recommended_Position'] = ((df['Alpha_Score'] + 100) / 2).clip(0, 100)
    return df


# ==========================================
# 6. 主儀表板繪製 (Updated)
# ==========================================
def generate_market_analysis(df, margin_df, short_df):
    """
    根據當前數據生成前瞻性市場分析報告 (HTML 版本)
    特色：使用 HTML/CSS 進行顏色強調，移除 Markdown 符號。
    配色：台股邏輯 (紅=多/買/強，綠=空/賣/弱，黃=中性/警示)
    """
    if df.empty: return "<p>無足夠數據進行分析</p>"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. 提取關鍵數據
    close = last['Close']
    ma20 = last['MA20'] if 'MA20' in last else 0
    ma60 = last['MA60'] if 'MA60' in last else 0
    rsi = last['RSI']
    vix = last['VIX']
    score = last['Alpha_Score']
    ma20_slope = last['MA20'] - prev['MA20']
    
    # 2. 判定市場體制
    bias_60 = (close - ma60) / ma60 if ma60 != 0 else 0
    is_panic = (vix > 25) or (rsi < 30) or (bias_60 < -0.10)
    
    html_content = ""
    
    # 定義顏色常數
    C_RED = "#ff5252"   # 紅色 (多/買/強)
    C_GREEN = "#69f0ae" # 綠色 (空/賣/弱)
    C_WARN = "#ffd740"  # 黃色 (警示)
    C_TEXT = "#e0e0e0"  # 一般文字
    
    # --- A. 體制診斷與核心策略 ---
    if is_panic:
        # 標題 (綠色背景或邊框代表空頭體制，但如果是機會則用紅色字強調)
        status_html = f"<h3 style='color: {C_GREEN}; border-bottom: 2px solid {C_GREEN}; padding-bottom: 5px;'>🐻 空頭/恐慌體制 (Panic Regime)</h3>"
        desc_html = f"<p style='color: {C_TEXT};'>市場處於高波動與非理性拋售階段。此時傳統支撐線易失效，需關注乖離率收斂。</p>"
        
        if score > 20:
            # 恐慌中的黃金坑 -> 紅色強力建議
            strategy_html = f"""
            <div style='background-color: rgba(255, 82, 82, 0.1); padding: 10px; border-left: 4px solid {C_RED}; border-radius: 4px;'>
                <span style='color: {C_RED}; font-weight: bold; font-size: 1.1em;'>💡 前瞻建議：危機入市 (黃金坑)</span><br>
                <span style='color: {C_TEXT};'>數據顯示超跌訊號浮現。建議<span style='color: {C_RED}; font-weight: bold;'>分批逆勢建倉</span>，目標搶反彈，但需嚴設資金控管。</span>
            </div>
            """
        else:
            # 恐慌且未止跌 -> 綠色避險建議
            strategy_html = f"""
            <div style='background-color: rgba(105, 240, 174, 0.1); padding: 10px; border-left: 4px solid {C_GREEN}; border-radius: 4px;'>
                <span style='color: {C_GREEN}; font-weight: bold; font-size: 1.1em;'>🛡️ 前瞻建議：保守避險</span><br>
                <span style='color: {C_TEXT};'>跌勢未止且尚未出現足夠的清洗訊號。建議<span style='color: {C_GREEN}; font-weight: bold;'>保留現金</span>，靜待 VIX 見頂轉折。</span>
            </div>
            """
    else:
        status_html = f"<h3 style='color: {C_RED}; border-bottom: 2px solid {C_RED}; padding-bottom: 5px;'>🐂 多頭/正常體制 (Normal Regime)</h3>"
        desc_html = f"<p style='color: {C_TEXT};'>市場處於理性波動階段，股價沿趨勢線 (均線) 運行。</p>"
        
        if close > ma20 and ma20_slope > 0:
            strategy_html = f"""
            <div style='background-color: rgba(255, 82, 82, 0.1); padding: 10px; border-left: 4px solid {C_RED}; border-radius: 4px;'>
                <span style='color: {C_RED}; font-weight: bold; font-size: 1.1em;'>🚀 前瞻建議：順勢操作</span><br>
                <span style='color: {C_TEXT};'>均線呈多頭排列，趨勢穩健。操作應<span style='color: {C_RED}; font-weight: bold;'>順勢而為</span>，遇月線回測不破為最佳加碼點。</span>
            </div>
            """
        elif close < ma20:
            strategy_html = f"""
            <div style='background-color: rgba(255, 215, 64, 0.1); padding: 10px; border-left: 4px solid {C_WARN}; border-radius: 4px;'>
                <span style='color: {C_WARN}; font-weight: bold; font-size: 1.1em;'>⚠️ 前瞻建議：區間防禦</span><br>
                <span style='color: {C_TEXT};'>短期動能轉弱，跌破月線。建議<span style='color: {C_WARN};'>縮減短線多單</span>，提防回測季線。</span>
            </div>
            """
        else:
            strategy_html = f"""
            <div style='padding: 10px; border-left: 4px solid gray; border-radius: 4px;'>
                <span style='color: gray; font-weight: bold; font-size: 1.1em;'>👀 前瞻建議：區間震盪</span><br>
                <span style='color: {C_TEXT};'>趨勢不明顯，建議採取區間低買高賣策略，不宜追價。</span>
            </div>
            """

    html_content += status_html + desc_html + strategy_html + "<br>"

    # --- B. 關鍵指標解析 (使用 List 呈現) ---
    html_content += f"<h4 style='color: {C_TEXT}; margin-top: 10px;'>📊 關鍵指標解析</h4><ul style='color: {C_TEXT};'>"

    # 1. VIX
    vix_text = f"<li><b>恐慌指數 (VIX: {vix:.2f})</b>："
    if vix > 30:
        vix_text += f"<span style='color: {C_RED}; font-weight: bold;'>處於極端高檔</span>。歷史統計顯示，短線極高機率出現<span style='color: {C_RED};'>報復性反彈</span>。</li>"
    elif vix > 20 and (last['VIX'] > prev['VIX']):
        vix_text += f"<span style='color: {C_WARN};'>持續攀升中</span>，避險情緒增溫。不宜過度樂觀。</li>"
    elif vix < 15:
        vix_text += f"<span style='color: {C_GREEN};'>處於低檔安逸區</span>。需提防市場過度樂觀引發的修正。</li>"
    else:
        vix_text += "處於正常波動區間。</li>"
    html_content += vix_text

    # 2. RSI
    rsi_text = f"<li><b>動能指標 (RSI: {rsi:.1f})</b>："
    if rsi < 25:
        rsi_text += f"<span style='color: {C_RED}; font-weight: bold;'>進入嚴重超賣區</span> (鈍化)。若出現底背離，將是強烈的<span style='color: {C_RED};'>止跌訊號</span>。</li>"
    elif rsi > 75:
        rsi_text += f"<span style='color: {C_GREEN};'>進入過熱區</span>。若量能不繼，需提防高檔假突破。</li>"
    elif 45 <= rsi <= 55:
        rsi_text += "動能中性，多空力道均衡。</li>"
    else:
        rsi_text += "動能維持正常。</li>"
    html_content += rsi_text

    # --- C. 籌碼結構 ---
    if not margin_df.empty and not short_df.empty:
        try:
            m_curr = margin_df['TodayBalance'].iloc[-1]
            m_prev = margin_df['TodayBalance'].iloc[-5]
            p_chg = (close - df['Close'].iloc[-5]) / df['Close'].iloc[-5]
            m_chg = (m_curr - m_prev) / m_prev
            
            chip_text = "<li><b>籌碼結構</b>："
            if p_chg < -0.05 and m_chg < -0.03:
                chip_text += f"<span style='color: {C_RED}; font-weight: bold;'>📉 清洗浮額 (Washout)</span> - 融資大減，籌碼安定，<span style='color: {C_RED};'>有利於築底</span>。</li>"
            elif p_chg < -0.05 and m_chg > 0.01:
                chip_text += f"<span style='color: {C_GREEN}; font-weight: bold;'>⚠️ 融資套牢</span> - 散戶接刀，上檔賣壓重，<span style='color: {C_GREEN};'>反彈空間有限</span>。</li>"
            elif p_chg > 0.05 and m_chg > 0.02:
                chip_text += f"<span style='color: {C_WARN};'>🔥 散戶追價</span> - 過熱訊號，留意主力出貨。</li>"
            else:
                chip_text += "資券變化在正常範圍內。</li>"
            html_content += chip_text
        except: pass
        
    html_content += "</ul>"

    return html_content

def draw_market_dashboard(market_df, start_date, end_date):
    """
    繪製總體市場儀表板：Metrics、HTML 前瞻分析、Plotly 圖表
    """
    st.markdown("### 🌍 總體市場戰情 (Macro)")
    target_start = pd.to_datetime(start_date)
    plot_df = market_df[market_df['Date'] >= target_start].copy()
    
    if plot_df.empty: 
        st.error("無大盤數據")
        return
    
    # =========================================================
    # 1. 資料準備
    # =========================================================
    if 'Market_RSI' in plot_df.columns: plot_df['RSI'] = plot_df['Market_RSI']
    else: plot_df['RSI'] = 50 

    if 'Market_MA20' in plot_df.columns: plot_df['MA20'] = plot_df['Market_MA20']
    if 'Market_MA60' in plot_df.columns: plot_df['MA60'] = plot_df['Market_MA60']

    if 'Volume' in plot_df.columns:
        plot_df['Vol_MA20'] = plot_df['Volume'].rolling(20).mean()

    # =========================================================
    # 2. 籌碼數據
    # =========================================================
    margin_df_raw = get_margin_data(start_date.strftime('%Y-%m-%d'))
    margin_df = pd.DataFrame(); short_df = pd.DataFrame()
    if not margin_df_raw.empty:
        sliced = margin_df_raw[(margin_df_raw['date'] >= target_start) & (margin_df_raw['date'] <= pd.to_datetime(end_date))]
        margin_df = sliced[sliced['name'] == 'MarginPurchaseMoney']
        short_df = sliced[sliced['name'] == 'ShortSale']
    
    # =========================================================
    # 3. 核心運算
    # =========================================================
    plot_df = calculate_alpha_score(plot_df, margin_df, short_df)
    
    last = plot_df.iloc[-1]
    score = last['Alpha_Score']
    vix = last['VIX']
    close = last['Close']
    ma60 = last['MA60'] if 'MA60' in last else close
    
    # 判斷體制 (用於 Metrics 標籤)
    bias = (close - ma60) / ma60
    is_panic_regime = (vix > 25) or (last['RSI'] < 30) or (bias < -0.10)
    regime_label = "🐻 空頭/恐慌體制" if is_panic_regime else "🐂 多頭/正常體制"

    # 生成 Alpha Score 評語
    txt = "中性觀望"; c_score = "gray"
    if score >= 60: 
        txt = "💎 危機入市" if is_panic_regime else "🚀 強力趨勢買進"
        c_score = "#ff5252" # 紅
    elif score >= 20: 
        txt = "分批承接" if is_panic_regime else "偏多操作"
        c_score = "#ff8a80" # 淺紅
    elif score <= -60: 
        txt = "崩盤迴避" if is_panic_regime else "強力賣出"
        c_score = "#69f0ae" # 綠
    elif score <= -20: 
        txt = "保守觀望" if is_panic_regime else "偏空調節"
        c_score = "#b9f6ca" # 淺綠

    vix_st = "極度恐慌" if vix>30 else ("恐慌警戒" if vix>20 else ("樂觀貪婪" if vix<15 else "正常波動"))

    # =========================================================
    # 4. 顯示 Metrics
    # =========================================================
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("加權指數 / 體制", f"{last['Close']:.0f}", regime_label, delta_color="off")
    c2.metric("市場情緒 (RSI)", f"{last['Market_RSI']:.1f}", "區間: 0~100", delta_color="off")
    c3.metric("恐慌指數 (VIX)", f"{vix:.2f}", vix_st, delta_color="inverse" if vix > 25 else "off")
    
    c4.markdown(
        f"""
        <div style="background-color: #262730; border: 1px solid #444; border-radius: 5px; padding: 5px 10px; text-align: center;">
            <div style="font-size: 0.8rem; color: #ccc;">買賣評等 (Alpha)</div>
            <div style="font-size: 1.5rem; font-weight: bold; color: {c_score};">{score:.0f} 分</div>
            <div style="font-size: 0.9rem; color: {c_score};">{txt}</div>
        </div>
        """, 
        unsafe_allow_html=True
    )

    # =========================================================
    # 5. [修改] 顯示 HTML 前瞻分析報告
    # =========================================================
    st.write("")
    st.markdown("### 📋 AI 戰情室前瞻分析")
    
    # 取得 HTML 字串
    analysis_html = generate_market_analysis(plot_df, margin_df, short_df)
    
    # 直接渲染 HTML
    with st.container():
        st.markdown(analysis_html, unsafe_allow_html=True)

    # =========================================================
    # 6. Plotly 圖表
    # =========================================================
    st.write("")
    fig = make_subplots(rows=8, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                        row_heights=[0.3, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                        subplot_titles=("加權指數", "買賣評等 (Alpha Score)", "籌碼能量 (OBV)", "動能指標 (RSI)", "恐慌指數 (VIX)", "建議持股水位 (%)", "融資餘額", "融券餘額"))
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Close'], name='收盤價', line=dict(color='white')), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['MA20'], name='月線', line=dict(color='yellow', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['MA60'], name='季線', line=dict(color='rgba(255, 255, 255, 0.5)', width=1)), row=1, col=1)
    
    colors_score = ['#ff5252' if v > 0 else '#69f0ae' for v in plot_df['Alpha_Score']]
    fig.add_trace(go.Bar(x=plot_df['Date'], y=plot_df['Alpha_Score'], name='評等', marker_color=colors_score), row=2, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['OBV'], name='OBV', line=dict(color='orange')), row=3, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['RSI'], name='RSI', line=dict(color='cyan')), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['VIX'], name='VIX', line=dict(color='#ab47bc')), row=5, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=25, y1=25, line=dict(color="red", dash="dash"), row=5, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Recommended_Position'], name='持股%', line=dict(color='#00e676'), fill='tozeroy'), row=6, col=1)
    
    if not margin_df.empty: fig.add_trace(go.Scatter(x=margin_df['date'], y=margin_df['TodayBalance'], name='融資', line=dict(color='#ef5350'), fill='tozeroy'), row=7, col=1)
    if not short_df.empty: fig.add_trace(go.Scatter(x=short_df['date'], y=short_df['TodayBalance'], name='融券', line=dict(color='#26a69a'), fill='tozeroy'), row=8, col=1)

    fig.update_xaxes(range=[start_date, end_date])
    fig.update_yaxes(side='right')
    fig.update_yaxes(range=[-110, 110], row=2, col=1, side='right')
    fig.update_yaxes(range=[0, 100], row=6, col=1, side='right')
    fig.update_layout(height=1600, template="plotly_dark", margin=dict(l=50, r=50, t=60, b=40), hovermode="x unified", showlegend=False)
    
    st.plotly_chart(fig, use_container_width=True)


def send_analysis_email(df, market_analysis_text):
    if df.empty: return False

    tw = pytz.timezone('Asia/Taipei')
    now_tw = datetime.now(tw)
    subject = f"📊 持股評分變動通知 - {now_tw.strftime('%H:%M')}"
    
    email_df = df.copy()
    
    # [修正] 確保收盤價是字串格式，避免 float 格式化錯誤
    if "收盤價" in email_df.columns:
        email_df["收盤價"] = pd.to_numeric(email_df["收盤價"], errors='coerce').fillna(0)
        email_df["收盤價"] = email_df["收盤價"].apply(lambda x: f"{x:,.2f}")

    # [修正] 動態抓取評分欄位 (優先抓 '分數變動'，沒有則抓 '綜合評分')
    target_score_col = "分數變動" if "分數變動" in email_df.columns else "綜合評分"
    
    # [修正] 確保選取的欄位真的存在，避免 KeyError
    cols_to_check = ["代號", "名稱", "收盤價", target_score_col, "AI 建議"]
    final_cols = [c for c in cols_to_check if c in email_df.columns]
    
    html_table = email_df[final_cols].to_html(
        index=False, 
        classes='table', 
        border=1, 
        justify='center',
        escape=False 
    )
 
    # 優化表格樣式：將表頭背景設為深色，文字置中
    html_table = html_table.replace('<table border="1" class="dataframe table">', '<table style="border-collapse: collapse; width: 100%; font-family: Arial, sans-serif;">')
    html_table = html_table.replace('<th>', '<th style="background-color: #f2f2f2; padding: 8px; text-align: center; border: 1px solid #ddd;">')
    html_table = html_table.replace('<td>', '<td style="padding: 8px; text-align: center; border: 1px solid #ddd;">')

    # 組合 Email 內文
    email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #333;">🔔 持股評分變動通知</h2>
        <p>系統偵測到您的持股組合出現變化，詳細數據如下：</p>
        <p>時間：{now_tw.strftime('%Y-%m-%d %H:%M:%S')} (Taipei)</p>
        <hr>
        <h3>📊 持股最新評級</h3>
        {html_table}
        <br>
        <h3>📋 AI 市場前瞻</h3>
        <div style='background-color: #f8f9fa; padding: 15px; border-left: 5px solid #007bff; border-radius: 4px;'>
            {market_analysis_text}
        </div>
        <br>
        <p style="font-size: 12px; color: #888;"><i>本信件由 Quant Pro v6.0 自動觸發，請勿直接回信。</i></p>
    </body>
    </html>
    """

    # 2. 執行發送
    try:
        msg = MIMEMultipart()
        msg['From'] = SENDER_EMAIL
        msg['To'] = RECEIVER_EMAIL
        msg['Subject'] = Header(subject, 'utf-8')
        msg.attach(MIMEText(email_body, 'html', 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print("✅ Email 發送成功！")
        return True
    except Exception as e:
        print(f"❌ Email 發送失敗: {e}")
        return False
    


# ==========================================
# 前端介面
# ==========================================
with st.sidebar:
    st.title("⚔️ 機構戰情室")
    
    # ==========================================
    # [修正版] 登入系統 (修正 Cookie 寫入問題)
    # ==========================================
    
    # 1. 嘗試從 Cookie 獲取使用者
    # 注意：get_all() 通常比 get() 更穩定，我們改抓全部再取值
    cookies = cookie_manager.get_all()
    cookie_user = cookies.get("invest_user") if cookies else None
    
    # 初始化 Session
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['username'] = ''

    # 自動登入邏輯：如果 Cookie 有值，且 Session 還沒登入 -> 同步狀態
    if cookie_user and not st.session_state['logged_in']:
        st.session_state['logged_in'] = True
        st.session_state['username'] = cookie_user

    # 2. 介面顯示
    if not st.session_state['logged_in']:
        st.info("🔒 請登入以啟用雲端儲存")
        choice = st.selectbox("功能", ["登入", "註冊新帳號"])
        
        user = st.text_input("帳號")
        passwd = st.text_input("密碼", type='password')
        
        if choice == "登入":
            if st.button("登入"):
                if login_user(user, passwd):
                    # A. 設定 Session (讓介面當下立刻反應)
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    
                    # B. 寫入 Cookie
                    # 注意：這裡不呼叫 st.rerun()！
                    # 套件會在寫入完成後自動刷新頁面，手動 rerun 會打斷寫入
                    expires = datetime.now() + timedelta(days=30)
                    cookie_manager.set("invest_user", user, expires_at=expires)
                    
                    st.success("登入成功！(正在寫入記憶...)")
                else:
                    st.error("帳號或密碼錯誤")
        else: # 註冊
            if st.button("建立帳號"):
                if add_user(user, passwd):
                    st.success("註冊成功！請切換至登入頁面。")
                else:
                    st.error("此帳號已被使用")
        
        st.warning("訪客模式：資料僅暫存，刷新後消失。")
        st.markdown("---")
        
    else:
        # 3. 已登入狀態
        st.success(f"👤 歡迎, {st.session_state['username']}")
        
        if st.button("登出"):
            # A. 刪除 Cookie (套件會自動刷新)
            cookie_manager.delete("invest_user")
            
            # B. 清除 Session
            st.session_state['logged_in'] = False
            st.session_state['username'] = ''
            # 這裡也不需要 rerun，delete 會觸發刷新
            
        st.markdown("---")
        
    # [修改] 加入 "💼 持股健診與建議"
    page = st.radio("導航", ["🌍 市場總覽 (Macro)", "📊 單股深度分析", "🚀 科技股掃描", "💼 持股健診與建議", "📋 全台股清單", "🧪 策略實驗室"])

    today = datetime.today()
    # 設定台北時區
    tw_tz = pytz.timezone('Asia/Taipei')
    today = datetime.now(tw_tz).date() # 強制使用台北時間的今天
    st.markdown("---")
with st.expander("⚙️ 參數與日期設定", expanded=False):
            today = datetime.now(tw_tz).date()
            start_date = st.date_input("開始", value=today - timedelta(days=365*2+1))
            end_date = st.date_input("結束", value=today)
            
            st.caption("交易成本設定")
            fee_input = st.number_input("手續費(%)", value=0.1425, step=0.01) / 100
            tax_input = st.number_input("交易稅(%)", value=0.3000, step=0.01) / 100
            
            # [新增] 策略開關
            st.caption("策略組態")
# ... (原本的籌碼開關)
            enable_chip_strategy = st.toggle("啟用籌碼佈局策略 (Strategy C)", value=True)
            
            # [新增] 強制出場開關
            enable_strict_bear_exit = st.toggle("啟用「長空破月線」強制出場", value=True)
            st.caption("若關閉，則長空時僅依賴停損或趨勢轉弱出場。")

market_df = get_market_data(start_date, end_date)

# --- 頁面 1 ---
if page == "🌍 市場總覽 (Macro)":
    draw_market_dashboard(market_df, start_date, end_date)

# --- 頁面 2 (手機介面優化版): 單股深度分析 ---
elif page == "📊 單股深度分析":
    # ==================================================
    # 1. 資料準備與搜尋清單建立
    # ==================================================
    if st.session_state['all_stock_list'] is None:
        st.session_state['all_stock_list'] = get_master_stock_data()
    
    df_all = st.session_state['all_stock_list']
    
    # 建立搜尋清單 (代號 + 名稱)
    search_list = [f"{row['代號']} {row['名稱']}" for idx, row in df_all.iterrows()]
    base_search_list = [f"{k} {v}" for k, v in TW_STOCK_NAMES_STATIC.items()]
    # 排序並去重，確保順序固定
    full_search_options = sorted(list(set(search_list + base_search_list)))

    # 確保核心變數 last_ticker 有值
    if 'last_ticker' not in st.session_state:
        st.session_state['last_ticker'] = "2330"

    # ==================================================
    # 2. 定義 Callback (只負責處理邏輯變數)
    # ==================================================
    
    # 當使用者手動選取選單時
    def on_selector_change():
        selection = st.session_state['stock_selector']
        st.session_state['last_ticker'] = selection.split(" ")[0]

# 當使用者點擊按鈕時
    def on_button_click(direction):
        current_ticker = st.session_state['last_ticker']
        
        # [修正] 增加 try-except 防呆
        try:
            # 嘗試找出當前 ticker 在完整清單中的位置
            current_idx = 0
            for i, opt in enumerate(full_search_options):
                if opt.startswith(str(current_ticker)):
                    current_idx = i
                    break
        except:
            current_idx = 0 # 若發生任何錯誤，歸零
        
        # 計算新的 Index
        new_idx = (current_idx + direction) % len(full_search_options)
        new_option = full_search_options[new_idx]
        
        st.session_state['last_ticker'] = new_option.split(" ")[0]

    # ==================================================
    # 3. [核心修正] 強制介面同步 (View <-> Model Sync)
    # ==================================================
    # 在畫出選單之前，強制將選單的 State 設定為 last_ticker 對應的選項
    # 這確保了無論是按按鈕、還是外部更新，選單顯示永遠正確
    
    current_gui_option = full_search_options[0] # 預設值
    target_ticker = st.session_state['last_ticker']
    
    # 在清單中找到對應的完整字串 (例如 "2330" -> "2330 台積電")
    for opt in full_search_options:
        if opt.startswith(str(target_ticker)):
            current_gui_option = opt
            break
    
    # 強制寫入 Session State，讓 Selectbox 乖乖聽話
    st.session_state['stock_selector'] = current_gui_option

    # ==================================================
    # 4. 介面佈局
    # ==================================================
    
    # --- Row 1: 搜尋與 Go 按鈕 ---
    with st.container():
        col_search, col_run = st.columns([3, 1])
        
        with col_search:
            # 這裡我們不設 index，而是依賴上方的 st.session_state['stock_selector'] 強制同步
            st.selectbox(
                "搜尋股票 (支援代號或中文)",
                options=full_search_options,
                label_visibility="collapsed",
                key="stock_selector",
                on_change=on_selector_change # 綁定手動變更
            )
            
        with col_run:
            if st.button("Go", type="primary", use_container_width=True):
                # 強制重跑
                st.session_state['last_ticker'] = st.session_state['stock_selector'].split(" ")[0]
                st.rerun()

    # --- Row 2: 上一檔 / 下一檔 ---
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        st.button("◀ 上一檔", use_container_width=True, on_click=on_button_click, args=(-1,))

    with col_next:
        st.button("下一檔 ▶", use_container_width=True, on_click=on_button_click, args=(1,))

    # 取得最終要分析的代號
    ticker_input = st.session_state['last_ticker']

    # ==================================================
    # 3. 確保變數同步 (最後一道防線)
    # ==================================================
    # 如果使用者直接改了選單但沒按 Go，自動偵測並更新
    if st.session_state['stock_selector'].split(" ")[0] != st.session_state['last_ticker']:
         st.session_state['last_ticker'] = st.session_state['stock_selector'].split(" ")[0]

    ticker_input = st.session_state['last_ticker']

    
    if ticker_input: 
        with st.spinner(f'正在分析 {ticker_input} ...'):
            current_fee = fee_input if 'fee_input' in locals() else 0.001425
            current_tax = tax_input if 'tax_input' in locals() else 0.003
            
            # 初始化變數，防止 NameError
            final_df = None
            best_params = None
            validation_result = None
            
            # 1. 獲取資料
            raw_df, fmt_ticker = get_stock_data(ticker_input, start_date, end_date)
            name = get_stock_name(fmt_ticker)
            
            # 2. 判斷資料是否獲取成功
            if raw_df.empty:
                st.error(f"❌ 無法獲取 {ticker_input} 資料。原因可能是：\n1. 代號錯誤\n2. 該 ETF/股票剛上市，Yahoo Finance 尚未收錄\n3. 該商品無近期交易量")
            else:
                # 3. 若成功，才執行策略運算
                best_params, final_df = run_optimization(
                    raw_df, market_df, start_date, current_fee, current_tax, 
                    use_chip_strategy=enable_chip_strategy,
                    use_strict_bear_exit=enable_strict_bear_exit  # <--- 加入參數
                )
                validation_result = validate_strategy_robust(raw_df, market_df, 0.7, current_fee, current_tax)

            # 4. 顯示結果 (檢查 final_df 是否存在且不為空)
            if final_df is None or final_df.empty:
                if not raw_df.empty: # 如果有原始資料但策略跑不出結果 (極少見)
                    st.warning("⚠️ 選定區間內無足夠資料進行策略運算 (可能上市時間太短)。")
            else:
                # ... (以下顯示邏輯保持不變，直接沿用原本的程式碼即可) ...
                # 為節省篇幅，請保留您原本從 `stock_alpha_df = calculate_alpha_score(...)` 開始的後續顯示程式碼
                # 只要替換上方這段輸入控制邏輯即可
                
                # [以下為原本的代碼接續點，請確認您的代碼中有這部分]
                stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                base_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                base_log = stock_alpha_df['Score_Log'].iloc[-1]
                
                # ... (後續的 Context-Aware Adjustment 與 UI 繪圖部分完全不用動) ...
                
                # 這裡為了完整性，我將後續關鍵變數計算補上，避免您複製貼上時斷掉
                adjusted_score = base_score
                adjustment_log = ""
                current_price = final_df['Close'].iloc[-1]
                ma20 = final_df['MA20'].iloc[-1]
                ma60 = final_df['MA60'].iloc[-1]
                rsi_now = final_df['RSI'].iloc[-1]
                rsi_prev = final_df['RSI'].iloc[-2]
                last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                is_rebound_strategy = False
                if last_trade is not None:
                    buy_reason = str(last_trade['Reason'])
                    if any(x in buy_reason for x in ["反彈", "超賣", "回測", "低檔"]): is_rebound_strategy = True
                
                action, color, reason = analyze_signal(final_df)

                if action == "✊ 續抱" or action == "🚀 買進":
                    if is_rebound_strategy:
                        if current_price < ma60: 
                            adjusted_score += 15; adjustment_log += "[反彈位階修正+15]"
                        if rsi_now > rsi_prev:
                            adjusted_score += 10; adjustment_log += "[RSI翻揚+10]"
                        ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                        if current_price > ma5:
                            adjusted_score += 10; adjustment_log += "[站穩MA5+10]"
                    else:
                        if current_price > ma20 and ma20 > ma60:
                            adjusted_score += 10; adjustment_log += "[多頭排列+10]"
                        if final_df['Volume'].iloc[-1] > final_df['Vol_MA20'].iloc[-1]:
                            adjusted_score += 5; adjustment_log += "[量增+5]"

                # 限制分數範圍 (-100 ~ 100)
                final_composite_score = max(min(adjusted_score, 100), -100)
                
                # [關鍵修正] 防呆處理：如果分數是 NaN (無效值)，強制設為 0，避免 int() 報錯
                import math
                if math.isnan(final_composite_score):
                    final_composite_score = 0
                
                # 組合最終顯示日誌
                full_log_text = f"{base_log} {adjustment_log}" if base_log or adjustment_log else "無顯著特徵"
                
                # 計算其餘指標
                beta, vol, personality = calculate_stock_personality(final_df, market_df)
                hit_rate, hits, total = calculate_target_hit_rate(final_df)
                real_win_rate, real_wins, real_total, avg_pnl = calculate_realized_win_rate(final_df)
                risk_metrics = calculate_risk_metrics(final_df)
                
# ==========================================
                # UI 顯示部分 (已優化：新增現價顯示)
                # ==========================================
                
                # 1. 準備漲跌數據
                last_close = final_df['Close'].iloc[-1]
                prev_close = final_df['Close'].iloc[-2]
                price_chg = last_close - prev_close
                price_pct = (price_chg / prev_close) * 100
                
                # 2. 頂部資訊欄 (標題 + 現價)
                # 使用 columns 將版面切分為 [左: 資訊, 右: 股價]
                col_header, col_price = st.columns([3, 1])
                
                with col_header:
                    st.markdown(f"## {ticker_input} {name}")
                    # 使用不同顏色區分波動率屬性
                    vol_color = "red" if "高波動" in personality else ("green" if "低波動" in personality else "orange")
                    st.markdown(f"**策略邏輯**: `{reason}` | **波動屬性**: :{vol_color}[{personality}] ({vol})")
                
                with col_price:
                    # 顯示大字體現價
                    st.metric(
                        label="最新現價", 
                        value=f"{last_close:.2f}", 
                        delta=f"{price_chg:.2f} ({price_pct:.2f}%)",
                        delta_color="inverse"
                    )

                st.markdown("---")

                # 3. AI 評分區塊 (維持不變，僅微調版面)
                st.markdown("### 🏆 AI 綜合評分與決策依據")
                score_col, log_col = st.columns([1, 3])
                
                with score_col:
                    s_color = "normal"
                    if final_composite_score >= 60: s_color = "off" 
                    elif final_composite_score <= -20: s_color = "inverse"
                    
                    st.metric(
                        label="綜合評分 (Alpha Score)",
                        value=f"{int(final_composite_score)} 分",
                        delta=action,
                        delta_color=s_color
                    )
                
                with log_col:
                    st.info(f"**🧮 演算歷程解析：**\n\n{full_log_text}")

                # 1. 計算策略績效
                strat_mdd = calculate_mdd(final_df['Cum_Strategy'])
                strat_ret = best_params['Return'] * 100
                
                # 2. [新增] 計算 Buy & Hold (基準) 績效
                # Cum_Market 是已經計算好的市場累積權益曲線 (代表該股本身)
                bh_ret = (final_df['Cum_Market'].iloc[-1] - 1) * 100
                bh_mdd = calculate_mdd(final_df['Cum_Market'])
                
                # 判斷策略是否戰勝大盤 (用於標註顏色或差異)
                beat_market = strat_ret - bh_ret

                # ==========================================
                # 自訂指標卡片函式 (保持不變)
                # ==========================================
                def KPI_Card(col, title, value, sub_value, is_good):
                    color = "#ff5252" if is_good else "#00e676" 
                    arrow = "▲" if is_good else "▼"
                    bg_color = "rgba(255, 82, 82, 0.1)" if is_good else "rgba(0, 230, 118, 0.1)"
                    
                    col.markdown(
                        f"""
                        <div style="
                            border: 1px solid #333; 
                            border-radius: 8px; 
                            padding: 15px; 
                            background-color: #262730;
                            text-align: center;
                            height: 100%;">
                            <div style="color: #aaa; font-size: 14px; margin-bottom: 5px;">{title}</div>
                            <div style="color: {color}; font-size: 26px; font-weight: bold; margin-bottom: 5px;">
                                {value}
                            </div>
                            <div style="
                                display: inline-block;
                                background-color: {bg_color};
                                color: {color};
                                padding: 2px 8px;
                                border-radius: 4px;
                                font-size: 13px;">
                                {arrow} {sub_value}
                            </div>
                        </div>
                        """, 
                        unsafe_allow_html=True
                    )

                # 準備其他數據
                pf = risk_metrics.get('Profit_Factor', 0)
                sharpe = risk_metrics.get('Sharpe', 0)
                try: win_rate_val = float(real_win_rate.strip('%'))
                except: win_rate_val = 0
                
                # [修改] 改為 5 欄佈局
                m1, m2, m3, m4, m5 = st.columns(5)
                
                # Card 1: 策略淨報酬
                KPI_Card(
                    m1, 
                    "策略淨報酬 (Active)", 
                    f"{strat_ret:+.1f}%", 
                    f"MDD: {strat_mdd:.1f}%", 
                    is_good=(strat_ret > 0)
                )
                
                # Card 2: [新增] 買進持有
                # 這裡的 is_good 判斷：如果是正報酬顯示紅，負報酬顯示綠
                KPI_Card(
                    m2, 
                    "買進持有 (Buy & Hold)", 
                    f"{bh_ret:+.1f}%", 
                    f"MDD: {bh_mdd:.1f}%", 
                    is_good=(bh_ret > 0)
                )
                
                # Card 3: 實際勝率
                KPI_Card(
                    m3, 
                    "實際勝率 (Win Rate)", 
                    real_win_rate, 
                    f"{real_wins}勝 / {real_total}總", 
                    is_good=(win_rate_val >= 50)
                )
                
                # Card 4: 目標達成率
                KPI_Card(
                    m4, 
                    "目標達成率 (Target)", 
                    hit_rate, 
                    f"{hits}次達標 (+15%)", 
                    is_good=(hits > 0)
                )
                
                # Card 5: 盈虧因子 PF
                KPI_Card(
                    m5, 
                    "盈虧因子 (PF)", 
                    f"{pf:.2f}", 
                    f"夏普: {sharpe:.2f}", 
                    is_good=(pf > 1)
                )
                
                st.write("") 
                
                # 如果策略跑輸買進持有，給個提示
                if beat_market < 0:
                    st.caption(f"⚠️ 注意：此策略績效落後買進持有 {abs(beat_market):.1f}%，建議直接長期持有即可。")
                else:
                    st.caption(f"🎉 優異：此策略創造了 {beat_market:+.1f}% 的超額報酬 (Alpha)。")

                # [修改] 移除蒙地卡羅，只保留三個分頁
                tab1, tab2, tab3 = st.tabs(["📈 操盤決策圖", "💰 權益曲線", "🧪 有效性驗證"])
                
                # [Tab 1: K線圖] (保持不變)
                with tab1:
                    # 1. 準備數據
                    final_df['Alpha_Score'] = stock_alpha_df['Alpha_Score']
                    final_df['Alpha_Slope'] = final_df['Alpha_Score'].diff().fillna(0)

                    # 2. 建立子圖
                    fig = make_subplots(
                        rows=6, cols=1, 
                        shared_xaxes=True, 
                        vertical_spacing=0.02, 
                        row_heights=[0.35, 0.13, 0.13, 0.13, 0.13, 0.13], 
                        subplot_titles=("", "買賣評等 (Alpha Score)", "評分動能 (Alpha Slope)", "成交量", "法人籌碼 (OBV)", "相對強弱指標 (RSI)")
                    )
            
                    # --- Row 1: K線 ---
                    fig.add_trace(go.Candlestick(
                        x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                        low=final_df['Low'], close=final_df['Close'], name='K線',
                        increasing_line_color='#ef5350', decreasing_line_color='#00bfa5' 
                    ), row=1, col=1)
                    
                    # 停損基準線 (SuperTrend)
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['SuperTrend'], mode='lines', line=dict(color='yellow', width=1.5), name='停損基準線'), row=1, col=1)
                    
                    # 季線 (MA60) - 白色半透明
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線'), row=1, col=1)

                    # [新增] 半年線 (MA120) - 天藍色
                    if 'MA120' in final_df.columns:
                        fig.add_trace(go.Scatter(
                            x=final_df['Date'], 
                            y=final_df['MA120'], 
                            mode='lines', 
                            line=dict(color='#2979ff', width=1.5), 
                            name='半年線 (MA120)'
                        ), row=1, col=1)

                    # [新增] 年線 (MA240) - 紫色
                    # 使用紫色 (#e040fb) 標示年線，方便區分長期趨勢
                    if 'MA240' in final_df.columns:
                        fig.add_trace(go.Scatter(
                            x=final_df['Date'], 
                            y=final_df['MA240'], 
                            mode='lines', 
                            line=dict(color='#e040fb', width=1.5), 
                            name='年線 (MA240)'
                        ), row=1, col=1)
                    

                    # 買賣點標記
                    final_df['Buy_Y'] = final_df['Low'] * 0.92
                    final_df['Sell_Y'] = final_df['High'] * 1.08

                    def get_buy_text(sub_df):
                        return [f"<b>{int(score)}</b>" for score in sub_df['Alpha_Score']]

                    def get_sell_text(sub_df):
                        labels = []
                        for idx, row in sub_df.iterrows():
                            ret = row['Return_Label']
                            reason_str = row['Reason'].replace("觸發", "").replace("操作", "")
                            labels.append(f"{ret}<br>({reason_str})")
                        return labels

                    # 繪製買賣訊號
                    buy_trend = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('突破|回測|動能'))]
                    if not buy_trend.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_trend['Date'], y=buy_trend['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_trend), textposition="bottom center",
                            textfont=dict(color='#FFD700', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#FFD700', line=dict(width=1, color='black')), 
                            name='買進 (趨勢)', hovertext=buy_trend['Reason']
                        ), row=1, col=1)
                    
                    buy_panic = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('反彈|超賣'))]
                    if not buy_panic.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_panic['Date'], y=buy_panic['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_panic), textposition="bottom center",
                            textfont=dict(color='#00FFFF', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#00FFFF', line=dict(width=1, color='black')), 
                            name='買進 (反彈)', hovertext=buy_panic['Reason']
                        ), row=1, col=1)
                    
                    buy_chip = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('籌碼|佈局'))]
                    if not buy_chip.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_chip['Date'], y=buy_chip['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_chip), textposition="bottom center",
                            textfont=dict(color='#DDA0DD', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#DDA0DD', line=dict(width=1, color='black')), 
                            name='買進 (籌碼)', hovertext=buy_chip['Reason']
                        ), row=1, col=1)

                    sell_all = final_df[final_df['Action'] == 'Sell']
                    if not sell_all.empty:
                        fig.add_trace(go.Scatter(
                            x=sell_all['Date'], y=sell_all['Sell_Y'], mode='markers+text', 
                            text=get_sell_text(sell_all), textposition="top center",
                            textfont=dict(color='white', size=11),
                            marker=dict(symbol='triangle-down', size=14, color='#FF00FF', line=dict(width=1, color='black')), 
                            name='賣出', hovertext=sell_all['Reason']
                        ), row=1, col=1)
                    
                    # --- Row 2: Alpha Score ---
                    colors_score = ['#ef5350' if v > 0 else '#26a69a' for v in final_df['Alpha_Score']]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Alpha_Score'], name='Alpha Score', marker_color=colors_score), row=2, col=1)
                    fig.update_yaxes(range=[-110, 110], row=2, col=1)

                    # --- Row 3: Alpha Slope ---
                    colors_slope = ['#ef5350' if v > 0 else ('#26a69a' if v < 0 else 'gray') for v in final_df['Alpha_Slope']]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Alpha_Slope'], name='Alpha Slope', marker_color=colors_slope), row=3, col=1)
                    fig.add_hline(y=0, line_width=1, line_color="gray", row=3, col=1)

                    # --- Row 4: 成交量 (改為張數) ---
                    colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'] / 1000, marker_color=colors_vol, name='成交量(張)'), row=4, col=1)
                    
                    # --- Row 5: OBV ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=5, col=1)
                    
                    # --- Row 6: RSI ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=6, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=6, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=6, col=1)
                    
                    # Layout
                    fig.update_layout(height=1200, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=20, r=40, t=30, b=20),
                                            legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1))
                    fig.update_yaxes(side='right')
                    st.plotly_chart(fig, use_container_width=True)

                # [Tab 2: 權益曲線] (保持不變)
                with tab2:
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Market'], name='買進持有 (Benchmark)', line=dict(color='gray', dash='dot')))
                    fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Strategy'], name='AI 策略淨值', line=dict(color='#ef5350', width=2), fill='tozeroy'))
                    
                    buy_pts = final_df[final_df['Action']=='Buy']
                    sell_pts = final_df[final_df['Action']=='Sell']
                    if not buy_pts.empty:
                        fig_c.add_trace(go.Scatter(x=buy_pts['Date'], y=buy_pts['Cum_Strategy'], mode='markers', marker=dict(symbol='triangle-up', size=10, color='#FFD700'), name='買進'))
                    if not sell_pts.empty:
                        fig_c.add_trace(go.Scatter(x=sell_pts['Date'], y=sell_pts['Cum_Strategy'], mode='markers', marker=dict(symbol='triangle-down', size=10, color='#FF00FF'), name='賣出'))
                        
                    fig_c.update_layout(template="plotly_dark", height=450, title="策略 vs 買持 績效對決", margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_c, use_container_width=True)
                    
                # [Tab 3: 因子有效性驗證] (原 Tab 4 移至此)
                with tab3:
                    st.markdown("### 🧪 Alpha Score 預測力檢驗 (IC 分析)")
                    st.caption("此頁面分析歷史數據中「Alpha Score」與「未來股價表現」的統計相關性，驗證 AI 評分的預測能力。")
                    
                    if final_df is not None and len(final_df) > 60:
                        val_df = final_df.copy()
                        val_df['Ret_1d'] = val_df['Close'].shift(-1) / val_df['Close'] - 1
                        val_df['Ret_5d'] = val_df['Close'].shift(-5) / val_df['Close'] - 1
                        val_df = val_df.dropna(subset=['Ret_1d', 'Ret_5d'])
                        
                        ic_1d = val_df['Alpha_Score'].corr(val_df['Ret_1d'])
                        ic_5d = val_df['Alpha_Score'].corr(val_df['Ret_5d'])
                        
                        c_ic1, c_ic2, c_desc = st.columns([1, 1, 2])
                        def get_ic_color(val):
                            if val > 0.05: return "normal"
                            if val < -0.05: return "inverse"
                            return "off"
                            
                        c_ic1.metric("1日 IC (預測隔日)", f"{ic_1d:.3f}", "正值=有效", delta_color=get_ic_color(ic_1d))
                        c_ic2.metric("5日 IC (預測一週)", f"{ic_5d:.3f}", "正值=有效", delta_color=get_ic_color(ic_5d))
                        
                        with c_desc:
                            if ic_5d > 0.1: st.success("✅ **高顯著性**：Alpha Score 對未來一週股價有極強的預測力。")
                            elif ic_5d > 0.02: st.info("👌 **有效**：分數越高，股價傾向於上漲，具參考價值。")
                            else: st.warning("⚠️ **隨機漫步**：當前分數與未來漲跌相關性低 (可能是震盪盤)。")
                        
                        st.markdown("---")
                        
                        bins = [-110, -50, -10, 10, 50, 110]
                        labels = ['極弱勢 (<-50)', '弱勢 (-50~-10)', '盤整 (-10~10)', '強勢 (10~50)', '極強勢 (>50)']
                        val_df['Score_Group'] = pd.cut(val_df['Alpha_Score'], bins=bins, labels=labels)
                        
                        group_stats = val_df.groupby('Score_Group', observed=True).agg({'Ret_5d': ['mean', 'count'], 'Ret_1d': 'mean'})
                        group_stats.columns = ['Avg_Ret_5d', 'Count', 'Avg_Ret_1d']
                        win_rates = val_df.groupby('Score_Group', observed=True)['Ret_5d'].apply(lambda x: (x > 0).mean() * 100)
                        
                        st.markdown("#### 📊 分數區間 vs 未來一週表現")
                        fig_bucket = make_subplots(specs=[[{"secondary_y": True}]])
                        colors_bar = ['#ef5350' if v > 0 else '#00e676' for v in group_stats['Avg_Ret_5d']]
                        
                        fig_bucket.add_trace(go.Bar(
                            x=group_stats.index, y=group_stats['Avg_Ret_5d'] * 100,
                            name='未來5日平均漲跌(%)', marker_color=colors_bar, opacity=0.7
                        ), secondary_y=False)
                        
                        fig_bucket.add_trace(go.Scatter(
                            x=win_rates.index, y=win_rates, name='上漲機率(%)',
                            mode='lines+markers', line=dict(color='yellow', width=3), marker=dict(size=8)
                        ), secondary_y=True)
                        
                        fig_bucket.update_yaxes(title_text="平均漲跌幅 (%)", secondary_y=False)
                        fig_bucket.update_yaxes(title_text="上漲機率 (%)", range=[0, 100], secondary_y=True)
                        fig_bucket.update_layout(template="plotly_dark", height=400, legend=dict(orientation="h", y=1.1), margin=dict(l=20, r=20, t=40, b=20))
                        st.plotly_chart(fig_bucket, use_container_width=True)
                        
                        st.markdown("#### 📋 詳細統計數據")
                        display_table = pd.DataFrame({
                            '樣本數': group_stats['Count'],
                            '平均漲幅(5日)': (group_stats['Avg_Ret_5d']*100).map('{:+.2f}%'.format),
                            '上漲機率': win_rates.map('{:.1f}%'.format),
                            '期望值': (group_stats['Avg_Ret_5d'] * 100).map('{:+.2f}%'.format)
                        })
                        st.dataframe(display_table.T, use_container_width=True)
                    else:
                        st.warning("數據不足，無法進行統計驗證。")



# --- 頁面 3: 戰略雷達 (含資金流向與擴充清單) ---
elif page == "🚀 科技股掃描":
    st.markdown(f"### 🚀 戰略雷達：全市場機會掃描")
    st.caption("AI 全檢測與資金流向分析。系統將自動計算 Alpha Score 並排序潛力標的。")

    # ==========================================
    # 1. 定義擴充清單 (Sector Presets)
    # ==========================================

    # ==========================================
    # 2. 介面控制 (修正版：雙向綁定)
    # ==========================================
    
    # 初始化 session state (若無則預設載入熱門50)
    if 'scan_list_input' not in st.session_state:
        st.session_state['scan_list_input'] = "\n".join(PRESET_LISTS["🔥 台股熱門 50 (權值)"])

    col_sel, col_btn = st.columns([3, 1])
    
    with col_sel:
        # 下拉選單 (綁定 key 以便 callback 讀取)
        st.selectbox("📂 選擇掃描板塊", list(PRESET_LISTS.keys()), key="sector_selector")
    
    with col_btn:
        st.write("") # Layout spacing
        
        # 定義載入清單的 Callback
        def load_preset_callback():
            # 從下拉選單的 key 讀取目前選項
            sector = st.session_state['sector_selector']
            # 更新 text_area 綁定的 key
            st.session_state['scan_list_input'] = "\n".join(PRESET_LISTS[sector])

        # 按鈕綁定 callback
        st.button("📥 載入清單", on_click=load_preset_callback)

    # [關鍵修正]：
    # 1. 移除 value=... 參數 (因為已經設了 key，Streamlit 會自動讀取 state)
    # 2. 設定 key="scan_list_input"，這樣您手動打字時，session_state 會同步更新
    st.text_area(
        "掃描清單 (可手動增減，每行一支)", 
        height=150, 
        key="scan_list_input" 
    )
    
    # 掃描控制按鈕區
    col_go, col_stop = st.columns([1, 1])
    
    # 定義啟動與停止 Callback
    def start_scan_callback():
        st.session_state['is_scanning'] = True
        st.session_state['stop_scan'] = False
        # [新增] 重置斷點狀態，確保是「全新」的掃描
        st.session_state['scan_current_index'] = 0
        st.session_state['scan_temp_results'] = []

    def stop_scan_callback():
        st.session_state['is_scanning'] = False
        st.session_state['stop_scan'] = True

    with col_go:
        st.button("🔥 啟動戰略掃描", type="primary", use_container_width=True, on_click=start_scan_callback)
        
    with col_stop:
        st.button("🛑 強制停止", use_container_width=True, on_click=stop_scan_callback)

    if 'is_scanning' not in st.session_state:
        st.session_state['is_scanning'] = False

    # ==========================================
    # 3. 執行掃描 (修復資料儲存邏輯)
    # ==========================================
    if st.session_state['is_scanning']:
        
        raw_list = st.session_state.get('scan_list_input', "")
        current_sector = st.session_state.get('sector_selector', '自訂清單')
        
        tickers = [t.strip().replace(',','') for t in raw_list.split('\n') if t.strip()]
        tickers = list(set(tickers)) 
        
        if not tickers:
            st.warning("⚠️ 清單為空，請輸入代號。")
            st.session_state['is_scanning'] = False
        else:
            if len(tickers) > 1000:
                st.warning(f"⚠️ 標的數量 ({len(tickers)}) 過多，建議分批執行。")
            
            # 初始化斷點與暫存
            if 'scan_current_index' not in st.session_state:
                st.session_state['scan_current_index'] = 0
            
            if 'scan_temp_results' not in st.session_state:
                st.session_state['scan_temp_results'] = []

            start_idx = st.session_state['scan_current_index']
            remaining_tickers = tickers[start_idx:]
            
            result_container = st.container()
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            import time 
            
            # 定義一個內部函式來將暫存結果轉正 (避免重複代碼)
            def flush_results_to_dataframe():
                temp_res = st.session_state['scan_temp_results']
                if temp_res:
                    full_df = pd.DataFrame(temp_res)
                    # 排序
                    top_10_df = full_df.sort_values(by=['Alpha_Score', '回測報酬'], ascending=[False, False]).head(10)
                    top_10_df.index = range(1, len(top_10_df) + 1)
                    
                    st.session_state['scan_results_df'] = full_df
                    st.session_state['top_10_df'] = top_10_df
                else:
                    # 若無結果，確保它是空的 DataFrame 而不是 None/List
                    st.session_state['scan_results_df'] = pd.DataFrame()
                    st.session_state['top_10_df'] = pd.DataFrame()

            # 若已經全部掃完
            if not remaining_tickers and start_idx > 0:
                pass 
            else:
                for loop_idx, ticker in enumerate(remaining_tickers):
                    current_real_idx = start_idx + loop_idx
                    
                    # [關鍵修正] 中止時，立刻將目前的暫存結果轉為 DataFrame
                    if st.session_state.get('stop_scan'):
                        status_text.warning(f"🛑 掃描已由使用者中止。")
                        st.session_state['is_scanning'] = False 
                        flush_results_to_dataframe() # <--- 這裡確保資料被儲存
                        break
                        
                    status_text.text(f"AI 正在運算 ({current_real_idx+1}/{len(tickers)}): {ticker} ...")
                    progress_bar.progress((current_real_idx + 1) / len(tickers))
                    
                    try:
                        time.sleep(0.05) 
                        raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
                        
                        if raw_df.empty or len(raw_df) < 60: 
                            st.session_state['scan_current_index'] = current_real_idx + 1
                            continue
                            
                        best_params, final_df = run_optimization(
                            raw_df, market_df, start_date, fee_rate=fee_input, tax_rate=tax_input,
                            use_chip_strategy=enable_chip_strategy,
                            use_strict_bear_exit=enable_strict_bear_exit  # <--- 加入參數
                        )
                        
                        
                        if final_df is not None and not final_df.empty:
                            # 1. 取得基礎 Alpha Score
                            stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                            base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                            base_log = stock_alpha_df['Score_Log'].iloc[-1]
                            
                            action, color, tech_reason = analyze_signal(final_df)
                            name = get_stock_name(fmt_ticker)
                            
                            # ==========================================
                            # 2. [關鍵修正] 完整情境微調 (與 Page 2 完全同步)
                            # ==========================================
                            final_score = base_alpha_score
                            adjustment_log = []
                            
                            # 準備數據
                            current_price = final_df['Close'].iloc[-1]
                            ma20 = final_df['MA20'].iloc[-1]
                            ma60 = final_df['MA60'].iloc[-1]
                            vol_now = final_df['Volume'].iloc[-1]
                            vol_ma = final_df['Vol_MA20'].iloc[-1]
                            
                            # 判斷是否為反彈策略
                            last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                            is_rebound = False
                            if last_trade is not None:
                                buy_reason = str(last_trade['Reason'])
                                if any(x in buy_reason for x in ["反彈", "超賣", "回測", "籌碼"]): is_rebound = True
                            
                            # 針對「續抱」或「買進」狀態進行加分
                            if action == "✊ 續抱" or action == "🚀 買進":
                                if is_rebound:
                                    # --- 情境 A: 反彈策略 (抄底) ---
                                    if current_price < ma60: 
                                        final_score += 15; adjustment_log.append("反彈位階+15")
                                    
                                    ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                                    if current_price > ma5: 
                                        final_score += 10; adjustment_log.append("站穩MA5+10")
                                    
                                    rsi_now = final_df['RSI'].iloc[-1]
                                    rsi_prev = final_df['RSI'].iloc[-2]
                                    if rsi_now > rsi_prev: 
                                        final_score += 10; adjustment_log.append("動能翻揚+10")
                                    elif rsi_now < 30:
                                        final_score += 5; adjustment_log.append("低檔鈍化+5")
                                else:
                                    # --- 情境 B: 順勢策略 (追價) ---
                                    # [修正點] 補回 Page 2 有的加分項目
                                    if current_price > ma20 and ma20 > ma60:
                                        final_score += 10; adjustment_log.append("多頭排列+10")
                                        
                                    if vol_now > vol_ma:
                                        final_score += 5; adjustment_log.append("量增+5")
                                        
                                    # 高檔爆量滯漲扣分 (風險提示)
                                    if vol_now > vol_ma * 2.5 and final_df['Close'].pct_change().iloc[-1] < 0.005:
                                        final_score -= 15; adjustment_log.append("高檔爆量滯漲-15")

                            # 限制分數範圍
                            final_score = max(min(final_score, 100), -100)
                            
                            display_reason = base_log
                            if adjustment_log: display_reason += f" ➜ 修正: {','.join(adjustment_log)}"
                            
                            # 3. 存入結果
                            prev_price = final_df['Close'].iloc[-2]
                            price_chg_pct = (current_price - prev_price) / prev_price
                            turnover = current_price * vol_now

                            res_item = {
                                "代號": fmt_ticker.split('.')[0], 
                                "名稱": name, 
                                "建議": action,
                                "收盤價": current_price,
                                "漲跌幅": price_chg_pct,
                                "成交金額": turnover,
                                "Alpha_Score": int(final_score), 
                                "計算過程": display_reason,
                                "回測報酬": best_params['Return'],
                                "板塊": current_sector
                            }
                            st.session_state['scan_temp_results'].append(res_item)

                    except Exception as e:
                        pass
                    
                    # 更新斷點
                    st.session_state['scan_current_index'] = current_real_idx + 1
                    
                    # [可選] 每掃 5 支就存一次檔，避免意外崩潰全沒了
                    if loop_idx > 0 and loop_idx % 5 == 0:
                        flush_results_to_dataframe()

            status_text.empty()
            progress_bar.empty()
            
            # 掃描完成 (進度 >= 總數)
            if st.session_state['scan_current_index'] >= len(tickers):
                st.session_state['is_scanning'] = False
                st.session_state['scan_current_index'] = 0 
                flush_results_to_dataframe() # <--- 完成時轉正
                
                if not st.session_state['scan_temp_results']:
                     if not st.session_state.get('stop_scan'):
                        st.warning("未發現有效標的。")
                else:
                    st.success(f"✅ 掃描完成！")

    # ==========================================
    # 4. 結果顯示與資金流向圖 (修復 AttributeError)
    # ==========================================
    
    # [關鍵修正] 檢查 key 是否存在 + 是否為 DataFrame + 是否不為空
    has_results = False
    if 'scan_results_df' in st.session_state:
        df_obj = st.session_state['scan_results_df']
        # 這裡用 isinstance 確保它是 DataFrame，避免 NoneType 或 List 報錯
        if isinstance(df_obj, pd.DataFrame) and not df_obj.empty:
            has_results = True

    if has_results:
        df_res = st.session_state['scan_results_df']
        
# [優化功能] Alpha 動能散佈圖 (Scatter Plot)
        st.markdown("### 🎯 Alpha 動能戰略地圖 (Strategy Matrix)")
        st.caption("此圖結合 **AI 預測 (X軸)** 與 **市場現況 (Y軸)**。氣泡越大代表資金越熱。")
        
        if not df_res.empty:
            import plotly.express as px
            
            # 準備繪圖數據
            df_chart = df_res.copy()
            # 漲跌幅換算成百分比
            df_chart['漲跌%'] = df_chart['漲跌幅'] * 100
            
            # 建立散佈圖
            fig_scatter = px.scatter(
                df_chart,
                x="Alpha_Score",
                y="漲跌%",
                size="成交金額",        # 氣泡大小：資金流向
                color="Alpha_Score",    # 顏色：AI 評分高低
                # 台股紅漲綠跌配色 (高分紅/低分綠)
                color_continuous_scale=['#00e676', '#26a69a', '#424242', '#ef5350', '#ff1744'],
                color_continuous_midpoint=0,
                text="名稱",            # 直接顯示股名
                hover_data=["代號", "收盤價", "建議"],
                title=""
            )
            
            # 優化圖表佈局
            fig_scatter.update_traces(
                textposition='top center',
                marker=dict(line=dict(width=1, color='DarkSlateGrey')), # 氣泡邊框
                textfont=dict(size=13, color='#e0e0e0')
            )
            
            # 繪製十字準星 (劃分四象限)
            fig_scatter.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
            fig_scatter.add_vline(x=0, line_dash="dash", line_color="gray", opacity=0.5)
            
            # 設定座標軸範圍與標籤
            fig_scatter.update_layout(
                template="plotly_dark",
                height=550,
                margin=dict(t=30, l=10, r=10, b=10),
                xaxis=dict(title="Alpha Score (AI 預測分數)", showgrid=True, zeroline=False),
                yaxis=dict(title="今日漲跌幅 (%)", showgrid=True, zeroline=False),
                coloraxis_colorbar=dict(title="評分")
            )
            
            # 加入象限註解 (幫助使用者判讀)
            fig_scatter.add_annotation(x=90, y=9, text="🚀 強勢動能", showarrow=False, font=dict(color="#ff5252", size=14))
            fig_scatter.add_annotation(x=90, y=-9, text="💎 低檔佈局 (高潛力)", showarrow=False, font=dict(color="#ffecb3", size=14))
            fig_scatter.add_annotation(x=-90, y=-9, text="💀 空頭修正", showarrow=False, font=dict(color="#00e676", size=14))
            
            st.plotly_chart(fig_scatter, use_container_width=True)
            
            # 提供判讀指南
            with st.expander("📖 如何解讀這張戰略地圖？", expanded=False):
                st.markdown("""
                * **右下象限 (💎 低檔佈局區)**：**最值得關注！** Alpha 分數高 (AI看好)，但今日股價尚未大漲 (漲跌幅低或負)。這通常是主力正在吃貨或錯殺的**黃金買點**。
                * **右上象限 (🚀 強勢動能區)**：Alpha 分數高，且股價正在上漲。適合**順勢追價**，但需留意乖離過大。
                * **左下象限 (💀 空頭修正區)**：分數低且股價在跌，建議**避開或放空**。
                * **氣泡大小**：越大顆代表成交金額越大，流動性越好，但也可能代表短線過熱。
                """)

        st.markdown("---")
        
        st.markdown("### 🏆 AI 嚴選：最佳持有評分 Top 10")
        
        # 確保 top_10_df 存在且正確
        if 'top_10_df' in st.session_state and isinstance(st.session_state['top_10_df'], pd.DataFrame):
            top10 = st.session_state['top_10_df']
            
            c1, c2, c3 = st.columns(3)
            if len(top10) >= 1:
                r = top10.iloc[0]
                c1.metric(f"🥇 {r['名稱']}", f"{r['Alpha_Score']}分", f"{r['建議']}", delta_color="normal")
            if len(top10) >= 2:
                r = top10.iloc[1]
                c2.metric(f"🥈 {r['名稱']}", f"{r['Alpha_Score']}分", f"{r['建議']}", delta_color="normal")
            if len(top10) >= 3:
                r = top10.iloc[2]
                c3.metric(f"🥉 {r['名稱']}", f"{r['Alpha_Score']}分", f"{r['建議']}", delta_color="normal")

            def highlight_top_score(val):
                if val >= 80: color = '#ffcdd2'
                elif val >= 50: color = '#fff9c4'
                else: color = 'white'
                return f'background-color: {color}; color: black; font-weight: bold'

            # 這裡就是原本報錯的地方，現在因為上方加了 isinstance 檢查，安全了
            st.dataframe(
                top10.style
                .format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}", "漲跌幅": "{:.2%}"})
                .applymap(highlight_top_score, subset=['Alpha_Score']),
                use_container_width=True,
                column_order=["代號", "名稱", "Alpha_Score", "建議", "收盤價", "漲跌幅", "回測報酬", "計算過程"]
            )
            
            with st.expander("📄 查看完整掃描清單", expanded=False):
                 st.dataframe(
                    df_res.sort_values(by='Alpha_Score', ascending=False)
                    .style.format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}", "漲跌幅": "{:.2%}"})
                    .background_gradient(subset=['Alpha_Score'], cmap='Reds'),
                    use_container_width=True
                )
            
    elif 'scan_results_df' in st.session_state:
         # 只有在真的沒有結果時才顯示提示，避免剛掃到一半顯示這個
         if not st.session_state.get('is_scanning', False):
             st.info("請選擇板塊並點擊「啟動戰略掃描」開始分析。")


# --- 頁面 4: 全台股清單 ---
elif page == "📋 全台股清單":
    st.markdown("### 📋 上市櫃股票基本面快篩")
    if st.button("🔄 下載/更新最新清單"):
        with st.spinner("正在獲取資料..."):
            st.cache_data.clear()
            df_all = get_master_stock_data()
            st.session_state['all_stock_list'] = df_all
    
    if 'all_stock_list' not in st.session_state or st.session_state['all_stock_list'] is None:
        st.session_state['all_stock_list'] = get_master_stock_data()

    if st.session_state['all_stock_list'] is not None:
        df_show = st.session_state['all_stock_list']
        search_term = st.text_input("🔍 搜尋代號或名稱")
        if search_term:
            df_show = df_show[df_show['代號'].str.contains(search_term) | df_show['名稱'].str.contains(search_term)]
        st.dataframe(df_show, use_container_width=True, hide_index=True)

# --- 頁面 3.5 (局部無感刷新版): 持股健診 ---
elif page == "💼 持股健診與建議":
    st.markdown("### 💼 智能持股健診 (Portfolio Doctor)")
    
    # 登入狀態提示
    if st.session_state.get('logged_in'):
        st.caption(f"✅ 雲端連線中 (User: {st.session_state['username']})")
    else:
        st.caption("⚠️ 訪客模式")

    # ==========================================
    # 1. [修正-防呆版] 準備輸入資料 (使用 Callback 鎖定狀態)
    # ==========================================
    
    # 定義 Callback：當表格被編輯時，立刻執行此函式存檔
    def on_portfolio_change():
        # 從 editor 取出資料
        edited_val = st.session_state.get("portfolio_editor")
        
        # [防呆機制] 確保轉換為 DataFrame
        new_df = pd.DataFrame() # 預設空表
        
        if isinstance(edited_val, pd.DataFrame):
            new_df = edited_val.copy() # 複製一份，切斷參照
        elif isinstance(edited_val, list):
            new_df = pd.DataFrame(edited_val)
        elif isinstance(edited_val, dict):
            # 極少數情況會變成 dict，嘗試救援，若失敗則忽略
            try: new_df = pd.DataFrame(edited_val)
            except: return 

        # 確保欄位型態正確 (防止空值導致計算錯誤)
        if not new_df.empty:
            # 嘗試將持有股數轉為數字，非數字補 0
            if '持有股數' in new_df.columns:
                new_df['持有股數'] = pd.to_numeric(new_df['持有股數'], errors='coerce').fillna(0).astype(int)
            # 確保代號是字串
            if '代號' in new_df.columns:
                new_df['代號'] = new_df['代號'].astype(str)

        # 更新 Session State (確保它是乾淨的 DataFrame)
        st.session_state['portfolio_data'] = new_df

        st.session_state['data_version'] = datetime.now().timestamp()
        
        # 如果已登入，同步寫入資料庫
        if st.session_state.get('logged_in'):
            save_portfolio_to_db(st.session_state['username'], new_df)

    # ==========================================
    # 1. [優化版] 準備輸入資料 (表單批次處理)
    # ==========================================

    # 初始化資料 (只在第一次執行或資料異常時執行)
    if 'portfolio_data' not in st.session_state or not isinstance(st.session_state['portfolio_data'], pd.DataFrame):
        if st.session_state.get('logged_in'):
            db_df = load_portfolio_from_db(st.session_state['username'])
            start_df = db_df if not db_df.empty else pd.DataFrame([{"代號": "2330", "持有股數": 1000}])
        else:
            start_df = pd.DataFrame([
                {"代號": "2330", "持有股數": 1000}, {"代號": "2317", "持有股數": 2000}, {"代號": "2603", "持有股數": 5000}
            ])
            
        # 初始化時自動補上名稱
        if '代號' in start_df.columns:
            start_df['名稱'] = start_df['代號'].apply(lambda x: get_stock_name(str(x)))
        
        st.session_state['portfolio_data'] = start_df

    col_input, col_ctrl = st.columns([3, 1])
    
    with col_input:
        st.markdown("#### 1. 輸入持股明細")
        st.caption("📝 請直接編輯表格，輸入完畢後請務必點擊下方 **「💾 確認儲存」** 按鈕。")
        
        # [關鍵修正] 使用 st.form 將編輯器包起來
        # 這樣輸入過程中的 Enter 或 Tab 都不會觸發 Rerun，直到按下 Submit 按鈕
        with st.form("portfolio_input_form"):
            edited_df = st.data_editor(
                st.session_state['portfolio_data'], 
                num_rows="dynamic", 
                use_container_width=True, 
                key="portfolio_editor_widget", 
                column_order=["代號", "名稱", "持有股數"],
                column_config={
                    "代號": st.column_config.TextColumn("股票代號", help="輸入代號 (如 2330)"),
                    "名稱": st.column_config.TextColumn("股票名稱", disabled=True, help="儲存後自動更新"), 
                    "持有股數": st.column_config.NumberColumn("持有股數 (股)", min_value=1, step=1000, format="%d")
                }
            )
            
            # 表單提交按鈕
            submit_btn = st.form_submit_button("💾 確認儲存並分析", type="primary", use_container_width=True)

        # [處理邏輯] 只有在按下按鈕後才執行資料處理與存檔
        if submit_btn:
            # 1. 資料清洗
            if edited_df is not None:
                # 確保股數是數字
                if '持有股數' in edited_df.columns:
                    edited_df['持有股數'] = pd.to_numeric(edited_df['持有股數'], errors='coerce').fillna(0).astype(int)
                # 確保代號是字串
                if '代號' in edited_df.columns:
                    edited_df['代號'] = edited_df['代號'].astype(str)

                # 2. 自動更新股名 (這是批次執行的，不會卡頓)
                with st.spinner("正在更新股票名稱與存檔..."):
                    if '代號' in edited_df.columns:
                        edited_df['名稱'] = edited_df['代號'].apply(lambda x: get_stock_name(str(x)) if x else "")

                # 3. 更新 Session State
                st.session_state['portfolio_data'] = edited_df
                st.session_state['data_version'] = datetime.now().timestamp()
                
                # 4. 同步寫入資料庫 (若已登入)
                if st.session_state.get('logged_in'):
                    save_portfolio_to_db(st.session_state['username'], edited_df)
                
                st.success("✅ 持股明細已更新！")
                st.rerun()
                
    with col_ctrl:
        st.markdown("#### 2. 監控設定")
        st.info("👇 點擊下方按鈕後，下方區域將進入實時監控模式，每 300 秒僅更新圖表數據，不會重載整頁。")
        enable_monitor = st.toggle("🔴 啟動盤中實時監控 (每 300 秒更新)", value=False)

    # ==========================================
    # 3. 定義局部刷新片段 (The Fragment)
    # [注意] 這個函式必須放在 enable_monitor 定義之後
    # ==========================================

    # 初始化：用於記錄上次寄出時的各股分數狀態 (Fingerprint)
    if 'last_sent_scores' not in st.session_state:
        st.session_state['last_sent_scores'] = {}

    # 初始化上次寄信時間
    if 'last_email_time' not in st.session_state:
        st.session_state['last_email_time'] = datetime.min

    @st.fragment(run_every=300 if enable_monitor else None)  
    def render_live_dashboard(target_df):
        if target_df.empty:
            st.warning("⚠️ 請先輸入持股資料。")
            return

        # 強制轉換為台北時間顯示
        tw_tz = pytz.timezone('Asia/Taipei')
        update_time = datetime.now(tw_tz).strftime("%H:%M:%S")
        
        if enable_monitor:
            st.caption(f"⚡ 實時監控中... (最後更新: {update_time})")
        else:
            st.caption(f"Analysis Snapshot (時間: {update_time})")

        portfolio_results = []
        
        # 使用 status container 顯示動態進度
        with st.status(f"正在全方位分析 {len(target_df)} 檔持股結構...", expanded=True) as status:
            
            # === 核心迴圈開始 ===
            for idx, row in target_df.iterrows():
                ticker = str(row['代號']).strip()
                shares = row['持有股數']
                
                if not ticker or shares <= 0: continue
                
                # 1. 獲取即時資料
                raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
                name = get_stock_name(fmt_ticker)
                
                if raw_df.empty or len(raw_df) < 60: continue 
                    
                # 2. 執行策略回測
                best_params, final_df = run_optimization(
                    raw_df, market_df, start_date, fee_input, tax_input,
                    use_chip_strategy=enable_chip_strategy,
                    use_strict_bear_exit=enable_strict_bear_exit  # <--- 加入參數
                )
                
                if final_df is None or final_df.empty: continue

                # 3. 計算基礎數值
                current_price = final_df['Close'].iloc[-1]
                market_value = current_price * shares
                
                # 4. 計算 Alpha Score
                stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                base_score_log = stock_alpha_df['Score_Log'].iloc[-1] 
                
                # 5. 取得技術訊號
                action, color, tech_reason = analyze_signal(final_df)
                
                # 6. 情境感知調整 (Context-Aware Adjustment)
                final_score = base_alpha_score
                adjustment_log = []
                
                # 判斷策略屬性
                last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                is_rebound = False
                if last_trade is not None:
                    buy_reason = str(last_trade['Reason'])
                    if any(x in buy_reason for x in ["反彈", "超賣", "回測", "籌碼"]): is_rebound = True
                
                # 分數修正邏輯
                if action == "✊ 續抱" or action == "🚀 買進":
                    if is_rebound:
                        if current_price < final_df['MA60'].iloc[-1]: 
                            final_score += 15; adjustment_log.append("反彈無視季線+15")
                        ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                        if current_price > ma5: 
                            final_score += 10; adjustment_log.append("站穩MA5+10")
                        else:
                            final_score -= 5; adjustment_log.append("破MA5-5")
                        
                        rsi_now = final_df['RSI'].iloc[-1]
                        rsi_prev = final_df['RSI'].iloc[-2]
                        if rsi_now > rsi_prev: 
                            final_score += 10; adjustment_log.append("動能翻揚+10")
                        elif rsi_now < 30:
                            final_score += 5; adjustment_log.append("低檔鈍化+5")
                    else:
                        vol_now = final_df['Volume'].iloc[-1]
                        vol_ma = final_df['Vol_MA20'].iloc[-1]
                        if vol_now > vol_ma * 2.5 and final_df['Close'].pct_change().iloc[-1] < 0.005:
                            final_score -= 15; adjustment_log.append("高檔爆量滯漲-15")

                # 限制分數範圍
                final_score = max(min(final_score, 100), -100)

                # 7. 產生 AI 建議
                final_advice = ""
                if action == "🚀 買進": 
                    final_advice = "🔥 強力加碼" if final_score > 30 else "✅ 買進訊號"
                elif action == "⚡ 賣出": 
                    final_advice = "💀 清倉/放空" if final_score < -20 else "📉 獲利了結"
                elif action == "✊ 續抱": 
                    if final_score > 40: final_advice = "✨ 抱緊處理"
                    elif final_score > 0: final_advice = "✊ 續抱觀察"
                    elif final_score > -15: final_advice = "🛡️ 策略持倉"
                    else: final_advice = "⚠️ 減碼觀望"
                else: 
                    final_advice = "👀 留意買點" if final_score > 60 else "💤 觀望"

                # 8. 組合顯示理由
                display_reason = base_score_log
                if adjustment_log:
                    display_reason += f" ➜ 修正: {','.join(adjustment_log)}"
                if not display_reason:
                    display_reason = f"Alpha:{int(final_score)} | {tech_reason}"

                portfolio_results.append({
                    "代號": fmt_ticker.split('.')[0], 
                    "名稱": name, 
                    "持有股數": shares,
                    "收盤價": current_price, 
                    "市值": market_value, 
                    "綜合評分": int(final_score), 
                    "AI 建議": final_advice, 
                    "詳細理由": display_reason
                })
            
            status.update(label="AI 分析完成！", state="complete", expanded=False)

        # ==========================================
        # [優化] 自動寄信邏輯：智慧訊號過濾
        # ==========================================
        if enable_monitor and portfolio_results:
            
            # 1. 建立當前快照 (包含分數與建議)
            # 使用字典儲存更多資訊: {代號: {'score': 分數, 'advice': 建議}}
            current_snapshot = {
                item['代號']: {'score': item['綜合評分'], 'advice': item['AI 建議']}
                for item in portfolio_results
            }
            
            # 讀取上次的快照 (若無則為空)
            last_snapshot = st.session_state.get('last_sent_snapshot', {})
            
            # 2. 檢查是否觸發「重要條件」
            should_send_email = False
            email_data_list = []
            
            for ticker, curr_info in current_snapshot.items():
                curr_score = curr_info['score']
                curr_advice = curr_info['advice']
                
                # 取得舊資料
                prev_info = last_snapshot.get(ticker)
                
                is_alert_needed = False
                change_str = f"{curr_score}"
                
                if prev_info is None:
                    # A. 新加入的持股 -> 通知
                    is_alert_needed = True
                    change_str = f"<span style='color:blue'>New ({curr_score})</span>"
                else:
                    prev_score = prev_info['score']
                    prev_advice = prev_info['advice']
                    
                    # B. 建議改變 (例如: 續抱 -> 賣出) -> 重要！通知
                    if curr_advice != prev_advice:
                        is_alert_needed = True
                        change_str = f"{prev_score} ➜ <b>{curr_score}</b> ({prev_advice}➜{curr_advice})"
                        
                    # C. 分數劇烈波動 (變動 > 5 分) -> 顯著！通知
                    elif abs(curr_score - prev_score) >= 5:
                        is_alert_needed = True
                        arrow = "🔺" if curr_score > prev_score else "🔻"
                        color = "red" if curr_score > prev_score else "green"
                        change_str = f"{prev_score} <b style='color:{color}'>{arrow} {curr_score}</b>"
                
                # 如果符合任一條件，加入發送列表
                if is_alert_needed:
                    should_send_email = True
                    # 找出原始資料以便複製
                    original_item = next((x for x in portfolio_results if x['代號'] == ticker), None)
                    if original_item:
                        item_copy = original_item.copy()
                        item_copy['分數變動'] = change_str
                        email_data_list.append(item_copy)

            # 3. 執行發送
            if should_send_email:
                st.toast(f"⚡ 偵測到 {len(email_data_list)} 筆重要異動，發送通知...", icon="📧")
                
                res_df_for_email = pd.DataFrame(email_data_list)
                
                # 準備市場分析文字 (避免 API 頻繁呼叫，可設為簡單文字或快取)
                try:
                    market_scored_df = calculate_alpha_score(market_df, pd.DataFrame(), pd.DataFrame())
                    analysis_html_for_email = generate_market_analysis(market_scored_df, pd.DataFrame(), pd.DataFrame())
                except:
                    analysis_html_for_email = "<p>暫無法獲取市場分析數據</p>"
                
                with st.spinner("📧 正在發送重要通知信..."):
                    success = send_analysis_email(res_df_for_email, analysis_html_for_email)
                    
                if success:
                    # 發送成功後，更新快照
                    st.session_state['last_sent_snapshot'] = current_snapshot
                    st.toast(f"✅ 通知已發送！")
                else:
                    st.toast("❌ Email 發送失敗", icon="⚠️")
                                        
        # ==========================================
        # 顯示結果
        # ==========================================
        if portfolio_results:
            res_df = pd.DataFrame(portfolio_results)
            total_val = res_df['市值'].sum()
            res_df['權重%'] = (res_df['市值'] / total_val * 100) if total_val > 0 else 0
            health = (res_df['綜合評分'] * res_df['市值']).sum() / total_val if total_val > 0 else 0
            
            # 儀表板
            c_gauge, c_info = st.columns([1, 2])
            with c_gauge:
                fig_g = go.Figure(go.Indicator(
                    mode = "gauge+number", value = health, 
                    title = {'text': "組合健康度"},
                    gauge = {'axis': {'range': [-100, 100]}, 'bar': {'color': "#00e676" if health > 0 else "#ef5350"}}
                ))
                fig_g.update_layout(height=200, margin=dict(t=30, b=10, l=20, r=20), paper_bgcolor='rgba(0,0,0,0)', font={'color': "white"})
                st.plotly_chart(fig_g, use_container_width=True)
            
            with c_info:
                st.metric("💰 總資產估值", f"NT$ {int(total_val):,}", delta=None)
                st.info(f"💡 若開啟即時監控，當持股評分發生變化時，系統將自動寄發 Email 通知。")

            # 表格樣式與顯示
            def highlight_advice(val):
                color = 'white'
                val_str = str(val)
                if '加碼' in val_str or '買進' in val_str or '抱緊' in val_str: color = '#ffcdd2' 
                elif '減碼' in val_str or '賣出' in val_str or '清倉' in val_str: color = '#c8e6c9'
                elif '策略持倉' in val_str: color = '#bbdefb'
                elif '觀望' in val_str: color = '#cfd8dc'
                return f'background-color: {color}; color: black; font-weight: bold'

            def highlight_score(val):
                try:
                    v = float(val)
                    color = '#ef5350' if v >= 30 else ('#00e676' if v <= -20 else 'gray')
                    return f'color: {color}; font-weight: bold'
                except: return ''

            st.dataframe(
                res_df.style
                .map(highlight_advice, subset=['AI 建議']) 
                .map(highlight_score, subset=['綜合評分']) 
                .format({"權重%": "{:.1f}%", "收盤價": "{:.2f}", "市值": "{:,.0f}", "持有股數": "{:.0f}"}),
                use_container_width=True
            )

    # ==========================================
    # 4. 呼叫片段 (主程式進入點)
    # ==========================================
    st.markdown("---")
    render_live_dashboard(st.session_state['portfolio_data'])

# --- 頁面 5: 策略實驗室 (Strategy Lab) ---
elif page == "🧪 策略實驗室":
    st.markdown("### 🧪 全市場策略驗證實驗室 (Strategy Lab)")
    st.caption("此模組用於遍歷大量標的，驗證策略在不同市場環境下的普適性、抗跌性與獲利能力。")

    # ==========================================
    # 1. 實驗參數設定
    # ==========================================
    with st.expander("⚙️ 實驗參數設定", expanded=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            target_universe = st.selectbox("樣本範圍", ["🔥 台股熱門 50", "🤖 AI 伺服器概念", "🚢 航運股", "📋 全上市櫃 (耗時)", "🎲 隨機抽樣 30 檔"])
        with c2:
            test_start_date = st.date_input("回測開始", value=datetime.today() - timedelta(days=365*3))
        with c3:
            test_end_date = st.date_input("回測結束", value=datetime.today())
            
    # 準備清單
    target_tickers = []
    if target_universe == "🔥 台股熱門 50":
        target_tickers = PRESET_LISTS["🔥 台股熱門 50 (權值)"]
    elif target_universe == "🤖 AI 伺服器概念":
        target_tickers = PRESET_LISTS["🤖 AI 伺服器與散熱"]
    elif target_universe == "🚢 航運股":
        target_tickers = PRESET_LISTS["🚢 航運與原物料"]
    elif target_universe == "🎲 隨機抽樣 30 檔":
        if st.session_state['all_stock_list'] is None:
            st.session_state['all_stock_list'] = get_master_stock_data()
        all_codes = st.session_state['all_stock_list']['代號'].tolist()
        import random
        target_tickers = random.sample(all_codes, 30) if len(all_codes) > 30 else all_codes
    elif target_universe == "📋 全上市櫃 (耗時)":
        if st.session_state['all_stock_list'] is None:
            st.session_state['all_stock_list'] = get_master_stock_data()
        target_tickers = st.session_state['all_stock_list']['代號'].tolist()

    # Session State 初始化
    if 'lab_running' not in st.session_state: st.session_state['lab_running'] = False
    if 'lab_results' not in st.session_state: st.session_state['lab_results'] = []
    if 'lab_stop' not in st.session_state: st.session_state['lab_stop'] = False

    # 控制按鈕
    c_run, c_stop, c_clear = st.columns([1, 1, 1])
    with c_run:
        if st.button("🚀 開始全遍歷驗證", type="primary", use_container_width=True):
            st.session_state['lab_running'] = True
            st.session_state['lab_stop'] = False
            st.session_state['lab_results'] = [] # 重置
    with c_stop:
        if st.button("🛑 強制停止", use_container_width=True):
            st.session_state['lab_running'] = False
            st.session_state['lab_stop'] = True
    with c_clear:
        if st.button("🗑️ 清除結果", use_container_width=True):
            st.session_state['lab_results'] = []

    # ==========================================
    # 2. 核心遍歷迴圈
    # ==========================================
    if st.session_state['lab_running']:
        lab_market_df = get_market_data(test_start_date, test_end_date)
        progress_bar = st.progress(0)
        status_text = st.empty()
        result_area = st.container()
        
        total = len(target_tickers)
        results = []

        for i, ticker in enumerate(target_tickers):
            if st.session_state['lab_stop']:
                st.warning("使用者中止測試")
                break
                
            status_text.text(f"正在驗證 ({i+1}/{total}): {ticker} ...")
            progress_bar.progress((i + 1) / total)

            try:
                # A. 獲取數據 (修改這裡：增加獲取股名的邏輯)
                raw_df, fmt_ticker = get_stock_data(ticker, test_start_date, test_end_date)
                if raw_df.empty or len(raw_df) < 100: continue
                
                # [新增] 取得股名並組合成顯示字串
                stock_name = get_stock_name(fmt_ticker)
                display_label = f"{ticker} {stock_name}"

                # B. 執行策略
                best_params, strat_df = run_optimization(
                    raw_df, lab_market_df, test_start_date, fee_input, tax_input,
                    use_chip_strategy=enable_chip_strategy,
                    use_strict_bear_exit=enable_strict_bear_exit  # <--- 加入參數
                )
                
                if strat_df is None or strat_df.empty: continue

                # C. 計算關鍵指標 (維持不變)
                strat_ret = strat_df['Cum_Strategy'].iloc[-1] - 1
                bh_ret = strat_df['Cum_Market'].iloc[-1] - 1
                alpha = strat_ret - bh_ret

                total_days = len(strat_df)
                market_bull_days = strat_df[strat_df['Close'] > strat_df['MA60']]
                market_bear_days = strat_df[strat_df['Close'] < strat_df['MA60']]
                
                bull_held_days = market_bull_days[market_bull_days['Position'] == 1]
                bull_capture = len(bull_held_days) / len(market_bull_days) if len(market_bull_days) > 0 else 0
                
                bear_held_days = market_bear_days[market_bear_days['Position'] == 1]
                bear_exposure = len(bear_held_days) / len(market_bear_days) if len(market_bear_days) > 0 else 0

                panic_buys = strat_df[(strat_df['Action'] == 'Buy') & (strat_df['Reason'].str.contains('反彈|超賣'))]
                panic_wins = 0
                panic_count = len(panic_buys)
                
                if panic_count > 0:
                    for idx in panic_buys.index:
                        future = strat_df.loc[idx:]
                        sells = future[future['Action'] == 'Sell']
                        if not sells.empty:
                            sell_idx = sells.index[0]
                            pnl = (strat_df.loc[sell_idx, 'Close'] - strat_df.loc[idx, 'Close']) / strat_df.loc[idx, 'Close']
                            if pnl > 0: panic_wins += 1
                
                panic_win_rate = (panic_wins / panic_count) if panic_count > 0 else np.nan

                # [修正] 先正確獲取勝率數據
                wr_str, wins, totals, avg_pnl = calculate_realized_win_rate(strat_df)
                
                # 將 "65.5%" 轉為 0.655
                try:
                    final_win_rate = float(wr_str.strip('%')) / 100
                except:
                    final_win_rate = 0.0

                # D. 存入結果
                res_item = {
                    "代號": display_label,
                    "策略報酬": strat_ret,
                    "買持報酬": bh_ret,
                    "Alpha": alpha,
                    "勝率": final_win_rate,  # <--- 修正這裡，使用正確轉換後的勝率
                    "MDD": calculate_mdd(strat_df['Cum_Strategy']),
                    "多頭捕捉率": bull_capture,
                    "空頭曝險率": bear_exposure,
                    "抄底次數": panic_count,
                    "抄底勝率": panic_win_rate
                }

                results.append(res_item)
                st.session_state['lab_results'] = results 

            except Exception as e:
                print(f"Error analyzing {ticker}: {e}")
                continue

        st.session_state['lab_running'] = False
        st.success("✅ 驗證完成！")

    # ==========================================
    # 3. 結果分析與視覺化
    # ==========================================
    if st.session_state['lab_results']:
        df_res = pd.DataFrame(st.session_state['lab_results'])
        
        st.markdown("---")
        st.markdown("### 📊 實驗報告摘要")

        # A. 核心統計卡片
        avg_strat = df_res['策略報酬'].mean()
        avg_bh = df_res['買持報酬'].mean()
        avg_alpha = df_res['Alpha'].mean()
        median_alpha = df_res['Alpha'].median()
        win_rate_avg = df_res['勝率'].mean()
        
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("平均策略報酬", f"{avg_strat:.1%}", f"vs 買持 {avg_bh:.1%}")
        k2.metric("平均 Alpha (超額)", f"{avg_alpha:.1%}", f"中位數 {median_alpha:.1%}", delta_color="normal")
        k3.metric("平均勝率", f"{win_rate_avg:.1%}", "目標 > 50%")
        k4.metric("正 Alpha 佔比", f"{(df_res['Alpha'] > 0).mean():.1%}", "打敗大盤機率")

        # B. 圖表分析
        tab_v1, tab_v2, tab_v3 = st.tabs(["📈 報酬分佈", "🛡️ 多空執行力", "📉 抄底有效性"])

        with tab_v1:
            st.markdown("#### 策略 vs 買進持有 (Buy & Hold) 報酬分佈")
            fig_dist = go.Figure()
            fig_dist.add_trace(go.Histogram(x=df_res['策略報酬'], name='策略報酬', opacity=0.75, marker_color='#ef5350'))
            fig_dist.add_trace(go.Histogram(x=df_res['買持報酬'], name='買持報酬', opacity=0.75, marker_color='gray'))
            fig_dist.update_layout(barmode='overlay', template="plotly_dark", xaxis_tickformat='.0%')
            st.plotly_chart(fig_dist, use_container_width=True)
            
            st.caption("說明：紅色分佈若整體位於灰色右側，代表策略具有普遍的正期望值。")

        with tab_v2:
            st.markdown("#### 市場體制適應性分析")
            # 散佈圖：X軸=空頭曝險率，Y軸=多頭捕捉率
            fig_regime = px.scatter(
                df_res, x="空頭曝險率", y="多頭捕捉率", 
                color="Alpha", hover_data=["代號"],
                color_continuous_scale=['#00e676', '#26a69a', 'gray', '#ef5350', '#ff1744'],
                color_continuous_midpoint=0,
                title="避險 vs 進攻 能力分佈"
            )
            # 劃分理想區域
            fig_regime.add_hline(y=0.5, line_dash="dash", line_color="gray")
            fig_regime.add_vline(x=0.3, line_dash="dash", line_color="gray")
            
            fig_regime.update_layout(template="plotly_dark", xaxis_tickformat='.0%', yaxis_tickformat='.0%')
            st.plotly_chart(fig_regime, use_container_width=True)

        with tab_v3:
            st.markdown("#### 恐慌抄底 (Panic Rebound) 有效性驗證")
            df_panic = df_res[df_res['抄底次數'] > 0].copy()
            if not df_panic.empty:
                fig_panic = px.box(df_panic, y="抄底勝率", points="all", title="抄底策略勝率分佈")
                fig_panic.update_layout(template="plotly_dark", yaxis_tickformat='.0%', yaxis_range=[0, 1.1])
                st.plotly_chart(fig_panic, use_container_width=True)
                st.metric("平均抄底勝率", f"{df_panic['抄底勝率'].mean():.1%}", f"樣本數: {len(df_panic)} 檔")
            else:
                st.info("選定樣本中無觸發抄底訊號。")

        # C. 詳細數據表
        st.markdown("### 📋 詳細驗證數據")
        
        # 格式化顯示
        def color_alpha(val):
            color = '#ffcdd2' if val > 0 else '#c8e6c9'
            return f'background-color: {color}; color: black'

        st.dataframe(
            df_res.style.format({
                "策略報酬": "{:.1%}", "買持報酬": "{:.1%}", "Alpha": "{:.1%}", 
                "勝率": "{:.1%}", "MDD": "{:.1f}%", 
                "多頭捕捉率": "{:.1%}", "空頭曝險率": "{:.1%}", "抄底勝率": "{:.1%}"
            }).applymap(color_alpha, subset=['Alpha']),
            use_container_width=True
        )
