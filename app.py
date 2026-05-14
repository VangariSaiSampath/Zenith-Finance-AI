import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
import sqlite3
from duckduckgo_search import DDGS
from google import genai
import requests

# --- CONFIGURATION & STYLING ---
st.set_page_config(page_title="Zenith Finance AI", layout="wide", page_icon="💎")

# Replace with your working Gemini API Key
GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]

def local_css():
    st.markdown("""
        <style>
        /* Global App Colors */
        .stApp { background-color: #0e1117; color: #ffffff; }
        
        /* Ticker CSS */
        .ticker-wrapper {
            width: 100%; overflow: hidden; background: #1e222d;
            padding: 8px 0; border-bottom: 1px solid #363c4e; margin-bottom: 20px;
        }
        .ticker-transition {
            display: inline-block; white-space: nowrap;
            animation: ticker 35s linear infinite;
        }
        @keyframes ticker {
            0% { transform: translateX(100%); }
            100% { transform: translateX(-100%); }
        }
        .price-up { color: #00ff88; font-weight: bold; }
        .price-down { color: #ff4b4b; font-weight: bold; }
        
        /* FIX 1: Brighten Metric Labels (Live Price, P/E Ratio) */
        div[data-testid="stMetricLabel"] > div {
            color: ##d1d5db !important; 
            font-size: 1.1rem !important;
            font-weight: 600;
        }
        div[data-testid="stMetricValue"] { font-size: 2rem; color: #00ff88; }
        
        /* FIX 2: Brighten Tab Fonts */
        .stTabs [data-baseweb="tab-list"] button [data-testid="stMarkdownContainer"] p {
            color: #d1d5db !important; /* Light Grey for unselected */
            font-size: 16px;
        }
        .stTabs [aria-selected="true"] [data-testid="stMarkdownContainer"] p {
            color: #ffffff !important; /* Bright White for selected */
            font-weight: 800 !important;
            border-bottom: 2px solid #00ff88;
        }
        
        /* FIX 3: Brighten Buttons */
        .stButton>button {
            border: 1px solid #00ff88;
            color: #ffffff;
            background-color: #1e222d;
            font-weight: bold;
            transition: 0.3s;
        }
        .stButton>button:hover {
            background-color: #00ff88;
            color: #000000;
        }
        
        /* Custom UI Cards */
        .glass-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 10px; padding: 20px;
            border: 1px solid rgba(255, 255, 255, 0.1);
            margin-bottom: 15px;
        }
        </style>
    """, unsafe_allow_html=True)

# --- DATABASE LOGIC ---
def init_db():
    conn = sqlite3.connect('zenith_finance.db')
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS history (symbol TEXT, timestamp DATETIME)')
    c.execute('CREATE TABLE IF NOT EXISTS wishlist (symbol TEXT PRIMARY KEY)')
    c.execute('CREATE TABLE IF NOT EXISTS custom_tickers (symbol TEXT PRIMARY KEY, name TEXT)')
    
    # Seed default ticker symbols if empty
    c.execute('SELECT COUNT(*) FROM custom_tickers')
    if c.fetchone()[0] == 0:
        defaults = [('^NSEI', 'Nifty 50'), ('^BSESN', 'Sensex'), ('^GSPC', 'S&P 500'), ('BTC-USD', 'Bitcoin')]
        c.executemany('INSERT INTO custom_tickers VALUES (?, ?)', defaults)
        
    conn.commit()
    conn.close()

def add_history(symbol):
    conn = sqlite3.connect('zenith_finance.db')
    c = conn.cursor()
    c.execute('INSERT INTO history VALUES (?, ?)', (symbol, datetime.now()))
    conn.commit()
    conn.close()

def manage_wishlist(symbol, action):
    conn = sqlite3.connect('zenith_finance.db')
    c = conn.cursor()
    if action == "add":
        c.execute('INSERT OR IGNORE INTO wishlist VALUES (?)', (symbol,))
    else:
        c.execute('DELETE FROM wishlist WHERE symbol = ?', (symbol,))
    conn.commit()
    conn.close()

# --- DATA & AI LOGIC ---
def search_yahoo_tickers(query):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={query}"
    headers = {'User-Agent': 'Mozilla/5.0'} 
    try:
        response = requests.get(url, headers=headers)
        data = response.json()
        results = []
        for q in data.get('quotes', []):
            if 'symbol' in q and 'shortname' in q:
                results.append(f"{q['symbol']} - {q['shortname']} ({q.get('exchDisp', 'Unknown')})")
        return results
    except Exception:
        return []

def get_ticker_data():
    conn = sqlite3.connect('zenith_finance.db')
    tickers = pd.read_sql('SELECT * FROM custom_tickers', conn)
    conn.close()
    
    data = []
    for _, row in tickers.iterrows():
        try:
            t = yf.Ticker(row['symbol'])
            hist = t.history(period="2d")
            if len(hist) > 1:
                change = hist['Close'].iloc[-1] - hist['Close'].iloc[-2]
                pct = (change / hist['Close'].iloc[-2]) * 100
                data.append({"name": row['name'], "price": round(hist['Close'].iloc[-1], 2), "pct": round(pct, 2)})
        except:
            pass
    return data

def get_ai_recommendation(symbol):
    with DDGS() as ddgs:
        search_query = f"{symbol} stock buy sell target recommendations latest news"
        results = list(ddgs.text(search_query, max_results=6))
    
    stock = yf.Ticker(symbol)
    info = stock.info
    
    context = f"""
    Stock: {symbol}
    Current Price: {info.get('currentPrice', 'N/A')}
    PE Ratio: {info.get('trailingPE', 'N/A')}
    Analyst Insights found online: {str(results)}
    """
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""
    Act as a Senior Wall Street Analyst. Analyze this data for {symbol}:
    {context}
    
    Provide:
    1. FINAL RECOMMENDATION (BUY/SELL/HOLD).
    2. Key Reasons (Bull vs Bear case).
    3. Consensus Target Price.
    
    IMPORTANT: Use ONLY markdown bullet points and small headers (###). Do not use huge # tags. Be concise and professional.
    """
    
    response = client.models.generate_content(
        model='gemini-3-flash-preview', 
        contents=prompt
    )
    return response.text, results

# --- STATE MANAGEMENT ---
# This allows clicking sidebar items to load the stock
if 'active_stock' not in st.session_state:
    st.session_state['active_stock'] = None

# --- UI INITIALIZATION ---
local_css()
init_db()

# 1. Scrolling Customizable Ticker
ticker_data = get_ticker_data()
if ticker_data:
    ticker_html = "".join([
        f"<span style='margin-right:50px;'>{d['name']}: {d['price']} "
        f"<span class='{'price-up' if d['pct'] > 0 else 'price-down'}'>({d['pct']}%)</span></span>" 
        for d in ticker_data
    ])
    st.markdown(f"<div class='ticker-wrapper'><div class='ticker-transition'>{ticker_html}</div></div>", unsafe_allow_html=True)

# 2. Sidebar (Now Fully Clickable & Customizable)
with st.sidebar:
    st.title("💎 Zenith Data")
    st.markdown("---")
    
    # Wishlist Module
    with st.expander("⭐ My Wishlist", expanded=True):
        conn = sqlite3.connect('zenith_finance.db')
        wishlist = pd.read_sql('SELECT * FROM wishlist', conn)
        if not wishlist.empty:
            for w in wishlist['symbol']:
                col_w, col_del = st.columns([4, 1])
                # Clicking the stock name loads it into the main app
                if col_w.button(w, key=f"load_wl_{w}", use_container_width=True):
                    st.session_state['active_stock'] = w
                    st.rerun()
                # Deleting removes it
                if col_del.button("✖", key=f"del_wl_{w}"):
                    manage_wishlist(w, "remove")
                    st.rerun()
        else:
            st.write("Your wishlist is empty.")

    # Recent Searches Module
    with st.expander("🕒 Recent Searches"):
        history = pd.read_sql('SELECT DISTINCT symbol FROM history ORDER BY timestamp DESC LIMIT 5', conn)
        for h in history['symbol']:
            if st.button(h, key=f"load_hist_{h}", use_container_width=True):
                st.session_state['active_stock'] = h
                st.rerun()

    # Custom Ticker Module
    with st.expander("📈 Manage Top Ticker (Scrolling Bar)"):
        st.write("Add indices or stocks to the top bar:")
        new_ticker = st.text_input("Symbol (e.g., INFY.NS, GC=F)", placeholder="Symbol")
        new_ticker_name = st.text_input("Display Name", placeholder="e.g., Infosys")
        
        if st.button("Add to Ticker Bar", use_container_width=True):
            if new_ticker and new_ticker_name:
                conn = sqlite3.connect('zenith_finance.db')
                c = conn.cursor()
                c.execute('INSERT OR IGNORE INTO custom_tickers VALUES (?, ?)', (new_ticker.upper(), new_ticker_name))
                conn.commit()
                st.rerun()
                
        st.markdown("**Current Tickers:**")
        tickers = pd.read_sql('SELECT * FROM custom_tickers', conn)
        for _, row in tickers.iterrows():
            c1, c2 = st.columns([4, 1])
            c1.write(f"{row['name']}")
            if c2.button("✖", key=f"del_tick_{row['symbol']}"):
                c = conn.cursor()
                c.execute('DELETE FROM custom_tickers WHERE symbol=?', (row['symbol'],))
                conn.commit()
                st.rerun()
        conn.close()

# 3. Main Search Engine
st.markdown("<h1 style='text-align: center;'>Zenith Finance AI</h1>", unsafe_allow_html=True)
st.markdown("<p style='text-align: center; color: #888; margin-bottom: 30px;'>Global stock research, powered by real-time AI consensus.</p>", unsafe_allow_html=True)

col_spacer1, col_search, col_spacer2 = st.columns([1, 2, 1])

with col_search:
    search_keyword = st.text_input("🔍 Search Company Name or Ticker", placeholder="e.g., HDFC, Tata, Apple...")
    
    if search_keyword:
        dropdown_options = search_yahoo_tickers(search_keyword)
        if dropdown_options:
            selected_full_string = st.selectbox("Select the exact stock:", dropdown_options, label_visibility="collapsed")
            selected_symbol_extracted = selected_full_string.split(" - ")[0]
            
            # Explicit analyze button to prevent auto-loading while scrolling through dropdown
            if st.button("🚀 Analyze Stock", use_container_width=True):
                st.session_state['active_stock'] = selected_symbol_extracted
                st.rerun()
        else:
            st.warning("No stocks found. Try a different keyword.")

# 4. The Main Dashboard Engine
if st.session_state['active_stock']:
    active_sym = st.session_state['active_stock']
    st.markdown("---")
    add_history(active_sym)
    
    # Fetch Data
    stock = yf.Ticker(active_sym)
    info = stock.info
    hist_data = stock.history(period="3mo")
    
    # --- TOP ROW: KEY METRICS ---
    col_title, col_m1, col_m2, col_m3, col_btn = st.columns([3, 2, 2, 2, 2])
    
    with col_title:
        st.subheader(active_sym)
        st.caption(info.get('shortName', 'Unknown Company'))
        
    if not hist_data.empty and len(hist_data) >= 2:
        current_price = hist_data['Close'].iloc[-1]
        prev_price = hist_data['Close'].iloc[-2]
        change = current_price - prev_price
        pct_change = (change / prev_price) * 100
        
        col_m1.metric("Live Price", f"₹{current_price:.2f}" if ".NS" in active_sym or ".BO" in active_sym else f"${current_price:.2f}", f"{change:.2f} ({pct_change:.2f}%)")
    else:
        col_m1.metric("Live Price", "N/A")
        
    col_m2.metric("P/E Ratio", info.get('trailingPE', 'N/A'))
    col_m3.metric("52W High", info.get('fiftyTwoWeekHigh', 'N/A'))
    
    with col_btn:
        st.write("") # Spacer to push button down to align
        if st.button("⭐ Add to Wishlist", use_container_width=True):
            manage_wishlist(active_sym, "add")
            st.toast(f"{active_sym} added to wishlist!")

    st.write("") # Spacer

    # --- 5-TAB SYSTEM FOR CLEAN UX ---
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 Interactive Chart", "🤖 AI Research", "🏢 Company Profile", "💰 Financials", "📰 Sources"])
    
    with tab1:
        if hist_data.empty:
            st.error(f"❌ Market data currently unavailable for {active_sym}.")
        else:
            fig = go.Figure(data=[go.Candlestick(
                x=hist_data.index, 
                open=hist_data['Open'], high=hist_data['High'], 
                low=hist_data['Low'], close=hist_data['Close'],
                increasing_line_color='#00ff88', decreasing_line_color='#ff4b4b'
            )])
            fig.update_layout(
                template="plotly_dark", 
                margin=dict(l=0, r=0, t=10, b=0), height=500,
                xaxis_rangeslider_visible=False
            )
            st.plotly_chart(fig, use_container_width=True)

    with tab2:
        if not hist_data.empty:
            with st.spinner("AI is analyzing real-time web sentiment and data..."):
                try:
                    rec, sources = get_ai_recommendation(active_sym)
                    st.markdown(rec)
                    st.session_state['current_sources'] = sources 
                    
                    # BONUS: Download Button for the AI Report
                    st.download_button(
                        label="📥 Download AI Report (.txt)",
                        data=rec,
                        file_name=f"{active_sym}_Zenith_AI_Research.txt",
                        mime="text/plain"
                    )
                except Exception as e:
                    st.error("⚠️ AI Research Failed. Technical error:")
                    st.code(str(e))
        else:
            st.warning("Cannot generate AI research without valid market data.")
            
    with tab3:
        st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
        st.subheader("About the Company")
        st.write(f"**Sector:** {info.get('sector', 'N/A')} | **Industry:** {info.get('industry', 'N/A')}")
        st.write(f"**Website:** {info.get('website', 'N/A')}")
        st.markdown("---")
        st.write(info.get('longBusinessSummary', 'Company biography is currently unavailable.'))
        st.markdown("</div>", unsafe_allow_html=True)
        
    with tab4:
        st.markdown("<div class='glass-card'>", unsafe_allow_html=True)
        st.subheader("Key Financial Metrics")
        c1, c2, c3 = st.columns(3)
        c1.metric("Market Cap", f"{info.get('marketCap', 0) / 1e9:.2f} Billion" if info.get('marketCap') else "N/A")
        c2.metric("Total Revenue", f"{info.get('totalRevenue', 0) / 1e9:.2f} Billion" if info.get('totalRevenue') else "N/A")
        c3.metric("Gross Margins", f"{info.get('grossMargins', 0) * 100:.2f}%" if info.get('grossMargins') else "N/A")
        
        c4, c5, c6 = st.columns(3)
        c4.metric("Return on Equity", f"{info.get('returnOnEquity', 0) * 100:.2f}%" if info.get('returnOnEquity') else "N/A")
        c5.metric("Dividend Yield", f"{info.get('dividendYield', 0) * 100:.2f}%" if info.get('dividendYield') else "N/A")
        c6.metric("Debt to Equity", info.get('debtToEquity', 'N/A'))
        st.markdown("</div>", unsafe_allow_html=True)

    with tab5:
        st.subheader("Reference Materials")
        st.write("The AI synthesized its recommendation from the following live web sources:")
        if 'current_sources' in st.session_state:
            for s in st.session_state['current_sources']:
                st.markdown(f"- **[{s['title']}]({s['href']})**")
        else:
            st.info("Run the AI Research first to see sources.")
