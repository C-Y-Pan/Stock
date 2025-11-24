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
            
            return df[['Date', 'Market_RSI', 'Market_MA20', 'Market_MA60', 'Close', 'OBV', 'OBV_MA20', 'VIX']]
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
# 5. [核心演算法] 買賣評等 (Alpha Score) - 最終修正版
# ==========================================
def calculate_alpha_score(df, margin_df, short_df):
    df = df.copy(); df['Alpha_Score'] = 0.0
    
    # === 關鍵修正：確保使用個股自身的均線 (MA60, MA20) ===
    # 防呆：如果資料庫沒有計算到 MA60，先用收盤價填補避免報錯 (雖然 calculate_indicators 應該要有)
    if 'MA60' not in df.columns: df['MA60'] = df['Close']
    if 'MA20' not in df.columns: df['MA20'] = df['Close']
    
    # 1. 趨勢面 (Trend)
    # 個股是否站上「它自己的」季線 (+15)
    df.loc[df['Close'] > df['MA60'], 'Alpha_Score'] += 15
    df.loc[df['Close'] < df['MA60'], 'Alpha_Score'] -= 15
    
    # 個股是否站上「它自己的」月線 (+10)
    df.loc[df['Close'] > df['MA20'], 'Alpha_Score'] += 10
    df.loc[df['Close'] < df['MA20'], 'Alpha_Score'] -= 15
    
    # 大盤環境加分 (若大盤月線 > 季線，整體環境偏多，全體加分)
    if 'Market_MA20' in df.columns and 'Market_MA60' in df.columns:
        df.loc[df['Market_MA20'] > df['Market_MA60'], 'Alpha_Score'] += 5

    # 2. 動能 & 恐慌 (Momentum)
    df.loc[df['Market_RSI'] < 30, 'Alpha_Score'] += 20
    df.loc[df['Market_RSI'] < 20, 'Alpha_Score'] += 25
    df.loc[df['Market_RSI'] > 80, 'Alpha_Score'] -= 10
    
    # VIX 恐慌加分
    df.loc[df['VIX'] > 20, 'Alpha_Score'] += 5
    df.loc[df['VIX'] > 30, 'Alpha_Score'] += 15
    df.loc[df['VIX'] < 13, 'Alpha_Score'] -= 5

    # 3. 籌碼 (Chips)
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
    
    colors_score = ['#ef5350' if v > 0 else '#00e676' for v in plot_df['Alpha_Score']]
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
    st.title("⚔️ 台股戰情室")
    st.caption("Pro v6.0: AI-Alpha Edition")
    
    # [修改] 加入 "💼 持股健診與建議"
    page = st.radio("導航", ["🌍 市場總覽 (Macro)", "📊 單股深度分析", "🚀 科技股掃描", "💼 持股健診與建議", "📋 全台股清單"])
    st.markdown("---")
    st.sidebar.info("🔥 v6.0 更新：Alpha Score 評等系統、融資券監控、蒙地卡羅風險模擬")
    st.markdown("---")
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
    # 1. 資料準備與索引定位
    # ==================================================
    if st.session_state['all_stock_list'] is None:
        st.session_state['all_stock_list'] = get_master_stock_data()
    
    # 取得排序後的所有代號列表 (含上市櫃 + 靜態清單)
    df_all = st.session_state['all_stock_list']
    all_tickers = sorted(df_all['代號'].astype(str).tolist())
    base_tickers = list(TW_STOCK_NAMES_STATIC.keys())
    all_tickers = sorted(list(set(all_tickers + base_tickers)))

    # 定位當前股票索引
    current_ticker_clean = st.session_state['last_ticker'].split('.')[0]
    try:
        current_index = all_tickers.index(current_ticker_clean)
    except ValueError:
        current_index = 0 

    # ==================================================
    # [Step 3] 導航介面優化：手指友善版 (Finger-Friendly)
    # ==================================================
    # 設計思路：
    # 手機畫面窄，為了好按，將 "搜尋" 與 "切換" 分成上下兩層。
    # 上層：輸入框 + Go 按鈕
    # 下層：上一檔 + 下一檔 (並排顯示)
    
    # --- Row 1: 搜尋與確認 ---
    with st.container():
        col_search, col_run = st.columns([3, 1])
        with col_search:
            # 使用 callback 或 value 綁定
            ticker_input_val = st.text_input(
                "輸入股票代號", 
                key="last_ticker_input", 
                value=st.session_state['last_ticker'], 
                label_visibility="collapsed", 
                placeholder="輸入代號 (如 2330)"
            )
        with col_run:
            # 加大按鈕寬度，方便點擊
            if st.button("Go", type="primary", use_container_width=True):
                st.session_state['last_ticker'] = ticker_input_val
                st.rerun()

    # --- Row 2: 大拇指導航區 (上一檔 / 下一檔) ---
    # 使用 columns([1, 1]) 確保手機上這兩個按鈕是「並排」而不是「堆疊」
    col_prev, col_next = st.columns([1, 1])
    
    with col_prev:
        if st.button("◀ 上一檔", use_container_width=True):
            new_index = (current_index - 1) % len(all_tickers)
            st.session_state['last_ticker'] = all_tickers[new_index]
            st.rerun()

    with col_next:
        if st.button("下一檔 ▶", use_container_width=True):
            new_index = (current_index + 1) % len(all_tickers)
            st.session_state['last_ticker'] = all_tickers[new_index]
            st.rerun()

    # ==================================================
    # 2. 自動執行分析邏輯
    # ==================================================
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
                    # 計算各項指標
                    beta, vol, personality = calculate_stock_personality(final_df, market_df)
                    action, color, reason = analyze_signal(final_df)
                    hit_rate, hits, total = calculate_target_hit_rate(final_df)
                    real_win_rate, real_wins, real_total, avg_pnl = calculate_realized_win_rate(final_df)
                    risk_metrics = calculate_risk_metrics(final_df)
                    
                    # 存入 Session
                    st.session_state['analysis_history'][fmt_ticker] = {
                        'df': final_df, 'params': best_params, 'action': action,
                        'reason': reason, 'beta': beta, 'vol': vol, 'personality': personality,
                        'name': name, 
                        'hit_rate': hit_rate, 'hits': hits, 'total_trades': total,
                        'real_win_rate': real_win_rate, 'real_wins': real_wins, 'real_total': real_total, 'avg_pnl': avg_pnl,
                        'risk': risk_metrics,
                        'validation': validation_result
                    }

    # ==================================================
    # [Step 4] 數據顯示優化：Grid Layout (避免手機堆疊)
    # ==================================================
    current_ticker = st.session_state['last_ticker']
    possible_keys = [k for k in st.session_state['analysis_history'].keys() if current_ticker in k]
    
    if possible_keys:
        data = st.session_state['analysis_history'][possible_keys[0]]
        final_df = data['df']
        risk = data.get('risk', {})
        
        strat_mdd = calculate_mdd(final_df['Cum_Strategy'])
        strat_ret = data['params']['Return'] * 100
        
        st.markdown(f"## {possible_keys[0]} {data['name']}")
        st.caption(f"策略邏輯: {data['reason']} | 波動率: {data['vol']}")
        
        # --- 使用 2x2 網格取代 1x5 排列 ---
        
        # Row A: 核心建議 & 獲利能力
        ma_1, ma_2 = st.columns(2)
        ma_1.metric("策略建議", data['action'], data['reason'])
        ma_2.metric("淨報酬 (含成本)", f"{strat_ret:.1f}%", f"MDD: {strat_mdd:.1f}%")
        
        # Row B: 勝率 & 風險指標
        mb_1, mb_2 = st.columns(2)
        mb_1.metric("實際勝率", data.get('real_win_rate', '0%'), f"{data.get('real_wins', 0)}勝")
        mb_2.metric("夏普值 (Sharpe)", f"{risk.get('Sharpe', 0):.2f}", f"PF: {risk.get('Profit_Factor', 0):.2f}")
        
        # Row C: 目標達成率 (單獨一行顯示)
        st.metric("目標觸及率 (Target Hit)", data['hit_rate'], f"{data['hits']}/{data['total_trades']} 次 (目標+15%)")
        
        # ==================================================
        # Tabs 繪圖區 (內容保持不變，僅恢復結構)
        # ==================================================
        tab1, tab2, tab3, tab4 = st.tabs(["📈 操盤決策圖", "💰 權益曲線", "🎲 蒙地卡羅模擬", "🧪 有效性驗證"])
        
# [Tab 1: K線圖與詳細註記]
        with tab1:
            # 建立子圖架構
            fig = make_subplots(rows=4, cols=1, shared_xaxes=True, vertical_spacing=0.03, 
                                row_heights=[0.5, 0.15, 0.15, 0.20], 
                                subplot_titles=("", "成交量", "法人籌碼 (OBV)", "相對強弱指標 (RSI)"))
            
            # --- 1. K線圖 (紅漲綠跌) ---
            fig.add_trace(go.Candlestick(
                x=final_df['Date'], open=final_df['Open'], high=final_df['High'], 
                low=final_df['Low'], close=final_df['Close'], name='K線',
                increasing_line_color='#ef5350', decreasing_line_color='#00bfa5' 
            ), row=1, col=1)
            
            # 均線與指標
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['SuperTrend'], mode='lines', 
                                     line=dict(color='yellow', width=1.5), name='停損基準線'), row=1, col=1)
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['MA60'], mode='lines', 
                                     line=dict(color='rgba(255, 255, 255, 0.5)', width=1), name='季線'), row=1, col=1)

            # --- 2. 買賣點標記與理由註記 ---
            final_df['Buy_Y'] = final_df['Low'] * 0.92 # 買點標記位置 (K線下方)
            final_df['Sell_Y'] = final_df['High'] * 1.08 # 賣點標記位置 (K線上方)

            # 輔助函式：產生「信心分數 + 理由」的文字
            # 為了避免圖表太亂，買進我們顯示「信心值」，顏色代表「理由」
            def get_buy_text(sub_df):
                return [f"<b>{score}</b>" for score in sub_df['Confidence']]

            # 輔助函式：產生「報酬率 + 簡短理由」的文字
            def get_sell_text(sub_df):
                labels = []
                for idx, row in sub_df.iterrows():
                    ret = row['Return_Label']
                    reason = row['Reason']
                    # 簡化理由文字以節省空間
                    short_reason = reason.replace("觸發", "").replace("操作", "")
                    labels.append(f"{ret}<br>({short_reason})") # 使用 <br> 換行
                return labels

            # [買進 A] 動能突破/回測 (金黃色 Triangle)
            buy_trend = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('突破|回測|動能'))]
            if not buy_trend.empty:
                fig.add_trace(go.Scatter(
                    x=buy_trend['Date'], y=buy_trend['Buy_Y'], mode='markers+text',
                    text=get_buy_text(buy_trend), textposition="bottom center",
                    textfont=dict(color='#FFD700', size=11),
                    marker=dict(symbol='triangle-up', size=14, color='#FFD700', line=dict(width=1, color='black')), 
                    name='買進 (趨勢)', hovertext=buy_trend['Reason']
                ), row=1, col=1)
            
            # [買進 B] 超賣反彈 (青色 Triangle)
            buy_panic = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('反彈|超賣'))]
            if not buy_panic.empty:
                fig.add_trace(go.Scatter(
                    x=buy_panic['Date'], y=buy_panic['Buy_Y'], mode='markers+text',
                    text=get_buy_text(buy_panic), textposition="bottom center",
                    textfont=dict(color='#00FFFF', size=11),
                    marker=dict(symbol='triangle-up', size=14, color='#00FFFF', line=dict(width=1, color='black')), 
                    name='買進 (反彈)', hovertext=buy_panic['Reason']
                ), row=1, col=1)
            
            # [買進 C] 籌碼佈局 (淡紫色 Triangle)
            buy_chip = final_df[(final_df['Action'] == 'Buy') & (final_df['Reason'].str.contains('籌碼|佈局'))]
            if not buy_chip.empty:
                fig.add_trace(go.Scatter(
                    x=buy_chip['Date'], y=buy_chip['Buy_Y'], mode='markers+text',
                    text=get_buy_text(buy_chip), textposition="bottom center",
                    textfont=dict(color='#DDA0DD', size=11),
                    marker=dict(symbol='triangle-up', size=14, color='#DDA0DD', line=dict(width=1, color='black')), 
                    name='買進 (籌碼)', hovertext=buy_chip['Reason']
                ), row=1, col=1)

            # [賣出] 顯示報酬率與理由 (洋紅色 Down Triangle)
            sell_all = final_df[final_df['Action'] == 'Sell']
            if not sell_all.empty:
                fig.add_trace(go.Scatter(
                    x=sell_all['Date'], y=sell_all['Sell_Y'], mode='markers+text', 
                    text=get_sell_text(sell_all), # 這裡會顯示如 "+15% (停利)"
                    textposition="top center",
                    textfont=dict(color='white', size=11),
                    marker=dict(symbol='triangle-down', size=14, color='#FF00FF', line=dict(width=1, color='black')), 
                    name='賣出', hovertext=sell_all['Reason']
                ), row=1, col=1)
            
            # --- 副圖指標繪製 ---
            colors_vol = ['#ef5350' if row['Open'] < row['Close'] else '#26a69a' for idx, row in final_df.iterrows()]
            fig.add_trace(go.Bar(x=final_df['Date'], y=final_df['Volume'], marker_color=colors_vol, name='成交量'), row=2, col=1)
            
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['OBV'], mode='lines', line=dict(color='orange', width=1.5), name='OBV'), row=3, col=1)
            
            fig.add_trace(go.Scatter(x=final_df['Date'], y=final_df['RSI'], name='RSI', line=dict(color='cyan', width=1.5)), row=4, col=1)
            fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=30, y1=30, line=dict(color="green", dash="dot"), row=4, col=1)
            fig.add_shape(type="line", x0=final_df['Date'].min(), x1=final_df['Date'].max(), y0=70, y1=70, line=dict(color="red", dash="dot"), row=4, col=1)
            
            fig.update_layout(
                height=800, 
                template="plotly_dark", 
                xaxis_rangeslider_visible=False,
                margin=dict(l=20, r=40, t=30, b=20),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig, use_container_width=True)
            
        # [Tab 2: 權益曲線]
        with tab2:
            fig_c = go.Figure()
            fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Market'], name='大盤', line=dict(color='gray', dash='dot')))
            fig_c.add_trace(go.Scatter(x=final_df['Date'], y=final_df['Cum_Strategy'], name='策略淨值', line=dict(color='#ef5350', width=2), fill='tozeroy'))
            
            # 標記買賣點
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
            st.markdown("### 🎲 蒙地卡羅模擬")
            last_price = final_df['Close'].iloc[-1]
            sim_df, var95 = run_monte_carlo_sim(last_price, data['vol'], days=120, sims=200)
            
            final_prices = sim_df.iloc[-1]
            optimistic_price = np.percentile(final_prices, 95)
            pessimistic_price = np.percentile(final_prices, 5)
            prob_up = (final_prices > last_price).mean() * 100
            
            c_mc1, c_mc2 = st.columns([3, 1])
            with c_mc1:
                fig_mc = go.Figure()
                for col in sim_df.columns[:30]: # 只畫前30條避免太亂
                    fig_mc.add_trace(go.Scatter(y=sim_df[col], mode='lines', line=dict(width=1, color='rgba(0,255,255,0.1)'), showlegend=False))
                fig_mc.add_hline(y=last_price, line_dash="dash", line_color="white", annotation_text="現價")
                fig_mc.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig_mc, use_container_width=True)
            with c_mc2:
                st.metric("上漲機率", f"{prob_up:.1f}%")
                st.metric("潛在獲利", f"+{(optimistic_price-last_price)/last_price*100:.1f}%")
                st.metric("潛在風險", f"-{(last_price-pessimistic_price)/last_price*100:.1f}%")

        # [Tab 4: 驗證]
        with tab4:
            val_res = data.get('validation')
            if val_res:
                st.markdown(f"### 🧪 樣本外測試")
                train_cagr = val_res['train']['cagr'] * 100
                test_cagr = val_res['test']['cagr'] * 100
                
                vt1, vt2 = st.columns(2)
                vt1.metric("訓練集報酬", f"{train_cagr:.1f}%")
                vt2.metric("測試集報酬", f"{test_cagr:.1f}%", f"{(test_cagr-train_cagr):.1f}%")
                
                fig_val = go.Figure()
                fig_val.add_trace(go.Scatter(x=val_res['train']['df']['Date'], y=val_res['train']['df']['Cum_Strategy'], name='訓練', line=dict(color='gray', dash='dot')))
                scale = val_res['train']['df']['Cum_Strategy'].iloc[-1]
                fig_val.add_trace(go.Scatter(x=val_res['test']['df']['Date'], y=val_res['test']['df']['Cum_Strategy']*scale, name='測試', line=dict(color='#00e676')))
                fig_val.add_vline(x=val_res['split_date'].timestamp()*1000, line_dash="dash", line_color="white")
                fig_val.update_layout(template="plotly_dark", height=400, margin=dict(l=10, r=10, t=30, b=10))
                st.plotly_chart(fig_val, use_container_width=True)
            else:
                st.warning("數據不足，無法驗證。")

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
    
    if scan_btn:
        st.session_state['is_scanning'] = True
        tickers = [t.strip().replace(',','') for t in user_list.split('\n') if t.strip()]
        tickers = list(set(tickers)) 
        results = []
        
        progress_text = "AI 正在逐一分析個股結構與籌碼..."
        my_bar = st.progress(0, text=progress_text)
        
        for idx, ticker in enumerate(tickers):
            my_bar.progress((idx + 1) / len(tickers), text=f"正在運算 ({idx+1}/{len(tickers)}): {ticker}")
            
            raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
            if not raw_df.empty:
                # 執行最佳化與回測
                best_params, final_df = run_optimization(raw_df, market_df, start_date, fee_rate=fee_input, tax_rate=tax_input)
                
                if final_df is not None and not final_df.empty:
                    # 1. 計算 Alpha Score (Base)
                    stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
                    base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
                    
                    # 2. 技術面分析
                    action, color, reason = analyze_signal(final_df)
                    name = get_stock_name(fmt_ticker)
                    
                    # 3. 情境感知調整 (同步 Portfolio 頁面的邏輯)
                    final_score = base_alpha_score
                    current_price = final_df['Close'].iloc[-1]
                    
                    # 判斷是否為逆勢策略
                    last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
                    is_rebound = False
                    if last_trade is not None:
                        buy_reason = str(last_trade['Reason'])
                        if any(x in buy_reason for x in ["反彈", "超賣", "回測"]): is_rebound = True
                    
                    # 針對持有或買進狀態進行加分檢查
                    if action == "✊ 續抱" or action == "🚀 買進":
                        if is_rebound:
                            # 補償機制
                            if current_price < final_df['MA60'].iloc[-1]: final_score += 15
                            ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                            if current_price > ma5: final_score += 10
                            rsi_now = final_df['RSI'].iloc[-1]
                            rsi_prev = final_df['RSI'].iloc[-2]
                            if rsi_now > rsi_prev: final_score += 10
                        else:
                            # 順勢機制：強勢股加分
                            if current_price > final_df['MA20'].iloc[-1]: final_score += 5
                    
                    final_score = max(min(final_score, 100), -100)
                    hit_rate, hits, total = calculate_target_hit_rate(final_df)
                    
                    # === 修正：移除過濾器，讓所有結果都顯示 ===
                    # 為了不讓列表太雜，可以只過濾掉極度沒有意義的 (例如觀望且分數極低)，但這裡我們先全開
                    results.append({
                        "代號": fmt_ticker.split('.')[0], 
                        "名稱": name, 
                        "建議": action,
                        "收盤價": current_price,
                        "Alpha_Score": int(final_score), 
                        "理由": f"{reason} | Alpha:{int(final_score)}", 
                        "回測報酬": best_params['Return'],
                        "達標率": hit_rate
                    })
                        
        my_bar.empty()
        
        if results:
            full_df = pd.DataFrame(results)
            
            # 排序：Alpha Score 高到低
            top_10_df = full_df.sort_values(by=['Alpha_Score', '回測報酬'], ascending=[False, False]).head(10)
            top_10_df.index = range(1, len(top_10_df) + 1)
            
            st.session_state['scan_results_df'] = full_df
            st.session_state['top_10_df'] = top_10_df
        else:
            st.session_state['scan_results_df'] = pd.DataFrame()
            st.session_state['top_10_df'] = pd.DataFrame()
            
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
            use_container_width=True
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

# --- 頁面 3.5 (完整優化版): 持股健診 ---
elif page == "💼 持股健診與建議":
    st.markdown("### 💼 智能持股健診 (Portfolio Doctor)")
    st.markdown("""
    > **系統說明**：請在下方輸入您的 **持有股數**。系統將自動抓取最新股價計算市值，並透過 **「情境感知演算法 (Context-Aware)」**，區分順勢與逆勢策略，提供最理性的操作建議。
    """)

    # 1. 建立可編輯的表格 (Data Editor) - 輸入股數
    default_data = pd.DataFrame([
        {"代號": "2330", "持有股數": 1000}, # 台積電
        {"代號": "2317", "持有股數": 2000}, # 鴻海
        {"代號": "2603", "持有股數": 5000}, # 長榮
    ])
    
    col_input, col_chart = st.columns([1, 1])
    
    with col_input:
        st.markdown("#### 1. 輸入持股明細")
        edited_df = st.data_editor(
            default_data, 
            num_rows="dynamic", 
            use_container_width=True,
            column_config={
                "代號": st.column_config.TextColumn("股票代號", help="請輸入台股代號 (如 2330)"),
                "持有股數": st.column_config.NumberColumn("持有股數 (股)", min_value=1, format="%d", help="請輸入實際股數，例如 1 張請輸入 1000")
            }
        )
        start_diag_btn = st.button("⚡ 開始診斷", type="primary", use_container_width=True)

    # 2. 執行診斷邏輯
    if start_diag_btn:
        portfolio_results = []
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 遍歷使用者輸入的每一檔股票
        total_rows = len(edited_df)
        for idx, row in edited_df.iterrows():
            ticker = str(row['代號']).strip()
            shares = row['持有股數']
            
            # 防呆：若無代號或股數為0則跳過
            if not ticker or shares <= 0: continue
            
            status_text.text(f"AI 運算中 ({idx+1}/{total_rows}): {ticker} ...")
            
            # 獲取資料
            raw_df, fmt_ticker = get_stock_data(ticker, start_date, end_date)
            name = get_stock_name(fmt_ticker)
            
            # 資料異常處理
            if raw_df.empty:
                portfolio_results.append({
                    "代號": ticker, "名稱": "無資料", "建議": "⚠️ 異常", "持有股數": shares,
                    "收盤價": 0, "市值": 0, "評分": 0, "理由": "無法獲取數據", "AI 建議": "略過", "技術訊號": "N/A"
                })
                continue
                
            # 執行核心策略 (取得技術面參數與 DataFrame)
            best_params, final_df = run_optimization(raw_df, market_df, start_date, fee_input, tax_input)
            
            if final_df is None or final_df.empty:
                portfolio_results.append({
                    "代號": ticker, "名稱": name, "建議": "⚠️ 數據不足", "持有股數": shares,
                    "收盤價": 0, "市值": 0, "評分": 0, "理由": "區間內無交易", "AI 建議": "略過", "技術訊號": "N/A"
                })
                continue

            # === [Step 1] 自動計算市值 ===
            current_price = final_df['Close'].iloc[-1]
            market_value = current_price * shares
            
            # === [Step 2] 計算基礎 Alpha Score (原始分數) ===
            # 這裡傳入空的 margin/short df 以節省 API 呼叫時間，主要依賴均線與 RSI 評分
            stock_alpha_df = calculate_alpha_score(final_df, pd.DataFrame(), pd.DataFrame())
            base_alpha_score = stock_alpha_df['Alpha_Score'].iloc[-1]
            
            # === [Step 3] 取得技術面訊號與理由 ===
            action, color, tech_reason = analyze_signal(final_df)
            
            # === [Step 4] 情境感知評分調整 (Context-Aware Adjustment) ===
            # 目的：根據「策略屬性」動態調整評分標準，避免用順勢的標準去錯殺逆勢的單
            final_score = base_alpha_score
            adjustment_log = [] # 用於記錄調整理由
            
            # 判斷最後一次買進是否為「逆勢/反彈」策略
            last_trade = final_df[final_df['Action'] == 'Buy'].iloc[-1] if not final_df[final_df['Action'] == 'Buy'].empty else None
            is_rebound_strategy = False
            
            if last_trade is not None:
                buy_reason = str(last_trade['Reason'])
                if any(x in buy_reason for x in ["反彈", "超賣", "回測", "籌碼"]):
                    is_rebound_strategy = True
            
            # --- 針對「持有中 (Hold)」的部位進行深度檢視 ---
            if action == "✊ 續抱":
                # 情境 A: 逆勢策略 (抄底/反彈)
                if is_rebound_strategy:
                    # 取得即時指標
                    ma5 = final_df['Close'].rolling(5).mean().iloc[-1]
                    rsi_now = final_df['RSI'].iloc[-1]
                    rsi_prev = final_df['RSI'].iloc[-2]
                    
                    # 補償 1: 不看長均線 (因為抄底必定在季線下)
                    if current_price < final_df['MA60'].iloc[-1]:
                        final_score += 15 # 補回被 MA60 扣的分數
                        adjustment_log.append("反彈策略忽略季線")
                        
                    # 補償 2: 檢視反彈有效性 (True Test)
                    # 條件一：站上 5 日線 (短線止穩)
                    if current_price > ma5:
                        final_score += 10
                        adjustment_log.append("站穩MA5")
                    else:
                        final_score -= 5 # 連 5 日線都站不上，反彈失敗
                        adjustment_log.append("未站回MA5")
                        
                    # 條件二：RSI 動能
                    if rsi_now > rsi_prev:
                        final_score += 10
                        adjustment_log.append("動能翻揚")
                    elif rsi_now < 30: 
                        final_score += 5
                        adjustment_log.append("低檔鈍化")
                    else:
                        final_score -= 5 # RSI 下彎
                        
                # 情境 B: 順勢策略 (突破)
                else:
                    # 順勢交易維持原標準，但若高檔爆量不漲，要扣分
                    vol_now = final_df['Volume'].iloc[-1]
                    vol_ma = final_df['Vol_MA20'].iloc[-1]
                    if vol_now > vol_ma * 2.5 and final_df['Close'].pct_change().iloc[-1] < 0.005:
                        final_score -= 15
                        adjustment_log.append("高檔爆量滯漲")

            # 確保分數在合理區間
            final_score = max(min(final_score, 100), -100)

            # === [Step 5] 綜合決策輸出 ===
            final_advice = ""; advice_color = ""
            
            if action == "🚀 買進":
                if final_score > 30: final_advice = "🔥 強力加碼"; advice_color = "red"
                else: final_advice = "✅ 買進訊號"; advice_color = "red"
                
            elif action == "⚡ 賣出":
                if final_score < -20: final_advice = "💀 清倉/放空"; advice_color = "green"
                else: final_advice = "📉 獲利了結"; advice_color = "green"
                
            elif action == "✊ 續抱": 
                if final_score > 40: 
                    final_advice = "✨ 抱緊處理"; advice_color = "red"
                elif final_score > 0: # 只要分數是正的，代表反彈有效或趨勢尚可
                    final_advice = "✊ 續抱觀察"; advice_color = "gray"
                elif final_score > -15: # 微幅負分，但有技術單在，不輕易殺低
                    final_advice = "🛡️ 策略持倉"; advice_color = "blue"
                else: 
                    final_advice = "⚠️ 減碼觀望"; advice_color = "orange"
            else: 
                if final_score > 60: final_advice = "👀 留意買點"; advice_color = "blue"
                else: final_advice = "💤 觀望"; advice_color = "gray"

            # 產生詳細理由字串
            reason_display = f"Alpha:{int(final_score)} | {tech_reason}"
            if adjustment_log:
                reason_display = f"原:{int(base_alpha_score)}➜修:{int(final_score)} ({','.join(adjustment_log)})"

            portfolio_results.append({
                "代號": fmt_ticker.split('.')[0],
                "名稱": name,
                "持有股數": shares,
                "收盤價": current_price,
                "市值": market_value,
                "綜合評分": int(final_score), 
                "AI 建議": final_advice,
                "技術訊號": action,
                "詳細理由": reason_display
            })
            
            progress_bar.progress((idx + 1) / total_rows)
            
        progress_bar.empty()
        status_text.empty()
        
        # 3. 呈現結果與儀表板
        if portfolio_results:
            res_df = pd.DataFrame(portfolio_results)
            
            # 計算權重 (基於自動計算出的總市值)
            total_market_value = res_df['市值'].sum()
            if total_market_value > 0:
                res_df['權重%'] = (res_df['市值'] / total_market_value) * 100
                portfolio_health = (res_df['綜合評分'] * res_df['市值']).sum() / total_market_value
            else:
                res_df['權重%'] = 0
                portfolio_health = 0
                
            with col_chart:
                st.markdown("#### 2. 組合健康度總覽")
                st.caption(f"💰 總資產估值: NT$ {int(total_market_value):,}") 
                
                # 繪製儀表板
                fig_gauge = go.Figure(go.Indicator(
                    mode = "gauge+number",
                    value = portfolio_health,
                    title = {'text': "投資組合健康指數"},
                    gauge = {
                        'axis': {'range': [-100, 100]},
                        'bar': {'color': "#00e676" if portfolio_health > 0 else "#ef5350"},
                        'steps': [
                            {'range': [-100, -30], 'color': "rgba(255, 0, 0, 0.3)"},
                            {'range': [-30, 30], 'color': "rgba(128, 128, 128, 0.3)"},
                            {'range': [30, 100], 'color': "rgba(0, 255, 0, 0.3)"}
                        ],
                        'threshold': {'line': {'color': "white", 'width': 4}, 'thickness': 0.75, 'value': portfolio_health}
                    }
                ))
                fig_gauge.update_layout(height=250, margin=dict(l=20, r=20, t=50, b=20), paper_bgcolor='rgba(0,0,0,0)', font={'color': "white"})
                st.plotly_chart(fig_gauge, use_container_width=True)

            st.markdown("---")
            st.markdown("#### 3. 個股操作建議明細")
            
            # 定義樣式函式
            def highlight_advice(val):
                color = 'white'
                val_str = str(val)
                if '加碼' in val_str or '買進' in val_str or '抱緊' in val_str: color = '#ffcdd2' # 紅底
                elif '減碼' in val_str or '賣出' in val_str or '清倉' in val_str: color = '#c8e6c9' # 綠底
                elif '策略持倉' in val_str: color = '#bbdefb' # 藍底
                elif '觀望' in val_str: color = '#cfd8dc' # 灰底
                return f'background-color: {color}; color: black; font-weight: bold'

            def highlight_score(val):
                color = 'red' if val >= 30 else ('green' if val <= -20 else 'gray')
                return f'color: {color}; font-weight: bold'

            # 調整欄位順序與格式
            final_display_cols = ["代號", "名稱", "持有股數", "收盤價", "市值", "權重%", "綜合評分", "AI 建議", "技術訊號", "詳細理由"]
            
            st.dataframe(
                res_df[final_display_cols].style
                .applymap(highlight_advice, subset=['AI 建議'])
                .applymap(highlight_score, subset=['綜合評分'])
                .format({
                    "權重%": "{:.1f}%", 
                    "收盤價": "{:.1f}", 
                    "市值": "{:,.0f}", 
                    "持有股數": "{:.0f}"
                }),
                use_container_width=True,
                height=500
            )
            
            # 文字總結
            health_desc = "偏多" if portfolio_health > 20 else ("轉弱" if portfolio_health < -20 else "震盪")
            st.info(f"💡 **AI 總結**：目前持有 {len(res_df)} 檔標的，總市值約 **NT$ {int(total_market_value/10000):,} 萬**。組合健康分為 **{portfolio_health:.1f}** ({health_desc})。")
