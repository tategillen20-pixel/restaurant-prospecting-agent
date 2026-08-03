import os
import re
import json
from dataclasses import dataclass
from datetime import date
from typing import Dict, List
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI

st.set_page_config(
    page_title="Restaurant Prospecting Agent",
    page_icon="🍽️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

load_dotenv(override=True)


def load_secret(name: str) -> str:
    """Load local environment values first, then Streamlit Cloud secrets."""
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except (FileNotFoundError, KeyError):
        return ""


OPENAI_API_KEY = load_secret("OPENAI_API_KEY")
GOOGLE_MAPS_API_KEY = load_secret("GOOGLE_MAPS_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


def inject_custom_css():
    st.markdown("""
    <style>
    :root { --navy:#182230; --charcoal:#344054; --muted:#667085; --border:#E4E7EC; --red:#E52B2F; }
    .stApp { background:#F5F7FA; color:var(--charcoal); }
    .block-container { max-width:1500px; padding:2rem 2.25rem 3rem; }
    h1,h2,h3 { color:var(--navy); letter-spacing:-.02em; }
    p, label, [data-testid="stCaptionContainer"] { color:var(--muted); }
    [data-testid="stHeader"] { background:transparent; }
    .app-header { display:flex; align-items:center; gap:22px; background:#fff; border:1px solid var(--border); border-radius:14px; padding:20px 24px; box-shadow:0 1px 3px rgba(16,24,40,.05); margin-bottom:18px; }
    .app-header img { width:118px; height:auto; display:block; }
    .header-divider { width:1px; height:58px; background:var(--border); }
    .header-title { font-size:34px; line-height:1.1; font-weight:750; color:var(--navy); }
    .header-subtitle { font-size:15px; margin-top:6px; color:var(--charcoal); }
    .header-kicker { font-size:12px; margin-top:4px; color:var(--muted); }
    .section-card,.results-card,.detail-card,.overview-card { background:#fff; border:1px solid var(--border); border-radius:14px; padding:22px 24px; box-shadow:0 1px 3px rgba(16,24,40,.04); }
    .section-card { margin:8px 0 18px; }
    .card-title { color:var(--navy); font-size:20px; font-weight:700; margin-bottom:4px; }
    .card-description { color:var(--muted); font-size:14px; margin-bottom:14px; }
    .info-callout { background:#EEF2F6; border:1px solid #D8DEE7; border-radius:10px; padding:15px 16px; color:var(--charcoal); font-size:13px; line-height:1.55; }
    .metric-card { min-height:116px; background:#fff; border:1px solid var(--border); border-radius:12px; padding:16px 18px; box-shadow:0 1px 2px rgba(16,24,40,.03); }
    .metric-label,.detail-label { color:var(--muted); font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.04em; }
    .metric-value { color:var(--navy); font-size:25px; font-weight:750; margin:5px 0 2px; overflow-wrap:anywhere; }
    .metric-description,.detail-description { color:#98A2B3; font-size:12px; }
    .detail-row { padding:10px 0; border-bottom:1px solid #F0F2F5; }
    .detail-row:last-child { border-bottom:0; }
    .detail-value { color:var(--navy); font-size:14px; font-weight:550; margin-top:3px; overflow-wrap:anywhere; }
    .detail-value a { color:#175CD3; text-decoration:none; }
    .footer-note { color:#98A2B3; font-size:12px; margin:14px 2px; }
    .stButton>button[kind="primary"], .stDownloadButton>button { border-radius:9px; min-height:42px; font-weight:650; }
    .stButton>button[kind="primary"] { background:var(--red); border-color:var(--red); }
    .stButton>button[kind="primary"]:hover { background:#C92025; border-color:#C92025; }
    .stTextInput input,.stSelectbox>div>div,.stNumberInput input { border-radius:9px; border-color:#D0D5DD; min-height:42px; }
    .stTabs [data-baseweb="tab-list"] { gap:26px; border-bottom:1px solid var(--border); }
    .stTabs [data-baseweb="tab"] { color:var(--charcoal); font-weight:600; padding:12px 4px; }
    .stTabs [aria-selected="true"] { color:var(--red) !important; }
    .stTabs [data-baseweb="tab-highlight"] { background:var(--red); }
    [data-testid="stDataFrame"] { border:1px solid var(--border); border-radius:10px; overflow:hidden; }
    @media(max-width:720px){ .block-container{padding:1rem}.app-header{align-items:flex-start;flex-direction:column;gap:10px}.header-divider{width:100%;height:1px}.header-title{font-size:28px}.metric-card{min-height:auto} }
    </style>
    """, unsafe_allow_html=True)


def format_display_value(value, fallback="Not found"):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    cleaned = str(value).strip()
    return fallback if cleaned.lower() in {"", "unknown", "none", "null", "n/a", "not specified"} else cleaned


def _currency_number(value):
    if isinstance(value, (int, float)) and not pd.isna(value):
        return float(value)
    match = re.search(r"([\d,.]+)\s*([kKmM]?)", str(value or "").replace("$", ""))
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return number * ({"k": 1_000, "m": 1_000_000}.get(match.group(2).lower(), 1))


def format_compact_currency(value):
    number = _currency_number(value)
    if number is None:
        return "Not available"
    if abs(number) >= 1_000_000:
        compact = f"{number / 1_000_000:.2f}".rstrip("0").rstrip(".")
        return f"${compact}M"
    if abs(number) >= 1_000:
        return f"${number / 1_000:.0f}K"
    return f"${number:,.0f}"


def format_revenue_range(value, suffix=""):
    if isinstance(value, dict):
        values = [value.get(k) for k in ("low", "high")]
    elif isinstance(value, (list, tuple)):
        values = list(value[:2])
    else:
        values = re.findall(r"\$?[\d,.]+\s*[kKmM]?", str(value or ""))[:2]
    if len(values) < 2:
        return format_display_value(value, "Not available")
    return f"{format_compact_currency(values[0])} – {format_compact_currency(values[1])}{suffix}"


def sanitize_filename_component(value):
    return re.sub(r"[^a-z0-9]+", "_", str(value).lower()).strip("_") or "market"


def determine_result_note(row):
    status = str(row.get("Google Status", "")).upper()
    opening = str(row.get("Opening Status", "")).lower()
    timeline = str(row.get("Opening Timeline", "")).lower().strip()
    if "CLOSED_PERMANENTLY" in status or "PERMANENTLY CLOSED" in status:
        return "Permanently closed"
    if "OPERATIONAL" in status:
        return "Already operational"
    if timeline in {"", "unknown", "not specified", "none", "n/a"}:
        return "Timeline unclear"
    years = [int(y) for y in re.findall(r"20\d{2}", timeline)]
    if years and max(years) < pd.Timestamp.today().year:
        return "Possibly outdated"
    if any(term in opening for term in ("coming soon", "announced", "under construction", "hiring")):
        return "Future opening"
    if "new location" in opening:
        return "Current lead"
    return "Current lead"


def prepare_opening_results_dataframe(df):
    prepared = df.copy()
    prepared = prepared.drop(columns=["Rank"], errors="ignore")
    if "Opportunity Score" in prepared:
        prepared["Opportunity Score"] = pd.to_numeric(prepared["Opportunity Score"], errors="coerce")
        prepared = prepared.sort_values("Opportunity Score", ascending=False, na_position="last")
    prepared = prepared.reset_index(drop=True)
    prepared.insert(0, "Rank", range(1, len(prepared) + 1))
    return prepared


def render_metric_card(label, value, description=""):
    st.markdown(f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div><div class="metric-description">{description}</div></div>', unsafe_allow_html=True)


def render_detail_row(label, value, link=False):
    shown = format_display_value(value)
    body = f'<a href="{shown}" target="_blank">Open website</a>' if link and shown.startswith(("http://", "https://")) else shown
    st.markdown(f'<div class="detail-row"><div class="detail-label">{label}</div><div class="detail-value">{body}</div></div>', unsafe_allow_html=True)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def fetch_google_news(city: str) -> List[Dict]:
    queries = [
        f"new restaurant opening {city}",
        f"restaurant coming soon {city}",
        f"restaurant grand opening {city}",
        f"new bar opening {city}",
        f"new coffee shop opening {city}",
        f"new brewery opening {city}",
        f"restaurant new location {city}",
        f"restaurant now hiring {city}",
    ]

    articles = []
    seen = set()

    for query in queries:
        url = "https://news.google.com/rss/search?q=" + quote_plus(query)
        feed = feedparser.parse(url)

        for entry in feed.entries:
            title = clean_text(getattr(entry, "title", ""))
            summary = clean_text(getattr(entry, "summary", ""))
            link = getattr(entry, "link", "")

            key = title.lower()

            if key in seen:
                continue

            seen.add(key)

            articles.append({
                "title": title,
                "summary": summary,
                "link": link,
            })

    return articles[:50]


def extract_restaurants_with_ai(articles: List[Dict], progress_callback=None) -> List[Dict]:
    if not client:
        st.error("Missing OPENAI_API_KEY in .env")
        return []

    results = []
    seen = set()

    for i, article in enumerate(articles[:35]):
        if progress_callback:
            progress_callback((i + 1) / max(min(len(articles), 35), 1))

        prompt = f"""
You are helping a POS sales rep find restaurant prospects.

Read this ONE article and extract real named businesses only.

Return a business only if it appears to be:
- opening soon
- newly announced
- under construction
- hiring before opening
- soft-opening
- opening a new location

Do NOT return generic phrases like:
- new restaurant
- pizza restaurant
- burger chain
- popular restaurant
- brewery
- coffee shop
- chef
- location

Return valid JSON only.

If no valid business is found, return [].

Format:
[
  {{
    "restaurant_name": "...",
    "opening_status": "coming soon | announced | under construction | hiring | soft opening | new location | needs review",
    "opening_timeline": "...",
    "evidence": "short reason this is a prospect"
  }}
]

TITLE:
{article["title"]}

SUMMARY:
{article["summary"]}

SOURCE:
{article["link"]}
"""

        try:
            response = client.responses.create(
                model="gpt-4.1-mini",
                input=prompt,
            )

            text = response.output_text.strip()
            text = text.replace("```json", "").replace("```", "").strip()
            data = json.loads(text)

            if not isinstance(data, list):
                continue

            for item in data:
                name = clean_text(item.get("restaurant_name", ""))

                if not name:
                    continue

                low = name.lower()

                banned = [
                    "restaurant", "new restaurant", "pizza restaurant",
                    "burger restaurant", "coffee shop", "brewery",
                    "popular restaurant", "chef", "location",
                ]

                if low in banned:
                    continue

                if len(name.split()) > 7:
                    continue

                if low in seen:
                    continue

                seen.add(low)

                results.append({
                    "name": name,
                    "status": clean_text(item.get("opening_status", "needs review")),
                    "timeline": clean_text(item.get("opening_timeline", "Unknown")),
                    "evidence": clean_text(item.get("evidence", "")),
                    "source": article["link"],
                })

        except Exception:
            continue

    return results[:20]


def google_places_lookup(name: str, city: str) -> Dict:
    if not GOOGLE_MAPS_API_KEY:
        return {}

    url = "https://places.googleapis.com/v1/places:searchText"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": (
            "places.displayName,"
            "places.formattedAddress,"
            "places.websiteUri,"
            "places.nationalPhoneNumber,"
            "places.businessStatus,"
            "places.rating,"
            "places.userRatingCount"
        ),
    }

    body = {
        "textQuery": f"{name} {city}",
        "maxResultCount": 1,
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=15)

        if r.status_code != 200:
            return {}

        places = r.json().get("places", [])

        if not places:
            return {}

        return places[0]

    except Exception:
        return {}


def opportunity_score(candidate: Dict, place: Dict) -> float:
    score = 7.0

    status = candidate.get("status", "").lower()

    if "coming soon" in status:
        score += 1.5
    if "under construction" in status:
        score += 1.5
    if "hiring" in status:
        score += 1.0
    if "new location" in status:
        score += 0.8

    if place.get("websiteUri"):
        score += 0.5
    if place.get("nationalPhoneNumber"):
        score += 0.5

    reviews = place.get("userRatingCount", 0)

    try:
        reviews = int(reviews)
        if reviews < 50:
            score += 0.5
        if reviews > 500:
            score -= 1.0
    except Exception:
        pass

    return round(min(max(score, 1), 10), 1)

POS_SIGNATURES = {
    "Toast": ["toasttab", "order.toasttab.com", "toast online ordering", "toast pos"],
    "Square": ["squareup", "square.site", "checkout.square.site", "square online"],
    "Clover": ["clover.com", "clover pos", "clover online ordering"],
    "Shopify POS": ["shopify", "myshopify", "cdn.shopify.com"],
    "Lightspeed": ["lightspeedhq", "shoplightspeed", "lightspeed restaurant"],
    "Revel": ["revelsystems", "revel pos"],
    "NCR / Aloha": ["aloha pos", "ncr", "alohaenterprise"],
    "Oracle MICROS": ["oracle micros", "micros pos", "simphony"],
    "SpotOn": ["spoton.com", "spoton restaurant"],
    "Shift4 / SkyTab": ["shift4", "skytab"],
    "Olo": ["olo.com", "order.olo.com", "olo-order"],
    "ChowNow": ["chownow", "ordering.chownow.com"],
    "Owner.com": ["owner.com", "order.owner.com"],
    "Popmenu": ["popmenu", "popmenucloud"],
    "BentoBox": ["getbento", "bentobox"],
    "DoorDash": ["doordash.com/store", "doordashstorefront"],
    "Uber Eats": ["ubereats.com", "eats.uber.com"],
    "Grubhub": ["grubhub.com"],
}


def fetch_website_html(url: str) -> str:
    if not url or url == "Unknown":
        return ""

    try:
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
            allow_redirects=True,
        )
        return r.text[:500000]
    except Exception:
        return ""


def detect_pos_system(html: str) -> Dict:
    lower = html.lower()

    hits = {}

    for system, clues in POS_SIGNATURES.items():
        found = [clue for clue in clues if clue.lower() in lower]

        if found:
            hits[system] = found

    priority = [
        "Toast", "Square", "Clover", "SpotOn", "Shift4 / SkyTab",
        "Olo", "ChowNow", "Owner.com", "Popmenu", "BentoBox",
        "Shopify POS", "Lightspeed", "Revel", "NCR / Aloha", "Oracle MICROS"
    ]

    for system in priority:
        if system in hits:
            return {
                "system": system,
                "confidence": "High" if len(hits[system]) >= 2 else "Medium",
                "evidence": ", ".join(hits[system][:3]),
            }

    return {
        "system": "Unknown",
        "confidence": "Low",
        "evidence": "No POS clues found on website.",
    }


def extract_contact_info(html: str) -> Dict:
    text = BeautifulSoup(html, "html.parser").get_text(" ") if html else ""

    emails = sorted(set(re.findall(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        text
    )))[:5]

    phones = sorted(set(re.findall(
        r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}",
        text
    )))[:5]

    return {
        "emails": emails,
        "phones": phones,
    }


def estimate_years_opened(text: str) -> str:
    current_year = pd.Timestamp.today().year

    matches = re.findall(
        r"(?:since|est\.?|established|opened|founded)\s*(?:in)?\s*(19\d{2}|20\d{2})",
        text,
        flags=re.I,
    )

    years = [int(y) for y in matches if 1900 <= int(y) <= current_year]

    if not years:
        return "Unknown"

    year = min(years)
    return f"Since about {year} ({current_year - year} years)"


def extract_owner_with_ai(business: str, city: str, evidence: str) -> str:
    if not client:
        return "Unknown"

    prompt = f"""
Find the owner, founder, operator, general manager, or decision-maker for this business if the evidence clearly says it.

Business: {business}
City: {city}

Evidence:
{evidence[:4000]}

Return only the name/title if found.
If not found, return Unknown.
"""

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
        )

        answer = response.output_text.strip()

        if not answer:
            return "Unknown"

        return answer[:200]

    except Exception:
        return "Unknown"

def estimate_revenue_with_ai(intel_facts: Dict) -> Dict:
    if not client:
        return {
            "monthly_range": "Unknown",
            "annual_range": "Unknown",
            "confidence": "Low",
            "reasoning": "Missing OpenAI API key."
        }

    prompt = f"""
You estimate restaurant revenue for POS sales prospecting.

Use ONLY these known facts. Do not invent facts.

Known facts:
{json.dumps(intel_facts, indent=2)}

Return valid JSON only:
{{
  "monthly_range": "$X - $Y",
  "annual_range": "$X - $Y",
  "confidence": "Low | Medium | High",
  "reasoning": "brief explanation"
}}

Rules:
- Keep the revenue range reasonably narrow.
- Be conservative.
- If facts are limited, use Low confidence.
- This is an estimate, not verified financial data.
"""

    try:
        response = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
        )

        text = response.output_text.strip()
        text = text.replace("```json", "").replace("```", "").strip()

        return json.loads(text)

    except Exception as e:
        return {
            "monthly_range": "Unknown",
            "annual_range": "Unknown",
            "confidence": "Low",
            "reasoning": f"Revenue estimate failed: {e}"
        }

def collect_existing_business_intel(business: str, city: str) -> Dict:
    place = google_places_lookup(business, city)

    name = place.get("displayName", {}).get("text", business)
    address = place.get("formattedAddress", "Unknown")
    phone = place.get("nationalPhoneNumber", "Unknown")
    website = place.get("websiteUri", "Unknown")
    rating = place.get("rating", "Unknown")
    reviews = place.get("userRatingCount", "Unknown")
    google_status = place.get("businessStatus", "Unknown")
    html = fetch_website_html(website)
    website_text = BeautifulSoup(html, "html.parser").get_text(" ") if html else ""

    pos = detect_pos_system(html)
    contact = extract_contact_info(html)
    years_opened = estimate_years_opened(website_text)
    revenue = estimate_revenue_with_ai({
        "business": name, "city": city, "address": address, "phone": phone,
        "website": website, "rating": rating, "reviews": reviews,
        "google_status": google_status, "years_open": years_opened,
    })

    news_results = fetch_google_news(f"{business} {city}")
    evidence = " ".join([
        f"{a.get('title', '')} {a.get('summary', '')}"
        for a in news_results[:10]
    ]) + " " + website_text[:3000]

    owner = extract_owner_with_ai(business, city, evidence)

    return {
        "Business": name,
        "Owner / Decision Maker": owner,
        "Address": address,
        "Phone": phone,
        "Website": website,
        "Years Open": years_opened,
        "Current POS Guess": pos["system"],
        "POS Confidence": pos["confidence"],
        "POS Evidence": pos["evidence"],
        "Rating": rating,
        "Reviews": reviews,
        "Google Status": google_status,
        "Emails Found": ", ".join(contact["emails"]) if contact["emails"] else "Unknown",
        "Website Phones Found": ", ".join(contact["phones"]) if contact["phones"] else "Unknown",
        "Estimated Monthly Revenue": revenue.get("monthly_range", "Unknown"),
        "Estimated Annual Revenue": revenue.get("annual_range", "Unknown"),
        "Revenue Confidence": revenue.get("confidence", "Low"),
        "Revenue Reasoning": revenue.get("reasoning", "Unknown"),
    }


def scan_new_openings(city: str, progress_callback=None) -> pd.DataFrame:
    articles = fetch_google_news(city)
    if progress_callback:
        progress_callback(.18, "Extracting possible restaurant prospects")

    candidates = extract_restaurants_with_ai(
        articles,
        lambda value: progress_callback(.18 + value * .42, "Extracting possible restaurant prospects") if progress_callback else None,
    )

    rows = []

    for i, candidate in enumerate(candidates):
        if progress_callback:
            progress_callback(.60 + ((i + 1) / max(len(candidates), 1)) * .32, "Verifying locations and business details")

        name = candidate["name"]
        place = google_places_lookup(name, city)

        verified_name = place.get("displayName", {}).get("text", name)

        rows.append({
            "Restaurant": verified_name,
            "Opening Status": candidate.get("status", "needs review"),
            "Opening Timeline": candidate.get("timeline", "Unknown"),
            "Address": place.get("formattedAddress", "Unknown"),
            "Phone": place.get("nationalPhoneNumber", "Unknown"),
            "Website": place.get("websiteUri", "Unknown"),
            "Google Status": place.get("businessStatus", "Unknown"),
            "Rating": place.get("rating", "Unknown"),
            "Reviews": place.get("userRatingCount", "Unknown"),
            "Opportunity Score": opportunity_score(candidate, place),
            "Evidence": candidate.get("evidence", ""),
            "Source": candidate.get("source", ""),
        })

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values("Opportunity Score", ascending=False)

    df.attrs["articles_reviewed"] = len(articles)

    return df


def main():
    inject_custom_css()
    for key, default in {
        "opening_results": None, "opening_market": "", "opening_articles": 0,
        "existing_results": None, "existing_query": "",
    }.items():
        if key not in st.session_state:
            st.session_state[key] = default

    with st.container(border=True):
        logo_col, title_col = st.columns([1, 7], vertical_alignment="center")
        with logo_col:
            st.image("integsolu logo.png", width=120)
        with title_col:
            st.markdown('<div class="header-title">Restaurant Prospecting Agent</div><div class="header-subtitle">Restaurant sales intelligence and new-opening discovery</div><div class="header-kicker">Built for Integrity Solutions</div>', unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["New Opening Scanner", "Existing Business Intel"])

    with tab1:
        with st.container(border=True):
            st.markdown('<div class="card-title">Market Search</div><div class="card-description">Find restaurants that are opening soon or have recently announced a location in your target market.</div>', unsafe_allow_html=True)
            search_col, info_col = st.columns([2, 1], gap="large")
            with search_col:
                with st.form("opening_search_form"):
                    city = st.text_input("City / Market", value=st.session_state.opening_market or "Kansas City", key="opening_city")
                    opening_submit = st.form_submit_button("Find Opening Prospects", type="primary")
            with info_col:
                st.markdown('<div class="info-callout"><strong>How it works</strong><br>We scan recent news, public announcements, business listings, and other available sources to identify new restaurant opportunities.</div>', unsafe_allow_html=True)

        if opening_submit:
            if not city.strip():
                st.warning("Enter a market to begin identifying restaurant opportunities.")
            elif not OPENAI_API_KEY:
                st.error(
                    "Opening search is not configured. Add OPENAI_API_KEY to the "
                    "project's .env file, then restart Streamlit."
                )
            else:
                with st.status("Searching recent restaurant-opening sources", expanded=True) as status:
                    progress = st.progress(0, text="Searching recent restaurant-opening sources")
                    def update_progress(value, text):
                        progress.progress(min(float(value), 1.0), text=text)
                    try:
                        df = scan_new_openings(city.strip(), update_progress)
                        progress.progress(.96, text="Scoring opportunities and preparing ranked results")
                        st.session_state.opening_results = df
                        st.session_state.opening_market = city.strip()
                        st.session_state.opening_articles = df.attrs.get("articles_reviewed", 0)
                        progress.progress(1.0, text="Ranked results ready")
                        status.update(label=f"{len(df)} restaurant prospects found and ranked.", state="complete", expanded=False)
                    except Exception:
                        status.update(label="The search could not be completed. Please try again.", state="error")
                        st.session_state.opening_results = None

        source_df = st.session_state.opening_results
        if isinstance(source_df, pd.DataFrame):
            if source_df.empty:
                st.info("No matching restaurant prospects were found for this market. Try expanding the market name or searching a nearby city.")
            else:
                enriched = source_df.copy()
                enriched["Result Note"] = enriched.apply(determine_result_note, axis=1)
                scores = pd.to_numeric(enriched.get("Opportunity Score"), errors="coerce")
                metric_cols = st.columns(4)
                with metric_cols[0]: render_metric_card("Articles Reviewed", st.session_state.opening_articles, "Sources scanned")
                with metric_cols[1]: render_metric_card("Prospects Found", len(enriched), "Possible restaurant openings")
                with metric_cols[2]: render_metric_card("High-Priority Prospects", int((scores >= 8).sum()), "Score 8.0 or higher")
                with metric_cols[3]: render_metric_card("Average Opportunity Score", f"{scores.mean():.1f}" if scores.notna().any() else "Not available", "Out of 10")

                st.markdown("### Ranked prospects")
                f1, f2, f3 = st.columns([1, 1, 1])
                statuses = sorted(enriched["Opening Status"].dropna().astype(str).unique())
                notes = sorted(enriched["Result Note"].dropna().astype(str).unique())
                with f1: selected_status = st.multiselect("Opening status", statuses, key="opening_status_filter")
                with f2: selected_note = st.multiselect("Result note", notes, key="result_note_filter")
                with f3: minimum_score = st.number_input("Minimum opportunity score", 0.0, 10.0, 0.0, .5, key="minimum_score_filter")
                filtered = enriched.copy()
                if selected_status: filtered = filtered[filtered["Opening Status"].astype(str).isin(selected_status)]
                if selected_note: filtered = filtered[filtered["Result Note"].isin(selected_note)]
                filtered = filtered[pd.to_numeric(filtered["Opportunity Score"], errors="coerce").fillna(0) >= minimum_score]
                export_df = prepare_opening_results_dataframe(filtered)
                header_left, header_right = st.columns([4, 1])
                header_left.success(f"{len(export_df)} restaurant prospects found and ranked.")
                filename = f"restaurant_prospects_{sanitize_filename_component(st.session_state.opening_market)}_{date.today().isoformat()}.csv"
                header_right.download_button("Download prospect list", export_df.to_csv(index=False).encode("utf-8"), filename, "text/csv", use_container_width=True)
                display_columns = ["Rank", "Restaurant", "Opportunity Score", "Opening Status", "Opening Timeline", "Result Note", "Address", "Phone", "Website", "Google Status", "Rating", "Reviews", "Evidence"]
                display_df = export_df[[c for c in display_columns if c in export_df]].copy()
                if "Evidence" in display_df:
                    display_df["Evidence"] = display_df["Evidence"].map(lambda x: str(x) if len(str(x)) <= 160 else str(x)[:157] + "…")
                column_config = {
                    "Rank": st.column_config.NumberColumn("Rank", width="small", format="%d"),
                    "Restaurant": st.column_config.TextColumn("Restaurant", width="medium"),
                    "Opportunity Score": st.column_config.NumberColumn("Opportunity Score", width="small", format="%.1f"),
                    "Website": st.column_config.LinkColumn("Website", width="medium", display_text="Open website"),
                    "Rating": st.column_config.NumberColumn("Rating", width="small", format="%.1f"),
                    "Reviews": st.column_config.NumberColumn("Reviews", width="small", format="%d"),
                    "Evidence": st.column_config.TextColumn("Evidence", width="large"),
                    "Address": st.column_config.TextColumn("Address", width="large"),
                }
                st.dataframe(display_df, hide_index=True, use_container_width=True, height=520, column_config=column_config)
                st.markdown('<div class="footer-note">Results are based on publicly available information and may be incomplete or subject to change.</div>', unsafe_allow_html=True)
        else:
            st.info("Enter a market above to begin identifying restaurant opportunities.")

    with tab2:
        with st.container(border=True):
            st.markdown('<div class="card-title">Business Research</div><div class="card-description">Research a restaurant’s contact information, current technology, online presence, and estimated sales opportunity.</div>', unsafe_allow_html=True)
            with st.form("business_research_form"):
                bcol, ccol = st.columns([3, 2])
                with bcol: business = st.text_input("Business Name", placeholder="Joe's Kansas City BBQ")
                with ccol: city2 = st.text_input("City / Market", value="Kansas City", key="existing_city")
                business_submit = st.form_submit_button("Research Existing Business", type="primary")
        if business_submit:
            if not business.strip():
                st.warning("Enter a business name first.")
            elif not GOOGLE_MAPS_API_KEY:
                st.error(
                    "Business research is not configured. Add GOOGLE_MAPS_API_KEY "
                    "to the project's .env file, then restart Streamlit."
                )
            else:
                try:
                    with st.spinner("Researching contact, technology, and business signals..."):
                        st.session_state.existing_results = collect_existing_business_intel(business.strip(), city2.strip())
                        st.session_state.existing_query = business.strip()
                except Exception:
                    st.session_state.existing_results = None
                    st.error("We could not find enough public information for this business. Check the business name and city, then try again.")

        intel = st.session_state.existing_results
        if isinstance(intel, dict):
            with st.container(border=True):
                st.markdown(f'<div class="card-title">{format_display_value(intel.get("Business"), st.session_state.existing_query)}</div><div class="card-description">Business overview</div>', unsafe_allow_html=True)
                o1, o2, o3 = st.columns(3)
                with o1: render_detail_row("Address", intel.get("Address")); render_detail_row("Phone", intel.get("Phone"))
                with o2: render_detail_row("Website", intel.get("Website"), link=True); render_detail_row("Google Status", intel.get("Google Status"), )
                with o3: render_detail_row("Rating", intel.get("Rating")); render_detail_row("Review count", f'{int(intel["Reviews"]):,}' if str(intel.get("Reviews", "")).isdigit() else intel.get("Reviews"))
            cards = st.columns(4)
            with cards[0]: render_metric_card("Current POS Guess", format_display_value(intel.get("Current POS Guess"), "Insufficient evidence"), "Detected from public technology clues")
            with cards[1]: render_metric_card("POS Confidence", format_display_value(intel.get("POS Confidence"), "Low"), "Confidence in technology match")
            with cards[2]: render_metric_card("Google Rating", format_display_value(intel.get("Rating"), "Not available"), f'{format_display_value(intel.get("Reviews"), "No")} reviews')
            with cards[3]: render_metric_card("Estimated Annual Revenue", format_revenue_range(intel.get("Estimated Annual Revenue")), f'{format_display_value(intel.get("Revenue Confidence"), "Low")} confidence')
            st.markdown("### Research details")
            d1, d2 = st.columns(2)
            with d1:
                with st.container(border=True):
                    st.markdown("#### Contact and Location")
                    for field in ("Address", "Phone", "Website", "Owner / Decision Maker", "Emails Found", "Website Phones Found"): render_detail_row(field, intel.get(field), link=field == "Website")
                with st.container(border=True):
                    st.markdown("#### Business Performance")
                    for field in ("Rating", "Reviews", "Years Open", "Google Status"): render_detail_row(field, intel.get(field))
            with d2:
                with st.container(border=True):
                    st.markdown("#### Technology Intelligence")
                    for field in ("Current POS Guess", "POS Confidence", "POS Evidence"): render_detail_row(field, intel.get(field), )
                with st.container(border=True):
                    st.markdown("#### Revenue Estimate")
                    render_detail_row("Estimated Monthly Revenue", format_revenue_range(intel.get("Estimated Monthly Revenue"), " monthly"))
                    render_detail_row("Estimated Annual Revenue", format_revenue_range(intel.get("Estimated Annual Revenue"), " annually"))
                    for field in ("Revenue Confidence", "Revenue Reasoning"): render_detail_row(field, intel.get(field))
            st.download_button("Download Business Intel JSON", json.dumps(intel, indent=2), f"{sanitize_filename_component(st.session_state.existing_query)}_intel.json", "application/json")
            st.markdown('<div class="footer-note">Results are based on publicly available information and may be incomplete or subject to change.</div>', unsafe_allow_html=True)
        else:
            st.info("Enter a business name and market above to begin researching an existing restaurant.")


if __name__ == "__main__":
    main()
