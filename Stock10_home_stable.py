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
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import extra_streamlit_components as stx
import concurrent.futures
from contextlib import contextmanager
import time
import urllib3

# 忽略 SSL 不安全警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ==========================================
# 0. 全域設定與 CSS
# ==========================================
st.set_page_config(page_title="量化投資決策系統 (Quant Pro v7.0)", layout="wide")

# Email 設定 (請自行修改)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "cypan2000@gmail.com"
SENDER_PASSWORD = "amds ieiu wgqk exir" 
RECEIVER_EMAIL = "cypan2000@gmail.com"

# Cookie Manager
@st.cache_resource
def get_cookie_manager():
    return stx.CookieManager(key="invest_cookie_manager")

cookie_manager = get_cookie_manager()

# CSS 優化
st.markdown("""
    <style>
        .block-container {
            padding-top: 2rem !important;
            padding-bottom: 2rem !important;
        }
        [data-testid="stMetric"] {
            background-color: #1E1E1E;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 10px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        }
        button { min-height: 45px !important; }
        @media (max-width: 768px) {
            .block-container { padding-left: 0.5rem !important; padding-right: 0.5rem !important; }
        }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 1. 資料庫管理模組 (Optimized)
# ==========================================
DB_NAME = "invest_pro.db"

@contextmanager
def get_db_connection():
    """使用 Context Manager 管理資料庫連線"""
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """初始化資料庫並啟用 WAL 模式"""
    with get_db_connection() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute('''CREATE TABLE IF NOT EXISTS users 
                        (username TEXT PRIMARY KEY, password TEXT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS portfolios 
                        (username TEXT, ticker TEXT, shares INTEGER, 
                         FOREIGN KEY(username) REFERENCES users(username))''')
        conn.commit()

def make_hashes(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def check_hashes(password, hashed_text):
    return make_hashes(password) == hashed_text

def add_user(username, password):
    with get_db_connection() as conn:
        try:
            conn.execute('INSERT INTO users(username, password) VALUES (?,?)', 
                         (username, make_hashes(password)))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

def login_user(username, password):
    with get_db_connection() as conn:
        cur = conn.execute('SELECT password FROM users WHERE username = ?', (username,))
        data = cur.fetchone()
        if data:
            return check_hashes(password, data['password'])
    return False

def save_portfolio_to_db(username, df):
    with get_db_connection() as conn:
        conn.execute('DELETE FROM portfolios WHERE username = ?', (username,))
        for _, row in df.iterrows():
            conn.execute('INSERT INTO portfolios (username, ticker, shares) VALUES (?,?,?)',
                         (username, str(row['代號']), int(row['持有股數'])))
        conn.commit()

def load_portfolio_from_db(username):
    with get_db_connection() as conn:
        try:
            df = pd.read_sql_query(f"SELECT ticker as '代號', shares as '持有股數' FROM portfolios WHERE username = '{username}'", conn)
            return df
        except:
            return pd.DataFrame()

# 初始化 DB
init_db()

# ==========================================
# 2. 數據獲取與處理
# ==========================================
TW_STOCK_NAMES_STATIC = {
    '2330': '台積電', '2454': '聯發科', '2303': '聯電', '2317': '鴻海', '2382': '廣達',
    '3008': '大立光', '3711': '日月光投控', '3034': '聯詠', '3661': '世芯-KY'
}

@st.cache_data(ttl=3600, show_spinner=False)
def get_master_stock_data():
    """獲取上市櫃全清單"""
    stock_map = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    
    urls = [
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "上市"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", "上櫃")
    ]
    
    for url, market in urls:
        try:
            res = requests.get(url, headers=headers, timeout=5, verify=False)
            if res.status_code == 200:
                data = res.json()
                for row in data:
                    code = row.get('Code') or row.get('SecuritiesCompanyCode')
                    name = row.get('Name') or row.get('CompanyName')
                    if code and name:
                        stock_map[code] = {"代號": code, "名稱": name, "市場": market}
        except: pass
        
    if not stock_map:
        return pd.DataFrame(columns=["代號", "名稱", "市場"])
    return pd.DataFrame(list(stock_map.values()))

def get_stock_name(ticker):
    code = str(ticker).split('.')[0]
    if code in TW_STOCK_NAMES_STATIC: return TW_STOCK_NAMES_STATIC[code]
    # 若有快取清單則查表
    if 'all_stock_list' in st.session_state and st.session_state['all_stock_list'] is not None:
        df = st.session_state['all_stock_list']
        row = df[df['代號'] == code]
        if not row.empty: return row.iloc[0]['名稱']
    return code

@st.cache_data(ttl=60, show_spinner=False)
def get_stock_data(ticker, start_date, end_date):
    ticker = str(ticker).strip().upper()
    candidates = [ticker]
    if '.' not in ticker:
        candidates = [f"{ticker}.TW", f"{ticker}.TWO", ticker]
    elif ticker.endswith('.TW') or ticker.endswith('.TWO'):
        candidates = [ticker]

    for t in candidates:
        try:
            stock = yf.Ticker(t)
            df = stock.history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1), auto_adjust=False, actions=True)
            if df.empty or len(df) < 5: continue
            
            # 簡單清洗
            df = df.sort_index()
            if 'Dividends' not in df.columns: df['Dividends'] = 0.0
            
            # 智慧還原股價 (若發生分割) - 簡化版
            close = df['Close'].values
            if len(close) > 1:
                prev = close[:-1]
                curr = close[1:]
                ratio = curr / prev
                # 簡單偵測：若跌幅超過 40% 視為分割
                split_indices = np.where(ratio < 0.6)[0]
                for idx in split_indices:
                    factor = curr[idx] / prev[idx]
                    df.iloc[:idx+1, df.columns.get_loc('Close')] *= factor
                    df.iloc[:idx+1, df.columns.get_loc('Open')] *= factor
                    df.iloc[:idx+1, df.columns.get_loc('High')] *= factor
                    df.iloc[:idx+1, df.columns.get_loc('Low')] *= factor

            # 裁切時間
            mask = (df.index >= pd.to_datetime(start_date - timedelta(days=100)).tz_localize(df.index.tz))
            df = df.loc[mask].reset_index()
            df['Date'] = df['Date'].dt.tz_localize(None).dt.normalize()
            
            return df, t
        except: continue
            
    return pd.DataFrame(), ticker

@st.cache_data(ttl=300, show_spinner=False)
def get_market_data(start_date, end_date):
    try:
        df = yf.Ticker("^TWII").history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
        df_vix = yf.Ticker("^VIX").history(start=start_date - timedelta(days=400), end=end_date + timedelta(days=1))
        
        if not df.empty:
            df = df.reset_index()
            df['Date'] = df['Date'].dt.tz_localize(None).dt.normalize()
            if not df_vix.empty:
                df_vix = df_vix.reset_index()
                df_vix['Date'] = df_vix['Date'].dt.tz_localize(None).dt.normalize()
                df = pd.merge(df, df_vix[['Date', 'Close']].rename(columns={'Close': 'VIX'}), on='Date', how='left')
                df['VIX'] = df['VIX'].ffill().fillna(20)
            else:
                df['VIX'] = 20.0
                
            delta = df['Close'].diff()
            gain = (delta.where(delta>0, 0)).rolling(14).mean()
            loss = (-delta.where(delta<0, 0)).rolling(14).mean()
            df['Market_RSI'] = (100 - (100 / (1 + gain/loss))).fillna(50)
            df['Market_MA20'] = df['Close'].rolling(20).mean()
            df['Market_MA60'] = df['Close'].rolling(60).mean()
            
            # 補充 OBV
            df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()
            df['OBV_MA20'] = df['OBV'].rolling(20).mean()
            
            return df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume', 'Market_RSI', 'Market_MA20', 'Market_MA60', 'VIX', 'OBV', 'OBV_MA20']]
    except: pass
    return pd.DataFrame()

# ==========================================
# 3. 核心運算：指標與 Alpha Score v12
# ==========================================
def calculate_indicators(df, market_df=None):
    data = df.copy()
    
    # 均線
    data['MA5'] = data['Close'].rolling(5).mean()
    data['MA10'] = data['Close'].rolling(10).mean()
    data['MA20'] = data['Close'].rolling(20).mean()
    data['MA60'] = data['Close'].rolling(60).mean()
    data['MA120'] = data['Close'].rolling(120).mean()
    data['MA240'] = data['Close'].rolling(240).mean()
    
    # 成交量
    data['Vol_MA20'] = data['Volume'].rolling(20).mean().replace(0, 1)
    
    # RSI
    delta = data['Close'].diff()
    gain = (delta.where(delta>0, 0)).rolling(14).mean()
    loss = (-delta.where(delta<0, 0)).rolling(14).mean()
    data['RSI'] = (100 - (100 / (1 + gain/loss))).fillna(50)
    
    # OBV
    data['OBV'] = (np.sign(data['Close'].diff()) * data['Volume']).fillna(0).cumsum()
    data['OBV_MA20'] = data['OBV'].rolling(20).mean()
    
    # BBands
    data['BB_Mid'] = data['MA20']
    data['BB_Std'] = data['Close'].rolling(20).std()
    data['BB_Lower'] = data['BB_Mid'] - 2*data['BB_Std']
    data['BB_Upper'] = data['BB_Mid'] + 2*data['BB_Std']
    
    # [v12 新增] MACD
    exp12 = data['Close'].ewm(span=12, adjust=False).mean()
    exp26 = data['Close'].ewm(span=26, adjust=False).mean()
    data['MACD'] = exp12 - exp26
    data['Signal_Line'] = data['MACD'].ewm(span=9, adjust=False).mean()
    data['MACD_Hist'] = data['MACD'] - data['Signal_Line']
    
    # [v12 新增] KD (Stochastic)
    low_min = data['Low'].rolling(9).min()
    high_max = data['High'].rolling(9).max()
    data['RSV'] = (data['Close'] - low_min) / (high_max - low_min) * 100
    data['K'] = data['RSV'].ewm(com=2, adjust=False).mean()
    data['D'] = data['K'].ewm(com=2, adjust=False).mean()
    
    # ATR & SuperTrend (簡化版)
    tr = data[['High', 'Low', 'Close']].apply(lambda x: max(x['High']-x['Low'], abs(x['High']-x['Close']), abs(x['Low']-x['Close'])), axis=1)
    data['ATR'] = tr.ewm(span=10, adjust=False).mean()
    data['SuperTrend'] = (data['High']+data['Low'])/2 - 3*data['ATR'] # 僅作示意
    
    # 合併大盤
    if market_df is not None and not market_df.empty:
        data = pd.merge(data, market_df[['Date', 'VIX', 'Market_RSI']], on='Date', how='left')
        data['VIX'] = data['VIX'].ffill().fillna(20)
        data['Market_RSI'] = data['Market_RSI'].ffill().fillna(50)
    else:
        data['VIX'] = 20
        data['Market_RSI'] = 50
        
    return data.dropna(subset=['MA60'])

def calculate_alpha_score(df, margin_df, short_df):
    """Alpha Score v12.0 (Trend + Momentum + MACD/KD)"""
    df = df.copy()
    if 'MACD_Hist' not in df.columns: return df # 防呆

    scores = []
    details = []
    
    # 預計算斜率
    df['MA240_Slope'] = df['MA240'].diff(5).fillna(0)
    
    for i in range(len(df)):
        row = df.iloc[i]
        prev = df.iloc[i-1] if i > 0 else row
        
        score = 0
        reasons = []
        neg_acc = 0 # 負分累計
        
        # 1. 均線與趨勢
        if row['Close'] > row['MA20']:
            score += 20; reasons.append("股價 > 月線 (+20)")
        else:
            deduct = -20; score += deduct; neg_acc += deduct; reasons.append("股價破月線 (-20)")
            
        if row['Close'] > row['MA60']:
            score += 15; reasons.append("股價 > 季線 (+15)")
        else:
            deduct = -15; score += deduct; neg_acc += deduct; reasons.append("股價破季線 (-15)")
            
        if row['MA20'] > row['MA60']:
            score += 10; reasons.append("均線多頭排列 (+10)")
            
        # 2. RSI 動能
        if row['RSI'] >= 60:
            score += 10; reasons.append(f"RSI 強勢 ({int(row['RSI'])}) (+10)")
        elif row['RSI'] < 30:
            deduct = -10; score += deduct; neg_acc += deduct; reasons.append(f"RSI 超賣 ({int(row['RSI'])}) (-10)")
            
        # 3. 量能
        if row['Volume'] > row['Vol_MA20'] and row['Close'] > row['Open']:
            score += 10; reasons.append("出量上漲 (+10)")
            
        # 4. [v12] MACD 趨勢確認
        if row['MACD_Hist'] > 0 and row['MACD_Hist'] > prev['MACD_Hist']:
            score += 10; reasons.append("MACD 多頭擴張 (+10)")
        elif row['MACD_Hist'] < 0 and row['MACD_Hist'] > prev['MACD_Hist']:
            score += 5; reasons.append("MACD 空頭收斂 (+5)")
            
        # 5. [v12] KD 訊號
        if row['K'] > row['D'] and prev['K'] < prev['D'] and row['K'] < 30:
            score += 15; reasons.append("KD 低檔金叉 (+15)")
        elif row['K'] < row['D'] and row['K'] > 80:
            deduct = -10; score += deduct; neg_acc += deduct; reasons.append("KD 高檔死叉 (-10)")

        # 6. 策略訊號 (黃金坑邏輯)
        if 'Action' in row and row['Action'] == 'Buy':
            is_panic = '反彈' in str(row.get('Reason','')) or '超賣' in str(row.get('Reason',''))
            is_bull = row['MA240_Slope'] > 0
            
            if is_panic and is_bull:
                restore = abs(neg_acc)
                score += restore + 40
                reasons.insert(0, "<b>💎 牛市黃金坑 (+40)</b>")
            else:
                score += 20
                reasons.insert(0, "<b>🚀 策略買訊 (+20)</b>")
                
        elif 'Action' in row and row['Action'] == 'Sell':
            score -= 30
            reasons.insert(0, "<b>⚡ 策略賣訊 (-30)</b>")
            
        # 限制範圍
        final_score = max(min(score, 100), -100)
        scores.append(final_score)
        
        # HTML 格式化
        color = "#ff5252" if final_score > 0 else "#00e676"
        html = f"<b>Alpha Score: <span style='color:{color}'>{int(final_score)}</span></b><br>"
        html += "<br>".join([r for r in reasons])
        details.append(html)
        
    df['Alpha_Score'] = scores
    df['Score_Detail'] = details
    
    conds = [(df['Alpha_Score']>=60), (df['Alpha_Score']>=20), (df['Alpha_Score']<=-60), (df['Alpha_Score']<=-20)]
    choices = ["🔥 極強勢", "📈 多頭", "⚡ 極弱勢", "📉 空頭"]
    df['Score_Log'] = np.select(conds, choices, default="☁️ 觀望")
    
    return df

# ==========================================
# 4. 策略邏輯 (Strategy)
# ==========================================
def run_simple_strategy(data, rsi_thresh, fee_rate=0.001425, tax_rate=0.003, use_chip=True, strict_bear=True):
    df = data.copy()
    close = df['Close'].values
    ma20 = df['MA20'].values
    ma60 = df['MA60'].values
    rsi = df['RSI'].values
    vol = df['Volume'].values
    vol_ma = df['Vol_MA20'].values
    
    position = 0
    entry_price = 0
    actions, reasons = [], []
    
    for i in range(len(df)):
        act, rsn = "Hold", ""
        is_buy, is_sell = False, False
        
        # 簡易邏輯
        if position == 0:
            # 策略 A: 動能
            if close[i] > ma60[i] and close[i] > ma20[i] and rsi[i] > 55 and vol[i] > vol_ma[i]:
                is_buy = True; rsn = "動能突破"
            # 策略 B: 回測
            elif close[i] > ma60[i] and close[i] < ma20[i]*1.02 and close[i] > ma20[i]:
                is_buy = True; rsn = "均線回測"
            # 策略 C: 恐慌反彈
            elif rsi[i] < rsi_thresh and close[i] < ma20[i]*0.9:
                is_buy = True; rsn = "超賣反彈"
                
            if is_buy:
                position = 1; entry_price = close[i]; act = "Buy"
                
        elif position == 1:
            # 停損
            if close[i] < entry_price * 0.9:
                is_sell = True; rsn = "停損"
            # 停利/出場
            elif close[i] < ma20[i] and strict_bear:
                is_sell = True; rsn = "破線出場"
            elif rsi[i] > 80:
                is_sell = True; rsn = "過熱獲利"
                
            if is_sell:
                position = 0; act = "Sell"
                
        actions.append(act)
        reasons.append(rsn)
        
    df['Action'] = actions
    df['Reason'] = reasons
    
    # 計算績效 (向量化)
    df['Pos'] = df['Action'].apply(lambda x: 1 if x=='Buy' else (0 if x=='Sell' else np.nan)).ffill().fillna(0)
    # 修正 Pos: 賣出當天仍有部位
    df['Pos'] = df['Pos'].shift(1).fillna(0)
    
    df['Ret'] = df['Close'].pct_change().fillna(0)
    df['Strategy_Ret'] = df['Pos'] * df['Ret']
    
    # 扣成本
    costs = np.where(df['Action']=='Buy', fee_rate, 0) + np.where(df['Action']=='Sell', fee_rate+tax_rate, 0)
    df['Strategy_Ret'] -= costs
    
    df['Cum_Strategy'] = (1 + df['Strategy_Ret']).cumprod()
    df['Cum_Market'] = (1 + df['Ret']).cumprod()
    
    return df

def run_optimization(raw_df, market_df, start_date, fee=0.001425, tax=0.003, use_chip_strategy=True, use_strict_bear_exit=True):
    # 1. 計算指標 (v12)
    df_ind = calculate_indicators(raw_df, market_df)
    target_start = pd.to_datetime(start_date).tz_localize(None)
    df_slice = df_ind[df_ind['Date'] >= target_start].copy()
    
    if df_slice.empty: return None, None
    
    # 2. 執行單一策略 (可擴充為迴圈最佳化)
    final_df = run_simple_strategy(df_slice, 25, fee, tax, use_chip_strategy, use_strict_bear_exit)
    
    ret = final_df['Cum_Strategy'].iloc[-1] - 1
    return {'Return': ret}, final_df

# ==========================================
# 5. 平行掃描模組 (Parallel Scanner)
# ==========================================
def process_single_ticker(ticker, market_df, start_date, fee, tax, use_chip, use_bear):
    """單一標的處理函數 (供執行緒呼叫)"""
    try:
        raw_df, fmt_ticker = get_stock_data(ticker, start_date, datetime.now().date())
        if raw_df.empty or len(raw_df) < 60: return None
        
        best_params, final_df = run_optimization(raw_df, market_df, start_date, fee, tax, use_chip, use_bear)
        if final_df is None or final_df.empty: return None
        
        # 計算 Alpha
        scored_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
        last = scored_df.iloc[-1]
        
        return {
            "代號": fmt_ticker.split('.')[0],
            "名稱": get_stock_name(fmt_ticker),
            "Alpha_Score": int(last['Alpha_Score']),
            "建議": last['Score_Log'],
            "收盤價": last['Close'],
            "漲跌幅": (last['Close'] - scored_df.iloc[-2]['Close']) / scored_df.iloc[-2]['Close'],
            "成交金額": last['Close'] * last['Volume'],
            "回測報酬": best_params['Return']
        }
    except: return None

def run_parallel_scan(tickers, market_df, start_date, fee, tax, use_chip, use_bear):
    results = []
    total = len(tickers)
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # 平行處理 (建議 max_workers=8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_to_ticker = {
            executor.submit(process_single_ticker, t, market_df, start_date, fee, tax, use_chip, use_bear): t 
            for t in tickers
        }
        
        completed = 0
        for future in concurrent.futures.as_completed(future_to_ticker):
            completed += 1
            status_text.text(f"掃描進度: {completed}/{total}")
            progress_bar.progress(completed / total)
            
            res = future.result()
            if res: results.append(res)
            
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(results)

# ==========================================
# 6. 前端介面 (Main UI)
# ==========================================
with st.sidebar:
    st.title("⚔️ 機構戰情室 v7.0")
    
    # 登入邏輯
    cookies = cookie_manager.get_all()
    cookie_user = cookies.get("invest_user")
    
    if 'logged_in' not in st.session_state:
        st.session_state['logged_in'] = False
        st.session_state['username'] = ''
        
    if cookie_user and not st.session_state['logged_in']:
        st.session_state['logged_in'] = True
        st.session_state['username'] = cookie_user
        
    if not st.session_state['logged_in']:
        tab_login, tab_reg = st.tabs(["登入", "註冊"])
        with tab_login:
            user = st.text_input("帳號", key="l_u")
            pwd = st.text_input("密碼", type='password', key="l_p")
            if st.button("登入"):
                if login_user(user, pwd):
                    st.session_state['logged_in'] = True
                    st.session_state['username'] = user
                    expires = datetime.now() + timedelta(days=30)
                    cookie_manager.set("invest_user", user, expires_at=expires)
                    st.rerun()
                else: st.error("錯誤")
        with tab_reg:
            new_u = st.text_input("新帳號")
            new_p = st.text_input("新密碼", type='password')
            if st.button("建立"):
                if add_user(new_u, new_p): st.success("成功")
                else: st.error("已存在")
    else:
        st.success(f"Hi, {st.session_state['username']}")
        if st.button("登出"):
            cookie_manager.delete("invest_user")
            st.session_state['logged_in'] = False
            st.rerun()
            
    st.markdown("---")
    page = st.radio("導航", ["🌍 市場總覽", "📊 單股分析", "🚀 戰略雷達 (多執行緒)", "💼 持股健診"])

# 共用參數
tw_tz = pytz.timezone('Asia/Taipei')
today = datetime.now(tw_tz).date()
start_date = today - timedelta(days=365*2)
market_df = get_market_data(start_date, today)

# --- Page 1: Macro ---
if page == "🌍 市場總覽":
    if not market_df.empty:
        df_scored = calculate_alpha_score(calculate_indicators(market_df), pd.DataFrame(), pd.DataFrame())
        last = df_scored.iloc[-1]
        
        c1, c2, c3 = st.columns(3)
        c1.metric("加權指數", f"{last['Close']:.0f}", f"RSI: {last['Market_RSI']:.1f}")
        c2.metric("VIX 恐慌指數", f"{last['VIX']:.2f}", delta_color="inverse")
        
        score = last['Alpha_Score']
        color = "#ff5252" if score > 0 else "#00e676"
        c3.markdown(f"""
            <div style='background:#222; padding:10px; border-radius:5px; text-align:center'>
                <div style='color:#ccc; font-size:12px'>大盤評分 (Alpha)</div>
                <div style='color:{color}; font-size:24px; font-weight:bold'>{int(score)}</div>
            </div>
        """, unsafe_allow_html=True)
        
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3])
        fig.add_trace(go.Scatter(x=df_scored['Date'], y=df_scored['Close'], name='Index'), row=1, col=1)
        fig.add_trace(go.Bar(x=df_scored['Date'], y=df_scored['Alpha_Score'], name='Score', marker_color=df_scored['Alpha_Score'].apply(lambda x: '#ff5252' if x>0 else '#00e676')), row=2, col=1)
        fig.update_layout(template="plotly_dark", height=600)
        st.plotly_chart(fig, use_container_width=True)

# --- Page 2: Single Stock ---
elif page == "📊 單股分析":
    col1, col2 = st.columns([3, 1])
    with col1:
        ticker_input = st.text_input("輸入代號", value="2330").split(" ")[0]
    with col2:
        st.write("")
        st.write("")
        run_btn = st.button("分析", type="primary")
        
    if run_btn:
        with st.spinner("Analyzing..."):
            raw_df, fmt_ticker = get_stock_data(ticker_input, start_date, today)
            if not raw_df.empty:
                params, final_df = run_optimization(raw_df, market_df, start_date)
                scored_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                
                last = scored_df.iloc[-1]
                st.markdown(f"### {fmt_ticker} {get_stock_name(fmt_ticker)}")
                
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("收盤價", f"{last['Close']:.2f}")
                m2.metric("Alpha Score", f"{int(last['Alpha_Score'])}", last['Score_Log'])
                m3.metric("策略回報", f"{params['Return']:.1%}")
                m4.metric("MACD", f"{last['MACD_Hist']:.2f}")
                
                # Plot
                fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.5, 0.25, 0.25])
                # K線
                fig.add_trace(go.Candlestick(x=scored_df['Date'], open=scored_df['Open'], high=scored_df['High'], low=scored_df['Low'], close=scored_df['Close'], name='OHLC'), row=1, col=1)
                fig.add_trace(go.Scatter(x=scored_df['Date'], y=scored_df['MA20'], line=dict(color='yellow', width=1), name='MA20'), row=1, col=1)
                fig.add_trace(go.Scatter(x=scored_df['Date'], y=scored_df['MA60'], line=dict(color='orange', width=1), name='MA60'), row=1, col=1)
                
                # 買賣點
                buys = scored_df[scored_df['Action']=='Buy']
                sells = scored_df[scored_df['Action']=='Sell']
                fig.add_trace(go.Scatter(x=buys['Date'], y=buys['Low']*0.95, mode='markers', marker=dict(symbol='triangle-up', color='red', size=10), name='Buy'), row=1, col=1)
                fig.add_trace(go.Scatter(x=sells['Date'], y=sells['High']*1.05, mode='markers', marker=dict(symbol='triangle-down', color='green', size=10), name='Sell'), row=1, col=1)

                # Alpha Score
                fig.add_trace(go.Bar(x=scored_df['Date'], y=scored_df['Alpha_Score'], marker_color=scored_df['Alpha_Score'].apply(lambda x: '#ff5252' if x>0 else '#00e676'), name='Alpha'), row=2, col=1)
                
                # MACD
                colors_macd = ['#ff5252' if v > 0 else '#00e676' for v in scored_df['MACD_Hist']]
                fig.add_trace(go.Bar(x=scored_df['Date'], y=scored_df['MACD_Hist'], marker_color=colors_macd, name='MACD'), row=3, col=1)
                
                fig.update_layout(template="plotly_dark", height=800, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
                
                with st.expander("詳細評分邏輯"):
                    st.markdown(last['Score_Detail'], unsafe_allow_html=True)
            else:
                st.error("查無資料")

# --- Page 3: Parallel Scanner ---
elif page == "🚀 戰略雷達 (多執行緒)":
    st.markdown("### 🚀 AI 全市場戰略掃描 (Parallel)")
    default_list = "2330\n2317\n2454\n2308\n2382\n3008\n3034\n2603\n2609\n2615\n1513\n1519"
    tickers_text = st.text_area("輸入代號 (換行分隔)", value=default_list, height=150)
    
    if st.button("🔥 啟動掃描"):
        tickers = [t.strip() for t in tickers_text.split('\n') if t.strip()]
        if not tickers:
            st.warning("清單為空")
        else:
            with st.spinner("AI 運算中..."):
                df_res = run_parallel_scan(tickers, market_df, start_date, 0.001425, 0.003, True, True)
                
            if not df_res.empty:
                # 散佈圖
                st.subheader("🎯 Alpha 動能地圖")
                fig_scatter = px.scatter(
                    df_res, x="Alpha_Score", y="漲跌幅", 
                    size="成交金額", color="Alpha_Score",
                    text="名稱", hover_data=["代號", "收盤價", "建議"],
                    color_continuous_scale=['#00e676', 'gray', '#ff5252'],
                    title="X軸: AI預測分數 vs Y軸: 今日表現"
                )
                fig_scatter.add_vline(x=0, line_dash="dash", line_color="gray")
                fig_scatter.add_hline(y=0, line_dash="dash", line_color="gray")
                fig_scatter.update_traces(textposition='top center')
                fig_scatter.update_layout(template="plotly_dark", height=600)
                st.plotly_chart(fig_scatter, use_container_width=True)
                
                # 表格
                st.subheader("🏆 排行榜")
                st.dataframe(
                    df_res.sort_values('Alpha_Score', ascending=False).style.format({
                        "收盤價": "{:.2f}", "漲跌幅": "{:.2%}", "回測報酬": "{:.1%}"
                    }).background_gradient(subset=['Alpha_Score'], cmap='RdYlGn'),
                    use_container_width=True
                )
            else:
                st.warning("無有效結果")

# --- Page 4: Portfolio ---
elif page == "💼 持股健診":
    st.markdown("### 💼 智能持股健診")
    
    if st.session_state['logged_in']:
        username = st.session_state['username']
        db_pf = load_portfolio_from_db(username)
        if db_pf.empty:
            df_pf = pd.DataFrame([{"代號": "2330", "持有股數": 1000}])
        else:
            df_pf = db_pf
    else:
        st.info("訪客模式 (資料不保存)")
        df_pf = pd.DataFrame([{"代號": "2330", "持有股數": 1000}])

    edited_df = st.data_editor(df_pf, num_rows="dynamic", use_container_width=True, key="pf_edit")
    
    if st.button("💾 分析並儲存"):
        # 存檔
        if st.session_state['logged_in']:
            save_portfolio_to_db(username, edited_df)
            st.success("已儲存至雲端")
            
        # 分析
        res_list = []
        tickers = [str(t) for t in edited_df['代號'].tolist() if t]
        
        if tickers:
            with st.spinner("健診中..."):
                scan_res = run_parallel_scan(tickers, market_df, start_date, 0.001425, 0.003, True, True)
            
            if not scan_res.empty:
                # 合併股數
                edited_df['代號'] = edited_df['代號'].astype(str)
                merged = pd.merge(edited_df, scan_res, on='代號', how='left')
                merged['市值'] = merged['收盤價'] * merged['持有股數']
                
                total_val = merged['市值'].sum()
                health = (merged['Alpha_Score'] * merged['市值']).sum() / total_val if total_val>0 else 0
                
                c1, c2 = st.columns([1, 2])
                c1.metric("總市值", f"{int(total_val):,}")
                
                fig_g = go.Figure(go.Indicator(
                    mode = "gauge+number", value = health, 
                    title = {'text': "健康度"},
                    gauge = {'axis': {'range': [-100, 100]}, 'bar': {'color': "#ff5252" if health>0 else "#00e676"}}
                ))
                fig_g.update_layout(height=250, margin=dict(t=30, b=10))
                c2.plotly_chart(fig_g, use_container_width=True)
                
                st.dataframe(merged, use_container_width=True)
