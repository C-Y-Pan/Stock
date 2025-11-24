import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import pytz

# --- 頁面設定 ---
st.set_page_config(page_title="量化投資決策系統 (Quant Pro v6.0)", layout="wide")

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

@st.cache_data(ttl=1, show_spinner=False)
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
@st.cache_data(ttl=1, show_spinner=False)
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

@st.cache_data(ttl=1, show_spinner=False)
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
            
            return df[['Date', 'Market_RSI', 'Market_MA20', 'Market_MA60', 'Close', 'OBV', 'OBV_MA20', 'VIX']]
    except: pass
    return pd.DataFrame()

@st.cache_data(ttl=43200, show_spinner=False)
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
# 3. 策略邏輯 & 輔助 (Updated)
# ==========================================
def run_simple_strategy(data, rsi_buy_thresh):
    df = data.copy()
    positions = []; reasons = []; actions = []; target_prices = []
    return_labels = [] # 新增：儲存報酬率標籤
    
    position = 0; days_held = 0; entry_price = 0.0; trade_type = 0
    
    close = df['Close'].values; trend = df['Trend'].values; rsi = df['RSI'].values
    bb_lower = df['BB_Lower'].values; ma20 = df['MA20'].values; ma60 = df['MA60'].values
    volume = df['Volume'].values; vol_ma20 = df['Vol_MA20'].values; obv = df['OBV'].values; obv_ma20 = df['OBV_MA20'].values
    market_panic = df['Is_Market_Panic'].values

    for i in range(len(df)):
        signal = position; reason_str = ""; action_code = "Hold" if position == 1 else "Wait"
        this_target = entry_price * 1.15 if position == 1 else np.nan
        ret_label = "" # 預設空字串

        if position == 0:
            is_buy = False
            # 理由專業化：順勢突破、均線回測、籌碼佈局、超賣反彈
            if (trend[i]==1 and (i>0 and trend[i-1]==-1) and volume[i]>vol_ma20[i] and close[i]>ma60[i] and rsi[i]>55 and obv[i]>obv_ma20[i]):
                is_buy=True; trade_type=1; reason_str="動能突破"
            elif trend[i]==1 and close[i]>ma60[i] and (df['Low'].iloc[i]<=ma20[i]*1.02) and close[i]>ma20[i] and volume[i]<vol_ma20[i] and rsi[i]>45:
                is_buy=True; trade_type=1; reason_str="均線回測"
            elif close[i]>ma60[i] and obv[i]>obv_ma20[i] and volume[i]<vol_ma20[i] and (close[i]<ma20[i] or rsi[i]<55) and close[i]>bb_lower[i]:
                is_buy=True; trade_type=3; reason_str="籌碼佈局"
            elif rsi[i]<rsi_buy_thresh and close[i]<bb_lower[i] and market_panic[i] and volume[i]>vol_ma20[i]*0.5:
                is_buy=True; trade_type=2; reason_str="超賣反彈"
            
            if is_buy:
                signal=1; days_held=0; entry_price=close[i]; action_code="Buy"
        
        elif position == 1:
            days_held+=1
            drawdown=(close[i]-entry_price)/entry_price
            
            if trade_type==2 and trend[i]==1: trade_type=1; reason_str="反彈轉波段"
            if trade_type==3 and volume[i]>vol_ma20[i]*1.2: trade_type=1; reason_str="佈局完成發動"
            
            # 停損與賣出邏輯
            is_sell = False
            if drawdown < -0.10:
                is_sell=True; reason_str="觸發停損"; action_code="Sell"
            elif days_held <= 3:
                action_code="Hold"; reason_str="鎖倉觀察"
            else:
                if trade_type==1 and trend[i]==-1: is_sell=True; reason_str="趨勢轉弱"
                elif trade_type==2 and days_held>10 and drawdown<0: is_sell=True; reason_str="逆勢操作超時"
                elif trade_type==3 and close[i]<bb_lower[i]: is_sell=True; reason_str="支撐確認失敗"
                
            if is_sell:
                signal=0; action_code="Sell"
                # 計算報酬率並格式化字串
                pnl = (close[i] - entry_price) / entry_price * 100
                sign = "+" if pnl > 0 else ""
                ret_label = f"{sign}{pnl:.1f}%"

        position=signal
        positions.append(signal); reasons.append(reason_str); actions.append(action_code)
        target_prices.append(this_target); return_labels.append(ret_label)
        
    df['Position']=positions; df['Reason']=reasons; df['Action']=actions
    df['Target_Price']=target_prices; df['Return_Label']=return_labels # 加入欄位
    
    df['Real_Position']=df['Position'].shift(1).fillna(0)
    df['Market_Return']=df['Close'].pct_change().fillna(0)
    df['Strategy_Return']=df['Real_Position']*df['Market_Return']
    df['Cum_Strategy']=(1+df['Strategy_Return']).cumprod()
    df['Cum_Market']=(1+df['Market_Return']).cumprod()
    return df

def run_optimization(raw_df, market_df, user_start_date):
    best_ret = -999; best_params = None; best_df = None; target_start = pd.to_datetime(user_start_date)
    for m in [3.0, 3.5]:
        for r in [25, 30]:
            df_ind = calculate_indicators(raw_df, 10, m, market_df)
            df_slice = df_ind[df_ind['Date'] >= target_start].copy()
            if df_slice.empty: continue
            df_res = run_simple_strategy(df_slice, r)
            ret = df_res['Cum_Strategy'].iloc[-1]-1
            if ret > best_ret:
                best_ret=ret; best_params={'Mult':m, 'RSI_Buy':r, 'Return':ret}; best_df=df_res
    return best_params, best_df

def calculate_target_hit_rate(df):
    if df is None or df.empty: return "0.0%", 0, 0
    buy_indices = df[df['Action']=='Buy'].index; total = len(buy_indices); hits = 0
    for idx in buy_indices:
        entry = df.loc[idx, 'Close']; target = entry*1.15
        future = df.loc[idx+1:]
        sell_rows = future[future['Action']=='Sell']
        period = df.loc[idx:sell_rows.index[0]] if not sell_rows.empty else df.loc[idx:]
        if period['High'].max() >= target: hits+=1
    return f"{(hits/total)*100:.1f}%", hits, total

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
# 5. [核心演算法] 買賣評等 (Alpha Score)
# ==========================================
def calculate_alpha_score(df, margin_df, short_df):
    df = df.copy(); df['Alpha_Score'] = 0.0
    
    # 1. 趨勢
    df.loc[df['Close'] > df['Market_MA60'], 'Alpha_Score'] += 15
    df.loc[df['Close'] < df['Market_MA60'], 'Alpha_Score'] -= 15
    df.loc[df['Close'] > df['Market_MA20'], 'Alpha_Score'] += 10
    df.loc[df['Close'] < df['Market_MA20'], 'Alpha_Score'] -= 15
    df.loc[df['Market_MA20'] > df['Market_MA60'], 'Alpha_Score'] += 5

    # 2. 動能 & 恐慌
    df.loc[df['Market_RSI'] < 30, 'Alpha_Score'] += 20
    df.loc[df['Market_RSI'] < 20, 'Alpha_Score'] += 25
    df.loc[df['Market_RSI'] > 80, 'Alpha_Score'] -= 10
    df.loc[df['Market_RSI'] > 90, 'Alpha_Score'] -= 20
    df.loc[df['VIX'] > 20, 'Alpha_Score'] += 5
    df.loc[df['VIX'] > 30, 'Alpha_Score'] += 15
    df.loc[df['VIX'] < 13, 'Alpha_Score'] -= 5

    # 3. 籌碼
    if not margin_df.empty and not short_df.empty:
        temp = pd.merge(df[['Date', 'Close']], margin_df[['date', 'TodayBalance']], left_on='Date', right_on='date', how='left')
        temp = pd.merge(temp, short_df[['date', 'TodayBalance']], left_on='Date', right_on='date', how='left', suffixes=('_M', '_S'))
        temp['M_Chg'] = temp['TodayBalance_M'].pct_change(5); temp['S_Chg'] = temp['TodayBalance_S'].pct_change(5); temp['P_Chg'] = temp['Close'].pct_change(5)
        
        mask_stable = (temp['P_Chg'] > 0.02) & (temp['M_Chg'] < -0.01)
        df.loc[mask_stable.values, 'Alpha_Score'] += 15
        mask_trap = (temp['P_Chg'] < -0.02) & (temp['M_Chg'] > 0.01)
        normal_rsi = (df['Market_RSI'] > 25)
        df.loc[mask_trap.values & normal_rsi.values, 'Alpha_Score'] -= 20
        mask_washout = (temp['P_Chg'] < -0.03) & (temp['M_Chg'] < -0.02)
        df.loc[mask_washout.values, 'Alpha_Score'] += 30
        mask_squeeze = (temp['P_Chg'] > 0.02) & (temp['S_Chg'] > 0.02)
        df.loc[mask_squeeze.values, 'Alpha_Score'] += 10

    df['Alpha_Score'] = df['Alpha_Score'].clip(-100, 100)
    df['Recommended_Position'] = ((df['Alpha_Score'] + 100) / 2).clip(0, 100)
    return df

# ==========================================
# 6. 主儀表板繪製 (Updated)
# ==========================================
def draw_market_dashboard(market_df, start_date, end_date):
    st.markdown("### 🌍 總體市場戰情 (Macro)")
    target_start = pd.to_datetime(start_date); plot_df = market_df[market_df['Date'] >= target_start].copy()
    if plot_df.empty: st.error("無大盤數據"); return
    
    # 獲取 FinMind 數據
    margin_df_raw = get_margin_data(start_date.strftime('%Y-%m-%d'))
    margin_df = pd.DataFrame(); short_df = pd.DataFrame()
    if not margin_df_raw.empty:
        sliced = margin_df_raw[(margin_df_raw['date'] >= target_start) & (margin_df_raw['date'] <= pd.to_datetime(end_date))]
        margin_df = sliced[sliced['name'] == 'MarginPurchaseMoney']; short_df = sliced[sliced['name'] == 'ShortSale']
    
    plot_df = calculate_alpha_score(plot_df, margin_df, short_df)
    last = plot_df.iloc[-1]; score = last['Alpha_Score']; vix = last['VIX']
    
    if score >= 60: txt="強力買進"; c_score="green"
    elif score >= 20: txt="偏多操作"; c_score="lightgreen"
    elif score <= -60: txt="強力賣出"; c_score="red"
    elif score <= -20: txt="偏空調節"; c_score="orange"
    else: txt="中性觀望"; c_score="gray"
    
    vix_st = "極度恐慌" if vix>30 else ("恐慌警戒" if vix>20 else ("樂觀貪婪" if vix<15 else "正常波動"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("加權指數", f"{last['Close']:.0f}", f"MA20: {last['Market_MA20']:.0f}", delta_color="off")
    c2.metric("市場情緒 (RSI)", f"{last['Market_RSI']:.1f}", "區間: 0~100", delta_color="off")
    c3.metric("恐慌指數 (VIX)", f"{vix:.2f}", vix_st, delta_color="off")
    c4.metric("買賣評等 (Alpha)", f"{score:.0f}", txt, delta_color="off")

    if not margin_df.empty and not short_df.empty:
        try:
            m_c = margin_df['TodayBalance'].iloc[-1]; m_p = margin_df['TodayBalance'].iloc[-5]
            s_c = short_df['TodayBalance'].iloc[-1]; s_p = short_df['TodayBalance'].iloc[-5]
            p_c = plot_df['Close'].iloc[-1]; p_p = plot_df['Close'].iloc[-5]
            m_chg = (m_c-m_p)/m_p; s_chg = (s_c-s_p)/s_p; p_chg = (p_c-p_p)/p_p
            
            msg = ""; typ = "info"
            if m_chg > 0.02 and p_chg < -0.02: msg = "⚠️ **籌碼警示** - 融資套牢，提防多殺多。"; typ="error"
            elif s_chg > 0.05 and p_chg > 0.02: msg = "🚀 **軋空訊號** - 空單被鎖，助漲多頭。"; typ="success"
            elif m_chg < -0.02 and p_chg > 0.02: msg = "💪 **籌碼安定** - 融資換手，籌碼流入安定手。"; typ="success"
            elif m_chg < -0.03 and p_chg < -0.03: msg = "🐻 **清洗浮額** - 融資斷頭，留意止跌訊號。"; typ="warning"
            else: msg = "⚖️ **籌碼觀望** - 資券變動不大。"; typ="info"
            
            if typ=="error": st.error(msg)
            elif typ=="success": st.success(msg)
            elif typ=="warning": st.warning(msg)
            else: st.info(msg)
        except: st.metric("AI 籌碼解讀", "N/A", "資料不足", delta_color="off")
    else: st.metric("AI 籌碼解讀", "N/A", "無法獲取資券資料", delta_color="off")

    fig = make_subplots(rows=8, cols=1, shared_xaxes=True, vertical_spacing=0.02, 
                        row_heights=[0.3, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
                        subplot_titles=("加權指數", "買賣評等 (Alpha Score)", "籌碼能量 (OBV)", "動能指標 (RSI)", "恐慌指數 (VIX)", "建議持股水位 (%)", "融資餘額", "融券餘額"))
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Close'], name='收盤價', line=dict(color='white')), row=1, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Market_MA20'], name='月線', line=dict(color='yellow')), row=1, col=1)
    
    colors_score = ['#00e676' if v > 0 else '#ef5350' for v in plot_df['Alpha_Score']]
    fig.add_trace(go.Bar(x=plot_df['Date'], y=plot_df['Alpha_Score'], name='評等', marker_color=colors_score), row=2, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['OBV'], name='OBV', line=dict(color='orange')), row=3, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Market_RSI'], name='RSI', line=dict(color='cyan')), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
    fig.add_shape(type="line", x0=plot_df['Date'].min(), x1=plot_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
    
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['VIX'], name='VIX', line=dict(color='#ab47bc')), row=5, col=1)
    fig.add_trace(go.Scatter(x=plot_df['Date'], y=plot_df['Recommended_Position'], name='持股%', line=dict(color='#00e676'), fill='tozeroy'), row=6, col=1)
    
    if not margin_df.empty: fig.add_trace(go.Scatter(x=margin_df['date'], y=margin_df['TodayBalance'], name='融資', line=dict(color='#ef5350'), fill='tozeroy'), row=7, col=1)
    if not short_df.empty: fig.add_trace(go.Scatter(x=short_df['date'], y=short_df['TodayBalance'], name='融券', line=dict(color='#26a69a'), fill='tozeroy'), row=8, col=1)

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
    st.title("⚔️ 機構法人戰情室")
    st.caption("Pro v6.0: AI-Alpha Edition")
    
    page = st.radio("導航", ["🌍 市場總覽 (Macro)", "📊 單股深度分析", "🚀 科技股掃描", "📋 全台股清單"])
    st.markdown("---")
    st.sidebar.info("🔥 v6.0 更新：Alpha Score 評等系統、融資券監控、蒙地卡羅風險模擬")
    st.markdown("---")
    today = datetime.today()
    # 設定台北時區
    tw_tz = pytz.timezone('Asia/Taipei')
    today = datetime.now(tw_tz).date() # 強制使用台北時間的今天

    # 修改原本的日期輸入
    start_date = st.date_input("回測開始", value=today - timedelta(days=365*2+1))
    end_date = st.date_input("回測結束", value=today) # 這裡會正確顯示台灣的今天

market_df = get_market_data(start_date, end_date)

# --- 頁面 1 ---
if page == "🌍 市場總覽 (Macro)":
    draw_market_dashboard(market_df, start_date, end_date)

# --- 頁面 2 ---
elif page == "📊 單股深度分析":
    with st.form(key='search_form'):
        col_in1, col_in2 = st.columns([3, 1])
        with col_in1:
            st.text_input("輸入股票代號", key="last_ticker")
        with col_in2:
            st.write("") 
            st.write("") 
            run_btn = st.form_submit_button("⚡ 執行分析", type="primary")

    ticker_input = st.session_state['last_ticker']

    if run_btn or ticker_input: 
        if run_btn: 
            with st.spinner(f'正在演算 {ticker_input}...'):
                raw_df, fmt_ticker = get_stock_data(ticker_input, start_date, end_date)
                name = get_stock_name(fmt_ticker)
                
                if raw_df.empty:
                    st.error("❌ 無法獲取資料。")
                else:
                    best_params, final_df = run_optimization(raw_df, market_df, start_date)
                    if final_df is None or final_df.empty:
                        st.warning("⚠️ 選定區間內無資料。")
                    else:
                        beta, vol, personality = calculate_stock_personality(final_df, market_df)
                        action, color, reason = analyze_signal(final_df)
                        hit_rate, hits, total = calculate_target_hit_rate(final_df)
                        risk_metrics = calculate_risk_metrics(final_df)
                        
                        st.session_state['analysis_history'][fmt_ticker] = {
                            'df': final_df, 'params': best_params, 'action': action,
                            'reason': reason, 'beta': beta, 'vol': vol, 'personality': personality,
                            'name': name, 'hit_rate': hit_rate, 'hits': hits, 'total_trades': total,
                            'risk': risk_metrics
                        }

    current_ticker = st.session_state['last_ticker']
    possible_keys = [k for k in st.session_state['analysis_history'].keys() if current_ticker in k]
    
    if possible_keys:
        data = st.session_state['analysis_history'][possible_keys[0]]
        final_df = data['df']
        risk = data.get('risk', {})
        
        strat_mdd = calculate_mdd(final_df['Cum_Strategy'])
        market_mdd = calculate_mdd(final_df['Cum_Market'])
        strat_ret = data['params']['Return'] * 100
        
        st.markdown(f"## {possible_keys[0]} {data['name']} 深度報告")
        st.caption(f"策略邏輯: {data['reason']} | 波動率: {data['vol']}")
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("策略建議", data['action'], data['reason'])
        m2.metric("策略總報酬", f"{strat_ret:.1f}%", f"MDD: {strat_mdd:.1f}%")
        m3.metric("夏普值 (Sharpe)", f"{risk.get('Sharpe', 0):.2f}", f"Profit Factor: {risk.get('Profit_Factor', 0):.2f}")
        m4.metric("目標觸及率", data['hit_rate'], f"{data['hits']}/{data['total_trades']} 次")
        
        tab1, tab2, tab3 = st.tabs(["📈 操盤決策圖", "💰 權益曲線", "🎲 蒙地卡羅模擬"])
        
        with tab1:
            # 建立子圖，並將主圖(Row1)的 Y 軸設為右側
            fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                                row_heights=[0.5, 0.15, 0.15, 0.20], 
                                subplot_titles=("", "成交量", "法人籌碼 (OBV)", "相對強弱指標 (RSI)"))
            
            # --- Row 1: 價格主圖 ---
            fig.add_trace(go.Candlestick(x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                                         low=final_df['Low'], close=final_df['Close'], name='K線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['SuperTrend'], mode='lines', 
                                     line=dict(color='yellow', width=1.5), name='停損基準線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', 
                                     line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線 (60MA)'), row=1, col=1)
            
            # 定義標記位置偏移，避免遮擋K線
            final_df['Buy_Y'] = final_df['Low'] * 0.90
            final_df['Sell_Y'] = final_df['High'] * 1.1

            # --- 買進訊號 (使用不同顏色區分策略) ---
            # 1. 動能突破/回測 (金黃)
            buy_trend = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('突破|回測|動能'))]
            fig.add_trace(go.Scatter(x=buy_trend['Date'], y=buy_trend['Buy_Y'], mode='markers', 
                                     marker=dict(symbol='triangle-up', size=12, color='#FFD700', line=dict(width=1, color='black')), 
                                     name='買進 (趨勢)'), row=1, col=1)
            
            # 2. 超賣反彈 (青色)
            buy_panic = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('反彈|超賣'))]
            fig.add_trace(go.Scatter(x=buy_panic['Date'], y=buy_panic['Buy_Y'], mode='markers', 
                                     marker=dict(symbol='triangle-up', size=12, color='#00FFFF', line=dict(width=1, color='black')), 
                                     name='買進 (反彈)'), row=1, col=1)
            
            # 3. 籌碼佈局 (淡紫)
            buy_chip = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('籌碼|佈局'))]
            fig.add_trace(go.Scatter(x=buy_chip['Date'], y=buy_chip['Buy_Y'], mode='markers', 
                                     marker=dict(symbol='triangle-up', size=12, color='#DDA0DD', line=dict(width=1, color='black')), 
                                     name='買進 (籌碼)'), row=1, col=1)

            # --- 賣出訊號 (顯示報酬率) ---
            # 1. 停損 (白底紅框)
            sell_stop = final_df[(final_df['Action'] == 'Sell') & (final_df['Reason'].str.contains('停損'))]
            fig.add_trace(go.Scatter(x=sell_stop['Date'], y=sell_stop['Sell_Y'], 
                                     mode='markers+text', # 顯示標記與文字
                                     text=sell_stop['Return_Label'], # 顯示報酬率
                                     textposition="top center",
                                     textfont=dict(color='#ff4d4d', size=11, weight='bold'),
                                     marker=dict(symbol='triangle-down', size=12, color='#FFFFFF', line=dict(width=1, color='red')), 
                                     name='賣出 (停損)'), row=1, col=1)
            
            # 2. 獲利/訊號出場 (洋紅)
            sell_profit = final_df[(final_df['Action'] == 'Sell') & (~final_df['Reason'].str.contains('停損'))]
            fig.add_trace(go.Scatter(x=sell_profit['Date'], y=sell_profit['Sell_Y'], 
                                     mode='markers+text', # 顯示標記與文字
                                     text=sell_profit['Return_Label'], # 顯示報酬率
                                     textposition="top center",
                                     textfont=dict(color='#00e676', size=11, weight='bold'),
                                     marker=dict(symbol='triangle-down', size=12, color='#FF00FF', line=dict(width=1, color='black')), 
                                     name='賣出 (獲利/調節)'), row=1, col=1)

            # --- Row 2: 成交量 ---
            colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
            fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'], marker_color=colors_vol, name='成交量'), row=2, col=1)

            # --- Row 3: OBV ---
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=3, col=1)
            
            # --- Row 4: RSI ---
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=4, col=1)
            fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
            fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
            
            # --- Layout 設定 (座標軸移至右側) ---
            # yaxis=dict(side='right') 將主圖座標軸移至右側，符合專業看盤軟體習慣
            fig.update_layout(
                height=800, 
                template="plotly_dark", 
                xaxis_rangeslider_visible=False,
                yaxis=dict(side='right', title="價格", showgrid=True), # 主圖右軸
                yaxis2=dict(side='right', showgrid=False), # 成交量右軸
                yaxis3=dict(side='right', showgrid=True),  # OBV右軸
                yaxis4=dict(side='right', showgrid=True, range=[0, 100]), # RSI右軸
                margin=dict(l=20, r=60, t=30, b=20), # 右側留白給座標數字
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig, use_container_width=True)

        with tab2:
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Market'], name='大盤', line=dict(color='gray', dash='dot')))
            fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Strategy'], name='策略', line=dict(color='#ef5350', width=2), fill='tozeroy'))
            fig_c.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(fig_c, use_container_width=True)
            
        with tab3:
            st.markdown("### 🎲 蒙地卡羅模擬：機率與風險壓力測試")
            
            # 1. 參數設定與執行模擬
            last_price = final_df['Close'].iloc[-1]
            # 獲取策略年化報酬率作為漂移項 (Drift)，若過低則設定為無風險利率+溢酬 (約8%)
            strat_ret_year = data['params']['Return'] * (252 / len(final_df))
            mu_input = max(strat_ret_year, 0.08) 
            
            # 執行模擬 (增加模擬次數至 200 以獲得更穩定的分布)
            sim_df, var95 = run_monte_carlo_sim(last_price, data['vol'], days=120, sims=200)
            
            # 2. 統計數據計算
            final_prices = sim_df.iloc[-1]
            median_price = np.percentile(final_prices, 50)
            optimistic_price = np.percentile(final_prices, 95) # 樂觀情境
            pessimistic_price = np.percentile(final_prices, 5) # 悲觀情境 (VaR)
            
            # 上漲機率
            prob_up = (final_prices > last_price).mean() * 100
            
            # 風險與報酬空間
            upside_space = (optimistic_price - last_price) / last_price
            downside_risk = (last_price - pessimistic_price) / last_price
            rr_ratio = upside_space / downside_risk if downside_risk != 0 else 0
            
            # 3. 視覺化：模擬路徑圖
            col_chart, col_stat = st.columns([3, 1])
            
            with col_chart:
                fig_mc = go.Figure()
                # 繪製前 50 條路徑避免圖表過亂
                for col in sim_df.columns[:50]:
                    fig_mc.add_trace(go.Scatter(y=sim_df[col], mode='lines', line=dict(width=1, color='rgba(0, 255, 255, 0.1)'), showlegend=False))
                
                fig_mc.add_hline(y=last_price, line_dash="dash", line_color="white", annotation_text="現價", annotation_position="bottom right")
                fig_mc.add_hline(y=optimistic_price, line_dash="dot", line_color="green", annotation_text=f"樂觀 (P95): {optimistic_price:.1f}", annotation_position="top right")
                fig_mc.add_hline(y=pessimistic_price, line_dash="dot", line_color="red", annotation_text=f"悲觀 (P5): {pessimistic_price:.1f}", annotation_position="bottom right")
                
                fig_mc.update_layout(template="plotly_dark", height=450, title="未來 120 交易日價格路徑模擬 (200次)", margin=dict(l=10, r=10, t=40, b=10))
                st.plotly_chart(fig_mc, use_container_width=True)

            with col_stat:
                st.markdown("#### 📊 機率分佈")
                st.metric("上漲機率 (Win Rate)", f"{prob_up:.1f}%", f"中位數: {median_price:.1f}")
                st.metric("潛在獲利空間 (Upside)", f"+{upside_space*100:.1f}%", f"目標: {optimistic_price:.1f}")
                st.metric("潛在下跌風險 (VaR)", f"-{downside_risk*100:.1f}%", f"下限: {pessimistic_price:.1f}")
                st.metric("盈虧比 (R/R Ratio)", f"{rr_ratio:.2f}", "建議 > 1.5")

            # 4. AI 前瞻決策建議
            st.markdown("---")
            st.markdown("#### 🧠 AI 策略長前瞻建議")
            
            advice_container = st.container()
            
            # 邏輯推演
            logic_summary = []
            signal_type = "neutral" # neutral, bullish, bearish, caution
            
            if prob_up > 65:
                logic_summary.append(f"✅ **多頭優勢顯著**：模擬結果顯示 {prob_up:.0f}% 的路徑最終獲利，趨勢動能強勁。")
                signal_type = "bullish"
            elif prob_up < 40:
                logic_summary.append(f"⚠️ **空頭壓力沉重**：僅 {prob_up:.0f}% 的路徑能獲利，建議觀望或保守操作。")
                signal_type = "bearish"
            else:
                logic_summary.append(f"⚖️ **多空膠著**：上漲機率約 {prob_up:.0f}%，市場方向未明，需耐心等待突破。")
            
            if rr_ratio > 2.0:
                logic_summary.append(f"💎 **高期望值交易**：潛在獲利是風險的 {rr_ratio:.1f} 倍，值得承擔風險。")
            elif rr_ratio < 1.0:
                logic_summary.append(f"❌ **低性價比**：潛在下跌風險大於獲利空間，數學期望值對您不利。")
                signal_type = "caution"
            
            if downside_risk > 0.25:
                logic_summary.append(f"🛡️ **高波動警示**：極端情況下可能回撤 {downside_risk*100:.0f}%，**務必縮小部位 (Position Sizing)** 以控制總資產曝險。")
            
            # 顯示建議
            if signal_type == "bullish" and rr_ratio > 1.5:
                advice_container.success("👉 **總結：積極操作區。** " + " ".join(logic_summary))
            elif signal_type == "bearish" or rr_ratio < 1.0:
                advice_container.error("👉 **總結：風險規避區。** " + " ".join(logic_summary))
            elif downside_risk > 0.3:
                advice_container.warning("👉 **總結：投機性操作區 (高風險)。** " + " ".join(logic_summary))
            else:
                advice_container.info("👉 **總結：中性觀察區。** " + " ".join(logic_summary))
            
            st.caption("註：模擬基於幾何布朗運動 (GBM)，假設未來波動率與過去一致。數據僅供風險評估，非絕對價格預測。")

# --- 頁面 3: 掃描 ---
elif page == "🚀 科技股掃描":
    st.markdown(f"### 🚀 全台股科技雷達 (v6.0 AI 訊號)")
    default_list = ALL_TECH_TICKERS
    user_list = st.text_area("股票清單 (每行一支)", value=default_list, height=150)
    scan_btn = st.button("🔥 啟動戰略掃描", type="primary")
    
    if scan_btn:
        st.session_state['is_scanning'] = True
        tickers = [t.strip().replace(',','') for t in user_list.split('\n') if t.strip()]
        tickers = list(set(tickers))
        results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        for idx, ticker in enumerate(tickers):
            status_text.text(f"AI 運算中 ({idx+1}/{len(tickers)}): {ticker} ...")
            raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
            if not raw_df.empty:
                best_params, final_df = run_optimization(raw_df, market_df, start_date)
                if final_df is not None and not final_df.empty:
                    action, color, reason = analyze_signal(final_df)
                    name = get_stock_name(fmt_ticker)
                    hit_rate, hits, total = calculate_target_hit_rate(final_df)
                    
                    if action != "👀 觀望":
                        results.append({
                            "代號": fmt_ticker, "名稱": name, "建議": action,
                            "收盤價": final_df['Close'].iloc[-1],
                            "理由": reason, "回測報酬": best_params['Return'],
                            "達標率": hit_rate
                        })
            progress_bar.progress((idx + 1) / len(tickers))
            
        status_text.text("掃描完成！")
        progress_bar.empty()
        
        if results:
            res_df = pd.DataFrame(results)
            priority_map = {"🚀 買進": 4, "➕ 加碼": 3, "✊ 續抱": 2, "➖ 減碼": 1, "⚡ 賣出": 0}
            res_df['P_Score'] = res_df['建議'].map(priority_map)
            res_df = res_df.sort_values(by=['P_Score', '回測報酬'], ascending=[False, False]).drop(columns=['P_Score'])
            st.session_state['scan_results_df'] = res_df
        else:
            st.session_state['scan_results_df'] = pd.DataFrame()
            
    if st.session_state['scan_results_df'] is not None and not st.session_state['scan_results_df'].empty:
        st.dataframe(st.session_state['scan_results_df'].style.format({"收盤價": "{:.1f}", "回測報酬": "{:.1%}"}).background_gradient(subset=['回測報酬'], cmap='Greens'), use_container_width=True)

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
