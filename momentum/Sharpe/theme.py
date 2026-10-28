"""Dual Momentum look & feel for Streamlit apps.

Usage (call once, right after st.set_page_config):

    from theme import inject_theme
    inject_theme()
"""
import streamlit as st

CSS = """
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700;750&family=Geist+Mono:wght@400;500&display=swap');

:root{
  --bg:#F6F7F9; --surface:#FFFFFF; --surface2:#F4F5F8;
  --line:#E3E6EB; --line2:#D0D5DD;
  --text:#0E1726; --text2:#3C4657; --muted:#5E6878; --faint:#98A1AE;
  --pos:#067647; --pos-bg:#E8F5EE; --pos-line:#B7E0C8;
  --neg:#B42318; --neg-bg:#FDEDEB; --neg-line:#F5C2BC;
  --warn:#B54708; --warn-bg:#FEF6EA; --warn-line:#F7D7A8;
  --accent:#4F46E5; --accent-bg:#EEF0FF; --accent-line:#C9CCFB;
  --info:#0369A1; --info-bg:#EFF7FC; --info-line:#BFDDF0;
  --shadow:0 1px 2px rgba(14,23,38,.04);
  --r:12px; --r-sm:8px;
  --font-ui:'Geist',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --font-mono:'Geist Mono',ui-monospace,SFMono-Regular,Menlo,monospace;
}

/* ---- base ---- */
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"]{
  font-family:var(--font-ui) !important; color:var(--text);
}
.stApp{ background:var(--bg); }
[data-testid="stHeader"]{ background:rgba(255,255,255,.92); backdrop-filter:blur(6px); border-bottom:1px solid var(--line); }
[data-testid="stMainBlockContainer"]{ padding-top:2.5rem; max-width:1280px; }
.stMarkdown p, .stMarkdown li, label, button, input, textarea, select{ font-family:var(--font-ui) !important; }
.stMarkdown p{ color:var(--text2); }
code, pre, [data-testid="stDataFrame"] *{ font-family:var(--font-mono); }

/* ---- headings ---- */
h1{ font-size:34px !important; font-weight:700 !important; letter-spacing:-.6px !important; color:var(--text) !important; }
h2{ font-size:23px !important; font-weight:750 !important; letter-spacing:-.46px !important; color:var(--text) !important; border-bottom:0 !important; }
h3{ font-size:18px !important; font-weight:700 !important; letter-spacing:-.3px !important; color:var(--text) !important; }
h4{ color:var(--text) !important; font-weight:700 !important; }
h2 .dm-h-meta{ font-size:15px; font-weight:500; letter-spacing:0; color:var(--muted); }
[data-testid="stCaptionContainer"]{ color:var(--muted); }

/* ---- cards: metrics, expanders, bordered containers, alerts ---- */
[data-testid="stMetric"]{
  background:var(--surface); border:1px solid var(--line); border-radius:var(--r);
  padding:14px 16px; box-shadow:var(--shadow);
}
[data-testid="stMetricLabel"]{ color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
[data-testid="stMetricValue"]{ color:var(--text); font-weight:700; letter-spacing:-.4px; }
[data-testid="stExpander"]{
  background:var(--surface); border:1px solid var(--line) !important; border-radius:var(--r) !important; box-shadow:var(--shadow);
}
[data-testid="stExpander"] summary{ font-weight:600; color:var(--text); }
[data-testid="stVerticalBlockBorderWrapper"]{ border-radius:var(--r); border-color:var(--line); }
/* keep Streamlit's own success / warning / error / info colours; only round them */
[data-testid="stAlert"]{ border-radius:var(--r-sm); }

/* ---- buttons ---- */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button{
  background:var(--surface); color:var(--text); font-weight:600;
  border:1px solid var(--line2); border-radius:10px; padding:.45rem .95rem; box-shadow:var(--shadow);
  transition:background .12s, border-color .12s;
}
.stButton > button:hover, .stDownloadButton > button:hover, .stFormSubmitButton > button:hover{
  background:var(--accent-bg); border-color:var(--accent-line); color:var(--accent);
}
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"]{ background:var(--accent); border-color:var(--accent); color:#fff; }
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover{ background:#4338CA; border-color:#4338CA; color:#fff; }

/* ---- tabs -> accent underline with tinted selected state ---- */
[data-baseweb="tab-list"]{ gap:4px; border-bottom:1px solid var(--line); }
[data-baseweb="tab"]{ font-weight:600; color:var(--muted); border-radius:var(--r-sm) var(--r-sm) 0 0; padding:.55rem .9rem; }
[data-baseweb="tab"][aria-selected="true"]{ color:var(--accent); background:var(--accent-bg); }
[data-baseweb="tab-highlight"]{ background:var(--accent) !important; height:2px; }
[data-baseweb="tab-border"]{ display:none; }

/* newer Streamlit (react-aria tabs) */
[data-testid="stTab"]{ font-weight:600; color:var(--muted); border-radius:var(--r-sm) var(--r-sm) 0 0; padding:.55rem .9rem; }
[data-testid="stTab"][aria-selected="true"]{ color:var(--accent); background:var(--accent-bg); }
[role="tablist"]{ border-bottom:1px solid var(--line); }

/* ---- inputs ---- */
[data-baseweb="select"] > div, [data-baseweb="input"] > div, [data-baseweb="textarea"]{
  background:var(--surface) !important; border:1px solid var(--line2) !important; border-radius:10px !important;
}
[data-baseweb="select"] > div:focus-within, [data-baseweb="input"] > div:focus-within{
  border-color:var(--accent) !important; box-shadow:0 0 0 3px var(--accent-bg);
}

/* ---- tables ---- */
[data-testid="stDataFrame"]{ border:1px solid var(--line); border-radius:var(--r); overflow:hidden; box-shadow:var(--shadow); background:var(--surface); }
[data-testid="stProgress"] > div > div > div{ background:var(--accent); }
hr{ border-color:var(--line); }

/* ---- app components: top bar, status strip, metric cards, score pill ---- */
.dm-topbar{
  display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap;
  background:var(--surface); border:1px solid var(--line); border-radius:var(--r);
  box-shadow:var(--shadow); padding:12px 18px;
}
.dm-topbar-title{ font-size:20px; font-weight:700; letter-spacing:-.3px; color:var(--text); }
.dm-pill{
  display:inline-flex; align-items:center; gap:7px; padding:4px 12px; border-radius:999px;
  background:var(--pos-bg); color:var(--pos); border:1px solid var(--pos-line);
  font-size:12.5px; font-weight:600; white-space:nowrap;
}
.dm-pill::before{ content:""; width:7px; height:7px; border-radius:50%; background:currentColor; }
.dm-subtitle{ color:var(--muted); font-size:13px; margin:8px 2px 18px; }

.dm-strip{
  display:flex; flex-wrap:wrap; align-items:stretch; margin:4px 0 16px;
  background:var(--surface); border:1px solid var(--line); border-radius:var(--r);
  box-shadow:var(--shadow); overflow:hidden;
}
.dm-strip-item{
  flex:1 1 170px; min-width:0; padding:12px 18px; border-left:1px solid var(--line);
  display:flex; flex-direction:column; justify-content:center; gap:3px;
}
.dm-strip-item:first-child{ border-left:0; }
.stMarkdown p.dm-label, p.dm-label{ margin:0; font-size:11px; font-weight:600; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); line-height:1.4; }
.stMarkdown p.dm-value, p.dm-value{ margin:0; font-size:15px; font-weight:700; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.4; }

.dm-metric{
  background:var(--surface); border:1px solid var(--line); border-radius:var(--r);
  box-shadow:var(--shadow); padding:10px 16px;
}
.dm-metric p.dm-label{ margin-bottom:2px; }
.stMarkdown .dm-metric p.dm-value{ font-size:19px; letter-spacing:-.3px; }

.score-high, .score-mid, .score-low{
  display:inline-flex; align-items:center; gap:7px; padding:4px 14px; border-radius:999px;
  font-weight:700; font-size:15px; border:1px solid transparent;
}
.score-high::before, .score-mid::before, .score-low::before{ content:""; width:8px; height:8px; border-radius:50%; background:currentColor; }
.score-high{ background:var(--pos-bg);  color:var(--pos);  border-color:var(--pos-line); }
.score-mid { background:var(--warn-bg); color:var(--warn); border-color:var(--warn-line); }
.score-low { background:var(--neg-bg);  color:var(--neg);  border-color:var(--neg-line); }
"""


def inject_theme() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
