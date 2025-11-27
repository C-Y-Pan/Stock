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

@st.cache_data(ttl=5, show_spinner=False)
def get_master_stock_data():
    """
    從證交所與櫃買中心獲取全市場股票清單與基本面數據 (修復上市資料遺失問題)
    """
    stock_list = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'application/json'
    }
    
    # 1. 上市 (TWSE) - 增加 verify=False 解決憑證問題
    try:
        url_twse = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"
        # verify=False 是關鍵，許多環境連線證交所需要關閉驗證
        res = requests.get(url_twse, headers=headers, timeout=15, verify=False) 
        if res.status_code == 200:
            data = res.json()
            for row in data:
                if row.get('Code') and row.get('Name'):
                    stock_list.append({
                        "代號": row.get('Code'), "名稱": row.get('Name'), "市場": "上市",
                        "本益比": row.get('PEratio', '-'), "殖利率(%)": row.get('DividendYield', '-'), "股價淨值比": row.get('PBratio', '-')
                    })
        else:
            st.warning(f"⚠️ 連線證交所 (上市) 失敗，狀態碼: {res.status_code}")
    except Exception as e:
        st.warning(f"⚠️ 無法獲取上市資料 (可能為網路阻擋或API維護): {e}")

    # 2. 上櫃 (TPEx)
    try:
        url_tpex = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis"
        res = requests.get(url_tpex, headers=headers, timeout=15, verify=False)
        if res.status_code == 200:
            data = res.json()
            for row in data:
                if row.get('SecuritiesCompanyCode') and row.get('CompanyName'):
                    stock_list.append({
                        "代號": row.get('SecuritiesCompanyCode'), "名稱": row.get('CompanyName'), "市場": "上櫃",
                        "本益比": row.get('PriceEarningRatio', '-'), "殖利率(%)": row.get('YieldRatio', '-'), "股價淨值比": row.get('PriceBookRatio', '-')
                    })
    except Exception as e:
        print(f"TPEx API Error: {e}") # 上櫃失敗通常較少見，僅後台印出
    
    if not stock_list:
        return pd.DataFrame(columns=["代號", "名稱", "市場", "本益比", "殖利率(%)", "股價淨值比"])
        
    return pd.DataFrame(stock_list)

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

# ==========================================
# 1. 數據獲取 (Updated)

@st.cache_data(ttl=300, show_spinner=False)
def get_stock_data(ticker, start_date, end_date):
    """
    [強健版] 獲取 價量 + 籌碼 數據
    使用 requests 直接連線，不依賴 finmind 套件，解決雲端部署 ModuleNotFoundError 問題。
    """
    ticker = str(ticker).strip()
    
    # 1. 先用 yfinance 抓即時價量 (速度快)
    yf_ticker = ticker
    if not (ticker.endswith('.TW') or ticker.endswith('.TWO')):
        yf_ticker = f"{ticker}.TW" 
        
    try:
        # 抓取價量
        stock = yf.Ticker(yf_ticker)
        df_price = stock.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
        
        # 如果上市抓不到，試試上櫃
        if df_price.empty:
            yf_ticker = f"{ticker}.TWO"
            stock = yf.Ticker(yf_ticker)
            df_price = stock.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
            
        if df_price.empty: return pd.DataFrame(), ticker
        
        df_price = df_price.reset_index()
        df_price['Date'] = df_price['Date'].dt.tz_localize(None).dt.normalize()
        
        # 2. [核心修正] 使用 requests 直接抓取 FinMind 籌碼
        # 這種寫法不需要 pip install finmind，只要有網路就能跑
        clean_ticker = ticker.split('.')[0]
        start_str = (start_date - timedelta(days=400)).strftime('%Y-%m-%d')
        
        url = "https://api.finmindtrade.com/api/v4/data"
        parameter = {
            "dataset": "TaiwanStockInstitutionalInvestorsBuySell",
            "data_id": clean_ticker,
            "start_date": start_str,
            "token": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJkYXRlIjoiMjAyNS0xMS0yNyAyMzoxMDoxOCIsInVzZXJfaWQiOiJjeXBhbiIsImlwIjoiMTgwLjE3Ny4yMDUuNzEiLCJleHAiOjE3NjQ4NjEwMTh9.ANQ9OWXh5FEejlwRfGjWgTWre9LjGLLRsPJ1HMWtoZQ" # 如果您有 token 可以填入，沒有也通常能抓一段時間
        }
        
        resp = requests.get(url, params=parameter)
        data = resp.json()
        
        if data['msg'] == 'success' and data['data']:
            df_chip = pd.DataFrame(data['data'])
            df_chip['date'] = pd.to_datetime(df_chip['date'])
            
            # 轉換數值型態
            df_chip['buy'] = pd.to_numeric(df_chip['buy'], errors='coerce').fillna(0)
            df_chip['sell'] = pd.to_numeric(df_chip['sell'], errors='coerce').fillna(0)
            
            # 匯總三大法人買賣超
            df_chip['net_buy'] = df_chip['buy'] - df_chip['sell']
            daily_chip = df_chip.groupby('date')['net_buy'].sum().reset_index()
            daily_chip.rename(columns={'net_buy': 'Inst_Net_Buy', 'date': 'Date'}, inplace=True)
            
            # 3. 合併數據
            df_final = pd.merge(df_price, daily_chip, on='Date', how='left')
            df_final['Inst_Net_Buy'] = df_final['Inst_Net_Buy'].fillna(0)
        else:
            # 抓不到籌碼就給 0 (降級模式)
            print(f"FinMind 無數據或連線失敗: {data.get('msg')}")
            df_final = df_price
            df_final['Inst_Net_Buy'] = 0
            
        return df_final, yf_ticker

    except Exception as e:
        print(f"Error fetching data: {e}")
        # 出錯時至少回傳價量，不讓程式崩潰
        if 'df_price' in locals() and not df_price.empty:
            df_price['Inst_Net_Buy'] = 0
            return df_price, yf_ticker
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
    
    # [關鍵修正] 合併大盤數據並處理空值
    if not market_df.empty:
        # 確保日期格式一致
        data['Date'] = pd.to_datetime(data['Date']).dt.normalize()
        market_df['Date'] = pd.to_datetime(market_df['Date']).dt.normalize()
        
        # Left Join: 保留個股所有日期
        data = pd.merge(data, market_df, on='Date', how='left', suffixes=('', '_Market'))
        
        # [Fix] 若個股有最新日資料但大盤尚未更新，合併後會產生 NaN
        # 使用 ffill() 讓今天的 VIX/Market_RSI 沿用昨日數值，避免計算 Alpha Score 時變成 NaN
        cols_to_fill = ['Market_RSI', 'Market_MA20', 'Market_MA60', 'VIX']
        for c in cols_to_fill:
            if c in data.columns:
                data[c] = data[c].ffill()
                
        # 防呆：若 ffill 後仍有空值 (例如第一天就沒資料)，填入預設值
        if 'Market_RSI' in data.columns: data['Market_RSI'] = data['Market_RSI'].fillna(50)
        if 'Market_MA20' in data.columns: data['Market_MA20'] = data['Market_MA20'].fillna(0)
        if 'VIX' in data.columns: data['VIX'] = data['VIX'].fillna(20)

    else:
        # 若無大盤資料，給予預設值以防報錯
        data['Market_RSI'] = 50
        data['Market_MA20'] = 0
        data['VIX'] = 20
    
    # --- 以下維持原有指標計算邏輯 ---
    data['OBV'] = (np.sign(data['Close'].diff()) * data['Volume']).fillna(0).cumsum()
    data['OBV_MA20'] = data['OBV'].rolling(20).mean()
    data['Vol_MA20'] = data['Volume'].rolling(20).mean().replace(0, 1).fillna(1)
    data['MA20'] = data['Close'].rolling(20).mean()
    data['MA60'] = data['Close'].rolling(60).mean() 
    
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
    
    # [重要] 這裡不再 dropna，改用 fillna 確保資料完整性，避免把最近幾天刪掉
    # 只要 SuperTrend 有值即可
    return data.dropna(subset=['SuperTrend'])

# ==========================================
# 3. 策略邏輯 & 輔助 (Modified with Confidence Score)
# ==========================================
def run_simple_strategy(data, buy_threshold=60, fee_rate=0.001425, tax_rate=0.003):
    """
    策略引擎 v20.0 (The Analog Surfer):
    配合 Bio-Feedback 分數，實現「嗅覺交易」。
    
    [核心進化]
    1. 嗅覺離場 (Scent Exit):
       - 監控 Alpha Score 的「變化率 (Delta)」。
       - 如果在高檔區 (>70) 發生單日劇烈下滑 (Delta < -15)，代表環境突變(VIX飆)或籌碼潰散。
       - 立即執行「預防性撤退」，不需等待跌破均線。
    2. 恐慌進場:
       - 分數由 VIX 助燃衝破 90 分，立即進場。
    """
    df = data.copy()
    if 'Alpha_Score' not in df.columns: return df
        
    positions = []; reasons = []; actions = []; return_labels = []
    confidences = [] 
    
    position = 0
    entry_price = 0.0
    highest_price = 0.0
    
    # 身份識別
    close_val = df['Close'].values
    vol_val = df['Volume'].values if 'Volume' in df.columns else np.zeros(len(df))
    avg_dollar_vol = np.nanmean(close_val * vol_val)
    if np.isnan(avg_dollar_vol): avg_dollar_vol = 0
    is_titan = avg_dollar_vol > 5_000_000_000 
    
    ma60 = df['MA60'].values if 'MA60' in df.columns else close_val
    scores = df['Alpha_Score'].values
    
    # 計算分數的變化率 (感知速度)
    score_delta = df['Alpha_Score'].diff().fillna(0).values
    
    atr = df['Close'].rolling(14).mean().fillna(0).values * 0.02

    for i in range(len(df)):
        signal = position
        reason_str = ""
        action_code = "Hold" if position == 1 else "Wait"
        ret_label = ""
        
        curr_price = close_val[i]
        curr_score = scores[i]
        curr_delta = score_delta[i]
        curr_ma60 = ma60[i]
        
        if np.isnan(curr_score): valid_score = 50
        else: valid_score = int(curr_score)
        
        # --- 進場 ---
        if position == 0:
            if valid_score >= buy_threshold:
                # 泰坦濾網：空頭不追高，除非恐慌 (>85)
                if is_titan and curr_price < curr_ma60 and valid_score < 85:
                    pass
                else:
                    signal = 1
                    entry_price = curr_price
                    highest_price = curr_price
                    action_code = "Buy"
                    reason_str = "恐慌抄底" if valid_score > 85 else "趨勢啟動"
                    
        # --- 出場 ---
        elif position == 1:
            if curr_price > highest_price: highest_price = curr_price
            
            pnl_pct = (curr_price - entry_price) / entry_price
            is_sell = False
            
            # [核心] 嗅覺離場機制
            
            # 1. 血味感知 (The Scent of Blood)
            # 條件：分數在高檔區 (>70) 且 突然暴跌 (Delta < -15)
            # 代表 VIX 突然飆高 或 法人突然大賣
            if valid_score > 60 and curr_delta < -15:
                is_sell = True
                reason_str = "嗅到血味(環境轉差)"
            
            # 2. 結構崩壞
            elif valid_score < 40:
                is_sell = True
                reason_str = "評分轉空"
            
            # 3. 泰坦防線 (最後一道牆)
            elif is_titan and curr_price < curr_ma60 * 0.99:
                is_sell = True
                reason_str = "破季線"
                
            # 4. 游擊停利
            elif not is_titan:
                if curr_price < highest_price - (3 * atr[i]):
                    is_sell = True; reason_str = "吊燈停利"

            # 5. 災難停損
            if pnl_pct < -0.15:
                is_sell = True; reason_str = "災難停損"

            if is_sell:
                signal = 0
                action_code = "Sell"
                sign = "+" if pnl_pct > 0 else ""
                ret_label = f"{sign}{pnl_pct*100:.1f}%"

        position = signal
        positions.append(signal)
        reasons.append(reason_str)
        actions.append(action_code)
        return_labels.append(ret_label)
        confidences.append(valid_score)

    df['Position'] = positions
    df['Reason'] = reasons
    df['Action'] = actions
    df['Return_Label'] = return_labels
    df['Confidence'] = confidences
    
    # 績效...
    df['Real_Position'] = df['Position'].shift(1).fillna(0)
    df['Market_Return'] = df['Close'].pct_change().fillna(0)
    cost_series = pd.Series(0.0, index=df.index)
    cost_series[df['Action'] == 'Buy'] = fee_rate
    cost_series[df['Action'] == 'Sell'] = fee_rate + tax_rate
    df['Strategy_Return'] = (df['Real_Position'] * df['Market_Return']) - cost_series
    df['Cum_Strategy'] = (1 + df['Strategy_Return']).cumprod()
    df['Cum_Market'] = (1 + df['Market_Return']).cumprod()
    
    return df



# 修改後：傳遞成本參數
def run_optimization(raw_df, market_df, user_start_date, fee_rate=0.001425, tax_rate=0.003):
    """
    優化器 v3.0: 
    不再窮舉技術指標參數，而是直接計算 Alpha Score 並以此執行回測。
    這確保了圖表看到的分析 (Analysis) 與回測績效 (Backtest) 是完全一致的。
    """
    # 1. 計算技術指標
    # 使用固定的通用參數 (v5.0 標準)
    df_ind = calculate_indicators(raw_df, 10, 3.0, market_df)
    
    target_start = pd.to_datetime(user_start_date)
    df_slice = df_ind[df_ind['Date'] >= target_start].copy()
    
    if df_slice.empty: return None, None

    # 2. [關鍵] 先計算 Alpha Score
    # 傳入空的 margin/short df，因為回測歷史數據時通常只看價量與技術面
    df_scored = calculate_alpha_score(df_slice, pd.DataFrame(), pd.DataFrame())
    
    # 3. 執行策略 (基於 Score)
    # 我們可以簡單測試兩個門檻，看哪個好，微調「進場標準」
    best_ret = -999
    best_df = None
    best_params = {}
    
    # 測試進場門檻: 60分(積極) vs 70分(保守)
    for buy_thresh in [60, 70]:
        res_df = run_simple_strategy(df_scored, buy_threshold=buy_thresh, fee_rate=fee_rate, tax_rate=tax_rate)
        
        if res_df.empty: continue
            
        final_ret = res_df['Cum_Strategy'].iloc[-1]
        
        if final_ret > best_ret:
            best_ret = final_ret
            best_df = res_df
            best_params = {'Threshold': buy_thresh, 'Return': final_ret - 1}
            
    return best_params, best_df

def validate_strategy_robust(raw_df, market_df, split_ratio=0.7, fee_rate=0.001425, tax_rate=0.003):
    """
    執行嚴謹的樣本外測試 (Walk-Forward Analysis) - Alpha Score 版
    """
    # 1. 資料切割
    total_len = len(raw_df)
    if total_len < 100: return None 
    
    split_idx = int(total_len * split_ratio)
    train_data_raw = raw_df.iloc[:split_idx].copy()
    test_data_raw = raw_df.iloc[split_idx:].copy()
    
    # 確保切分後的測試集有足夠數據
    if len(test_data_raw) < 30: return None

    # 2. 訓練階段 (In-Sample)
    # 找出過去這段時間表現最好的「進場門檻 (Threshold)」
    train_start_date = train_data_raw['Date'].min()
    best_params_train, train_res_df = run_optimization(train_data_raw, market_df, train_start_date, fee_rate, tax_rate)
    
    if best_params_train is None: return None

    # 3. 測試階段 (Out-of-Sample)
    # 用訓練好的門檻，去跑未來的數據
    
    # A. 計算技術指標 (使用與優化器一致的標準參數：週期10, 倍數3.0)
    test_ind = calculate_indicators(test_data_raw, 10, 3.0, market_df)
    
    # B. [關鍵] 計算測試集的 Alpha Score
    # 這裡必須執行，因為新的策略引擎依賴 Alpha_Score 欄位
    test_scored = calculate_alpha_score(test_ind, pd.DataFrame(), pd.DataFrame())
    
    # C. 執行策略 (使用訓練集找出的最佳 Threshold)
    test_res_df = run_simple_strategy(
        test_scored, 
        buy_threshold=best_params_train['Threshold'], 
        fee_rate=fee_rate, 
        tax_rate=tax_rate
    )
    
    # 4. 績效比較與指標計算
    def get_metrics(df):
        if df.empty: return 0, 0, 0
        cum_ret = df['Cum_Strategy'].iloc[-1] - 1
        mdd = calculate_mdd(df['Cum_Strategy'])
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

def analyze_alpha_performance(df):
    """
    因子有效性檢驗工具 v2 (修正版)
    """
    data = df.copy()
    
    # 1. 建立未來回報
    periods = {1: '1D', 3: '3D', 5: '5D', 10: '10D'}
    for n, suffix in periods.items():
        data[f'Fwd_Ret_{suffix}'] = data['Close'].shift(-n) / data['Close'] - 1

    if 'Alpha_Slope' not in data.columns:
        data['Alpha_Slope'] = data['Alpha_Score'].diff().fillna(0)

    valid_data = data.dropna(subset=[f'Fwd_Ret_{suffix}' for suffix in periods.values()])
    
    if valid_data.empty:
        return None, None, None

    # 2. 計算 IC (注意這裡的 Key 名稱)
    ic_metrics = []
    for factor in ['Alpha_Score', 'Alpha_Slope']:
        for n, suffix in periods.items():
            corr = valid_data[factor].corr(valid_data[f'Fwd_Ret_{suffix}'])
            ic_metrics.append({
                "因子": "評分" if factor == 'Alpha_Score' else "動能",
                "週期": f"{n}日", 
                "IC": corr   # 統一欄位名稱
            })
    ic_df = pd.DataFrame(ic_metrics)

    # 3. 分組績效
    bins = [-np.inf, -40, 0, 40, np.inf]
    labels = ['空頭/超賣 (<-40)', '弱勢盤整 (-40~0)', '強勢盤整 (0~40)', '多頭/過熱 (>40)']
    
    valid_data['Score_Bucket'] = pd.cut(valid_data['Alpha_Score'], bins=bins, labels=labels)
    
    def win_rate_calc(x):
        return (x > 0).mean() * 100

    bucket_stats = valid_data.groupby('Score_Bucket', observed=False)['Fwd_Ret_5D'].agg(['mean', 'count', win_rate_calc])
    bucket_stats.columns = ['Avg_Return', 'Samples', 'Win_Rate']
    bucket_stats['Avg_Return'] = bucket_stats['Avg_Return'] * 100

    return ic_df, bucket_stats, valid_data


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
import numpy as np
import pandas as pd

import numpy as np
import pandas as pd

def calculate_alpha_score(df, margin_df, short_df):
    """
    Alpha Score v26.2 (The Data Healer):
    針對「近期分數消失 (NaN)」進行最終極修復。
    
    [核心進化] 偏執狂防呆
    1. 源頭強制清洗：進入函式第一件事，把所有可能的 NaN 全部填滿。
       - 籌碼缺值 -> 補 0
       - VIX 缺值 -> 補昨日值 (ffill)
       - 均線缺值 -> 補收盤價
    2. 過程無縫處理：在 Z-Score、MACD 計算過程中，隨時攔截 NaN。
    3. 結尾強制輸出：確保輸出的 Alpha_Score 絕對是浮點數，絕無空值。
    """
    df = df.copy()
    if 'Score_Log' not in df.columns: df['Score_Log'] = ""

    # ====================================================
    # 1. 源頭數據清洗 (Paranoid Cleaning)
    # ====================================================
    
    # 籌碼防呆
    if 'Inst_Net_Buy' not in df.columns:
        df['Inst_Net_Buy'] = 0
    df['Inst_Net_Buy'] = df['Inst_Net_Buy'].fillna(0) # 缺值補0
    
    # VIX 防呆 (最常見的斷頭原因)
    if 'VIX' not in df.columns: df['VIX'] = 20.0
    # 先 ffill (沿用昨日)，若開頭就是空，則補 20
    df['VIX'] = df['VIX'].ffill().bfill().fillna(20.0) 
    
    # 價量防呆
    df['Close'] = df['Close'].ffill()
    df['Volume'] = df['Volume'].fillna(0)
    
    close = df['Close']
    vix = df['VIX']
    
    # 均線計算與填補
    df['MA20'] = close.rolling(20).mean().fillna(close)
    df['MA60'] = close.rolling(60).mean().fillna(close)
    
    # 身份識別
    # 注意：rolling mean 在資料開頭會是 NaN，需填補
    avg_dollar_vol = (close * df['Volume']).rolling(60).mean().fillna(0).iloc[-1]
    is_titan = avg_dollar_vol > 5_000_000_000 

    # ====================================================
    # 2. 因子計算 (逐步防呆)
    # ====================================================
    
    # [A] 籌碼因子
    inst_rate = df['Inst_Net_Buy'] / df['Volume'].replace(0, 1)
    # 處理除以零或無交易量的極端情況
    inst_rate = inst_rate.fillna(0).replace([np.inf, -np.inf], 0)
    
    inst_mean = inst_rate.rolling(60).mean().fillna(0)
    inst_std = inst_rate.rolling(60).std().fillna(1) # std 補 1 防止除以零
    inst_z = (inst_rate - inst_mean) / inst_std
    inst_z = inst_z.fillna(0)
    
    inst_health = np.tanh(inst_z) * 50
    
    # [B] 趨勢因子
    slope = df['MA60'].diff().fillna(0)
    slope_mean = slope.rolling(60).mean().fillna(0)
    slope_std = slope.rolling(60).std().fillna(1)
    slope_z = (slope - slope_mean) / slope_std
    slope_z = slope_z.fillna(0)
    
    trend_health = np.tanh(slope_z) * 50
    
    # 健康度總分
    health_score = (inst_health * 0.6) + (trend_health * 0.4)
    health_score = health_score.fillna(0) # 再次確保
    
    # [C] 壓力因子 (VIX)
    norm_vix = (vix - 15) / 10.0
    pressure_factor = np.exp(norm_vix * 0.8)
    pressure_factor = pressure_factor.fillna(1.0) # 預設壓力為 1
    
    # [D] 乖離率
    bias_pct = ((close - df['MA60']) / df['MA60']) * 100
    bias_pct = bias_pct.fillna(0)
    
    # ====================================================
    # 3. 合成運算
    # ====================================================
    
    final_score = np.zeros(len(df))
    logs = []
    
    # 轉 Numpy
    health_val = health_score.values
    pressure_val = pressure_factor.values
    bias_val = bias_pct.values
    
    for i in range(len(df)):
        # 防呆：確保數值有效
        if np.isnan(health_val[i]): h = 0
        else: h = health_val[i]
            
        if np.isnan(pressure_val[i]) or pressure_val[i] == 0: p = 1.0
        else: p = pressure_val[i]
            
        b = bias_val[i]
        
        # 邏輯核心
        if b > 0: # 順勢
            score = 50 + (h / p)
            if h < 0 and p > 2.0:
                score -= 50
                log = "🩸 窒息(高檔轉弱)"
            elif score > 60:
                log = "🚀 順勢"
            else:
                log = "盤整"
        else: # 逆勢
            if b < -5:
                panic_adrenaline = abs(b) * p * 1.5
                score = 50 + panic_adrenaline
                if score > 90: log = "💎 恐慌黃金坑"
                else: log = "📉 修正"
            else:
                score = 50 - (p * 10)
                log = "📉 空頭壓制"
                
        final_score[i] = score
        logs.append(log)

    # ====================================================
    # 4. 輸出修復
    # ====================================================
    
    # 將 numpy 轉回 Series 並處理最後的 NaN
    raw_series = pd.Series(final_score).fillna(50) 
    
    # 平滑化 (使用 fillna 確保最新的那一天不會因為 rolling 視窗而消失)
    # min_periods=1 是關鍵，保證即使只有一天數據也能算平均
    df['Alpha_Score'] = raw_series.rolling(2, min_periods=1).mean().fillna(raw_series).clip(0, 100)
    
    df['Score_Log'] = logs
    df['Recommended_Position'] = df['Alpha_Score']
    
    # 輔助
    df['Health_Index'] = health_score
    df['Pressure_Index'] = pressure_factor
    df['Inst_Z'] = inst_z
    
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
    """
    發送持股分析報告 Email (含格式優化與時區校正)
    """
    if df.empty: return

    # [關鍵修正] 設定台北時區
    tw = pytz.timezone('Asia/Taipei')
    # 獲取當前台北時間
    now_tw = datetime.now(tw)

    # 1. 準備內容與格式化
    subject = f"📊 持股評分變動通知 - {now_tw.strftime('%H:%M')}"
    
    # 建立副本
    email_df = df.copy()
    
    # 格式化收盤價
    try:
        email_df["收盤價"] = pd.to_numeric(email_df["收盤價"], errors='coerce')
        email_df["收盤價"] = email_df["收盤價"].map('{:,.2f}'.format)
    except: pass

    # 選取欄位
    cols = ["代號", "名稱", "收盤價", "綜合評分", "AI 建議"]
    final_cols = [c for c in cols if c in email_df.columns]
    
    # 轉為 HTML
    html_table = email_df[final_cols].to_html(
        index=False, 
        classes='table table-striped', 
        border=1, 
        justify='center'
    )
    
    # 組合 Email 內文 (時間改用 now_tw)
    email_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2 style="color: #333;">🔔 持股評分變動通知</h2>
        <p>系統偵測到您的持股組合出現評分變化，最新狀態如下：</p>
        <p>時間：{now_tw.strftime('%Y-%m-%d %H:%M:%S')} (Taipei)</p>
        <hr>
        <h3>📋 AI 市場前瞻</h3>
        <div style='background-color: #f8f9fa; padding: 15px; border-left: 5px solid #007bff; border-radius: 4px;'>
            {market_analysis_text}
        </div>
        <br>
        <h3>📊 持股最新評級</h3>
        {html_table}
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
    page = st.radio("導航", ["🌍 市場總覽 (Macro)", "📊 單股深度分析", "🚀 科技股掃描", "💼 持股健診與建議", "📋 全台股清單"])

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

market_df = get_market_data(start_date, end_date)

# --- 頁面 1 ---
if page == "🌍 市場總覽 (Macro)":
    draw_market_dashboard(market_df, start_date, end_date)

# --- 頁面 2 (手機介面優化版): 單股深度分析 ---
elif page == "📊 單股深度分析":
    # ==================================================
    # 1. 資料準備
    # ==================================================
    if st.session_state['all_stock_list'] is None:
        st.session_state['all_stock_list'] = get_master_stock_data()
    
    df_all = st.session_state['all_stock_list']
    
    # 建立搜尋清單
    search_list = [f"{row['代號']} {row['名稱']}" for idx, row in df_all.iterrows()]
    base_search_list = [f"{k} {v}" for k, v in TW_STOCK_NAMES_STATIC.items()]
    full_search_options = sorted(list(set(search_list + base_search_list)))

    # 確保 last_ticker 有值
    if 'last_ticker' not in st.session_state:
        st.session_state['last_ticker'] = "2330"

    # ==================================================
    # [KEY FIX] 定義按鈕的回調函數 (Callback)
    # 這段函數會在按鈕點擊後、頁面重繪前執行，解決報錯問題
    # ==================================================
    def change_stock_selection(direction):
        # 1. 取得當前選單的值
        current_val = st.session_state.get('stock_selector', full_search_options[0])
        
        # 2. 找出當前索引
        try:
            current_idx = full_search_options.index(current_val)
        except:
            current_idx = 0
            
        # 3. 計算新索引
        new_idx = (current_idx + direction) % len(full_search_options)
        new_option = full_search_options[new_idx]
        
        # 4. 更新 Session State (這時候更新是合法的)
        st.session_state['stock_selector'] = new_option
        st.session_state['last_ticker'] = new_option.split(" ")[0]

    # ==================================================
    # 2. 介面佈局
    # ==================================================
    
    # 找出當前 ticker 對應的 index (為了初始顯示正確)
    current_ticker = st.session_state['last_ticker']
    current_index_gui = 0
    for idx, opt in enumerate(full_search_options):
        if opt.startswith(str(current_ticker)):
            current_index_gui = idx
            break

    # --- Row 1: 搜尋與 Go 按鈕 ---
    with st.container():
        col_search, col_run = st.columns([3, 1])
        
        with col_search:
            # Selectbox
            # 注意：這裡不用再寫 index=...，因為我們綁定了 key，Streamlit 會自動優先使用 session_state['stock_selector']
            # 若 session_state 還沒這個 key，我們可以手動初始化它
            if 'stock_selector' not in st.session_state:
                st.session_state['stock_selector'] = full_search_options[current_index_gui]

            st.selectbox(
                "搜尋股票 (支援代號或中文)",
                options=full_search_options,
                label_visibility="collapsed",
                key="stock_selector" 
            )
            
        with col_run:
            # Go 按鈕 (這裡不需要 callback，因為它是讀取 selectbox 的值)
            if st.button("Go", type="primary", use_container_width=True):
                # 讀取使用者在選單選的值
                selected = st.session_state['stock_selector']
                st.session_state['last_ticker'] = selected.split(" ")[0]
                st.rerun()

    # --- Row 2: 上一檔 / 下一檔 (使用 on_click) ---
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        # args=(-1,) 代表傳入參數 -1 給 change_stock_selection
        st.button("◀ 上一檔", use_container_width=True, on_click=change_stock_selection, args=(-1,))

    with col_next:
        # args=(1,) 代表傳入參數 1
        st.button("下一檔 ▶", use_container_width=True, on_click=change_stock_selection, args=(1,))

    # ==================================================
    # 3. 確保 ticker同步 與 執行分析
    # ==================================================
    
    # 如果使用者手動更改了下拉選單但沒按 Go，我們也在這裡同步變數
    if 'stock_selector' in st.session_state:
        sel_val = st.session_state['stock_selector'].split(" ")[0]
        if sel_val != st.session_state['last_ticker']:
             st.session_state['last_ticker'] = sel_val

    ticker_input = st.session_state['last_ticker']
    
    if ticker_input: 
        with st.spinner(f'正在分析 {ticker_input} ...'):
            current_fee = fee_input if 'fee_input' in locals() else 0.001425
            current_tax = tax_input if 'tax_input' in locals() else 0.003
            
            raw_df, fmt_ticker = get_stock_data(ticker_input, start_date, end_date)
            name = get_stock_name(fmt_ticker)
            
            if raw_df.empty:
                st.error(f"❌ 無法獲取 {ticker_input} 資料，請確認代號是否正確。")
            else:
                # 執行運算
                best_params, final_df = run_optimization(raw_df, market_df, start_date, current_fee, current_tax)
                validation_result = validate_strategy_robust(raw_df, market_df, 0.7, current_fee, current_tax)

            if final_df is None or final_df.empty:
                st.warning("⚠️ 選定區間內無資料。")
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
                
                # UI 顯示部分
                st.markdown(f"## {ticker_input} {name}")
                st.caption(f"策略邏輯: {reason} | 波動率: {vol}")
                
                st.markdown("### 🏆 AI 綜合評分與決策依據")
                score_col, log_col = st.columns([1, 3])
                
                with score_col:
                    s_color = "normal"
                    if final_composite_score >= 60: s_color = "off" 
                    elif final_composite_score <= -20: s_color = "inverse"
                    
                    # 這裡現在安全了，因為我們確保了 final_composite_score 一定是數字
                    st.metric(
                        label="綜合評分 (Alpha Score)",
                        value=f"{int(final_composite_score)} 分",
                        delta=action,
                        delta_color=s_color
                    )
                
                with log_col:
                    st.info(f"**🧮 演算歷程解析：**\n\n{full_log_text}")

                # ... (後續的 Tabs 繪圖部分完全不用動) ...
                strat_mdd = calculate_mdd(final_df['Cum_Strategy'])
                strat_ret = best_params['Return'] * 100
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("淨報酬 (含成本)", f"{strat_ret:.1f}%", f"MDD: {strat_mdd:.1f}%")
                m2.metric("實際勝率 (Realized)", real_win_rate, f"{real_wins}勝 / {real_total}總")
                m3.metric("目標達成率 (Target)", hit_rate, f"{hits}次達標 (+15%)")
                m4.metric("盈虧因子 (PF)", f"{risk_metrics.get('Profit_Factor', 0):.2f}", f"夏普: {risk_metrics.get('Sharpe', 0):.2f}")
                
                stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                final_df['Alpha_Score'] = stock_alpha_df['Alpha_Score']
                # 計算 Alpha Score 斜率
                final_df['Alpha_Slope'] = final_df['Alpha_Score'].diff().fillna(0)

                # 原有的 tab 定義
                tab1, tab2, tab3, tab4 = st.tabs(["📈 操盤決策圖", "💰 權益曲線", "🎲 蒙地卡羅模擬", "🧪 有效性驗證"])                
                
                # [Tab 1: K線圖] (進階版：新增 Alpha Slope 動能圖)
                # [Tab 1: K線圖與買賣點分析]
                with tab1:
                    # 1. 準備數據
                    # 將 Alpha Score 等數據寫入 final_df 以便繪圖
                    final_df['Alpha_Score'] = stock_alpha_df['Alpha_Score']
                    final_df['Alpha_Slope'] = final_df['Alpha_Score'].diff().fillna(0)

                    # 建立子圖
                    fig = make_subplots(
                        rows=6, cols=1, 
                        shared_xaxes=True, 
                        vertical_spacing=0.02, 
                        row_heights=[0.4, 0.12, 0.12, 0.12, 0.12, 0.12], # 加大主圖比例
                        subplot_titles=(
                            "", 
                            "買賣評等 (Alpha Score)", 
                            "評分動能 (Slope)", 
                            "成交量", 
                            "法人籌碼 (OBV)", 
                            "相對強弱指標 (RSI)"
                        )
                    )
                    
                    # --- Row 1: 主圖 K 線 ---
                    fig.add_trace(go.Candlestick(
                        x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                        low=final_df['Low'], close=final_df['Close'], name='K線',
                        increasing_line_color='#ef5350', decreasing_line_color='#00bfa5' 
                    ), row=1, col=1)
                    
                    # 均線
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', 
                                            line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線'), row=1, col=1)

                    # ==================================================
                    # [核心新增] 買賣點與原因標記
                    # ==================================================
                    
                    # 定義標記位置 (Y軸)
                    final_df['Buy_Y'] = final_df['Low'] * 0.98  # K線下方 2%
                    final_df['Sell_Y'] = final_df['High'] * 1.02 # K線上方 2%

                    # 篩選買賣紀錄
                    buy_records = final_df[final_df['Action'] == 'Buy'].copy()
                    sell_records = final_df[final_df['Action'] == 'Sell'].copy()

                    # 1. 繪製買點 (Buy Signals)
                    if not buy_records.empty:
                        # 處理顯示文字：組合「評分」與「簡短原因」
                        def format_buy_text(row):
                            score = int(row['Confidence'])
                            reason = row['Reason'].split('/')[0] # 取斜線前的簡短原因
                            return f"<b>買 ({score}分)</b><br>{reason}"

                        buy_text = buy_records.apply(format_buy_text, axis=1)
                        
                        # 根據原因類型設定顏色 (黃金坑用金色，一般用青色)
                        buy_colors = ['#FFD700' if '黃金坑' in r else '#00FFFF' for r in buy_records['Reason']]

                        fig.add_trace(go.Scatter(
                            x=buy_records['Date'], 
                            y=buy_records['Buy_Y'], 
                            mode='markers+text',
                            text=buy_text, 
                            textposition="bottom center",
                            textfont=dict(size=10),
                            marker=dict(symbol='triangle-up', size=12, color=buy_colors, line=dict(width=1, color='black')), 
                            name='買進訊號', 
                            # 滑鼠懸停時顯示完整原因
                            hovertext=buy_records['Reason'] 
                        ), row=1, col=1)

                    # 2. 繪製賣點 (Sell Signals)
                    if not sell_records.empty:
                        # 處理賣出文字：顯示報酬率
                        sell_text = [f"<b>賣</b><br>{label}" for label in sell_records['Return_Label']]
                        
                        fig.add_trace(go.Scatter(
                            x=sell_records['Date'], 
                            y=sell_records['Sell_Y'], 
                            mode='markers+text', 
                            text=sell_text, 
                            textposition="top center",
                            textfont=dict(color='#ff8a80', size=10),
                            marker=dict(symbol='triangle-down', size=12, color='#ff5252', line=dict(width=1, color='black')), 
                            name='賣出訊號', 
                            hovertext=sell_records['Reason']
                        ), row=1, col=1)

                    # ==================================================

                    # --- Row 2: Alpha Score (狀態) ---
                    colors_score = ['#ef5350' if v > 0 else '#26a69a' for v in final_df['Alpha_Score']]
                    fig.add_trace(go.Bar(
                        x=final_df['Date'], y=final_df['Alpha_Score'], 
                        name='Alpha Score', marker_color=colors_score
                    ), row=2, col=1)
                    fig.update_yaxes(range=[-110, 110], row=2, col=1)
                    # 加入關鍵分數線
                    fig.add_hline(y=80, line_dash="dot", line_color="yellow", row=2, col=1, annotation_text="黃金坑(80)")
                    fig.add_hline(y=-20, line_dash="dot", line_color="gray", row=2, col=1)

                    # --- Row 3: Alpha Slope ---
                    colors_slope = ['#ef5350' if v > 0 else ('#26a69a' if v < 0 else 'gray') for v in final_df['Alpha_Slope']]
                    fig.add_trace(go.Bar(
                        x=final_df['Date'], y=final_df['Alpha_Slope'],
                        name='Alpha Slope', marker_color=colors_slope
                    ), row=3, col=1)

                    # --- Row 4: 成交量 ---
                    colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'], marker_color=colors_vol, name='成交量'), row=4, col=1)
                    
                    # --- Row 5: OBV ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=5, col=1)
                    
                    # --- Row 6: RSI ---
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=6, col=1)
                    fig.add_hline(y=30, line_dash="dot", line_color="green", row=6, col=1)
                    fig.add_hline(y=70, line_dash="dot", line_color="red", row=6, col=1)
                    
                    # Layout 設定
                    fig.update_layout(
                        height=1300, 
                        template="plotly_dark", 
                        xaxis_rangeslider_visible=False, 
                        margin=dict(l=20, r=40, t=30, b=20),
                        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="right", x=1),
                        hovermode="x unified"
                    )
                    fig.update_yaxes(side='right')
                    
                    st.plotly_chart(fig, use_container_width=True)

                # [Tab 2: 權益曲線]
                with tab2:
                    fig_c = go.Figure()
                    fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Market'], name='大盤', line=dict(color='gray', dash='dot')))
                    fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Strategy'], name='策略淨值', line=dict(color='#ef5350', width=2), fill='tozeroy'))
                    
                    buy_pts = final_df[final_df['Action']=='Buy']
                    sell_pts = final_df[final_df['Action']=='Sell']
                    if not buy_pts.empty:
                        fig_c.add_trace(go.Scatter(x=buy_pts['Date'], y=buy_pts['Cum_Strategy'], mode='markers', marker=dict(symbol='triangle-up', size=10, color='#FFD700'), name='買進'))
                    if not sell_pts.empty:
                        fig_c.add_trace(go.Scatter(x=sell_pts['Date'], y=sell_pts['Cum_Strategy'], mode='markers', marker=dict(symbol='triangle-down', size=10, color='#FF00FF'), name='賣出'))
                        
                    fig_c.update_layout(template="plotly_dark", height=450, title="策略權益成長曲線", margin=dict(l=10, r=10, t=40, b=10))
                    st.plotly_chart(fig_c, use_container_width=True)
                    
                # [Tab 3: 蒙地卡羅]
                with tab3:
                    st.markdown("### 🎲 蒙地卡羅風險模擬")
                    last_p = final_df['Close'].iloc[-1]
                    sim_df, var95 = run_monte_carlo_sim(last_p, vol, days=120, sims=200)
                    
                    fp = sim_df.iloc[-1]
                    opt_p = np.percentile(fp, 95)
                    pes_p = np.percentile(fp, 5)
                    prob_up = (fp > last_p).mean() * 100
                    
                    cm1, cm2 = st.columns([3, 1])
                    with cm1:
                        fig_mc = go.Figure()
                        for c in sim_df.columns[:30]:
                            fig_mc.add_trace(go.Scatter(y=sim_df[c], mode='lines', line=dict(width=1, color='rgba(0,255,255,0.1)'), showlegend=False))
                        fig_mc.add_hline(y=last_p, line_dash="dash", line_color="white", annotation_text="現價")
                        fig_mc.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=30, b=10))
                        st.plotly_chart(fig_mc, use_container_width=True)
                    with cm2:
                        st.metric("上漲機率", f"{prob_up:.1f}%")
                        st.metric("潛在獲利 (95%)", f"+{(opt_p-last_p)/last_p*100:.1f}%")
                        st.metric("潛在風險 (5%)", f"-{(last_p-pes_p)/last_p*100:.1f}%")

                # [Tab 4: 有效性驗證 (Fix)]
                with tab4:
                    st.markdown("### 🧪 策略與因子有效性驗證")
                    
                    # ... (策略樣本外測試部分保持不變) ...
                    with st.expander("策略樣本外測試 (Walk-Forward)", expanded=False):
                        if validation_result:
                            tr_cagr = validation_result['train']['cagr'] * 100
                            te_cagr = validation_result['test']['cagr'] * 100
                            vt1, vt2 = st.columns(2)
                            vt1.metric("訓練集年化報酬", f"{tr_cagr:.1f}%")
                            vt2.metric("測試集年化報酬", f"{te_cagr:.1f}%", f"差異: {(te_cagr-tr_cagr):.1f}%")
                            # ... (圖表代碼省略，維持原樣) ...

                    st.markdown("---")
                    st.markdown("### 🧬 Alpha 因子預測力檢驗 (IC Analysis)")

                    # 執行因子分析
                    ic_df, bucket_df, valid_data_for_plot = analyze_alpha_performance(final_df)

                    if ic_df is not None:
                        # A. IC 相關性顯示
                        st.markdown("#### 1. 因子預測力總覽")
                        
                        # [修正] 這裡的 index 和 values 必須對應上面函式定義的 Key
                        ic_pivot = ic_df.pivot(index="週期", columns="因子", values="IC")
                        
                        st.dataframe(
                            ic_pivot.style.background_gradient(cmap='RdYlGn', vmin=-0.1, vmax=0.1).format("{:.3f}"),
                            use_container_width=True
                        )

                        # B. 分組績效雙軸圖
                        st.markdown("#### 2. 分組績效透視 (報酬率 vs 勝率)")
                        
                        # 確保 bucket_df 是 DataFrame
                        if isinstance(bucket_df, pd.DataFrame):
                            fig_bucket = make_subplots(specs=[[{"secondary_y": True}]])

                            # Bar: 平均報酬
                            colors = ['#ef5350' if x > 0 else '#26a69a' for x in bucket_df['Avg_Return']]
                            fig_bucket.add_trace(go.Bar(
                                x=bucket_df.index.astype(str), # 轉字串避免類別錯誤
                                y=bucket_df['Avg_Return'],
                                name='平均報酬(%)',
                                marker_color=colors,
                                opacity=0.7
                            ), secondary_y=False)

                            # Line: 勝率
                            fig_bucket.add_trace(go.Scatter(
                                x=bucket_df.index.astype(str),
                                y=bucket_df['Win_Rate'],
                                name='勝率(%)',
                                mode='lines+markers+text',
                                text=[f"{v:.1f}%" for v in bucket_df['Win_Rate']],
                                textposition="top center",
                                line=dict(color='yellow', width=3)
                            ), secondary_y=True)

                            fig_bucket.update_layout(
                                template="plotly_dark",
                                height=400,
                                title_text="不同評分區間：5日後表現",
                                legend=dict(orientation="h", y=1.1)
                            )
                            fig_bucket.update_yaxes(title_text="平均報酬 (%)", secondary_y=False)
                            fig_bucket.update_yaxes(title_text="勝率 (%)", range=[0, 100], secondary_y=True)

                            st.plotly_chart(fig_bucket, use_container_width=True)
                            st.caption(f"樣本分佈參考：{dict(bucket_df['Samples'])}")
                    else:
                        st.warning("數據量不足，無法進行因子相關性分析。")

# --- 頁面 3 (修正版): 科技股/熱門股掃描 ---
elif page == "🚀 科技股掃描":
    st.markdown(f"### 🚀 戰略雷達：全市場機會掃描")
    st.markdown("此功能將對指定清單進行 **AI 全檢測**，並自動排序出當前 **Alpha 評分最高** 的前十名潛力標的。")

    # 定義熱門股清單
    HOT_STOCKS = [
        "2330", "2317", "2454", "2382", "2303", "2308", "3008", "3034", "3035", "3037", 
        "3443", "3661", "2603", "2609", "2615", "2376", "3231", "2356", "2357", "3017",
        "2059", "3324", "6669", "3529", "5269", "5274", "3045", "4966", "2344", "6274",
        "8046", "3016", "2360", "6239", "6213", "3533", "3653", "8210", "3131", "9958",
        "1513", "1519", "1503", "1504", "1605", "2881", "2882", "2891", "5871", "2886", "6781", "3211"
    ]
    HOT_STOCKS_STR = "\n".join(HOT_STOCKS)

    col_btn1, col_btn2 = st.columns([1, 3])
    with col_btn1:
        if st.button("📥 載入台股熱門 50 檔"):
            st.session_state['scan_list_input'] = HOT_STOCKS_STR
    
    if 'scan_list_input' not in st.session_state:
        st.session_state['scan_list_input'] = ALL_TECH_TICKERS
        
    user_list = st.text_area("掃描清單 (每行一支代號)", value=st.session_state['scan_list_input'], height=150)
    
    scan_btn = st.button("🔥 啟動戰略掃描", type="primary", use_container_width=True)
    
    # [新增] 掃描中斷按鈕 (放在迴圈外，利用 session_state 控制)
    if 'stop_scan' not in st.session_state:
        st.session_state['stop_scan'] = False

    if scan_btn:
        st.session_state['is_scanning'] = True
        st.session_state['stop_scan'] = False # 重置停止旗標
        
        tickers = [t.strip().replace(',','') for t in user_list.split('\n') if t.strip()]
        tickers = list(set(tickers)) 
        
        # 警告：如果數量太大，提示使用者
        if len(tickers) > 1000:
            st.warning(f"⚠️ 您即將掃描 {len(tickers)} 檔股票，這可能需要很長時間並導致連線逾時。建議分批進行 (例如一次 50 檔)。")
        
        # 建立容器來動態顯示結果 (不用等全部跑完)
        result_container = st.container()
        progress_bar = st.progress(0)
        status_text = st.empty()
        stop_button_placeholder = st.empty()
        
        # 在運算時顯示「停止按鈕」
        with stop_button_placeholder:
            if st.button("🛑 強制停止掃描"):
                st.session_state['stop_scan'] = True
        
        results = []
        
        import time # 引入時間模組
        
        for idx, ticker in enumerate(tickers):
            # 1. 檢查是否被使用者中止
            if st.session_state['stop_scan']:
                status_text.warning(f"🛑 掃描已由使用者中止。目前已完成 {len(results)} 檔分析。")
                break
                
            status_text.text(f"AI 正在運算 ({idx+1}/{len(tickers)}): {ticker} ...")
            progress_bar.progress((idx + 1) / len(tickers))
            
            try:
                # 2. 加入微小延遲，避免被 Yahoo API 封鎖 (Rate Limit)
                time.sleep(0.1) 
                
                raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
                
                if raw_df.empty or len(raw_df) < 60: # 資料太少也跳過
                    continue
                    
                # 執行運算
                best_params, final_df = run_optimization(raw_df, market_df, start_date, fee_rate=fee_input, tax_rate=tax_input)
                
                if final_df is not None and not final_df.empty:
                    # ==========================================
                    # 1. 計算基礎 Alpha Score 與 提取演算歷程
                    # ==========================================
                    # 傳入空 DataFrame 作為籌碼資料 (掃描模式下通常不逐一抓取資券以節省時間)
                    stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                    base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                    base_log = stock_alpha_df['Score_Log'].iloc[-1] # [新增] 獲取基礎評分細節

                    # 取得技術訊號與基本資訊
                    action, color, reason = analyze_signal(final_df)
                    name = get_stock_name(fmt_ticker)
                    
                    # ==========================================
                    # 2. 情境感知調整 (Context-Aware Adjustment)
                    # ==========================================
                    final_score = base_alpha_score
                    adjustment_log = "" # [新增] 用於記錄調整原因
                    
                    # 準備數據
                    current_price = final_df['Close'].iloc[-1]
                    ma20 = final_df['MA20'].iloc[-1]
                    ma60 = final_df['MA60'].iloc[-1]
                    
                    # 判斷最後一次買進訊號的理由，以識別是「反彈策略」還是「趨勢策略」
                    last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                    is_rebound_strategy = False
                    
                    if last_trade is not None:
                        buy_reason_str = str(last_trade['Reason'])
                        if any(x in buy_reason_str for x in ["反彈", "超賣", "回測", "低檔"]):
                            is_rebound_strategy = True
                    
                    # 針對「續抱」或「買進」狀態進行加分邏輯修正
                    if action == "✊ 續抱" or action == "🚀 買進":
                        if is_rebound_strategy:
                            # --- 情境 A: 反彈策略 (抄底邏輯) ---
                            # 補償 1: 反彈初期通常在季線下，基礎分會被扣分，這裡補回
                            if current_price < ma60: 
                                final_score += 15
                                adjustment_log += "[反彈位階+15]"
                            
                            # 補償 2: 檢查是否站上 5 日線 (短線轉強訊號)
                            ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                            if current_price > ma5: 
                                final_score += 10
                                adjustment_log += "[站穩MA5+10]"
                            
                            # 補償 3: RSI 動能翻揚
                            if final_df['RSI'].iloc[-1] > final_df['RSI'].iloc[-2]: 
                                final_score += 10
                                adjustment_log += "[RSI翻揚+10]"
                        else:
                            # --- 情境 B: 順勢策略 (突破邏輯) ---
                            # 獎勵多頭排列
                            if current_price > ma20: 
                                final_score += 5
                                adjustment_log += "[多頭排列+5]"
                            
                            # 獎勵量能支撐
                            if final_df['Volume'].iloc[-1] > final_df['Vol_MA20'].iloc[-1]:
                                final_score += 5
                                adjustment_log += "[量能支撐+5]"
                    
                    # 限制分數範圍 (-100 ~ 100)
                    final_score = max(min(final_score, 100), -100)
                    
                    # ==========================================
                    # 3. 資料彙整
                    # ==========================================
                    # 組合完整計算過程字串
                    full_calc_process = f"{base_log} {adjustment_log}"
                    if not full_calc_process.strip():
                        full_calc_process = "無顯著訊號"

                    # 計算勝率指標
                    hit_rate, hits, total = calculate_target_hit_rate(final_df)
                    
                    # 存入結果 List
                    res_item = {
                        "代號": fmt_ticker.split('.')[0], 
                        "名稱": name, 
                        "建議": action,
                        "收盤價": current_price,
                        "Alpha_Score": int(final_score), 
                        "計算過程": full_calc_process, # [關鍵新增] 顯示完整邏輯
                        "理由": f"{reason} | Score:{int(final_score)}", # 舊版兼容
                        "回測報酬": best_params['Return'],
                        "達標率": hit_rate
                    }
                    results.append(res_item)

            except Exception as e:
                print(f"Error scanning {ticker}: {e}")
                continue # 遇到錯誤直接跳過，不要崩潰

        # 掃描結束或中斷後的處理
        stop_button_placeholder.empty() # 隱藏停止按鈕
        progress_bar.empty()
        
        if results:
            full_df = pd.DataFrame(results)
            # 排序
            top_10_df = full_df.sort_values(by=['Alpha_Score', '回測報酬'], ascending=[False, False]).head(10)
            top_10_df.index = range(1, len(top_10_df) + 1)
            
            # 存入 Session
            st.session_state['scan_results_df'] = full_df
            st.session_state['top_10_df'] = top_10_df
            
            st.success(f"✅ 掃描完成！共發現 {len(full_df)} 檔符合條件標的。")
        else:
            st.warning("本次掃描未發現有效標的，或過程發生中斷。")

            
    # === 顯示結果區域 ===
    if 'top_10_df' in st.session_state and not st.session_state['top_10_df'].empty:
        
        st.markdown("---")
        st.markdown("### 🏆 AI 嚴選：最佳持有評分 Top 10")
        
        top10 = st.session_state['top_10_df']
        c1, c2, c3 = st.columns(3)
        if len(top10) >= 1:
            row = top10.iloc[0]
            c1.metric(f"🥇 {row['名稱']} ({row['代號']})", f"{row['Alpha_Score']} 分", f"{row['建議']}", delta_color="normal")
        if len(top10) >= 2:
            row = top10.iloc[1]
            c2.metric(f"🥈 {row['名稱']} ({row['代號']})", f"{row['Alpha_Score']} 分", f"{row['建議']}", delta_color="normal")
        if len(top10) >= 3:
            row = top10.iloc[2]
            c3.metric(f"🥉 {row['名稱']} ({row['代號']})", f"{row['Alpha_Score']} 分", f"{row['建議']}", delta_color="normal")
            
        st.write("")
        
        def highlight_top_score(val):
            if val >= 80: color = '#ffcdd2'
            elif val >= 50: color = '#fff9c4'
            else: color = 'white'
            return f'background-color: {color}; color: black; font-weight: bold'

        st.dataframe(
            top10.style
            .format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}"})
            .applymap(highlight_top_score, subset=['Alpha_Score']),
            use_container_width=True,
            # [新增] 指定欄位順序，將 "計算過程" 加入顯示
            column_order=["代號", "名稱", "Alpha_Score", "建議", "收盤價", "回測報酬", "計算過程", "達標率"]
        )
        
        st.markdown("---")
        with st.expander("📄 查看完整掃描清單 (含觀望股)", expanded=True):
            st.dataframe(
                st.session_state['scan_results_df'].sort_values(by='Alpha_Score', ascending=False)
                .style.format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}"})
                .background_gradient(subset=['Alpha_Score'], cmap='Reds'),
                use_container_width=True
            )
    elif 'scan_results_df' in st.session_state:
         st.info("請點擊「啟動戰略掃描」開始分析。")

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
    # 1. 準備輸入資料與 [關鍵] 控制開關
    # ==========================================
    if 'portfolio_data' not in st.session_state:
        if st.session_state.get('logged_in'):
            db_df = load_portfolio_from_db(st.session_state['username'])
            st.session_state['portfolio_data'] = db_df if not db_df.empty else pd.DataFrame([{"代號": "2330", "持有股數": 1000}])
        else:
            st.session_state['portfolio_data'] = pd.DataFrame([
                {"代號": "2330", "持有股數": 1000}, {"代號": "2317", "持有股數": 2000}, {"代號": "2603", "持有股數": 5000}
            ])

    col_input, col_ctrl = st.columns([1, 1])
    
    with col_input:
        st.markdown("#### 1. 輸入持股明細")
        edited_df = st.data_editor(
            st.session_state['portfolio_data'], num_rows="dynamic", use_container_width=True, key="portfolio_editor",
            column_config={
                "代號": st.column_config.TextColumn("股票代號", help="請輸入台股代號"),
                "持有股數": st.column_config.NumberColumn("持有股數 (股)", min_value=1, format="%d")
            }
        )
        if not edited_df.equals(st.session_state['portfolio_data']):
            st.session_state['portfolio_data'] = edited_df
            if st.session_state.get('logged_in'):
                save_portfolio_to_db(st.session_state['username'], edited_df)

    with col_ctrl:
        st.markdown("#### 2. 監控設定")
        st.info("👇 點擊下方按鈕後，下方區域將進入實時監控模式，每 60 秒僅更新圖表數據，不會重載整頁。")
        
        # [關鍵] 必須先定義這個變數，下面的 @st.fragment 才能讀取到
        enable_monitor = st.toggle("🔴 啟動盤中實時監控 (每 60 秒更新)", value=False)

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

    @st.fragment(run_every=60 if enable_monitor else None)  
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
                best_params, final_df = run_optimization(raw_df, market_df, start_date, fee_input, tax_input)
                
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
        # 自動寄信邏輯：評分變動觸發
        # ==========================================
        if enable_monitor and portfolio_results:
            current_scores_fingerprint = {
                item['代號']: item['綜合評分'] 
                for item in portfolio_results
            }
            
            has_score_changed = (current_scores_fingerprint != st.session_state['last_sent_scores'])
            
            if has_score_changed:
                st.toast("⚡ 偵測到評分變動，準備發送通知...", icon="📧")
                
                res_df = pd.DataFrame(portfolio_results)
                try:
                    market_scored_df = calculate_alpha_score(market_df, pd.DataFrame(), pd.DataFrame())
                    analysis_html_for_email = generate_market_analysis(market_scored_df, pd.DataFrame(), pd.DataFrame())
                except Exception as e:
                    print(f"市場分析生成失敗: {e}")
                    analysis_html_for_email = "<p>暫無法獲取市場分析數據</p>"
                
                with st.spinner("📧 評分異動，正在發送信件..."):
                    success = send_analysis_email(res_df, analysis_html_for_email)
                    
                if success:
                    st.session_state['last_sent_scores'] = current_scores_fingerprint
                    st.toast(f"✅ 已發送變動通知！")
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
