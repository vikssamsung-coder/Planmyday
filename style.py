"""Design system for Plan My Day — a single CSS layer injected over native
Streamlit. Keeps all logic intact; only changes how it looks.

Theme: 'Sunrise' — deep slate ink, warm amber accent, soft warm canvas, Inter
type, real cards with quiet shadows. Targets stable data-testid / data-baseweb
hooks so it survives Streamlit version drift.
"""

import streamlit as st

_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

:root{
  --ink:#1B2733; --ink-soft:#5C6B7A; --line:#E7E3DC;
  --canvas:#F6F4F0; --card:#FFFFFF;
  --slate:#2D4A5E; --slate-d:#22394A;
  --amber:#E8833A; --amber-d:#D2702A;
  --good:#2E9E6B; --warn:#E2A13B; --bad:#D9544D;
  --radius:14px; --shadow:0 1px 2px rgba(27,39,51,.04),0 6px 20px rgba(27,39,51,.06);
}

/* canvas + base type */
.stApp{ background:
  radial-gradient(1200px 400px at 100% -5%, #FBEFE2 0%, rgba(251,239,226,0) 60%),
  var(--canvas); }
html, body, [class*="css"]{ font-family:'Inter',system-ui,sans-serif; color:var(--ink); }
[data-testid="stHeader"]{ background:transparent; }
[data-testid="stToolbar"]{ display:none; }
#MainMenu, footer{ visibility:hidden; }
.block-container{ padding-top:2.2rem; padding-bottom:4rem; max-width:1200px; }

/* headings */
h1,h2,h3,h4{ font-family:'Inter'; letter-spacing:-.02em; color:var(--ink); font-weight:700; }
h3{ font-size:1.35rem; } h4{ font-size:1.08rem; }
.stCaption, [data-testid="stCaptionContainer"]{ color:var(--ink-soft) !important; }
hr{ border-color:var(--line); margin:1.1rem 0; }

/* cards — bordered containers */
[data-testid="stVerticalBlockBorderWrapper"]{
  background:var(--card); border:1px solid var(--line) !important;
  border-radius:var(--radius); box-shadow:var(--shadow);
  padding:6px 4px;
}
[data-testid="stVerticalBlockBorderWrapper"]:hover{ border-color:#DAD4CA !important; }

/* buttons */
.stButton > button{
  border-radius:10px; border:1px solid var(--line); background:var(--card);
  color:var(--ink); font-weight:600; padding:.5rem 1rem; transition:all .15s ease;
  box-shadow:0 1px 0 rgba(27,39,51,.02);
}
.stButton > button:hover{ border-color:#CDC6BB; background:#FBFAF8; transform:translateY(-1px); }
.stButton > button[kind="primary"], .stFormSubmitButton > button{
  background:linear-gradient(180deg,var(--amber),var(--amber-d));
  border:none; color:#fff; box-shadow:0 2px 10px rgba(210,112,42,.30);
}
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button:hover{
  filter:brightness(1.04); transform:translateY(-1px);
}

/* inputs */
[data-baseweb="input"], [data-baseweb="textarea"], [data-baseweb="select"] > div{
  border-radius:10px !important; border-color:var(--line) !important; background:var(--card) !important;
}
[data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within{
  border-color:var(--amber) !important; box-shadow:0 0 0 3px rgba(232,131,58,.14) !important;
}
.stTextInput label, .stNumberInput label, .stTextArea label, .stSelectbox label{
  font-weight:600; font-size:.82rem; color:var(--ink-soft);
}

/* forms */
[data-testid="stForm"]{
  border:1px solid var(--line); border-radius:var(--radius);
  background:linear-gradient(180deg,#FFFFFF, #FCFBF9); box-shadow:var(--shadow);
}

/* tabs (native st.tabs) */
.stTabs [data-baseweb="tab-list"]{ gap:4px; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"]{
  border-radius:8px 8px 0 0; padding:8px 16px; font-weight:600; color:var(--ink-soft);
}
.stTabs [aria-selected="true"]{ color:var(--slate); background:#EFEAE2; }

/* checkboxes (steps) */
[data-baseweb="checkbox"] [data-testid="stMarkdownContainer"]{ color:var(--ink); }

/* dataframes / tables */
[data-testid="stDataFrame"]{ border:1px solid var(--line); border-radius:12px; overflow:hidden; }

/* expanders */
[data-testid="stExpander"]{ border:1px solid var(--line) !important; border-radius:12px; background:var(--card); box-shadow:var(--shadow); }
[data-testid="stExpander"] summary{ font-weight:600; }

/* alerts a touch softer */
[data-testid="stAlert"]{ border-radius:12px; }

/* the brand wordmark (set via st.markdown with class) */
.pmd-brand{ display:flex; align-items:center; gap:.5rem; font-weight:800;
  font-size:1.5rem; letter-spacing:-.03em; }
.pmd-brand .dot{ width:30px;height:30px;border-radius:9px;
  background:linear-gradient(135deg,var(--amber),#F2B36B); display:inline-flex;
  align-items:center; justify-content:center; box-shadow:0 3px 10px rgba(232,131,58,.35); }

/* nav pills (streamlit-option-menu container) */
.nav-wrap{ margin-top:.2rem; }

/* close-my-day prominent bar */
.closeday-bar{
  margin:26px auto 6px; max-width:760px; text-align:center;
  background:linear-gradient(135deg,#2D4A5E,#3C6378);
  color:#fff; font-weight:700; font-size:1.15rem; letter-spacing:-.01em;
  padding:16px 22px; border-radius:14px;
  box-shadow:0 6px 22px rgba(45,74,94,.28);
}
.closeday-bar .sub{ display:block; font-weight:500; font-size:.82rem; opacity:.85; margin-top:3px; }

/* big designed header banner with daily sales quote */
.pmd-hero{
  position:relative; overflow:hidden;
  background:linear-gradient(120deg,#1B2C3A 0%, #2D4A5E 45%, #B4612A 130%);
  border-radius:20px; padding:24px 30px; margin:4px 0 10px;
  box-shadow:0 10px 34px rgba(27,44,58,.28);
}
.pmd-hero::after{
  content:""; position:absolute; right:-40px; top:-60px; width:260px; height:260px;
  background:radial-gradient(circle, rgba(242,179,107,.32), transparent 70%); pointer-events:none;
}
.pmd-hero .brand{ display:flex; align-items:center; gap:.6rem;
  font-size:2rem; font-weight:800; letter-spacing:-.035em; color:#fff; line-height:1; }
.pmd-hero .brand .dot{ width:42px;height:42px;border-radius:12px;
  background:linear-gradient(135deg,#F2B36B,#E8833A); display:inline-flex;
  align-items:center; justify-content:center; font-size:1.4rem;
  box-shadow:0 4px 14px rgba(232,131,58,.45); }
.pmd-hero .tagline{ color:#CFE0EA; font-size:.86rem; margin-top:4px; font-weight:500; }
.pmd-hero .quote{ margin-top:16px; padding-left:14px; border-left:3px solid #F2B36B;
  color:#fff; font-size:1.12rem; font-weight:600; font-style:italic; letter-spacing:-.01em;
  max-width:80%; line-height:1.45; }
.pmd-hero .quote .by{ display:block; font-style:normal; font-weight:600; font-size:.74rem;
  letter-spacing:.06em; text-transform:uppercase; color:#F2B36B; margin-top:8px; }
</style>
"""


def inject():
    st.markdown(_CSS, unsafe_allow_html=True)


# option_menu styles tuned to the theme — import and pass to option_menu(styles=...)
NAV_STYLES = {
    "container": {"padding": "6px 0", "background-color": "transparent",
                  "display": "flex", "flex-wrap": "nowrap"},
    "icon": {"font-size": "0.9rem", "color": "#5C6B7A"},
    "nav-link": {"font-size": "0.9rem", "font-weight": "600", "color": "#5C6B7A",
                 "padding": "9px 16px", "margin": "0 3px", "border-radius": "10px",
                 "white-space": "nowrap", "--hover-color": "#EFEAE2"},
    "nav-link-selected": {"background-color": "#2D4A5E", "color": "white",
                          "font-weight": "700"},
}
