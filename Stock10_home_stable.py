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
# ==========================================
@st.cache_data(ttl=5, show_spinner=False)
def get_stock_data(ticker, start_date, end_date):
    ticker = str(ticker).strip()
    candidates = [ticker]
    if ticker.isdigit(): candidates = [f"{ticker}.TW", f"{ticker}.TWO"]
    for t in candidates:
        try:
            stock = yf.Ticker(t)
            df = stock.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
            if not df.empty:
                df = df.reset_index()
                df['Date'] = df['Date'].dt.tz_localize(None).dt.normalize()
                return df, t
        except: continue
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
# 2. 指標計算 (Updated)
# ==========================================
def calculate_indicators(df, atr_period, multiplier, market_df):
    data = df.copy()
    if not market_df.empty:
        data = pd.merge(data, market_df, on='Date', how='left', suffixes=('', '_Market'))
        data['Market_RSI'] = data['Market_RSI'].ffill().fillna(50)
        data['Market_MA20'] = data['Market_MA20'].ffill().fillna(0)
    else:
        data['Market_RSI'] = 50
        data['Market_MA20'] = 0
    
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
    
    # RSI
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
    return data.dropna(subset=['MA60', 'SuperTrend', 'RSI'])

# ==========================================
# 3. 策略邏輯 & 輔助 (Modified with Confidence Score)
# ==========================================
def run_simple_strategy(data, rsi_buy_thresh, fee_rate=0.001425, tax_rate=0.003):
    """
    執行策略回測，計算含成本淨報酬，並加入「AI 信心值」計算
    """
    df = data.copy()
    positions = []; reasons = []; actions = []; target_prices = []
    return_labels = []; confidences = [] # [新增] 信心值列表
    
    position = 0; days_held = 0; entry_price = 0.0; trade_type = 0
    
    # 轉為 numpy array 加速迭代
    close = df['Close'].values; trend = df['Trend'].values; rsi = df['RSI'].values
    bb_lower = df['BB_Lower'].values; ma20 = df['MA20'].values; ma60 = df['MA60'].values
    volume = df['Volume'].values; vol_ma20 = df['Vol_MA20'].values
    obv = df['OBV'].values; obv_ma20 = df['OBV_MA20'].values
    market_panic = df['Is_Market_Panic'].values
    
    # [新增] 預先計算布林帶寬，用於判斷壓縮
    bb_width = (df['BB_Upper'] - df['BB_Lower']) / df['BB_Mid']
    bb_width_vals = bb_width.values

    for i in range(len(df)):
        signal = position; reason_str = ""; action_code = "Hold" if position == 1 else "Wait"
        this_target = entry_price * 1.15 if position == 1 else np.nan
        ret_label = ""; conf_score = 0 # [新增] 預設信心分數

        # --- 進場邏輯 ---
        if position == 0:
            is_buy = False
            # 策略 A: 動能突破
            if (trend[i]==1 and (i>0 and trend[i-1]==-1) and volume[i]>vol_ma20[i] and close[i]>ma60[i] and rsi[i]>55 and obv[i]>obv_ma20[i]):
                is_buy=True; trade_type=1; reason_str="動能突破"
            # 策略 B: 均線回測
            elif trend[i]==1 and close[i]>ma60[i] and (df['Low'].iloc[i]<=ma20[i]*1.02) and close[i]>ma20[i] and volume[i]<vol_ma20[i] and rsi[i]>45:
                is_buy=True; trade_type=1; reason_str="均線回測"
            # 策略 C: 籌碼佈局
            elif close[i]>ma60[i] and obv[i]>obv_ma20[i] and volume[i]<vol_ma20[i] and (close[i]<ma20[i] or rsi[i]<55) and close[i]>bb_lower[i]:
                is_buy=True; trade_type=3; reason_str="籌碼佈局"
            # 策略 D: 超賣反彈
            elif rsi[i]<rsi_buy_thresh and close[i]<bb_lower[i] and market_panic[i] and volume[i]>vol_ma20[i]*0.5:
                is_buy=True; trade_type=2; reason_str="超賣反彈"
            
            if is_buy:
                signal=1; days_held=0; entry_price=close[i]; action_code="Buy"
                
                # === [核心演算法] 計算信心值 (0-99) ===
                base_score = 60 # 基礎分
                
                # 1. 量能因子 (+15)
                if volume[i] > vol_ma20[i] * 1.5: base_score += 15
                elif volume[i] > vol_ma20[i]: base_score += 8
                
                # 2. 趨勢因子 (+10)
                # 判斷 MA60 斜率 (簡單判定：當前 > 5天前)
                if i > 5 and ma60[i] > ma60[i-5] and close[i] > ma60[i]: base_score += 10
                
                # 3. RSI 位階因子 (+10)
                # 突破策略在 60-75 最強，反彈策略在 <25 最強
                if trade_type == 1 and 60 <= rsi[i] <= 75: base_score += 10
                elif trade_type == 2 and rsi[i] <= 25: base_score += 10
                
                # 4. 波動壓縮因子 (+5)
                # 如果前幾天布林帶寬很窄 (小於 0.1)，現在擴大，代表噴出
                if i > 3 and bb_width_vals[i-1] < 0.15: base_score += 5
                
                conf_score = min(base_score, 99) # 上限 99
        
        # --- 出場邏輯 ---
        elif position == 1:
            days_held+=1
            drawdown=(close[i]-entry_price)/entry_price
            
            # 動態調整策略類型
            if trade_type==2 and trend[i]==1: trade_type=1; reason_str="反彈轉波段"
            if trade_type==3 and volume[i]>vol_ma20[i]*1.2: trade_type=1; reason_str="佈局完成發動"
            
            is_sell = False
            # 停損
            if drawdown < -0.10:
                is_sell=True; reason_str="觸發停損"; action_code="Sell"
            # 鎖倉期
            elif days_held <= 3:
                action_code="Hold"; reason_str="鎖倉觀察"
            # 條件出場
            else:
                if trade_type==1 and trend[i]==-1: is_sell=True; reason_str="趨勢轉弱"
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
        confidences.append(conf_score if action_code == "Buy" else 0) # 記錄信心值
        
    df['Position']=positions; df['Reason']=reasons; df['Action']=actions
    df['Target_Price']=target_prices; df['Return_Label']=return_labels
    df['Confidence'] = confidences # [新增]
    
    # === 計算含成本報酬 ===
    df['Real_Position'] = df['Position'].shift(1).fillna(0)
    df['Market_Return'] = df['Close'].pct_change().fillna(0)
    
    # 1. 策略毛利
    df['Strategy_Return'] = df['Real_Position'] * df['Market_Return']
    
    # 2. 扣除成本 (Buy: 手續費, Sell: 手續費+稅)
    cost_series = pd.Series(0.0, index=df.index)
    cost_series[df['Action'] == 'Buy'] = fee_rate
    cost_series[df['Action'] == 'Sell'] = fee_rate + tax_rate
    
    df['Strategy_Return'] = df['Strategy_Return'] - cost_series
    
    df['Cum_Strategy']=(1+df['Strategy_Return']).cumprod()
    df['Cum_Market']=(1+df['Market_Return']).cumprod()
    return df

def run_optimization(raw_df, market_df, user_start_date, fee_rate=0.001425, tax_rate=0.003):
    """
    在指定時間範圍內尋找最佳參數
    """
    best_ret = -999; best_params = None; best_df = None; target_start = pd.to_datetime(user_start_date)
    
    # 參數空間搜尋 (Grid Search)
    for m in [3.0, 3.5]:
        for r in [25, 30]:
            # 計算指標 (耗時操作建議移至迴圈外，但在這裡為了簡單保持結構)
            df_ind = calculate_indicators(raw_df, 10, m, market_df)
            df_slice = df_ind[df_ind['Date'] >= target_start].copy()
            
            if df_slice.empty: continue
            
            # 帶入成本進行回測
            df_res = run_simple_strategy(df_slice, r, fee_rate, tax_rate)
            
            # 使用累積報酬率作為評分標準
            ret = df_res['Cum_Strategy'].iloc[-1] - 1
            
            if ret > best_ret:
                best_ret = ret
                best_params = {'Mult':m, 'RSI_Buy':r, 'Return':ret}
                best_df = df_res
                
    return best_params, best_df

# 修改後：傳遞成本參數
def run_optimization(raw_df, market_df, user_start_date, fee_rate=0.001425, tax_rate=0.003):
    best_ret = -999; best_params = None; best_df = None; target_start = pd.to_datetime(user_start_date)
    
    # 為了節省運算，這裡只展示部分參數組合，實務上可擴增
    for m in [3.0, 3.5]:
        for r in [25, 30]:
            df_ind = calculate_indicators(raw_df, 10, m, market_df)
            df_slice = df_ind[df_ind['Date'] >= target_start].copy()
            if df_slice.empty: continue
            
            # [關鍵] 傳入成本參數
            df_res = run_simple_strategy(df_slice, r, fee_rate, tax_rate)
            
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
    改良版 Alpha Score：引入「市場體制 (Regime)」概念
    區分「順勢動能」與「恐慌超跌」兩種邏輯，避免崩盤時發出賣出訊號。
    """
    df = df.copy()
    if 'Alpha_Score' not in df.columns: df['Alpha_Score'] = 0.0
    if 'Score_Log' not in df.columns: df['Score_Log'] = ""

    # 輔助函式：記錄分數變動
    def apply_rule(mask, points, reason):
        if not mask.any(): return
        df.loc[mask, 'Alpha_Score'] += points
        sign = "+" if points > 0 else ""
        df.loc[mask, 'Score_Log'] = df.loc[mask, 'Score_Log'] + f"[{reason}{sign}{points}]"

    # ====================================================
    # 0. 計算必要指標 (確保欄位存在)
    # ====================================================
    if 'MA60' not in df.columns: df['MA60'] = df['Close'].rolling(60).mean()
    if 'MA20' not in df.columns: df['MA20'] = df['Close'].rolling(20).mean()
    if 'VIX' not in df.columns: df['VIX'] = 20.0 # 防呆預設
    
    # 計算乖離率 (Bias)：衡量超跌程度
    df['Bias_60'] = (df['Close'] - df['MA60']) / df['MA60']
    
    # 判斷市場體制 (Regime Identification)
    # 恐慌模式定義：VIX > 25 或 RSI < 30 或 季線負乖離超過 10%
    is_panic = (df['VIX'] > 25) | (df['RSI'] < 30) | (df['Bias_60'] < -0.10)
    
    # ====================================================
    # A. 趨勢與乖離 (Trend & Mean Reversion)
    # ====================================================
    
    # --- 正常體制 (Normal Regime)：順勢操作 ---
    # 邏輯：站上均線是好事，跌破是壞事
    normal_bull = (~is_panic) & (df['Close'] > df['MA60'])
    normal_bear = (~is_panic) & (df['Close'] < df['MA60'])
    
    apply_rule(normal_bull, 20, "順勢-站上季線")
    apply_rule(normal_bear, -20, "順勢-跌破季線")
    
    # --- 恐慌體制 (Panic Regime)：逆勢操作 ---
    # 邏輯：在恐慌時，嚴重跌破均線代表「黃金坑」，反而給予高分
    
    # 恐慌但負乖離適中 (跌破季線但未深跌)：觀望或小買
    panic_dip = (is_panic) & (df['Bias_60'] < 0) & (df['Bias_60'] > -0.15)
    apply_rule(panic_dip, 10, "恐慌-價值浮現") 
    
    # 極度恐慌且深跌 (負乖離 > 15%)：強力買進
    panic_deep_value = (is_panic) & (df['Bias_60'] <= -0.15)
    apply_rule(panic_deep_value, 40, "恐慌-超跌黃金坑") # 給予極高分以抵銷技術面的弱勢
    
    # 恐慌時若還站穩季線 (強勢股)
    panic_strong = (is_panic) & (df['Close'] > df['MA60'])
    apply_rule(panic_strong, 20, "恐慌-抗跌強勢")

    # ====================================================
    # B. 技術動能 (Momentum)
    # ====================================================
    # MACD / KD 邏輯維持不變，作為輔助
    exp12 = df['Close'].ewm(span=12, adjust=False).mean()
    exp26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['DIF'] = exp12 - exp26
    df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
    
    apply_rule(df['DIF'] > df['DEA'], 10, "MACD多")
    apply_rule(df['DIF'] < df['DEA'], -10, "MACD空")
    
    # RSI 極端值修正
    # 正常盤：RSI > 55 強
    apply_rule((~is_panic) & (df['RSI'] > 55), 5, "動能強")
    # 恐慌盤：RSI < 25 (超賣) 加分，而非扣分
    apply_rule((is_panic) & (df['RSI'] < 25), 15, "RSI超賣鈍化")

    # ====================================================
    # C. 恐慌指數 VIX 特別加權
    # ====================================================
    # VIX 越高，分數越高 (左側交易核心)
    apply_rule(df['VIX'] > 20, 10, "VIX警戒")
    apply_rule(df['VIX'] > 30, 20, "VIX極度恐慌") # 累加後共 +30

    # ====================================================
    # D. 籌碼面 (Chips)
    # ====================================================
    if not margin_df.empty and not short_df.empty:
        try:
            # 資料前處理與合併 (維持原邏輯，但加入錯誤處理)
            df['Date_Join'] = pd.to_datetime(df['Date']).dt.normalize()
            m_clean = margin_df[['date', 'TodayBalance']].copy()
            m_clean.columns = ['Date_Join', 'Margin_Bal']
            m_clean['Date_Join'] = pd.to_datetime(m_clean['Date_Join']).dt.normalize()
            s_clean = short_df[['date', 'TodayBalance']].copy()
            s_clean.columns = ['Date_Join', 'Short_Bal']
            s_clean['Date_Join'] = pd.to_datetime(s_clean['Date_Join']).dt.normalize()
            
            # 暫存合併
            temp_df = df.merge(m_clean, on='Date_Join', how='left').merge(s_clean, on='Date_Join', how='left')
            
            # 計算變化
            temp_df['Margin_Bal'] = temp_df['Margin_Bal'].ffill()
            temp_df['Short_Bal'] = temp_df['Short_Bal'].ffill()
            temp_df['M_Chg'] = temp_df['Margin_Bal'].pct_change(5)
            temp_df['S_Chg'] = temp_df['Short_Bal'].pct_change(5)
            temp_df['P_Chg'] = temp_df['Close'].pct_change(5)
            
            # 籌碼邏輯應用到原 df
            # 融資斷頭 (多殺多後通常是買點)：價跌 & 融資大減
            mask_wash = (temp_df['P_Chg'] < -0.05) & (temp_df['M_Chg'] < -0.03)
            apply_rule(mask_wash, 25, "融資斷頭清洗")
            
            # 軋空
            mask_squeeze = (temp_df['P_Chg'] > 0.02) & (temp_df['S_Chg'] > 0.02)
            apply_rule(mask_squeeze, 15, "軋空動能")

            # 清理
            df = df.drop(columns=['Date_Join', 'Bias_60'], errors='ignore') # 移除暫存欄位
        except: pass

    # ====================================================
    # E. 最終分數收斂與建議倉位計算
    # ====================================================
    df['Alpha_Score'] = df['Alpha_Score'].clip(-100, 100)
    
    # 建議倉位計算邏輯改良：
    # 如果是「恐慌體制」且分數轉正，建議倉位應該更激進 (Base Position)
    # 正常：0~100 線性對應
    # 恐慌：若 Score > 0 (代表超跌訊號確認)，底倉至少 40%
    
    def calculate_position(row):
        score = row['Alpha_Score']
        vix = row['VIX'] if 'VIX' in row else 0
        
        base_pos = (score + 100) / 2
        
        # 恐慌加權：如果 VIX 高且分數正 (代表 AI 認為跌過頭了)，強制拉高持股水位
        if vix > 25 and score > 10:
            return max(base_pos, 50) # 恐慌時若訊號轉多，至少半倉
        return base_pos

    df['Recommended_Position'] = df.apply(calculate_position, axis=1).clip(0, 100)
    
    return df

# ==========================================
# 6. 主儀表板繪製 (Updated)
# ==========================================
def generate_market_analysis(df, margin_df, short_df):
    """
    根據當前數據生成前瞻性市場分析報告
    包含：趨勢診斷、風險評估、籌碼結構、操作建議
    """
    if df.empty: return "無足夠數據進行分析"
    
    last = df.iloc[-1]
    prev = df.iloc[-2]
    
    # 1. 定義數據變數
    close = last['Close']
    ma20 = last['MA20'] if 'MA20' in last else 0
    ma60 = last['MA60'] if 'MA60' in last else 0
    rsi = last['RSI']
    vix = last['VIX']
    score = last['Alpha_Score']
    
    # 計算斜率與變化
    ma20_slope = (last['MA20'] - prev['MA20'])
    vix_change = last['VIX'] - prev['VIX']
    
    # 2. 判定市場體制 (Regime)
    bias_60 = (close - ma60) / ma60 if ma60 != 0 else 0
    is_panic = (vix > 25) or (rsi < 30) or (bias_60 < -0.10)
    
    analysis = []
    
    # --- A. 體制與趨勢診斷 ---
    if is_panic:
        status = "🔴 空頭/恐慌體制"
        desc = "市場處於高波動與情緒過度反應階段。此時技術支撐易失效，需關注「乖離率」與「VIX」何時收斂。"
        if score > 0:
            strategy = "💡 建議：數據顯示超跌訊號浮現 (黃金坑)，建議分批逆勢建倉，但需保留現金以防末跌段。"
        else:
            strategy = "🛡️ 建議：跌勢未止且尚未出現足夠的恐慌清洗，建議保留現金，靜待 VIX 見頂轉折。"
    else:
        status = "🟢 多頭/正常體制"
        desc = "市場處於理性波動階段，股價主要受趨勢線 (均線) 支撐。"
        if close > ma20 and ma20_slope > 0:
            strategy = "🚀 建議：均線多頭排列，趨勢穩健。操作應順勢而為，遇月線回測不破為加碼點。"
        elif close < ma20:
            strategy = "⚠️ 建議：短期趨勢轉弱，跌破月線。若無法在 3 日內站回，需提防向季線尋求支撐，建議縮減多單。"
        else:
            strategy = "👀 建議：趨勢不明，建議區間操作。"

    analysis.append(f"{status}")
    analysis.append(f"{desc}")
    analysis.append(f"> {strategy}")
    analysis.append("---")

    # --- B. 關鍵指標解析 ---
    # 1. 恐慌指數 (VIX)
    vix_analysis = f"- 恐慌指數 (VIX: {vix:.2f})："
    if vix > 30:
        vix_analysis += "處於極端高檔。歷史經驗顯示，VIX > 30 通常難以維持長久，**短線極高機率出現報復性反彈**。"
    elif vix > 20 and vix_change > 0:
        vix_analysis += "持續攀升中，避險情緒增溫。在 VIX 未轉折向下前，不宜過度樂觀。"
    elif vix < 15:
        vix_analysis += "處於低檔安逸區。需提防市場過度樂觀引發的修正，居高思危。"
    else:
        vix_analysis += "處於正常波動區間。"
    analysis.append(vix_analysis)

    # 2. 動能 (RSI)
    rsi_analysis = f"- 動能指標 (RSI: {rsi:.1f})："
    if rsi < 25:
        rsi_analysis += "進入嚴重超賣區 (鈍化)。若股價再創新低但 RSI 未破底 (底背離)，將是**強烈的止跌訊號**。"
    elif rsi > 75:
        rsi_analysis += "進入過熱區。若量能無法跟上，需提防高檔假突破。"
    elif 45 <= rsi <= 55:
        rsi_analysis += "動能中性，等待方向表態。"
    else:
        rsi_analysis += "動能維持正常。"
    analysis.append(rsi_analysis)

    # --- C. 籌碼結構 (若有數據) ---
    if not margin_df.empty and not short_df.empty:
        try:
            m_curr = margin_df['TodayBalance'].iloc[-1]
            m_prev = margin_df['TodayBalance'].iloc[-5]
            p_chg = (close - df['Close'].iloc[-5]) / df['Close'].iloc[-5]
            m_chg = (m_curr - m_prev) / m_prev
            
            chip_analysis = "- 籌碼結構："
            if p_chg < -0.05 and m_chg < -0.03:
                chip_analysis += "📉 清洗浮額 (Washout) - 股價急跌伴隨融資大減，散戶停損出場，籌碼趨於安定，**有利於築底**。"
            elif p_chg < -0.05 and m_chg > 0.01:
                chip_analysis += "⚠️ 融資套牢 - 股價下跌但融資逆勢增加，顯示散戶正在「接刀」。上檔賣壓沈重，**反彈空間有限**。"
            elif p_chg > 0.05 and m_chg > 0.02:
                chip_analysis += "🔥 散戶追價 - 融資隨股價大增，過熱訊號，需留意主力出貨。"
            else:
                chip_analysis += "資券變化在正常範圍內。"
            analysis.append(chip_analysis)
        except: pass

    return "\n".join(analysis)


def draw_market_dashboard(market_df, start_date, end_date):
    """
    繪製總體市場儀表板：包含 Metrics、AI 前瞻分析報告與 Plotly 圖表
    """
    st.markdown("### 🌍 總體市場戰情 (Macro)")
    target_start = pd.to_datetime(start_date)
    plot_df = market_df[market_df['Date'] >= target_start].copy()
    
    if plot_df.empty: 
        st.error("無大盤數據")
        return
    
    # =========================================================
    # 1. 資料前處理與欄位映射
    # =========================================================
    # 將 Market_XXX 映射為通用名稱，供 Alpha Score 計算使用
    if 'Market_RSI' in plot_df.columns: plot_df['RSI'] = plot_df['Market_RSI']
    else: plot_df['RSI'] = 50 

    if 'Market_MA20' in plot_df.columns: plot_df['MA20'] = plot_df['Market_MA20']
    if 'Market_MA60' in plot_df.columns: plot_df['MA60'] = plot_df['Market_MA60']

    if 'Volume' in plot_df.columns:
        plot_df['Vol_MA20'] = plot_df['Volume'].rolling(20).mean()

    # =========================================================
    # 2. 獲取外部籌碼數據 (FinMind)
    # =========================================================
    margin_df_raw = get_margin_data(start_date.strftime('%Y-%m-%d'))
    margin_df = pd.DataFrame(); short_df = pd.DataFrame()
    if not margin_df_raw.empty:
        sliced = margin_df_raw[(margin_df_raw['date'] >= target_start) & (margin_df_raw['date'] <= pd.to_datetime(end_date))]
        margin_df = sliced[sliced['name'] == 'MarginPurchaseMoney']
        short_df = sliced[sliced['name'] == 'ShortSale']
    
    # =========================================================
    # 3. 執行核心演算法 (Alpha Score & Regime Detection)
    # =========================================================
    # 呼叫改良後的計分函式
    plot_df = calculate_alpha_score(plot_df, margin_df, short_df)
    
    last = plot_df.iloc[-1]
    score = last['Alpha_Score']
    vix = last['VIX']
    close = last['Close']
    ma60 = last['MA60'] if 'MA60' in last else close
    
    # =========================================================
    # 4. 介面邏輯：判定體制與評語
    # =========================================================
    
    # 判定市場體制 (Regime)
    bias = (close - ma60) / ma60
    # 恐慌條件：VIX高 或 RSI低 或 負乖離大
    is_panic_regime = (vix > 25) or (last['RSI'] < 30) or (bias < -0.10)
    
    if is_panic_regime:
        regime_label = "🐻 空頭/恐慌體制"
        regime_color = "inverse" # 醒目顯示
    else:
        regime_label = "🐂 多頭/正常體制"
        regime_color = "normal"

    # 生成 Alpha Score 評語與顏色 (紅漲綠跌邏輯)
    txt = "中性觀望"; c_score = "gray"
    
    if score >= 60: 
        if is_panic_regime:
            txt = "💎 危機入市 (黃金坑)" # 恐慌時的高分 = 抄底
        else:
            txt = "🚀 強力趨勢買進"     # 正常時的高分 = 順勢
        c_score = "#ff5252" # 紅色
    elif score >= 20: 
        txt = "分批承接" if is_panic_regime else "偏多操作"
        c_score = "#ff8a80" # 淺紅
    elif score <= -60: 
        txt = "崩盤迴避" if is_panic_regime else "強力賣出"
        c_score = "#69f0ae" # 綠色
    elif score <= -20: 
        txt = "保守觀望" if is_panic_regime else "偏空調節"
        c_score = "#b9f6ca" # 淺綠

    vix_st = "極度恐慌" if vix>30 else ("恐慌警戒" if vix>20 else ("樂觀貪婪" if vix<15 else "正常波動"))

    # =========================================================
    # 5. 顯示 Metrics (數據概覽)
    # =========================================================
    c1, c2, c3, c4 = st.columns(4)
    
    c1.metric("加權指數 / 體制", f"{last['Close']:.0f}", regime_label, delta_color="off")
    c2.metric("市場情緒 (RSI)", f"{last['Market_RSI']:.1f}", "區間: 0~100", delta_color="off")
    
    # VIX 超過 25 變色警示
    vix_delta_color = "inverse" if vix > 25 else "off"
    c3.metric("恐慌指數 (VIX)", f"{vix:.2f}", vix_st, delta_color=vix_delta_color)
    
    # Alpha Score 自定義樣式
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
    # 6. 顯示 AI 前瞻分析報告 (整合新模組)
    # =========================================================
    st.write("") # Spacer
    st.markdown("### 📋 AI 戰情室前瞻分析")
    
    analysis_text = generate_market_analysis(plot_df, margin_df, short_df)
    
    # 根據體制改變邊框顏色，強化視覺提示
    border_color = "#ff5252" if is_panic_regime else "#69f0ae"
    
    st.markdown(
        f"""
        <div style="border-left: 5px solid {border_color}; padding: 15px; background-color: #1E1E1E; border-radius: 5px;">
            {analysis_text.replace('\n', '<br>')}
        </div>
        """, 
        unsafe_allow_html=True
    )
    
    # =========================================================
    # 7. 顯示籌碼警示 (舊版邏輯保留作為輔助)
    # =========================================================
    if not margin_df.empty and not short_df.empty:
        try:
            m_c = margin_df['TodayBalance'].iloc[-1]; m_p = margin_df['TodayBalance'].iloc[-5]
            s_c = short_df['TodayBalance'].iloc[-1]; s_p = short_df['TodayBalance'].iloc[-5]
            p_c = plot_df['Close'].iloc[-1]; p_p = plot_df['Close'].iloc[-5]
            
            # 防除以零
            if m_p == 0: m_p = 1
            if s_p == 0: s_p = 1
            if p_p == 0: p_p = 1
            
            m_chg = (m_c-m_p)/m_p; s_chg = (s_c-s_p)/s_p; p_chg = (p_c-p_p)/p_p
            
            msg = ""; typ = "info"
            if m_chg > 0.02 and p_chg < -0.02: msg = "⚠️ **籌碼警示** - 融資套牢，提防多殺多。"; typ="error"
            elif s_chg > 0.05 and p_chg > 0.02: msg = "🚀 **軋空訊號** - 空單被鎖，助漲多頭。"; typ="success"
            elif m_chg < -0.02 and p_chg > 0.02: msg = "💪 **籌碼安定** - 融資換手，籌碼流入安定手。"; typ="success"
            
            if typ=="error": st.error(msg)
            elif typ=="success": st.success(msg)
        except: pass

    # =========================================================
    # 8. 繪製圖表 (Plotly)
    # =========================================================
    st.write("")
    fig = make_subplots(rows=8, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                        row_heights=[0.3, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                        subplot_titles=("加權指數", "買賣評等 (Alpha Score)", "籌碼能量 (OBV)", "動能指標 (RSI)", "恐慌指數 (VIX)", "建議持股水位 (%)", "融資餘額", "融券餘額"))
    
    # Row 1: 價格
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Close'], name='收盤價', line=dict(color='white')), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['MA20'], name='月線', line=dict(color='yellow', width=1)), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['MA60'], name='季線', line=dict(color='rgba(255, 255, 255, 0.5)', width=1)), row=1, col=1)
    
    # Row 2: Alpha Score (紅漲綠跌)
    colors_score = ['#ff5252' if v > 0 else '#69f0ae' for v in plot_df['Alpha_Score']]
    fig.add_trace(go.Bar(x=plot_df['Date'], y=plot_df['Alpha_Score'], name='評等', marker_color=colors_score), row=2, col=1)
    
    # Row 3: OBV
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['OBV'], name='OBV', line=dict(color='orange')), row=3, col=1)
    
    # Row 4: RSI
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['RSI'], name='RSI', line=dict(color='cyan')), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
    
    # Row 5: VIX (紅線警戒)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['VIX'], name='VIX', line=dict(color='#ab47bc')), row=5, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=25, y1=25, line=dict(color="red", dash="dash"), row=5, col=1)
    
    # Row 6: 建議持股
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Recommended_Position'], name='持股%', line=dict(color='#00e676'), fill='tozeroy'), row=6, col=1)
    
    # Row 7/8: 資券
    if not margin_df.empty: fig.add_trace(go.Scatter(x=margin_df['date'], y=margin_df['TodayBalance'], name='融資', line=dict(color='#ef5350'), fill='tozeroy'), row=7, col=1)
    if not short_df.empty: fig.add_trace(go.Scatter(x=short_df['date'], y=short_df['TodayBalance'], name='融券', line=dict(color='#26a69a'), fill='tozeroy'), row=8, col=1)

    # 圖表設定
    fig.update_xaxes(range=[start_date, end_date])
    fig.update_yaxes(side='right')
    fig.update_yaxes(range=[-110, 110], row=2, col=1, side='right')
    fig.update_yaxes(range=[0, 100], row=6, col=1, side='right')
    fig.update_layout(height=1600, template="plotly_dark", margin=dict(l=50, r=50, t=60, b=40), hovermode="x unified", showlegend=False)
    
    st.plotly_chart(fig, use_container_width=True)

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
    # 1. 資料準備：建立搜尋清單 (代號 + 中文名稱)
    # ==================================================
    if st.session_state['all_stock_list'] is None:
        st.session_state['all_stock_list'] = get_master_stock_data()
    
    df_all = st.session_state['all_stock_list']
    
    # 建立 "代號 名稱" 的格式清單 (例如: "2330 台積電")
    # 這樣使用者在搜尋框打 "2330" 或 "台積電" 都能篩選到
    search_list = [f"{row['代號']} {row['名稱']}" for idx, row in df_all.iterrows()]
    
    # 確保基本清單 (靜態清單) 也在裡面，避免 API 失敗時完全無法運作
    base_search_list = [f"{k} {v}" for k, v in TW_STOCK_NAMES_STATIC.items()]
    # 合併並去重排序
    full_search_options = sorted(list(set(search_list + base_search_list)))

    # ==================================================
    # [Step 3] 導航介面優化：支援中文名稱搜尋
    # ==================================================
    
    # 1. 找出當前 Session 中 'last_ticker' 對應的選項索引
    current_ticker = st.session_state['last_ticker']
    current_index = 0
    
    # 嘗試在清單中找到目前選中的股票 (比對字串開頭是否為代號)
    for idx, opt in enumerate(full_search_options):
        if opt.startswith(str(current_ticker)):
            current_index = idx
            break

    # --- Row 1: 搜尋與確認 (使用 selectbox 取代 text_input) ---
    with st.container():
        col_search, col_run = st.columns([3, 1])
        
        with col_search:
            # [核心修改] 使用 selectbox 達成可搜尋中文與代號
            selected_option = st.selectbox(
                "搜尋股票 (支援代號或中文)",
                options=full_search_options,
                index=current_index,
                label_visibility="collapsed",
                key="stock_selector" # 設定 key 避免重繪問題
            )
            
        with col_run:
            if st.button("Go", type="primary", use_container_width=True):
                # 從選項 "2330 台積電" 中切割出 "2330"
                new_ticker = selected_option.split(" ")[0]
                st.session_state['last_ticker'] = new_ticker
                st.rerun()

    # --- Row 2: 大拇指導航區 (上一檔 / 下一檔) ---
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        if st.button("◀ 上一檔", use_container_width=True):
            new_index = (current_index - 1) % len(full_search_options)
            # 取出新選項的代號部分
            new_ticker = full_search_options[new_index].split(" ")[0]
            st.session_state['last_ticker'] = new_ticker
            st.rerun()

    with col_next:
        if st.button("下一檔 ▶", use_container_width=True):
            new_index = (current_index + 1) % len(full_search_options)
            # 取出新選項的代號部分
            new_ticker = full_search_options[new_index].split(" ")[0]
            st.session_state['last_ticker'] = new_ticker
            st.rerun()

    # ==================================================
    # 2. 自動執行分析邏輯 (更新 ticker_input 來源)
    # ==================================================
    
    # 確保 ticker_input 與上方選擇同步
    # 如果使用者剛剛選了下拉選單但沒按 Go，我們還是即時更新變數
    if 'stock_selector' in st.session_state:
        selected_code = st.session_state['stock_selector'].split(" ")[0]
        # 如果選擇變了，更新 last_ticker
        if selected_code != st.session_state['last_ticker']:
             st.session_state['last_ticker'] = selected_code

    ticker_input = st.session_state['last_ticker']

    if ticker_input: 
        # 只有當沒有快取資料或強制刷新時才顯示 spinner
        # 這裡為了流暢度，我們簡單用 spinner 包住
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
                # ==========================================
                # 1. 執行 Alpha 評分與演算歷程提取
                # ==========================================
                # 傳入空 DataFrame 以忽略籌碼面(除非有額外獲取個股資券)，專注於技術與趨勢面
                stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                
                # 提取基礎分數與日誌
                base_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                base_log = stock_alpha_df['Score_Log'].iloc[-1]
                
                # ==========================================
                # 2. 執行情境感知調整 (Context-Aware Adjustment)
                # ==========================================
                adjusted_score = base_score
                adjustment_log = ""
                
                current_price = final_df['Close'].iloc[-1]
                ma20 = final_df['MA20'].iloc[-1]
                ma60 = final_df['MA60'].iloc[-1]
                rsi_now = final_df['RSI'].iloc[-1]
                rsi_prev = final_df['RSI'].iloc[-2]
                
                # 判斷最後一次訊號理由，識別策略屬性
                last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                is_rebound_strategy = False
                if last_trade is not None:
                    buy_reason = str(last_trade['Reason'])
                    if any(x in buy_reason for x in ["反彈", "超賣", "回測", "低檔"]):
                        is_rebound_strategy = True

                # 取得當前建議動作
                action, color, reason = analyze_signal(final_df)

                # 針對「買進/續抱」狀態進行加權修正
                if action == "✊ 續抱" or action == "🚀 買進":
                    if is_rebound_strategy:
                        # 情境 A: 反彈策略 (容許破季線，重視短期動能)
                        if current_price < ma60: 
                            adjusted_score += 15
                            adjustment_log += "[反彈位階修正+15]"
                        
                        # 檢查 RSI 是否轉強
                        if rsi_now > rsi_prev:
                            adjusted_score += 10
                            adjustment_log += "[RSI翻揚+10]"
                        
                        # 檢查是否站上 5 日線 (模擬)
                        ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                        if current_price > ma5:
                            adjusted_score += 10
                            adjustment_log += "[站穩MA5+10]"
                    else:
                        # 情境 B: 趨勢策略 (重視均線排列)
                        if current_price > ma20 and ma20 > ma60:
                            adjusted_score += 10
                            adjustment_log += "[多頭排列+10]"
                        
                        if final_df['Volume'].iloc[-1] > final_df['Vol_MA20'].iloc[-1]:
                            adjusted_score += 5
                            adjustment_log += "[量增+5]"

                # 限制分數範圍 (-100 ~ 100)
                final_composite_score = max(min(adjusted_score, 100), -100)
                
                # 組合最終顯示日誌
                full_log_text = f"{base_log} {adjustment_log}" if base_log or adjustment_log else "無顯著特徵"

                # ==========================================
                # 3. 計算其餘績效指標
                # ==========================================
                beta, vol, personality = calculate_stock_personality(final_df, market_df)
                hit_rate, hits, total = calculate_target_hit_rate(final_df)
                real_win_rate, real_wins, real_total, avg_pnl = calculate_realized_win_rate(final_df)
                risk_metrics = calculate_risk_metrics(final_df)
                
                # 存入 Session State (快取用)
                st.session_state['analysis_history'][fmt_ticker] = {
                    'df': final_df, 'params': best_params, 'action': action,
                    'reason': reason, 'beta': beta, 'vol': vol, 'personality': personality,
                    'name': name, 
                    'hit_rate': hit_rate, 'hits': hits, 'total_trades': total,
                    'real_win_rate': real_win_rate, 'real_wins': real_wins, 'real_total': real_total, 'avg_pnl': avg_pnl,
                    'risk': risk_metrics,
                    'validation': validation_result,
                    'alpha_score': final_composite_score, # 新增
                    'score_log': full_log_text            # 新增
                }

                # ==========================================
                # 4. 前端介面顯示
                # ==========================================
                # --- (A) 標題與 AI 評分區塊 ---
                st.markdown(f"## {ticker_input} {name}")
                st.caption(f"策略邏輯: {reason} | 波動率: {vol}")

                # 建立評分專屬面板
                st.markdown("### 🏆 AI 綜合評分與決策依據")
                score_col, log_col = st.columns([1, 3])
                
                with score_col:
                    # 動態顏色設定
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

                # --- (B) 核心績效數據矩陣 ---
                strat_mdd = calculate_mdd(final_df['Cum_Strategy'])
                strat_ret = best_params['Return'] * 100

                # Row 1: 獲利能力 & 勝率
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("淨報酬 (含成本)", f"{strat_ret:.1f}%", f"MDD: {strat_mdd:.1f}%")
                m2.metric("實際勝率 (Realized)", real_win_rate, f"{real_wins}勝 / {real_total}總")
                m3.metric("目標達成率 (Target)", hit_rate, f"{hits}次達標 (+15%)")
                m4.metric("盈虧因子 (PF)", f"{risk_metrics.get('Profit_Factor', 0):.2f}", f"夏普: {risk_metrics.get('Sharpe', 0):.2f}")

                # ==========================================
                # 5. Tabs 繪圖區
                # ==========================================
                tab1, tab2, tab3, tab4 = st.tabs(["📈 操盤決策圖", "💰 權益曲線", "🎲 蒙地卡羅模擬", "🧪 有效性驗證"])
                
                # [Tab 1: K線圖]
                with tab1:
                    # 建立子圖
                    fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                                        row_heights=[0.5, 0.15, 0.15, 0.20], 
                                        subplot_titles=("", "成交量", "法人籌碼 (OBV)", "相對強弱指標 (RSI)"))
            
                    # 主圖 K 線
                    fig.add_trace(go.Candlestick(
                        x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                        low=final_df['Low'], close=final_df['Close'], name='K線',
                        increasing_line_color='#ef5350', decreasing_line_color='#00bfa5' 
                    ), row=1, col=1)
                    
                    # 均線
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['SuperTrend'], mode='lines', 
                                            line=dict(color='yellow', width=1.5), name='停損基準線'), row=1, col=1)
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', 
                                            line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線'), row=1, col=1)

                    # 買賣點標記函式
                    final_df['Buy_Y'] = final_df['Low'] * 0.92
                    final_df['Sell_Y'] = final_df['High'] * 1.08

                    def get_buy_text(sub_df):
                        # 在圖表上顯示該次買進時的信心分數
                        return [f"<b>{score}</b>" for score in sub_df['Confidence']]

                    def get_sell_text(sub_df):
                        labels = []
                        for idx, row in sub_df.iterrows():
                            ret = row['Return_Label']
                            reason_str = row['Reason'].replace("觸發", "").replace("操作", "")
                            labels.append(f"{ret}<br>({reason_str})")
                        return labels

                    # 繪製不同類型的買點
                    # A. 趨勢/突破
                    buy_trend = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('突破|回測|動能'))]
                    if not buy_trend.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_trend['Date'], y=buy_trend['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_trend), textposition="bottom center",
                            textfont=dict(color='#FFD700', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#FFD700', line=dict(width=1, color='black')), 
                            name='買進 (趨勢)', hovertext=buy_trend['Reason']
                        ), row=1, col=1)
                    
                    # B. 反彈/超賣
                    buy_panic = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('反彈|超賣'))]
                    if not buy_panic.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_panic['Date'], y=buy_panic['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_panic), textposition="bottom center",
                            textfont=dict(color='#00FFFF', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#00FFFF', line=dict(width=1, color='black')), 
                            name='買進 (反彈)', hovertext=buy_panic['Reason']
                        ), row=1, col=1)
                    
                    # C. 籌碼/佈局
                    buy_chip = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('籌碼|佈局'))]
                    if not buy_chip.empty:
                        fig.add_trace(go.Scatter(
                            x=buy_chip['Date'], y=buy_chip['Buy_Y'], mode='markers+text',
                            text=get_buy_text(buy_chip), textposition="bottom center",
                            textfont=dict(color='#DDA0DD', size=11),
                            marker=dict(symbol='triangle-up', size=14, color='#DDA0DD', line=dict(width=1, color='black')), 
                            name='買進 (籌碼)', hovertext=buy_chip['Reason']
                        ), row=1, col=1)

                    # 賣出點
                    sell_all = final_df[final_df['Action'] == 'Sell']
                    if not sell_all.empty:
                        fig.add_trace(go.Scatter(
                            x=sell_all['Date'], y=sell_all['Sell_Y'], mode='markers+text', 
                            text=get_sell_text(sell_all), textposition="top center",
                            textfont=dict(color='white', size=11),
                            marker=dict(symbol='triangle-down', size=14, color='#FF00FF', line=dict(width=1, color='black')), 
                            name='賣出', hovertext=sell_all['Reason']
                        ), row=1, col=1)
                    
                    # 副圖
                    colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
                    fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'], marker_color=colors_vol, name='成交量'), row=2, col=1)
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=3, col=1)
                    fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=4, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
                    fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
                    
                    fig.update_layout(height=800, template="plotly_dark", xaxis_rangeslider_visible=False, margin=dict(l=20, r=40, t=30, b=20),
                                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
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

                # [Tab 4: 有效性驗證]
                with tab4:
                    if validation_result:
                        st.markdown(f"### 🧪 樣本外測試 (Walk-Forward Analysis)")
                        tr_cagr = validation_result['train']['cagr'] * 100
                        te_cagr = validation_result['test']['cagr'] * 100
                        
                        vt1, vt2 = st.columns(2)
                        vt1.metric("訓練集年化報酬", f"{tr_cagr:.1f}%")
                        vt2.metric("測試集年化報酬", f"{te_cagr:.1f}%", f"差異: {(te_cagr-tr_cagr):.1f}%")
                        
                        fig_val = go.Figure()
                        fig_val.add_trace(go.Scatter(x=validation_result['train']['df']['Date'], y=validation_result['train']['df']['Cum_Strategy'], name='訓練', line=dict(color='gray', dash='dot')))
                        scale_factor = validation_result['train']['df']['Cum_Strategy'].iloc[-1]
                        fig_val.add_trace(go.Scatter(x=validation_result['test']['df']['Date'], y=validation_result['test']['df']['Cum_Strategy']*scale_factor, name='測試', line=dict(color='#00e676')))
                        fig_val.add_vline(x=validation_result['split_date'].timestamp()*1000, line_dash="dash", line_color="white")
                        fig_val.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=30, b=10))
                        st.plotly_chart(fig_val, use_container_width=True)
                    else:
                        st.warning("數據不足，無法執行樣本外驗證。")

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
    # 1. 準備輸入資料 (主執行緒)
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
        
        # 資料同步邏輯
        if not edited_df.equals(st.session_state['portfolio_data']):
            st.session_state['portfolio_data'] = edited_df
            if st.session_state.get('logged_in'):
                save_portfolio_to_db(st.session_state['username'], edited_df)

    with col_ctrl:
        st.markdown("#### 2. 監控設定")
        st.info("👇 點擊下方按鈕後，下方區域將進入實時監控模式，每 60 秒僅更新圖表數據，不會重載整頁。")
        # 使用 toggle 來切換「持續監控模式」
        enable_monitor = st.toggle("🔴 啟動盤中實時監控 (每 60 秒更新)", value=False)

    # ==========================================
    # 3. 定義局部刷新片段 (The Fragment)
    # ==========================================
    # 關鍵：這個函式內部的程式碼，會獨立於主程式之外自己循環運行
    # run_every=60 代表這個片段每 60 秒會自己重跑一次
    @st.fragment(run_every=60 if enable_monitor else None) 
    def render_live_dashboard(target_df):
        if target_df.empty:
            st.warning("⚠️ 請先輸入持股資料。")
            return

        # === [修正] 強制轉換為台北時間 ===
        # 伺服器預設是 UTC，我們加上時區校正
        tw_tz = pytz.timezone('Asia/Taipei')
        update_time = datetime.now(tw_tz).strftime("%H:%M:%S")
        
        if enable_monitor:
            st.caption(f"⚡ 實時監控中... (最後更新: {update_time})")
        else:
            st.caption(f"Analysis Snapshot (時間: {update_time})")

        portfolio_results = []
        
        # 使用 status container 顯示動態進度，跑完自動收合
        with st.status(f"正在全方位分析 {len(target_df)} 檔持股結構...", expanded=True) as status:
            
            # === 核心迴圈開始 ===
            for idx, row in target_df.iterrows():
                ticker = str(row['代號']).strip()
                shares = row['持有股數']
                
                # 基本防呆
                if not ticker or shares <= 0: continue
                
                # 1. 獲取即時資料 (利用 ttl=1 快取，實現盤中即時更新)
                raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
                name = get_stock_name(fmt_ticker)
                
                # 資料不足跳過
                if raw_df.empty or len(raw_df) < 60:
                    continue 
                    
                # 2. 執行策略回測 (取得技術買賣點)
                best_params, final_df = run_optimization(raw_df, market_df, start_date, fee_input, tax_input)
                
                if final_df is None or final_df.empty: continue

                # 3. 計算基礎數值
                current_price = final_df['Close'].iloc[-1]
                market_value = current_price * shares
                
                # 4. [透明化評分] 計算 Alpha Score 並取得計分歷程
                # 傳入空 df 以加速運算 (主要依賴技術指標: MACD, KD, 均線...)
                stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                
                # 取得該股最後一天的詳細評分字串 (例如: "[季線+15][MACD金叉+10]")
                base_score_log = stock_alpha_df['Score_Log'].iloc[-1] 
                
                # 5. 取得技術訊號 (Action)
                action, color, tech_reason = analyze_signal(final_df)
                
                # 6. [情境感知] 評分動態調整 (Context-Aware Adjustment)
                final_score = base_alpha_score
                adjustment_log = []
                
                # A. 判斷策略屬性 (是否為逆勢/反彈策略)
                last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                is_rebound = False
                if last_trade is not None:
                    buy_reason = str(last_trade['Reason'])
                    if any(x in buy_reason for x in ["反彈", "超賣", "回測", "籌碼"]):
                        is_rebound = True
                
                # B. 針對「續抱」或「買進」狀態進行邏輯分支
                if action == "✊ 續抱" or action == "🚀 買進":
                    if is_rebound:
                        # 情境 A: 反彈策略 (抄底邏輯)
                        # 補償 1: 不看長均線 (因為抄底必定在季線下，補回被扣的分數)
                        if current_price < final_df['MA60'].iloc[-1]: 
                            final_score += 15
                            adjustment_log.append("反彈無視季線+15")
                        
                        # 補償 2: 檢視反彈有效性 (站上短均?)
                        ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                        if current_price > ma5: 
                            final_score += 10
                            adjustment_log.append("站穩MA5+10")
                        else:
                            final_score -= 5
                            adjustment_log.append("破MA5-5")
                            
                        # 補償 3: 動能檢核 (RSI翻揚?)
                        rsi_now = final_df['RSI'].iloc[-1]
                        rsi_prev = final_df['RSI'].iloc[-2]
                        if rsi_now > rsi_prev: 
                            final_score += 10
                            adjustment_log.append("動能翻揚+10")
                        elif rsi_now < 30:
                            final_score += 5
                            adjustment_log.append("低檔鈍化+5")
                    
                    else:
                        # 情境 B: 順勢策略 (突破邏輯)
                        # 檢查隱憂：高檔爆量滯漲
                        vol_now = final_df['Volume'].iloc[-1]
                        vol_ma = final_df['Vol_MA20'].iloc[-1]
                        if vol_now > vol_ma * 2.5 and final_df['Close'].pct_change().iloc[-1] < 0.005:
                            final_score -= 15
                            adjustment_log.append("高檔爆量滯漲-15")

                # C. 限制分數範圍 (-100 ~ 100)
                final_score = max(min(final_score, 100), -100)

                # 7. 產生最終 AI 建議文字
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

                # 8. [組合顯示字串] 合併基礎評分與修正理由
                # 格式範例: "[季線+15][MACD金叉+10] ➜ 修正: 站穩MA5+10"
                display_reason = base_score_log
                if adjustment_log:
                    display_reason += f" ➜ 修正: {','.join(adjustment_log)}"
                
                # 防呆：若完全沒有評分細節
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
                    "詳細理由": display_reason # 完整呈現計算過程
                })
            
            # 更新狀態為完成
            status.update(label="AI 分析完成！", state="complete", expanded=False)

        # 顯示結果
        if portfolio_results:
            res_df = pd.DataFrame(portfolio_results)
            total_val = res_df['市值'].sum()
            res_df['權重%'] = (res_df['市值'] / total_val * 100) if total_val > 0 else 0
            health = (res_df['綜合評分'] * res_df['市值']).sum() / total_val if total_val > 0 else 0
            
            # --- 儀表板區塊 (會在 Fragment 內刷新) ---
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
                st.info(f"💡 最後更新: {update_time}\n\n若開啟即時監控，此區域數值與下方建議將每分鐘自動變動，不影響上方輸入框。")

            # --- 表格區塊 (修正版) ---
            
            # 1. 定義針對「文字建議」的樣式 (AI 建議欄位用)
            def highlight_advice(val):
                color = 'white'
                val_str = str(val)
                # 根據關鍵字給予背景色
                if '加碼' in val_str or '買進' in val_str or '抱緊' in val_str: color = '#ffcdd2' # 紅底
                elif '減碼' in val_str or '賣出' in val_str or '清倉' in val_str: color = '#c8e6c9' # 綠底
                elif '策略持倉' in val_str: color = '#bbdefb' # 藍底
                elif '觀望' in val_str: color = '#cfd8dc' # 灰底
                return f'background-color: {color}; color: black; font-weight: bold'

            # 2. 定義針對「數值評分」的樣式 (綜合評分欄位用)
            def highlight_score(val):
                try:
                    # 確保是數字才能比較
                    v = float(val)
                    color = '#ef5350' if v >= 30 else ('#00e676' if v <= -20 else 'gray')
                    return f'color: {color}; font-weight: bold'
                except:
                    return ''

            # 3. 套用樣式並顯示
            st.dataframe(
                res_df.style
                .map(highlight_advice, subset=['AI 建議'])  # [修正] 文字欄位用 highlight_advice
                .map(highlight_score, subset=['綜合評分'])  # [修正] 數字欄位用 highlight_score
                .format({"權重%": "{:.1f}%", "收盤價": "{:.1f}", "市值": "{:,.0f}", "持有股數": "{:.0f}"}),
                use_container_width=True
            )
    # ==========================================
    # 4. 呼叫片段 (主程式進入點)
    # ==========================================
    # 只有當使用者輸入了資料，且按下按鈕或開啟監控時，才顯示這個片段
    # 這裡我們直接呼叫它，讓它根據 enable_monitor 決定要不要自動刷新
    
    st.markdown("---")
    # 這裡傳入 session_state 的資料，讓 Fragment 內部去處理
    render_live_dashboard(st.session_state['portfolio_data'])
