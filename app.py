import os
import re
import json
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
from bs4 import BeautifulSoup
import streamlit as st
from dotenv import load_dotenv
from openai import OpenAI


load_dotenv(override=True)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


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


def extract_restaurants_with_ai(articles: List[Dict]) -> List[Dict]:
    if not client:
        st.error("Missing OPENAI_API_KEY in .env")
        return []

    results = []
    seen = set()

    progress = st.progress(0)

    for i, article in enumerate(articles[:35]):
        progress.progress((i + 1) / 35)

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
    revenue = estimate_revenue_with_ai({
    "business": name,
    "city": city,
    "address": address,
    "phone": phone,
    "website": website,
    "rating": rating,
    "reviews": reviews,
    "google_status": google_status,
    "years_open": years_opened if "years_opened" in locals() else "Unknown",
})

    html = fetch_website_html(website)
    website_text = BeautifulSoup(html, "html.parser").get_text(" ") if html else ""

    pos = detect_pos_system(html)
    contact = extract_contact_info(html)
    years_opened = estimate_years_opened(website_text)

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


def scan_new_openings(city: str) -> pd.DataFrame:
    articles = fetch_google_news(city)
    st.write(f"Found {len(articles)} opening-related articles.")

    candidates = extract_restaurants_with_ai(articles)
    st.write(f"Extracted {len(candidates)} possible restaurant prospects.")

    rows = []

    progress = st.progress(0)

    for i, candidate in enumerate(candidates):
        progress.progress((i + 1) / max(len(candidates), 1))

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

    return df


def main():
    st.set_page_config(page_title="Restaurant Prospecting Agent", layout="wide")

    col1, col2 = st.columns([1, 5])

    with col1:
        st.image("integsolu logo.png", width=120)

    with col2:
        st.title("Restaurant Prospecting Agent")
        st.caption("Built for Integrity Solutions")

    with st.sidebar:
        st.header("Setup")

        if OPENAI_API_KEY:
            st.success("OpenAI key loaded")
        else:
            st.error("Missing OpenAI key")

        if GOOGLE_MAPS_API_KEY:
            st.success("Google Maps key loaded")
        else:
            st.warning("Missing Google Maps key")

    tab1, tab2 = st.tabs(["New Opening Scanner", "Existing Business Intel"])

    with tab1:
        city = st.text_input("City / Market", value="Kansas City", key="opening_city")

        if st.button("Find Opening Prospects", type="primary"):
            with st.spinner("Scanning restaurant openings..."):
                df = scan_new_openings(city)

            if df.empty:
                st.warning("No prospects found. Try a broader city name like 'Kansas City, MO'.")
            else:
                st.success(f"Found {len(df)} prospects.")
                st.dataframe(df, use_container_width=True)

                st.download_button(
                    "Download CSV",
                    data=df.to_csv(index=False),
                    file_name="restaurant_opening_prospects.csv",
                    mime="text/csv",
                )

    with tab2:
        business = st.text_input("Business name", placeholder="Joe's Kansas City BBQ")
        city2 = st.text_input("City / Market", value="Kansas City", key="existing_city")

        if st.button("Research Existing Business", type="primary"):
            if not business:
                st.warning("Enter a business name first.")
            else:
                with st.spinner("Researching business..."):
                    intel = collect_existing_business_intel(business, city2)

                st.subheader(intel["Business"])

                c1, c2, c3 = st.columns(3)
                c1.metric("POS Guess", intel["Current POS Guess"])
                c2.metric("POS Confidence", intel["POS Confidence"])
                c3.metric("Google Rating", intel["Rating"])

                st.table(pd.DataFrame(intel.items(), columns=["Field", "Finding"]))

                st.download_button(
                    "Download Business Intel JSON",
                    data=json.dumps(intel, indent=2),
                    file_name=f"{business.lower().replace(' ', '_')}_intel.json",
                    mime="application/json",
                )


if __name__ == "__main__":
    main()