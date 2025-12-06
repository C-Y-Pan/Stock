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
    [修正版] 保留 Dividends 欄位以計算含息報酬。
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
                # A. 基礎事件還原 (Metadata)
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
                        # 這裡的還原邏輯主要是為了讓 K 線連續，但我們保留原始 Dividends 欄位供回測計算
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
            
            # [修正] 移除 'Stock Splits'，但保留 'Dividends'
            cols_to_drop = ['Stock Splits']
            df = df.drop(columns=[c for c in cols_to_drop if c in df.columns], errors='ignore')
            
            # 確保 Dividends 欄位存在且無 NaN
            if 'Dividends' not in df.columns:
                df['Dividends'] = 0.0
            else:
                df['Dividends'] = df['Dividends'].fillna(0.0)
            
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
    執行策略回測 v10 (Alpha Score Based):
    - 買賣時機完全基於 Alpha Score：正值則買入，負值則賣出
    - 保留停損機制作為風險控制
    """
    df = data.copy()
    
    if 'Dividends' not in df.columns: df['Dividends'] = 0.0
    df['Dividends'] = df['Dividends'].fillna(0.0)
    
    # 先計算 Alpha Score（不依賴 Action，用於買賣判斷）
    # 為了保證判斷一致性，我們分別計算空手狀態和持有狀態的分數
    
    # 1. 空手狀態下的分數（用於進場判斷）
    df['Action'] = 'Wait'  # 空手狀態
    df['Reason'] = ''
    df_with_alpha_wait = calculate_alpha_score(df, pd.DataFrame(), pd.DataFrame())
    alpha_scores_wait = df_with_alpha_wait['Alpha_Score'].values

    # 2. 持有狀態下的分數（用於出場判斷）
    # 持有狀態下，震盪洗盤信號會加分，避免輕易被洗出
    df['Action'] = 'Hold' # 持有狀態
    df_with_alpha_hold = calculate_alpha_score(df, pd.DataFrame(), pd.DataFrame())
    alpha_scores_hold = df_with_alpha_hold['Alpha_Score'].values
        
    positions = []; reasons = []; actions = []; target_prices = []
    return_labels = []; confidences = []
    
    position = 0; days_held = 0; entry_price = 0.0; trade_type = 0
    cum_div = 0.0 
    
    # 準備 Numpy Array
    close = df['Close'].values; trend = df['Trend'].values; rsi = df['RSI'].values
    bb_lower = df['BB_Lower'].values; ma20 = df['MA20'].values; ma60 = df['MA60'].values
    
    # 確保有 MA120 (若上游沒算，這裡需防呆)
    if 'MA120' not in df.columns: df['MA120'] = df['Close'].rolling(120).mean()
    
    ma120 = df['MA120'].bfill().values
    ma240 = df['MA240'].bfill().values
    ma30 = df['MA30'].ffill().values
    high_100d = df['High_100d'].fillna(0).values
    close_lag5 = df['Close_Lag5'].fillna(close[0]).values
    dividends = df['Dividends'].values
    
    volume = df['Volume'].values; vol_ma20 = df['Vol_MA20'].values
    obv = df['OBV'].values; obv_ma20 = df['OBV_MA20'].values
    market_panic = df['Is_Market_Panic'].values
    bb_width_vals = ((df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']).values

    # [新增] 預先計算「均線糾結指數」 (Rolling Congestion Index)
    # 使用 numpy 向量化計算加速
    ma_stack = np.vstack([ma60, ma120, ma240])
    ma_max = np.max(ma_stack, axis=0)
    ma_min = np.min(ma_stack, axis=0)
    # 瞬時差距
    raw_gap_ratio = np.divide((ma_max - ma_min), close, out=np.ones_like(close), where=close!=0)
    # 20日平均差距 (糾結指數)
    # 利用 pandas rolling 計算後轉回 numpy
    congestion_index = pd.Series(raw_gap_ratio).rolling(60, min_periods=1).mean().fillna(1.0).values

    for i in range(len(df)):
        # 初始化：根據當前持倉狀態設置默認值
        # 確保狀態轉換正確：空手時不能賣出，持有時不能買入
        if position == 0:
            # 空手狀態：只能買入或等待
            signal = 0  # 默認保持空手
            reason_str = ""
            action_code = "Wait"
            this_target = np.nan
            ret_label = ""
            conf_score = 0
        else:
            # 持有狀態：只能賣出或持有
            signal = 1  # 默認保持持有
            reason_str = ""
            action_code = "Hold"
            this_target = entry_price * 1.15
            ret_label = ""
            conf_score = 0

        # 趨勢狀態
        is_ma240_down = False
        is_ma60_up = False
        if i > 0:
            if ma240[i] < ma240[i-1]: is_ma240_down = True
            if ma60[i] > ma60[i-1]: is_ma60_up = True
            
        is_price_weak = (close[i] < ma60[i]) and (close[i] < ma20[i])
        is_strict_bear = is_ma240_down and (not is_ma60_up) and is_price_weak

        # [新增] 糾結禁買判定
        # 若糾結指數 < 3% (0.03)，禁止買入
        is_squeeze_ban = congestion_index[i] < 0.03

        # --- 進場邏輯：基於 Alpha Score（僅在空手時執行）---
        if position == 0:
            # 使用空手狀態的 Alpha Score（用於進場判斷）
            current_alpha_score = alpha_scores_wait[i] if i < len(alpha_scores_wait) else 0
            
            # [嚴格限制] Alpha Score < 10 則嚴禁買入
            # Alpha Score >= 10 則買入（確保空手時才能買入）
            if current_alpha_score >= 10:
                # 判斷買入類型（用於後續出場邏輯）
                if rsi[i] < 30 or (rsi[i] < rsi_buy_thresh and close[i] < bb_lower[i] and market_panic[i]):
                    trade_type = 2  # 恐慌抄底
                    reason_str = f"Alpha買進(恐慌抄底, 分數:{current_alpha_score:.0f})"
                elif trend[i] == 1 and close[i] > ma60[i]:
                    trade_type = 1  # 趨勢突破
                    reason_str = f"Alpha買進(趨勢突破, 分數:{current_alpha_score:.0f})"
                else:
                    trade_type = 1  # 一般買進
                    reason_str = f"Alpha買進(分數:{current_alpha_score:.0f})"
                
                # 買入：更新狀態
                signal = 1  # 變為持有
                days_held = 0
                entry_price = close[i]
                action_code = "Buy"
                cum_div = 0.0
                conf_score = min(abs(current_alpha_score), 99)
            else:
                # 不買入：保持空手（Alpha Score < 10 時嚴禁買入）
                signal = 0
                action_code = "Wait"
                reason_str = f"空手觀望(分數:{current_alpha_score:.0f}, Alpha<10嚴禁買入)"
        
        # --- 出場邏輯：基於 Alpha Score + 停損保護（僅在持有時執行）---
        elif position == 1:
            days_held += 1
            if dividends[i] > 0: cum_div += dividends[i]
            adjusted_current_value = close[i] + cum_div
            drawdown = (adjusted_current_value - entry_price) / entry_price
            
            # 使用持有狀態的 Alpha Score（用於出場判斷）
            # 持有時，震盪洗盤會加分，避免被洗出，因此要用 alpha_scores_hold
            current_alpha_score = alpha_scores_hold[i] if i < len(alpha_scores_hold) else 0
            
            is_sell = False
            stop_loss_limit = -0.10 if is_strict_bear else -0.12
            
            # 優先檢查停損（風險控制）- 停損優先於 Alpha Score 限制
            if drawdown < stop_loss_limit:
                is_sell = True
                reason_str = f"觸發停損({stop_loss_limit*100:.0f}%)"
            # [嚴格限制] Alpha Score > 0 則嚴禁賣出（除了停損）
            elif current_alpha_score > 0:
                # Alpha Score 為正，嚴禁賣出（停損除外）
                is_sell = False
                reason_str = f"持有中(分數:{current_alpha_score:.0f}, Alpha為正嚴禁賣出)"
            # [嚴格限制] Alpha Score <= -10 時賣出（100%由Alpha Score決定）
            elif current_alpha_score <= -10:
                is_sell = True
                reason_str = f"Alpha賣出(分數:{current_alpha_score:.0f})"
            else:
                # 其他情況（-10 < Alpha Score <= 0）：持有中（100%由Alpha Score決定）
                is_sell = False
                reason_str = f"持有中(分數:{current_alpha_score:.0f})"
            
            # 根據是否賣出更新狀態
            if is_sell:
                # 賣出：變為空手
                signal = 0
                action_code = "Sell"
                final_pnl_value = (close[i] + cum_div) - entry_price
                pnl = final_pnl_value / entry_price * 100
                sign = "+" if pnl > 0 else ""
                ret_label = f"{sign}{pnl:.1f}%"
            else:
                # 不賣出：保持持有
                signal = 1
                action_code = "Hold"
                this_target = entry_price * 1.15

        # 更新持倉狀態（確保狀態正確轉換）
        position = signal
        positions.append(signal); reasons.append(reason_str); actions.append(action_code)
        target_prices.append(this_target); return_labels.append(ret_label)
        confidences.append(conf_score if action_code == "Buy" else 0)
        
    df['Position']=positions; df['Reason']=reasons; df['Action']=actions
    df['Target_Price']=target_prices; df['Return_Label']=return_labels
    df['Confidence'] = confidences
    
    df['Real_Position'] = df['Position'].shift(1).fillna(0)
    df['Market_Return'] = (df['Close'] - df['Close'].shift(1) + df['Dividends'].fillna(0)) / df['Close'].shift(1)
    df['Market_Return'] = df['Market_Return'].fillna(0)
    
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
    """
    執行策略優化，尋找最佳參數組合
    性能優化：避免重複計算指標
    """
    best_ret = -999
    best_params = None
    best_df = None
    target_start = pd.to_datetime(user_start_date)
    
    # 優化：預先計算所有需要的指標（避免在循環中重複計算）
    # 只計算一次指標，然後在循環中重用
    try:
        # 使用第一個參數組合計算指標（指標計算不依賴 m 和 r）
        df_ind_base = calculate_indicators(raw_df, 10, 3.0, market_df)
        df_slice_base = df_ind_base[df_ind_base['Date'] >= target_start].copy()
        
        if df_slice_base.empty:
            return None, pd.DataFrame()
        
        # 循環測試不同的 RSI 閾值
        for r in [25, 30]:
            # 使用相同的指標數據，只改變 RSI 閾值
            df_res = run_simple_strategy(df_slice_base.copy(), r, fee_rate, tax_rate, use_chip_strategy, use_strict_bear_exit)
            
            if df_res is None or df_res.empty:
                continue
            
            ret = df_res['Cum_Strategy'].iloc[-1] - 1
            if ret > best_ret:
                best_ret = ret
                best_params = {'Mult': 3.0, 'RSI_Buy': r, 'Return': ret}  # 固定使用 3.0
                best_df = df_res
    except Exception as e:
        # 如果出錯，返回空結果
        return None, pd.DataFrame()
    
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
# 5. [核心演算法] 買賣評等 (Alpha Score) - v11 黃金坑優化版
# ==========================================
def calculate_alpha_score(df, margin_df=None, short_df=None):
    """
    Alpha Score v12.0 (Analog Edition):
    將評分改為連續函數，反映真實市場的漸進特性
    """
    df = df.copy()

    # 1. 基礎欄位防呆與補全
    if 'RSI' not in df.columns: df['RSI'] = 50
    if 'MA20' not in df.columns: df['MA20'] = df['Close'].rolling(20).mean()
    if 'MA60' not in df.columns: df['MA60'] = df['Close'].rolling(60).mean()
    if 'MA120' not in df.columns: df['MA120'] = df['Close'].rolling(120).mean()
    if 'MA240' not in df.columns: df['MA240'] = df['Close'].rolling(240).mean()
    if 'Vol_MA20' not in df.columns: df['Vol_MA20'] = df['Volume'].rolling(20).mean()
    if 'Action' not in df.columns: df['Action'] = 'Hold'
    if 'Reason' not in df.columns: df['Reason'] = ''
    # [新增] 停損基準線（SuperTrend）防呆
    if 'SuperTrend' not in df.columns:
        # 如果沒有 SuperTrend，使用 MA60 作為替代
        df['SuperTrend'] = df['MA60'] if 'MA60' in df.columns else df['Close']
    
    # [新增] 計算年線斜率（使用變化率，而非絕對差值）
    # 計算 MA240 的 5 日變化率（百分比），這樣才能正確反映年線的上揚或下彎
    # 公式：(MA240_today - MA240_5days_ago) / MA240_5days_ago
    ma240_5days_ago = df['MA240'].shift(5)
    df['MA240_Slope'] = ((df['MA240'] - ma240_5days_ago) / ma240_5days_ago.replace(0, 1)).fillna(0)
    
    # [新增] 計算均線糾結指數（如果沒有）
    if 'Congestion_Index' not in df.columns:
        # 計算 MA60, MA120, MA240 的差距比例
        if 'MA60' in df.columns and 'MA120' in df.columns and 'MA240' in df.columns:
            ma_max = df[['MA60', 'MA120', 'MA240']].max(axis=1)
            ma_min = df[['MA60', 'MA120', 'MA240']].min(axis=1)
            # 瞬時差距比例
            raw_gap_ratio = (ma_max - ma_min) / df['Close'].replace(0, 1)
            # 60日平均差距（糾結指數）
            df['Congestion_Index'] = raw_gap_ratio.rolling(60, min_periods=1).mean().fillna(1.0)
        else:
            # 如果沒有足夠的均線，使用預設值（表示不糾結）
            df['Congestion_Index'] = 1.0

    # ==========================================
    # [新增] 定義 Analog 輔助函式
    # ==========================================
    
    def sigmoid_score(x, center=0, steepness=1, max_score=20):
        """
        S型曲線評分函式 (平滑過渡)
        x: 輸入值 (如乖離率)
        center: 中心點 (0分位置)
        steepness: 陡峭度 (越大越接近階躍)
        max_score: 最大分數
        """
        import math
        return max_score * (2 / (1 + math.exp(-steepness * (x - center))) - 1)
    
    def linear_score(x, x_min, x_max, score_min, score_max):
        """
        線性映射評分 (適合已有明確區間的指標，如 RSI)
        """
        if x <= x_min: return score_min
        if x >= x_max: return score_max
        # 線性插值
        ratio = (x - x_min) / (x_max - x_min)
        return score_min + ratio * (score_max - score_min)

    final_scores = []
    score_details = []

    # 迭代每一天進行評分
    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i-1] if i > 0 else row
        
        # === 初始化 ===
        score = 0
        reasons = [] 
        
        # ==========================================
        # A. 趨勢面 (Trend) - 改用 Analog 計算
        # ==========================================
        close = row['Close']
        ma20 = row['MA20']
        ma60 = row['MA60']
        
        # 1. [改良] 相對月線的乖離率 -> 連續評分
        if ma20 > 0:
            bias_ma20 = (close - ma20) / ma20  # 例如: +0.05 (站上5%), -0.03 (破3%)
            
            # 使用 sigmoid 函式，讓評分平滑過渡
            # 當 bias = 0 時給 0 分，bias > 0 時逐漸加分，bias < 0 時逐漸扣分
            ma20_score = sigmoid_score(bias_ma20, center=0, steepness=50, max_score=20)
            score += ma20_score
            
            if ma20_score > 10:
                reasons.append(f"股價強勢站穩月線 (乖離{bias_ma20*100:+.1f}%) (+{ma20_score:.0f})")
            elif ma20_score > 0:
                reasons.append(f"股價略高於月線 (+{ma20_score:.0f})")
            elif ma20_score > -10:
                reasons.append(f"股價略低於月線 ({ma20_score:.0f})")
            else:
                reasons.append(f"股價明顯破月線 (乖離{bias_ma20*100:+.1f}%) ({ma20_score:.0f})")
        
        # 2. [改良] 相對季線的乖離率 -> 連續評分
        if ma60 > 0:
            bias_ma60 = (close - ma60) / ma60
            ma60_score = sigmoid_score(bias_ma60, center=0, steepness=40, max_score=15)
            score += ma60_score
            
            if abs(ma60_score) > 5:
                reasons.append(f"季線乖離 {bias_ma60*100:+.1f}% ({ma60_score:+.0f})")
        
        # 3. [改良] 均線排列 -> 考慮斜率與距離
        if ma20 > 0 and ma60 > 0:
            ma_gap = (ma20 - ma60) / ma60  # 月線與季線的相對距離
            arrange_score = sigmoid_score(ma_gap, center=0, steepness=100, max_score=10)
            score += arrange_score
            
            if arrange_score > 5:
                reasons.append(f"均線多頭排列 (+{arrange_score:.0f})")
            elif arrange_score < -5:
                reasons.append(f"均線空頭排列 ({arrange_score:.0f})")

        # ==========================================
        # B. 動能面 (Momentum) - 改用分段線性
        # ==========================================
        rsi = row['RSI']
        
        # [改良] RSI 使用分段線性評分
        # 設計邏輯：30以下扣分，50-70給高分，70以上略扣分(過熱)
        if rsi >= 70:
            rsi_score = linear_score(rsi, 70, 85, 5, -5)  # 過熱區逐漸扣分
            reasons.append(f"RSI 過熱區 ({int(rsi)}) ({rsi_score:+.0f})")
        elif rsi >= 60:
            rsi_score = 10
            reasons.append(f"RSI 強勢區 ({int(rsi)}) (+10)")
        elif rsi >= 50:
            rsi_score = linear_score(rsi, 50, 60, 5, 10)
            reasons.append(f"RSI 多方區 ({int(rsi)}) (+{rsi_score:.0f})")
        elif rsi >= 30:
            rsi_score = linear_score(rsi, 30, 50, -10, 5)
            reasons.append(f"RSI 中性/偏弱 ({int(rsi)}) ({rsi_score:+.0f})")
        else:
            rsi_score = linear_score(rsi, 15, 30, -15, -10)
            reasons.append(f"RSI 超賣區 ({int(rsi)}) ({rsi_score:+.0f})")
        
        score += rsi_score
        
        # [保留] RSI 動能方向
        if i > 0 and rsi > prev_row['RSI']:
            score += 5
            reasons.append("動能增強 (+5)")

        # ==========================================
        # C. 量價與結構 (保持原邏輯，但可微調)
        # ==========================================
        vol = row['Volume']
        vol_ma = row['Vol_MA20']
        
        if vol > vol_ma and close > row['Open']:
            score += 10
            reasons.append("出量上漲 (+10)")
        elif vol > vol_ma and close < row['Open']:
            score -= 10
            reasons.append("出量下跌 (-10)")
        elif vol < vol_ma * 0.6 and abs(close - row['Open']) / close < 0.005:
            if close > ma20:
                score += 5
                reasons.append("多頭縮量惜售 (+5)")
            else:
                score -= 5
                reasons.append("空頭人氣退潮 (-5)")

        # ==========================================
        # D. 策略訊號事件 (黃金坑邏輯保持)
        # ==========================================
        action = row['Action']
        reason_str = str(row['Reason'])
        
        if action == 'Buy':
            is_panic_buy = ('反彈' in reason_str) or ('超賣' in reason_str)
            is_bull_trend = row['MA240_Slope'] > 0
            
            if is_panic_buy and is_bull_trend:
                score += 40
                reasons.insert(0, f"<b>💎 牛市黃金坑 (+40)</b>")
            else:
                score += 20
                reasons.insert(0, f"<b>🚀 策略買進訊號 ({reason_str}) (+20)</b>")
                
        elif action == 'Sell':
            score -= 30
            reasons.insert(0, f"<b>⚡ 策略賣出訊號 ({reason_str}) (-30)</b>")

        # ==========================================
        # E. 輸出格式化
        # ==========================================
        final_score = max(min(score, 100), -100)
        # [修正] 處理 NaN 值，確保可以安全轉換為 int
        if np.isnan(final_score) or not np.isfinite(final_score):
            final_score = 0.0
        final_scores.append(final_score)
        
        title_color = "#ff5252" if final_score > 0 else "#00e676"
        html_str = f"<b>Alpha Score: <span style='color:{title_color}; font-size:18px'>{int(final_score)}</span></b><br>"
        html_str += "<span style='color:#666; font-size:10px'>─── Technical Analysis ───</span><br>"
        
        # 顯示理由
        # [修正] 顯示所有正分和負分項目，確保計算過程完整
        # 使用正則表達式提取分數，確保即使格式不標準也能正確分類
        import re
        
        pos_reasons = []
        neg_reasons = []
        
        for r in reasons:
            # [修正] 嚴格匹配帶有正負號的括號數字，例如 (+15) 或 (-5)
            # 避免匹配到像 "RSI (34)" 這樣不帶正負號的參數說明
            match = re.search(r'\(([+-]\d+)\)', r)
            if match:
                score_val = int(match.group(1))
                if score_val > 0:
                    pos_reasons.append(r)
                elif score_val < 0:
                    neg_reasons.append(r)
            else:
                # 如果沒有標準分數標記，根據文字內容猜測
                if "(+" in r:
                    pos_reasons.append(r)
                elif "(-" in r:
                    neg_reasons.append(r)
                else:
                    # 如果完全沒有正負號，當作中性或提示信息
                    pass 
        
        if pos_reasons:
            html_str += f"<span style='color:#ff8a80'>{'<br>'.join(pos_reasons)}</span><br>"
        if neg_reasons:
            html_str += f"<span style='color:#b9f6ca'>{'<br>'.join(neg_reasons)}</span>"
            
        score_details.append(html_str)

    df['Alpha_Score'] = final_scores
    df['Score_Detail'] = score_details
    
    conditions = [
        (df['Alpha_Score'] >= 60), (df['Alpha_Score'] >= 20), (df['Alpha_Score'] >= -20),
        (df['Alpha_Score'] <= -60), (df['Alpha_Score'] < -20)
    ]
    choices = ["🔥 極強勢", "📈 多頭格局", "⚖️ 震盪盤整", "⚡ 極弱勢", "📉 空頭修正"]
    df['Score_Log'] = np.select(conditions, choices, default="☁️ 觀望")
    df['Recommended_Position'] = ((df['Alpha_Score'] + 100) / 2).clip(0, 100)

    return df



def calculate_alpha_score(df, margin_df=None, short_df=None):
    """
    Alpha Score v15.0 (Universal Analog Edition - 統一類比版):
    建立一套可以適用在所有個股的統一 alpha score 計算函數
    
    核心理念：
    1. 完全基於市場狀態評分，不依賴任何買賣策略
    2. 分數為正 = 買入機會，分數為負 = 賣出機會
    3. 完全 analog 化：所有評分都是連續函數，無硬性門檻
    4. 統一標準：適用於所有個股，不刻意迎合特定策略
    
    優化目標：
    - 買在起漲點：識別趨勢轉折、動量啟動、突破信號
    - 賣在高點：識別超買、動量衰竭、背離信號
    - 洗盤時不交易：識別震盪、均線糾結、低波動（分數接近0）
    - 恐慌時抄底：識別超賣、恐慌信號、價值浮現
    """
    import numpy as np
    import pandas as pd
    
    df = df.copy()

    # ==========================================
    # 0. 基礎數據準備 (計算多組均線作為參考基準)
    # ==========================================
    
    # [新增] 使用費波納契數列的均線：MA2、MA3、MA5、MA8、MA13、MA21、MA34、MA55、MA89、MA144、MA233
    # 費波納契數列：2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233
    fibonacci_periods = [2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
    for period in fibonacci_periods:
        df[f'MA{period}'] = df['Close'].rolling(period, min_periods=max(1, period//2)).mean()
    
    # 保留原有的常用均線（向後兼容）
    for period in [5, 10, 20, 30, 60, 90, 120, 180, 240]:
        if f'MA{period}' not in df.columns:
            df[f'MA{period}'] = df['Close'].rolling(period, min_periods=1).mean()
    
    # RSI
    if 'RSI' not in df.columns:
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        df['RSI'] = (100 - (100 / (1 + gain / loss))).fillna(50)
    
    # 成交量均線
    if 'Vol_MA20' not in df.columns:
        df['Vol_MA20'] = df['Volume'].rolling(20, min_periods=1).mean()
    
    # 計算價格變化率（用於識別起漲點和賣點）
    df['Price_Change'] = df['Close'].pct_change()
    df['Price_Change_5'] = df['Close'].pct_change(5)  # 5日漲幅
    df['Price_Change_20'] = df['Close'].pct_change(20)  # 20日漲幅
    
    # 計算動量指標
    df['Momentum'] = df['Close'].diff(5) / df['Close'].shift(5)  # 5日動量
    df['Momentum_Accel'] = df['Momentum'].diff()  # 動量加速度
    
    # 計算波動率（用於識別洗盤）
    df['Volatility'] = df['Close'].rolling(20).std() / df['Close'].rolling(20).mean()
    
    # 計算價格相對位置（用於識別超買超賣）
    if len(df) >= 60:
        df['Price_Position'] = (df['Close'] - df['Close'].rolling(60).min()) / (df['Close'].rolling(60).max() - df['Close'].rolling(60).min())
    else:
        df['Price_Position'] = 0.5

    # ==========================================
    # 1. 定義核心 Analog 評分函數庫
    # ==========================================
    
    def smooth_sigmoid(x, inflection=0, steepness=1):
        """
        平滑 S 型函數 (輸出範圍 -1 ~ +1)
        x: 輸入值
        inflection: 轉折點 (0 分位置)
        steepness: 陡峭度
        """
        return 2 / (1 + np.exp(-steepness * (x - inflection))) - 1
    
    def gaussian_weight(distance, sigma=1.0):
        """
        高斯權重函數 (距離越近權重越高)
        distance: 距離
        sigma: 標準差 (控制衰減速度)
        """
        return np.exp(-(distance ** 2) / (2 * sigma ** 2))
    
    def detect_breakout_signal(price, ma_dict, vol, vol_ma, price_change, momentum, momentum_accel, rsi, prev_rsi):
        """
        識別起漲點信號 (買在起漲點) - 完全 analog 化
        條件：
        1. 價格突破關鍵均線（連續函數評估）
        2. 成交量放大（連續函數評估）
        3. 動量加速（連續函數評估）
        4. RSI 從低檔翻揚（連續函數評估）
        """
        if len(ma_dict) < 2 or vol_ma == 0:
            return 0
        
        # 1. 突破信號：價格相對均線的位置（連續評估）
        ma_values = [v for v in ma_dict.values() if v > 0]
        if len(ma_values) < 2:
            return 0
        
        # 計算價格相對每條均線的乖離率，加權平均
        bias_scores = []
        weights = []
        for period, ma_value in ma_dict.items():
            if ma_value > 0:
                bias = (price - ma_value) / ma_value
                # 短期均線權重高，長期均線權重低
                weight = 1.0 / (period / 5)  # 5日均線權重=1, 20日均線權重=0.25
                bias_scores.append(bias * weight)
                weights.append(weight)
        
        if sum(weights) > 0:
            weighted_bias = sum(bias_scores) / sum(weights)
            breakthrough_signal = smooth_sigmoid(weighted_bias * 100, inflection=0, steepness=2)  # 站上均線越多，分數越高
        else:
            breakthrough_signal = 0
        
        # 2. 量能放大（連續函數）
        vol_ratio = vol / vol_ma if vol_ma > 0 else 1.0
        vol_signal = smooth_sigmoid((vol_ratio - 1.0) * 2, inflection=0, steepness=3)  # 1.0倍=0分, 1.5倍=高分
        
        # 3. 價格動量（連續函數）
        momentum_signal = smooth_sigmoid(price_change * 200, inflection=0, steepness=5)  # 漲幅越大分數越高
        
        # 4. 動量加速度（連續函數）
        accel_signal = smooth_sigmoid(momentum_accel * 1000, inflection=0, steepness=2)  # 加速度越大分數越高
        
        # 5. RSI 翻揚（連續函數）
        rsi_momentum = rsi - prev_rsi if prev_rsi > 0 else 0
        rsi_turn_signal = smooth_sigmoid(rsi_momentum, inflection=0, steepness=0.3)  # RSI上升加分
        
        # 6. RSI 位置（超賣區開始反彈）
        rsi_position_signal = 0
        if rsi < 40:  # 低檔區
            rsi_position_signal = smooth_sigmoid(40 - rsi, inflection=0, steepness=0.2)  # 越低分數越高（超賣反彈）
        
        # 綜合評分（加權平均）
        # 注意：起漲點信號應總是加分或至少不扣分
        # [降低影響力] 降低起漲點信號的加分幅度，避免假的起漲點信號導致被騙進場
        raw_score = (
            breakthrough_signal * 0.25 +  # 突破均線
            vol_signal * 0.20 +            # 量能放大
            momentum_signal * 0.20 +       # 價格動量
            accel_signal * 0.15 +         # 動量加速
            rsi_turn_signal * 0.10 +      # RSI翻揚
            rsi_position_signal * 0.10    # RSI位置
        ) * 20  # 降低從最高 +40 分到 +20 分
        
        # 確保起漲點信號不會扣分（至少為0）
        # 如果計算出來是負分，表示不是起漲點，設為0
        breakout_score = max(raw_score, 0)
        
        return breakout_score
    
    def detect_peak_signal(rsi, price_change, momentum, momentum_accel, price_position, vol, vol_ma):
        """
        識別賣點信號 (賣在高點) - 優化版，更準確識別真正的高點
        優化策略：
        1. 提高閾值，避免在非高點處觸發
        2. 要求多個條件同時滿足才認為是高點
        3. 增加價格反轉確認
        """
        # 初始化各項信號強度
        rsi_overheat = 0
        momentum_exhaustion = 0
        position_penalty = 0
        divergence_penalty = 0
        overbought_signal = 0
        reversal_signal = 0
        
        # 1. RSI 過熱（提高閾值，更嚴格）- 從55提高到70
        # 只有在 RSI 真正過熱時才扣分
        if rsi > 70:  # 只有 RSI > 70 才認為是過熱
            rsi_overheat = smooth_sigmoid((rsi - 70) / 10, inflection=0, steepness=3) * 25  # RSI > 70 開始扣分，80+ 大幅扣分
        
        # 2. 動量衰竭（增強判斷，但要求更嚴格）
        # 動量為正但加速度為負 = 動能衰竭，且需要動量足夠大才認為是衰竭
        if momentum > 0.01 and momentum_accel < -0.002:  # 要求動量足夠大且加速度明顯為負
            exhaustion_ratio = -momentum_accel / (momentum + 0.001)  # 避免除零
            momentum_exhaustion = smooth_sigmoid(exhaustion_ratio * 15, inflection=0, steepness=2.5) * 20  # 降低最大扣分
        
        # 3. 價格位置過高（提高閾值，更嚴格）- 從0.65提高到0.80
        # 只有在價格真正處於高位時才扣分
        if price_position > 0.80:  # 提高閾值，只有價格在80%以上高位才扣分
            position_penalty = smooth_sigmoid((price_position - 0.80) * 20, inflection=0, steepness=4) * 20
        
        # 4. 量價背離（更嚴格）- 要求明顯的背離
        if price_change > 0.03 and vol_ma > 0:  # 要求漲幅超過3%且成交量明顯萎縮
            vol_ratio = vol / vol_ma
            if vol_ratio < 0.7:  # 提高閾值，要求成交量明顯萎縮（<70%）
                divergence_ratio = (0.7 - vol_ratio) * price_change  # 漲越多但量越縮 = 背離越嚴重
                divergence_penalty = smooth_sigmoid(divergence_ratio * 100, inflection=0, steepness=5) * 18
        
        # 5. 漲幅過大但動能減弱（更嚴格）
        # 要求漲幅足夠大（>3%）且動能明顯減弱
        if price_change > 0.03 and momentum_accel < -0.003:  # 提高閾值，要求更明顯的動能減弱
            overbought_ratio = price_change * (-momentum_accel)
            overbought_signal = smooth_sigmoid(overbought_ratio * 100, inflection=0, steepness=4) * 15
        
        # 6. [新增] 價格反轉確認（關鍵優化）
        # 如果價格開始下跌（price_change < 0），且之前處於高位，這是反轉信號
        if price_change < -0.01 and price_position > 0.75:  # 價格下跌超過1%且處於高位
            reversal_signal = smooth_sigmoid(-price_change * 50, inflection=0, steepness=3) * 15
        
        # 綜合扣分（要求至少2個條件同時滿足才認為是高點）
        total_signals = sum([
            rsi_overheat > 5,
            momentum_exhaustion > 5,
            position_penalty > 5,
            divergence_penalty > 5,
            overbought_signal > 5,
            reversal_signal > 5
        ])
        
        # 只有當至少2個信號同時觸發時，才給予扣分
        if total_signals >= 2:
            peak_penalty = -(rsi_overheat + momentum_exhaustion + position_penalty + divergence_penalty + overbought_signal + reversal_signal)
        else:
            # 如果信號不足，只給予輕微扣分（最多-5分）
            peak_penalty = -min((rsi_overheat + momentum_exhaustion + position_penalty + divergence_penalty + overbought_signal + reversal_signal) * 0.3, 5)
        
        return peak_penalty
    
    def detect_consolidation_signal(ma_dict, price, vol, vol_ma, volatility, price_change, is_holding=False):
        """
        識別洗盤/震盪信號 (洗盤時不交易) - 完全 analog 化
        條件：
        1. 均線糾結（連續函數）
        2. 低波動（連續函數）
        3. 成交量萎縮（連續函數）
        4. 價格在窄幅震盪（連續函數）
        
        邏輯：
        - 空手時：扣分（避免買入）
        - 持有時：加分（避免賣出）
        
        返回：根據持倉狀態調整分數
        """
        if len(ma_dict) < 3 or price == 0:
            return 0
        
        # 1. 均線糾結度（連續函數）
        ma_values = [v for v in ma_dict.values() if v > 0]
        if len(ma_values) < 3:
            return 0
        
        ma_std = np.std(ma_values) / price
        # 標準差越小 = 越糾結
        convergence_ratio = ma_std / 0.03  # 標準差 < 3% 視為糾結
        convergence_signal = (1 - smooth_sigmoid(convergence_ratio, inflection=1, steepness=3)) * 15
        
        # 2. 低波動（連續函數）
        # 波動率越低 = 越可能是洗盤
        low_volatility_signal = 0
        if volatility < 0.02:  # 波動率 < 2%
            low_volatility_signal = (1 - smooth_sigmoid(volatility * 50, inflection=1, steepness=5)) * 10
        
        # 3. 成交量萎縮（連續函數）
        vol_ratio = vol / vol_ma if vol_ma > 0 else 1.0
        low_vol_signal = (1 - smooth_sigmoid(vol_ratio - 0.7, inflection=0, steepness=5)) * 10
        
        # 4. 價格窄幅震盪（連續函數）
        # 價格變化率很小 = 震盪
        narrow_range_signal = 0
        if abs(price_change) < 0.01:  # 單日變化 < 1%
            narrow_range_signal = (1 - smooth_sigmoid(abs(price_change) * 100, inflection=1, steepness=10)) * 10
        
        # 綜合震盪信號強度
        consolidation_intensity = convergence_signal + low_volatility_signal + low_vol_signal + narrow_range_signal
        
        # 根據持倉狀態調整：
        # 空手時：扣分（避免買入）
        # 持有時：加分（避免賣出）
        if is_holding:
            # 持有時，震盪洗盤是好事（避免被洗出），給予加分
            return consolidation_intensity
        else:
            # 空手時，震盪洗盤應該觀望，給予扣分
            return -consolidation_intensity
    
    def detect_panic_bottom_signal(rsi, price_change, bias_60, vol, vol_ma, price_position, momentum, ma240_slope):
        """
        識別恐慌抄底信號 (恐慌時成功抄底) - 完全 analog 化
        條件：
        1. RSI 極度超賣（連續函數）- 越恐慌，加分越多
        2. 負乖離率大（連續函數）- 越恐慌，加分越多
        3. 價格位置過低（連續函數）- 越恐慌，加分越多
        4. 恐慌性下跌後出現抵抗（連續函數）
        5. 成交量放大（有人接盤）（連續函數）
        6. 年線斜率：年線斜率为正且越大，恐慌抄底加分越多；年線斜率为负，恐慌抄底不加分
        7. [新增] 年線下彎時，禁止出現恐慌抄底機會
        """
        # [嚴格限制] 年線斜率必須 > 0.0000 時才能恐慌抄底
        # 年線斜率 <= 0.0000 時（包括下彎和接近水平），嚴禁對恐慌抄底機會進行加分
        if ma240_slope <= 0.0000:
            return 0  # 年線斜率不足，嚴禁加分
        # 1. RSI 超賣（連續函數）
        # RSI 越低分數越高（超賣反彈機會）- 越恐慌，加分越多
        oversold_signal = smooth_sigmoid((30 - rsi) / 20, inflection=0, steepness=2) * 30  # RSI < 30 開始大幅加分
        
        # 2. 負乖離（深度下跌）（連續函數）
        # 乖離率越負分數越高 - 越恐慌，加分越多
        deep_dip_signal = 0
        if bias_60 < 0:
            deep_dip_signal = smooth_sigmoid(-bias_60 * 10, inflection=0, steepness=2) * 25  # 跌破越多分數越高
        
        # 3. 價格位置過低（連續函數）
        # 價格在60日區間的低位（<20%）開始加分 - 越恐慌，加分越多
        low_position_signal = 0
        if price_position < 0.3:
            low_position_signal = smooth_sigmoid((0.3 - price_position) * 10, inflection=0, steepness=3) * 15
        
        # 4. 恐慌後抵抗（價格開始反彈）（連續函數）
        rebound_signal = 0
        if price_change > 0 and rsi < 40:  # 超賣區開始反彈
            # 反彈幅度越大，RSI越低，分數越高
            rebound_intensity = price_change * (40 - rsi) / 40
            rebound_signal = smooth_sigmoid(rebound_intensity * 100, inflection=0, steepness=5) * 20
        
        # 5. 成交量放大（有人接盤）（連續函數）
        vol_signal = 0
        if vol_ma > 0:
            vol_ratio = vol / vol_ma
            if vol_ratio > 1.0:  # 成交量放大
                vol_signal = smooth_sigmoid((vol_ratio - 1.0) * 2, inflection=0, steepness=3) * 15
        
        # 6. 動量轉正（連續函數）
        # 從負動量轉為正動量 = 止跌信號
        momentum_turn_signal = 0
        if momentum > -0.02 and momentum < 0.02:  # 動量接近0（轉折點）
            momentum_turn_signal = smooth_sigmoid((0.02 - abs(momentum)) * 50, inflection=0, steepness=5) * 10
        
        # 7. 年線斜率調整（新增）
        # 年線斜率为正且越大，恐慌抄底加分越多；年線斜率为负，恐慌抄底不加分
        ma240_slope_bonus = 0
        if ma240_slope > 0:
            # 年線斜率為正，根據斜率大小給予加分（斜率越大，加分越多）
            # 使用 sigmoid 函數平滑處理，避免過度加分
            # 假設年線斜率通常在 -1 到 1 之間，我們將其映射到 0-20 分
            ma240_slope_bonus = smooth_sigmoid(ma240_slope * 100, inflection=0, steepness=2) * 20
        # 年線斜率為負時，不加分（ma240_slope_bonus 保持為 0）
        
        # 計算基礎恐慌分數（越恐慌，加分越多）
        base_panic_score = oversold_signal + deep_dip_signal + low_position_signal + rebound_signal + vol_signal + momentum_turn_signal
        
        # 綜合加分：基礎恐慌分數 + 年線斜率加分（僅當年線斜率為正時）
        panic_bottom_score = base_panic_score + ma240_slope_bonus
        return panic_bottom_score
    
    def adaptive_ma_score(price, ma_dict, weights=None):
        """
        [核心函數] 自適應均線評分
        不再依賴單一均線，而是綜合考慮所有均線的相對位置
        
        參數:
        - price: 當前價格
        - ma_dict: {天數: 均線值} 字典
        - weights: 各均線權重 (可選)
        
        返回: 連續評分 (-50 ~ +50)
        """
        if weights is None:
            # 預設權重：短期影響大，長期影響小
            weights = {
                5: 0.05, 10: 0.10, 20: 0.20, 30: 0.15,
                60: 0.20, 90: 0.15, 120: 0.10, 180: 0.03, 240: 0.02
            }
        
        total_score = 0
        total_weight = 0
        
        for period, ma_value in ma_dict.items():
            if ma_value == 0 or np.isnan(ma_value):
                continue
                
            # 計算乖離率
            bias = (price - ma_value) / ma_value
            
            # 使用 sigmoid 函數將乖離率映射到 -1 ~ +1
            # 短期均線用較陡的曲線 (反應靈敏)
            # 長期均線用較緩的曲線 (容忍度高)
            if period <= 30:
                steepness = 50  # 短期敏感
            elif period <= 120:
                steepness = 30  # 中期適中
            else:
                steepness = 15  # 長期寬容
            
            normalized_score = smooth_sigmoid(bias, inflection=0, steepness=steepness)
            
            # 加權累積
            w = weights.get(period, 0.1)
            total_score += normalized_score * w
            total_weight += w
        
        # 歸一化並放大到 -50 ~ +50（降低影響力）
        if total_weight > 0:
            return (total_score / total_weight) * 50
        return 0
    
    def ma_alignment_score(ma_dict):
        """
        均線排列評分 (考慮所有均線的相對順序)
        完美多頭排列 (MA5 > MA10 > ... > MA240) = +1
        完美空頭排列 (MA5 < MA10 < ... < MA240) = -1
        """
        periods = sorted(ma_dict.keys())
        ma_values = [ma_dict[p] for p in periods if not np.isnan(ma_dict[p]) and ma_dict[p] > 0]
        
        if len(ma_values) < 3:
            return 0
        
        # 計算順序一致性
        ascending_count = 0
        descending_count = 0
        total_pairs = len(ma_values) - 1
        
        for i in range(total_pairs):
            if ma_values[i] > ma_values[i + 1]:
                ascending_count += 1  # 多頭排列
            elif ma_values[i] < ma_values[i + 1]:
                descending_count += 1  # 空頭排列
        
        # 計算排列度 (-1 ~ +1)
        alignment = (ascending_count - descending_count) / total_pairs
        return alignment
    
    def ma_convergence_penalty(ma_dict, price):
        """
        均線糾結懲罰 (所有均線靠太近 = 盤整，扣分)
        返回: 0 ~ -20 的懲罰分數
        """
        ma_values = [v for v in ma_dict.values() if not np.isnan(v) and v > 0]
        if len(ma_values) < 3 or price == 0:
            return 0
        
        # 計算均線間的標準差 (相對於股價)
        ma_std = np.std(ma_values) / price
        
        # 標準差越小 = 越糾結，扣分越多
        # 使用反向 sigmoid (標準差 < 3% 時開始懲罰)
        convergence_ratio = ma_std / 0.03  # 歸一化
        penalty = -20 * (1 - smooth_sigmoid(convergence_ratio, inflection=1, steepness=3))
        
        return max(penalty, -20)
    
    def rsi_continuous_score(rsi_value):
        """
        RSI 評分 (僅在極端情況下進行加扣分)
        若非極端情況，避免進行RSI加扣分
        """
        # 設計理念：
        # 只在極端情況下進行加扣分：
        # - RSI < 20：極度超賣，可以加分
        # - RSI > 80：極度超買，可以扣分
        # - 20 <= RSI <= 80：正常範圍，不加扣分
        
        if rsi_value > 80:
            # 極度超買區：RSI > 80 開始扣分
            # 使用 sigmoid 函數，RSI 越高扣分越多（最多 -15 分）
            normalized = (rsi_value - 80) / 20  # 映射到 0-1（RSI 80-100）
            score = -15 * smooth_sigmoid(normalized, inflection=0, steepness=3)  # RSI 越高扣分越多
        elif rsi_value < 20:
            # 極度超賣區：RSI < 20 開始加分
            # 使用 sigmoid 函數，RSI 越低加分越多（最多 +15 分）
            normalized = (20 - rsi_value) / 20  # 映射到 0-1（RSI 0-20）
            score = 15 * smooth_sigmoid(normalized, inflection=0, steepness=3)  # RSI 越低加分越多
        else:
            # 正常範圍：20 <= RSI <= 80，不加扣分
            score = 0
        
        return score
    
    def volume_momentum_score(current_vol, vol_ma, price_change_pct):
        """
        量價配合度評分 (連續化)
        不再是「放量 = +10 或 -10」，而是考慮放量程度與價格變化的協同性
        """
        if vol_ma == 0:
            return 0
        
        # 1. 量能比率 (1.0 = 正常，2.0 = 爆量)
        vol_ratio = current_vol / vol_ma
        
        # 2. 量價協同係數
        # 放量上漲 = 正分，放量下跌 = 負分
        # 縮量震盪 = 中性偏正(多頭)或偏負(空頭)
        
        if vol_ratio > 1.0:
            # 放量：根據漲跌幅計算
            volume_intensity = min((vol_ratio - 1.0), 2.0)  # 限制在 0-2 之間
            score = volume_intensity * 10 * smooth_sigmoid(price_change_pct, inflection=0, steepness=100)
        else:
            # 縮量：輕微懲罰 (除非是惜售)
            score = -5 * (1 - vol_ratio)  # 最多扣 -5 分
            if abs(price_change_pct) < 0.01:  # 縮量盤整
                score *= 0.5  # 減輕懲罰
        
        return np.clip(score, -15, 15)

    # ==========================================
    # 2. 逐日計算評分 (統一標準，不依賴策略)
    # ==========================================
    
    final_scores = []
    score_details = []

    for i in range(len(df)):
        row = df.iloc[i]
        prev_row = df.iloc[i - 1] if i > 0 else row
        
        score = 0  # 基礎評分（完全基於市場狀態）
        reasons = []
        score_components = []  # [新增] 記錄每個細項的分數，用於驗證加總
        
        close = row['Close']
        if close == 0 or np.isnan(close):
            final_scores.append(0)
            score_details.append("無效數據")
            continue
        
        # ==========================================
        # A. 基礎趨勢評分 (Adaptive MA Score)
        # ==========================================
        
        # [新增] 使用費波納契數列的均線進行評估
        # 費波納契數列：2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233
        fibonacci_periods = [2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233]
        ma_dict = {}
        for period in fibonacci_periods:
            col_name = f'MA{period}'
            if col_name in df.columns and not np.isnan(row[col_name]) and row[col_name] > 0:
                ma_dict[period] = row[col_name]
        
        # 如果費波納契均線不足，補充常用均線（向後兼容）
        if len(ma_dict) < 3:
            for period in [5, 10, 20, 30, 60, 90, 120, 180, 240]:
                col_name = f'MA{period}'
                if col_name in df.columns and period not in ma_dict:
                    if not np.isnan(row[col_name]) and row[col_name] > 0:
                        ma_dict[period] = row[col_name]
        
        # [新增] 分別評估短期、中期、長期的多空頭狀態
        # 短期：2, 3, 5, 8, 13, 21
        # 中期：34, 55, 89
        # 長期：144, 233
        short_periods = [2, 3, 5, 8, 13, 21]
        mid_periods = [34, 55, 89]
        long_periods = [144, 233]
        
        def evaluate_trend_direction(periods, ma_dict, price):
            """評估特定週期組的多空頭狀態，返回 -1 到 +1 之間的分數"""
            if len(periods) == 0:
                return 0
            
            valid_periods = [p for p in periods if p in ma_dict]
            if len(valid_periods) == 0:
                return 0
            
            # 計算價格相對於每條均線的乖離率
            bias_scores = []
            for period in valid_periods:
                ma_value = ma_dict[period]
                if ma_value > 0:
                    bias = (price - ma_value) / ma_value
                    bias_scores.append(bias)
            
            if len(bias_scores) == 0:
                return 0
            
            # 計算平均乖離率，使用 sigmoid 函數平滑處理
            avg_bias = np.mean(bias_scores)
            # 使用 sigmoid 將乖離率映射到 -1 到 +1
            trend_direction = smooth_sigmoid(avg_bias * 50, inflection=0, steepness=2)
            return trend_direction
        
        # 評估短期、中期、長期的多空頭狀態
        short_trend = evaluate_trend_direction(short_periods, ma_dict, close)
        mid_trend = evaluate_trend_direction(mid_periods, ma_dict, close)
        long_trend = evaluate_trend_direction(long_periods, ma_dict, close)
        
        # 根據中期、長期的多空頭狀態進行加扣分（不考慮短期）
        # 中期權重 0.6，長期權重 0.4（總和為1.0）
        trend_score = (mid_trend * 0.6 + long_trend * 0.4) * 50  # 映射到 -50 ~ +50
        
        # 獲取用於恐慌抄底判斷的指標
        ma60 = row['MA60'] if 'MA60' in row and row['MA60'] > 0 else close
        bias_60 = (close - ma60) / ma60
        rsi = row['RSI']
        prev_rsi = prev_row['RSI'] if i > 0 else rsi
        vol = row['Volume']
        vol_ma = row['Vol_MA20']
        price_change = row['Price_Change'] if not np.isnan(row['Price_Change']) else 0
        momentum = row['Momentum'] if not np.isnan(row['Momentum']) else 0
        price_position = row['Price_Position'] if not np.isnan(row['Price_Position']) else 0.5
        
        # 正常情況：記錄趨勢分（不再豁免趨勢偏空）
        score_components.append(trend_score)
        score += trend_score
        
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        if abs(trend_score) > 0.1:  # 降低閾值，顯示更多細項
            # [新增] 顯示中期、長期的多空頭評估（不顯示短期）
            trend_detail = []
            if abs(mid_trend) > 0.1:
                trend_detail.append(f"中期{'多' if mid_trend > 0 else '空'}({mid_trend:+.2f})")
            if abs(long_trend) > 0.1:
                trend_detail.append(f"長期{'多' if long_trend > 0 else '空'}({long_trend:+.2f})")
            
            if trend_detail:
                reasons.append(f"趨勢{'偏多' if trend_score > 0 else '偏空'} ({trend_score:+.1f}) [{', '.join(trend_detail)}]")
            else:
                reasons.append(f"趨勢{'偏多' if trend_score > 0 else '偏空'} ({trend_score:+.1f})")
        
        # ==========================================
        # A-2. 費波納契均線評分（新增）
        # ==========================================
        # 若股價低於費波納契均線則扣分，高於則加分
        fibonacci_ma_score = 0
        if len(ma_dict) > 0:
            # 計算股價相對於所有費波納契均線的加權平均乖離率
            total_weight = 0
            weighted_bias = 0
            
            for period, ma_value in ma_dict.items():
                if ma_value > 0:
                    # 計算乖離率
                    bias = (close - ma_value) / ma_value
                    # 短期均線權重高，長期均線權重低
                    weight = 1.0 / (period / 5)  # 5日均線權重=1, 20日均線權重=0.25
                    weighted_bias += bias * weight
                    total_weight += weight
            
            if total_weight > 0:
                avg_bias = weighted_bias / total_weight
                # 使用 sigmoid 函數平滑處理，股價高於均線加分，低於均線扣分
                # 最多 ±20 分
                fibonacci_ma_score = smooth_sigmoid(avg_bias * 50, inflection=0, steepness=2) * 20
            
            score_components.append(fibonacci_ma_score)
            score += fibonacci_ma_score
            
            # 顯示費波納契均線評分
            if abs(fibonacci_ma_score) > 0.5:
                reasons.append(f"費波納契均線{'上方' if fibonacci_ma_score > 0 else '下方'} ({fibonacci_ma_score:+.1f})")
        
        # 均線排列加分/扣分
        alignment = ma_alignment_score(ma_dict)
        alignment_score = alignment * 15
        score_components.append(alignment_score)
        score += alignment_score
        
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        if abs(alignment_score) > 0.1:  # 降低閾值，顯示更多細項
            reasons.append(f"均線{'多排' if alignment > 0 else '空排'} ({alignment_score:+.1f})")
        
        # ==========================================
        # B. 動能評分 (Continuous RSI)
        # ==========================================
        
        rsi_score = rsi_continuous_score(rsi)
        score_components.append(rsi_score)
        score += rsi_score
        
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        if abs(rsi_score) > 0.1:  # 降低閾值，顯示更多細項
            reasons.append(f"RSI ({int(rsi)}) ({rsi_score:+.1f})")
        
        # RSI 動能方向
        if i > 0:
            rsi_momentum = rsi - prev_rsi
            momentum_score = smooth_sigmoid(rsi_momentum, inflection=0, steepness=0.5) * 5
            if abs(momentum_score) > 0.5:
                score_components.append(momentum_score)
                score += momentum_score
                reasons.append(f"動能{'增強' if momentum_score > 0 else '減弱'} ({momentum_score:+.0f})")
        
        # ==========================================
        # C. 量價配合度（已移除）
        # ==========================================
        # [移除] 不再考慮成交量的加扣分計算
        vol_score = 0  # 設為0，不再影響評分
        
        # ==========================================
        # C-2. 停損基準線評分（修改：只在穿越當天有效）
        # ==========================================
        # 只在向上穿越或向下穿越停損基準線的當天才進行加扣分，其他日期則不透過停損基準線進行加扣分
        supertrend_score = 0
        supertrend = row['SuperTrend'] if 'SuperTrend' in row and not np.isnan(row['SuperTrend']) and row['SuperTrend'] > 0 else close
        
        if supertrend > 0 and i > 0:
            # 獲取前一日數據
            prev_close = prev_row['Close'] if 'Close' in prev_row and not np.isnan(prev_row['Close']) else close
            prev_supertrend = prev_row['SuperTrend'] if 'SuperTrend' in prev_row and not np.isnan(prev_row['SuperTrend']) and prev_row['SuperTrend'] > 0 else prev_close
            
            # 判斷是否發生穿越
            # 向上穿越：前一日股價 <= 停損基準線，今日股價 > 停損基準線
            # 向下穿越：前一日股價 >= 停損基準線，今日股價 < 停損基準線
            is_cross_up = (prev_close <= prev_supertrend) and (close > supertrend)
            is_cross_down = (prev_close >= prev_supertrend) and (close < supertrend)
            
            if is_cross_up:
                # 向上穿越：加分
                supertrend_bias = (close - supertrend) / supertrend
                supertrend_score = smooth_sigmoid(supertrend_bias * 50, inflection=0, steepness=2) * 20  # 最多 +20 分
                score_components.append(supertrend_score)
                score += supertrend_score
                if abs(supertrend_score) > 0.5:
                    reasons.append(f"停損基準線向上穿越 (乖離{supertrend_bias*100:+.1f}%) ({supertrend_score:+.1f})")
            elif is_cross_down:
                # 向下穿越：扣分
                supertrend_bias = (close - supertrend) / supertrend
                supertrend_score = smooth_sigmoid(supertrend_bias * 50, inflection=0, steepness=2) * 20  # 最多 -20 分（負數）
                score_components.append(supertrend_score)
                score += supertrend_score
                if abs(supertrend_score) > 0.5:
                    reasons.append(f"停損基準線向下穿越 (乖離{supertrend_bias*100:+.1f}%) ({supertrend_score:+.1f})")
            # 其他日期：不進行加扣分（supertrend_score = 0）
        
        # ==========================================
        # D. 核心策略識別 (買在起漲點、賣在高點、洗盤不交易、恐慌抄底)
        # ==========================================
        
        # 獲取額外指標
        momentum_accel = row['Momentum_Accel'] if not np.isnan(row['Momentum_Accel']) else 0
        volatility = row['Volatility'] if not np.isnan(row['Volatility']) else 0.02
        
        # 1. 起漲點識別
        breakout_score = detect_breakout_signal(close, ma_dict, vol, vol_ma, price_change, momentum, momentum_accel, rsi, prev_rsi)
        score_components.append(breakout_score)  # [修正] 無論大小都記錄，確保加總匹配
        score += breakout_score
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        if abs(breakout_score) > 0.1:  # 降低閾值，顯示更多細項
            reasons.append(f"起漲點信號 ({breakout_score:+.1f})")
        
        # 2. 賣點識別
        peak_penalty = detect_peak_signal(rsi, price_change, momentum, momentum_accel, price_position, vol, vol_ma)
        score_components.append(peak_penalty)  # [修正] 無論大小都記錄，確保加總匹配
        score += peak_penalty
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        # [修正] 高點警示應為扣分，peak_penalty已經是負數，顯示時保持負號
        if abs(peak_penalty) > 0.1:  # 降低閾值，顯示更多細項
            reasons.append(f"高點警示 ({peak_penalty:+.1f})")  # peak_penalty已經是負數，會顯示為 (-X.X)
        
        # 3. 洗盤識別（根據持倉狀態調整）
        # 判斷是否持有中（通過 Action 字段判斷）
        # [修正] 買入當日應視為空手（因為買入決策是在當天開盤或盤中做的，當時是空手）
        # 只有 Action='Hold' 或 'Sell' (賣出當日還在持有) 才視為持有
        action = row['Action'] if 'Action' in row else 'Hold'
        is_holding = (action == 'Hold' or action == 'Sell')
        
        consolidation_score = detect_consolidation_signal(ma_dict, close, vol, vol_ma, volatility, price_change, is_holding)
        score_components.append(consolidation_score)  # [修正] 無論大小都記錄，確保加總匹配
        score += consolidation_score
        
        # [修正] 無論大小都顯示，確保顯示的細項加總與最終分數匹配
        if abs(consolidation_score) > 0.1:  # 降低閾值，顯示更多細項
            if is_holding:
                reasons.append(f"震盪洗盤(持有加分) ({consolidation_score:+.1f})")
            else:
                reasons.append(f"震盪洗盤(空手扣分) ({consolidation_score:+.1f})")
        
        # 4. 恐慌抄底識別
        # [徹底檢查] 獲取年線斜率，確保正確處理 NaN 和邊界情況
        ma240_slope = 0
        if 'MA240_Slope' in row:
            slope_value = row['MA240_Slope']
            if not np.isnan(slope_value):
                ma240_slope = float(slope_value)
        
        # [嚴格限制] 年線斜率必須 > 0.001 時才能恐慌抄底
        # 年線斜率 <= 0.001 時（包括下彎和接近水平），完全跳過恐慌抄底相關的所有計算
        # 絕對不進行任何計算，也不顯示任何恐慌抄底相關信息
        if ma240_slope <= 0.001:
            # 年線斜率不足，不計算恐慌抄底分數，也不顯示任何恐慌抄底相關信息
            panic_bottom_score = 0
            # 為了保持 score_components 的一致性，添加 0（但不影響總分）
            score_components.append(panic_bottom_score)
            # 不加到 score（因為是0），也不顯示
            # 完全跳過恐慌抄底相關的所有操作
        else:
            # 只有當年線斜率 > 0.001 時，才進行恐慌抄底相關計算
            panic_bottom_score = detect_panic_bottom_signal(rsi, price_change, bias_60, vol, vol_ma, price_position, momentum, ma240_slope)
            score_components.append(panic_bottom_score)  # 只有當年線斜率足夠時才記錄
            score += panic_bottom_score  # 只有當年線斜率足夠時才加到總分
            # 只有當年線斜率足夠且分數>0.1時才顯示
            if abs(panic_bottom_score) > 0.1:
                reasons.append(f"恐慌抄底機會 ({panic_bottom_score:+.1f}, 年線斜率:{ma240_slope:+.4f})")
        
        # ==========================================
        # D-2. 均線糾結指數評分（新增）
        # ==========================================
        # 均線糾結指數越低，空手觀望狀態扣分越多，持有狀態加分越多
        # 目的是在均線糾結時，不輕易進行交易
        congestion_index = row['Congestion_Index'] if 'Congestion_Index' in row and not np.isnan(row['Congestion_Index']) else 1.0
        # 均線糾結指數通常在 0-1 之間，越低表示越糾結
        # 使用 sigmoid 函數將糾結指數轉換為評分
        # 糾結指數越低（越糾結），評分影響越大
        # 當糾結指數 < 0.05 (5%) 時，認為是高度糾結
        congestion_intensity = 1.0 - smooth_sigmoid(congestion_index * 20, inflection=0.5, steepness=3)  # 0-1之間，越低越糾結
        
        if is_holding:
            # 持有狀態：均線糾結時加分（避免被洗出）
            # 糾結指數越低，加分越多（最多+15分）
            congestion_score = congestion_intensity * 15
            score_components.append(congestion_score)
            score += congestion_score
            if abs(congestion_score) > 0.5:
                reasons.append(f"均線糾結(持有加分, 指數:{congestion_index*100:.1f}%) ({congestion_score:+.1f})")
        else:
            # 空手狀態：均線糾結時扣分（避免輕易買入）
            # 糾結指數越低，扣分越多（最多-15分）
            congestion_score = -congestion_intensity * 15
            score_components.append(congestion_score)
            score += congestion_score
            if abs(congestion_score) > 0.5:
                reasons.append(f"均線糾結(空手扣分, 指數:{congestion_index*100:.1f}%) ({congestion_score:+.1f})")
        
        # ==========================================
        # E. [新增] 持有信心加分 / 觀望扣分 (避免高頻交易)
        # ==========================================
        # 根據市場情況動態調整，作為買賣的 buffer
        # 持有时：根據市場強度給予信心加分（避免輕易賣出）
        # 空手時：根據市場強度給予觀望扣分（避免輕易買入）
        
        # 計算市場強度指標（0-1之間，1表示市場很強）
        market_strength = 0.0
        
        # 1. 趨勢強度（0-0.4）
        # 趨勢評分範圍是 -50 ~ +50，所以除數調整為 25.0
        if trend_score > 0:
            market_strength += min(trend_score / 25.0, 0.4)  # 趨勢分越高，市場越強
        else:
            market_strength += max(trend_score / 25.0, -0.2)  # 趨勢分為負時，降低強度
        
        # 2. RSI 強度（0-0.3）
        if rsi > 50:
            market_strength += min((rsi - 50) / 50.0, 0.3)  # RSI越高，市場越強
        else:
            market_strength += max((rsi - 50) / 50.0, -0.15)  # RSI低時，降低強度
        
        # 3. 動能強度（0-0.2）
        if momentum > 0:
            market_strength += min(momentum * 2.0, 0.2)  # 動能為正時加分
        else:
            market_strength += max(momentum * 2.0, -0.1)  # 動能為負時扣分
        
        # 4. 量價配合（已移除）
        # [移除] 不再考慮成交量的加扣分計算
        
        # 限制市場強度在合理範圍
        market_strength = np.clip(market_strength, -0.5, 1.0)
        
        # 根據持倉狀態和市場強度計算 buffer 分數（增加影響力，避免交易頻率太高）
        if is_holding:
            # 持有时：市場越強，信心加分越多（避免在強勢時輕易賣出）
            # 市場強度為正時給予加分，為負時給予扣分（但幅度較小）
            holding_confidence = market_strength * 15.0  # 增加從最大 ±8 分到 ±15 分
            # 如果市場很弱（market_strength < -0.3），減少信心加分，甚至扣分
            if market_strength < -0.3:
                holding_confidence = market_strength * 20.0  # 市場很弱時，扣分更多（從12增加到20）
        else:
            # 空手時：市場越弱，觀望扣分越多（避免在弱勢時輕易買入）
            # 市場強度為負時給予扣分，為正時給予加分（但幅度較小）
            waiting_penalty = -market_strength * 12.0  # 增加從最大 ±6 分到 ±12 分
            # 如果市場很強（market_strength > 0.3），減少觀望扣分，甚至加分
            if market_strength > 0.3:
                waiting_penalty = -market_strength * 8.0  # 市場很強時，扣分較少（從4增加到8）
            holding_confidence = waiting_penalty
        
        # 限制 buffer 分數範圍（提高上限，增加影響力）
        holding_confidence = np.clip(holding_confidence, -20.0, 20.0)
        
        score_components.append(holding_confidence)
        score += holding_confidence
        
        # 顯示 buffer 分數
        if abs(holding_confidence) > 0.5:
            if is_holding:
                reasons.append(f"持有信心 ({holding_confidence:+.1f})")
            else:
                reasons.append(f"觀望扣分 ({holding_confidence:+.1f})")
        
        # ==========================================
        # F. 最終輸出（不進行任何策略相關的調整）
        # ==========================================
        
        final_score = np.clip(score, -100, 100)
        # [修正] 處理 NaN 值，確保可以安全轉換為 int
        if np.isnan(final_score) or not np.isfinite(final_score):
            final_score = 0.0
        final_scores.append(final_score)
        
        # 生成詳細說明
        title_color = "#ff5252" if final_score > 0 else "#00e676"
        html_str = f"<b>Alpha Score: <span style='color:{title_color}; font-size:18px'>{int(final_score)}</span></b><br>"
        html_str += "<span style='color:#666; font-size:10px'>─── Full Analog Analysis ───</span><br>"
        
        # [修正] 從顯示的 reasons 中提取分數並計算加總，確保顯示的細項加總與最終分數匹配
        import re
        pos_reasons = []
        neg_reasons = []
        neutral_reasons = []
        displayed_sum = 0  # 計算顯示的細項加總
        
        # 分類顯示 reasons 並提取分數
        for r in reasons:
            # 提取分數標記
            score_matches = re.findall(r'\(([+-]?\d+(?:\.\d+)?)\)', r)
            
            if score_matches:
                try:
                    score_val = float(score_matches[0])
                    displayed_sum += score_val  # 累加顯示的分數
                    if score_val > 0:
                        pos_reasons.append(r)
                    elif score_val < 0:
                        neg_reasons.append(r)
                    else:
                        neutral_reasons.append(r)
                except ValueError:
                    neutral_reasons.append(r)
            else:
                # 如果沒有分數標記，檢查是否包含正負號提示
                if "(+" in r or "加分" in r or "增強" in r or "調整" in r:
                    pos_reasons.append(r)
                elif "(-" in r or "扣分" in r or "減弱" in r or "背離" in r:
                    neg_reasons.append(r)
                else:
                    neutral_reasons.append(r)
        
        # [移除] 不再顯示"其他調整"項，讓顯示的細項更清晰
        # 如果顯示的細項加總與最終分數不匹配，會在驗證行顯示警告
        
        # 顯示所有細項
        if pos_reasons:
            html_str += f"<span style='color:#ff8a80'>{'<br>'.join(pos_reasons)}</span><br>"
        if neg_reasons:
            html_str += f"<span style='color:#b9f6ca'>{'<br>'.join(neg_reasons)}</span><br>"
        if neutral_reasons:
            html_str += f"<span style='color:#888'>{'<br>'.join(neutral_reasons)}</span><br>"
        
        # [移除] 不再顯示細項加總驗證，保持界面簡潔
        
        score_details.append(html_str)
    
    # ==========================================
    # 3. 回寫 DataFrame
    # ==========================================
    
    df['Alpha_Score'] = final_scores
    df['Score_Detail'] = score_details
    
    conditions = [
        (df['Alpha_Score'] >= 60), 
        (df['Alpha_Score'] >= 20), 
        (df['Alpha_Score'] >= -20),
        (df['Alpha_Score'] <= -60), 
        (df['Alpha_Score'] < -20)
    ]
    choices = ["🔥 極強勢", "📈 多頭格局", "⚖️ 震盪盤整", "⚡ 極弱勢", "📉 空頭修正"]
    df['Score_Log'] = np.select(conditions, choices, default="☁️ 觀望")
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
    
    # 檢查 market_df 是否為空或缺少必要欄位
    if market_df.empty:
        st.error("無大盤數據")
        return
    
    if 'Date' not in market_df.columns:
        st.error("大盤數據缺少 Date 欄位")
        return
    
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
    
    st.plotly_chart(fig, width='stretch')


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
            if st.button("Go", type="primary", width='stretch'):
                # 強制重跑
                st.session_state['last_ticker'] = st.session_state['stock_selector'].split(" ")[0]
                st.rerun()

    # --- Row 2: 上一檔 / 下一檔 ---
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        st.button("◀ 上一檔", width='stretch', on_click=on_button_click, args=(-1,))

    with col_next:
        st.button("下一檔 ▶", width='stretch', on_click=on_button_click, args=(1,))

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
            try:
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
                    with st.spinner('正在執行策略優化...'):
                        best_params, final_df = run_optimization(
                            raw_df, market_df, start_date, current_fee, current_tax, 
                            use_chip_strategy=enable_chip_strategy,
                            use_strict_bear_exit=enable_strict_bear_exit
                        )
                    
                    # 4. 執行驗證（可選，如果太慢可以註解掉）
                    if final_df is not None and not final_df.empty:
                        with st.spinner('正在驗證策略穩健性...'):
                            try:
                                validation_result = validate_strategy_robust(raw_df, market_df, 0.7, current_fee, current_tax)
                            except Exception as e:
                                st.warning(f"策略驗證過程出現錯誤: {str(e)}")
                                validation_result = None
            except Exception as e:
                st.error(f"❌ 分析過程出現錯誤: {str(e)}")
                st.info("請嘗試重新整理頁面或檢查股票代號是否正確")
                final_df = None
                best_params = None
                validation_result = None

            # 4. 顯示結果 (檢查 final_df 是否存在且不為空)
            if final_df is None or final_df.empty:
                if not raw_df.empty: # 如果有原始資料但策略跑不出結果 (極少見)
                    st.warning("⚠️ 選定區間內無足夠資料進行策略運算 (可能上市時間太短)。")
            else:
                # ... (以下顯示邏輯保持不變，直接沿用原本的程式碼即可) ...
                # 為節省篇幅，請保留您原本從 `stock_alpha_df = calculate_alpha_score(...)` 開始的後續顯示程式碼
                # 只要替換上方這段輸入控制邏輯即可
                
                # [以下為原本的代碼接續點，請確認您的代碼中有這部分]
                # 計算 Alpha Score（確保賣出當日視為持有狀態）
                # 注意：calculate_alpha_score 內部已經處理了賣出當日視為持有狀態的邏輯
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
                
                # [Tab 1: K線圖]
                with tab1:
                    # 1. 準備數據
                    # 確保 Alpha_Score 與柱狀圖一致（基於 calculate_alpha_score 的結果）
                    # 注意：calculate_alpha_score 已經處理了賣出當日視為持有狀態的邏輯
                    final_df['Alpha_Score'] = stock_alpha_df['Alpha_Score'].values

                    if 'Score_Detail' in stock_alpha_df.columns:
                        final_df['Score_Detail'] = stock_alpha_df['Score_Detail'].values
                    else:
                        # 防呆：萬一上游沒算出來，填入空字串避免報錯
                        final_df['Score_Detail'] = ""

                    # [修正] 計算 Alpha Slope 並加入平滑處理
                    # 方法 1: 使用移動平均 (MA)
                    # final_df['Alpha_Slope'] = final_df['Alpha_Score'].diff().rolling(5, min_periods=1).mean().fillna(0)

                    # 方法 2 (可選): 使用指數移動平均 (EMA) - 更平滑且更即時
                    final_df['Alpha_Slope'] = final_df['Alpha_Score'].diff().ewm(span=5, adjust=False).mean().fillna(0)
                    
                    # 確保長均線存在
                    if 'MA120' not in final_df.columns: final_df['MA120'] = final_df['Close'].rolling(120).mean()
                    if 'MA240' not in final_df.columns: final_df['MA240'] = final_df['Close'].rolling(240).mean()
                    
                    # 計算均線糾結度 (MA Congestion)
                    ma_subset = final_df[['MA60', 'MA120', 'MA240']].ffill().bfill()
                    ma_max = ma_subset.max(axis=1)
                    ma_min = ma_subset.min(axis=1)
                    
                    # 1. 瞬時 GAP
                    raw_gap = (ma_max - ma_min) / final_df['Close'] * 100
                    
                    # 2. 20日平均 GAP (糾結指數)
                    congestion_idx = raw_gap.rolling(20, min_periods=1).mean().fillna(100)
                    final_df['Congestion_Index'] = congestion_idx
                    
                    # [新增] 3. 糾結度斜率 (Slope) - 判斷發散或收斂
                    # 正值 = 發散中 (趨勢加速)
                    # 負值 = 收斂中 (進入盤整)
                    congestion_slope = congestion_idx.diff().fillna(0)
                    final_df['Congestion_Slope'] = congestion_slope

                    # 2. 建立子圖 (Rows 增加為 8)
                    fig = make_subplots(
                        rows=8, cols=1, 
                        shared_xaxes=True, 
                        vertical_spacing=0.02, 
                        # 調整高度比例
                        row_heights=[0.30, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10], 
                        subplot_titles=(
                            "", 
                            "買賣評等 (Alpha Score)", 
                            "評分動能 (Alpha Slope)", 
                            "成交量", 
                            "法人籌碼 (OBV)", 
                            "相對強弱指標 (RSI)",
                            "均線糾結指數 (20MA Gap%)",
                            "糾結度變化 (Slope)" # [新增標題]
                        )
                    )
            
                    # --- Row 1: K線 (含年線/半年線) ---
                    fig.add_trace(go.Candlestick(
                        x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                        low=final_df['Low'], close=final_df['Close'], name='K線',
                        increasing_line_color='#ef5350', decreasing_line_color='#00bfa5' 
                    ), row=1, col=1)
                    
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['SuperTrend'], mode='lines', line=dict(color='yellow', width=1.5), name='停損基準線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線'), row=1, col=1)
                    if 'MA120' in final_df.columns:
                        fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA120'], mode='lines', line=dict(color='#2979ff', width=1.5), name='半年線'), row=1, col=1)
                    if 'MA240' in final_df.columns:
                        fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA240'], mode='lines', line=dict(color='#e040fb', width=1.5), name='年線'), row=1, col=1)

                    # 買賣點標記
                    final_df['Buy_Y'] = final_df['Low'] * 0.92
                    final_df['Sell_Y'] = final_df['High'] * 1.08
                    
                    def get_buy_text(sub_df): return [f"<b>{int(score)}</b>" for score in sub_df['Alpha_Score']]
                    def get_sell_text(sub_df):
                        """
                        生成賣出標記的文本
                        確保顯示的分數與 Alpha Score 柱狀圖一致（基於 calculate_alpha_score 的結果）
                        """
                        labels = []
                        for idx, row in sub_df.iterrows():
                            ret = row['Return_Label']
                            # 清洗 Reason 字串，移除裡面可能包含的 "分數:..." 資訊，避免重複顯示
                            raw_reason = row['Reason'].replace("觸發", "").replace("操作", "")
                            import re
                            reason_str = re.sub(r'\(分數:.*?\)', '', raw_reason).strip()
                            
                            # 確保從 final_df 中獲取正確的 Alpha_Score（與柱狀圖一致）
                            # 使用日期匹配，確保分數正確
                            date_val = row['Date']
                            matching_row = final_df[final_df['Date'] == date_val]
                            
                            if not matching_row.empty and 'Alpha_Score' in matching_row.columns:
                                # 使用匹配行的 Alpha_Score（與柱狀圖一致）
                                alpha_score = int(matching_row['Alpha_Score'].iloc[0])
                            elif 'Alpha_Score' in row:
                                # 備用：直接從 row 讀取
                                alpha_score = int(row['Alpha_Score'])
                            else:
                                alpha_score = 0
                            
                            labels.append(f"{ret}<br>({reason_str})<br><b>分數: {alpha_score}</b>")
                        return labels

                    buy_trend = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('突破|回測|動能'))]
                    if not buy_trend.empty:
                        fig.add_trace(go.Scatter(x=buy_trend['Date'], y=buy_trend['Buy_Y'], mode='markers+text', text=get_buy_text(buy_trend), textposition="bottom center", textfont=dict(color='#FFD700', size=11), marker=dict(symbol='triangle-up', size=14, color='#FFD700', line=dict(width=1, color='black')), name='買進 (趨勢)', hovertext=buy_trend['Reason']), row=1, col=1)
                    
                    buy_panic = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('反彈|超賣'))]
                    if not buy_panic.empty:
                        fig.add_trace(go.Scatter(x=buy_panic['Date'], y=buy_panic['Buy_Y'], mode='markers+text', text=get_buy_text(buy_panic), textposition="bottom center", textfont=dict(color='#00FFFF', size=11), marker=dict(symbol='triangle-up', size=14, color='#00FFFF', line=dict(width=1, color='black')), name='買進 (反彈)', hovertext=buy_panic['Reason']), row=1, col=1)
                    
                    buy_chip = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('籌碼|佈局'))]
                    if not buy_chip.empty:
                        fig.add_trace(go.Scatter(x=buy_chip['Date'], y=buy_chip['Buy_Y'], mode='markers+text', text=get_buy_text(buy_chip), textposition="bottom center", textfont=dict(color='#DDA0DD', size=11), marker=dict(symbol='triangle-up', size=14, color='#DDA0DD', line=dict(width=1, color='black')), name='買進 (籌碼)', hovertext=buy_chip['Reason']), row=1, col=1)

                    # [新增] 捕捉所有其他買入信號 (避免遺漏)
                    # 排除已經畫過的類別
                    known_types = '突破|回測|動能|反彈|超賣|籌碼|佈局'
                    buy_other = final_df[(final_df['Action'] == 'Buy') & (~final_df['Reason'].str.contains(known_types))]
                    if not buy_other.empty:
                        fig.add_trace(go.Scatter(x=buy_other['Date'], y=buy_other['Buy_Y'], mode='markers+text', text=get_buy_text(buy_other), textposition="bottom center", textfont=dict(color='#FFFFFF', size=11), marker=dict(symbol='triangle-up', size=14, color='#FFFFFF', line=dict(width=1, color='black')), name='買進 (其他)', hovertext=buy_other['Reason']), row=1, col=1)

                    sell_all = final_df[final_df['Action'] == 'Sell']
                    if not sell_all.empty:
                        fig.add_trace(go.Scatter(x=sell_all['Date'], y=sell_all['Sell_Y'], mode='markers+text', text=get_sell_text(sell_all), textposition="top center", textfont=dict(color='white', size=11), marker=dict(symbol='triangle-down', size=14, color='#FF00FF', line=dict(width=1, color='black')), name='賣出', hovertext=sell_all['Reason']), row=1, col=1)

                    # --- Row 2: Alpha Score (Updated with Hover Detail) ---
                    colors_score = ['#ef5350' if v > 0 else '#26a69a' for v in final_df['Alpha_Score']]
             
                    fig.add_trace(go.Bar(
                        x=final_df['Date'], 
                        y=final_df['Alpha_Score'], 
                        name='Alpha Score', 
                        marker_color=colors_score,
                        # [關鍵修改] 綁定詳細 HTML 到 hovertext
                        hovertext=final_df['Score_Detail'],
                        # [設定] 顯示模式：只顯示我們自訂的 hovertext，加上 x 軸日期
                        hoverinfo="x+text" 
                    ), row=2, col=1)

                    # 設定 Y 軸範圍固定，視覺上比較穩定
                    fig.update_yaxes(range=[-110, 110], row=2, col=1)


                    # --- Row 3: Alpha Slope ---
                    colors_slope = ['#ef5350' if v > 0 else ('#26a69a' if v < 0 else 'gray') for v in final_df['Alpha_Slope']]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Alpha_Slope'], name='Alpha Slope', marker_color=colors_slope), row=3, col=1)
                    fig.add_hline(y=0, line_width=1, line_color="gray", row=3, col=1)

                    # --- Row 4: 成交量 ---
                    colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'] / 1000, marker_color=colors_vol, name='成交量(張)'), row=4, col=1)
                    
                    # --- Row 5: OBV ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=5, col=1)
                    
                    # --- Row 6: RSI ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=6, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=6, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=6, col=1)
                    
                    # --- Row 7: 均線糾結指數 (Congestion Index) ---
                    colors_gap = []
                    for v in congestion_idx:
                        if v < 5: colors_gap.append('#ef5350') # 紅色警戒 (糾結)
                        elif v < 15: colors_gap.append('#ffd740')
                        else: colors_gap.append('#00e676') # 綠色 (發散)
                    
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Congestion_Index'], name='均線糾結指數(60日)', marker_color=colors_gap), row=7, col=1)
                    fig.add_hline(y=5, line_width=1, line_dash="dash", line_color="red", annotation_text="糾結警戒(5%)", row=7, col=1)

                    # --- [新增] Row 8: 糾結度斜率 (Slope) ---
                    # 綠色: 發散中 (Gap變大，趨勢加速)
                    # 紅色: 收斂中 (Gap變小，趨勢休息)
                    colors_cong_slope = ['#00e676' if v > 0 else '#ef5350' for v in final_df['Congestion_Slope']]
                    fig.add_trace(go.Bar(
                        x=final_df['Date'], 
                        y=final_df['Congestion_Slope'], 
                        name='差距變動(Slope)', 
                        marker_color=colors_cong_slope
                    ), row=8, col=1)
                    fig.add_hline(y=0, line_width=1, line_color="gray", row=8, col=1)

                    # Layout
                    fig.update_layout(height=1600, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=20, r=40, t=30, b=20),
                                                    legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1))
                    fig.update_yaxes(side='right')
                    st.plotly_chart(fig, width='stretch')


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
                    st.plotly_chart(fig_c, width='stretch')
                    
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
                        st.plotly_chart(fig_bucket, width='stretch')
                        
                        st.markdown("#### 📋 詳細統計數據")
                        display_table = pd.DataFrame({
                            '樣本數': group_stats['Count'],
                            '平均漲幅(5日)': (group_stats['Avg_Ret_5d']*100).map('{:+.2f}%'.format),
                            '上漲機率': win_rates.map('{:.1f}%'.format),
                            '期望值': (group_stats['Avg_Ret_5d'] * 100).map('{:+.2f}%'.format)
                        })
                        st.dataframe(display_table.T, width='stretch')
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
        st.button("🔥 啟動戰略掃描", type="primary", width='stretch', on_click=start_scan_callback)
        
    with col_stop:
        st.button("🛑 強制停止", width='stretch', on_click=stop_scan_callback)

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
                            # [修正] 處理 NaN 值，確保可以安全轉換為 int
                            if np.isnan(final_score) or not np.isfinite(final_score):
                                final_score = 0.0
                            
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
                                # 這裡可以使用 Score_Log，或者擷取 Score_Detail 的純文字版 (若需要)
                                "計算過程": base_log, 
                                "回測報酬": best_params['Return'],
                                "板塊": current_sector,
                                # [新增] 如果你想在 raw data 裡保留 HTML 詳情以便除錯
                                "評分詳情": stock_alpha_df['Score_Detail'].iloc[-1] 
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
            
            st.plotly_chart(fig_scatter, width='stretch')
            
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
                width='stretch',
                column_order=["代號", "名稱", "Alpha_Score", "建議", "收盤價", "漲跌幅", "回測報酬", "計算過程"]
            )
            
            with st.expander("📄 查看完整掃描清單", expanded=False):
                 st.dataframe(
                    df_res.sort_values(by='Alpha_Score', ascending=False)
                    .style.format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}", "漲跌幅": "{:.2%}"})
                    .background_gradient(subset=['Alpha_Score'], cmap='Reds'),
                    width='stretch'
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
        st.dataframe(df_show, width='stretch', hide_index=True)

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
                width='stretch', 
                key="portfolio_editor_widget", 
                column_order=["代號", "名稱", "持有股數"],
                column_config={
                    "代號": st.column_config.TextColumn("股票代號", help="輸入代號 (如 2330)"),
                    "名稱": st.column_config.TextColumn("股票名稱", disabled=True, help="儲存後自動更新"), 
                    "持有股數": st.column_config.NumberColumn("持有股數 (股)", min_value=1, step=1000, format="%d")
                }
            )
            
            # 表單提交按鈕
            submit_btn = st.form_submit_button("💾 確認儲存並分析", type="primary", width='stretch')

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
                # [修正] 處理 NaN 值，確保可以安全轉換為 int
                if np.isnan(final_score) or not np.isfinite(final_score):
                    final_score = 0.0

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
                st.plotly_chart(fig_g, width='stretch')
            
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
                width='stretch'
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
        if st.button("🚀 開始全遍歷驗證", type="primary", width='stretch'):
            st.session_state['lab_running'] = True
            st.session_state['lab_stop'] = False
            st.session_state['lab_results'] = [] # 重置
    with c_stop:
        if st.button("🛑 強制停止", width='stretch'):
            st.session_state['lab_running'] = False
            st.session_state['lab_stop'] = True
    with c_clear:
        if st.button("🗑️ 清除結果", width='stretch'):
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
            st.plotly_chart(fig_dist, width='stretch')
            
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
            st.plotly_chart(fig_regime, width='stretch')

        with tab_v3:
            st.markdown("#### 恐慌抄底 (Panic Rebound) 有效性驗證")
            df_panic = df_res[df_res['抄底次數'] > 0].copy()
            if not df_panic.empty:
                fig_panic = px.box(df_panic, y="抄底勝率", points="all", title="抄底策略勝率分佈")
                fig_panic.update_layout(template="plotly_dark", yaxis_tickformat='.0%', yaxis_range=[0, 1.1])
                st.plotly_chart(fig_panic, width='stretch')
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
            width='stretch'
        )
