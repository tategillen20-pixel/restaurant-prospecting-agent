# POS Business Intelligence Agent

A runnable MVP for POS sales prospecting.

## What it does

### Existing Business Intel
Enter a business and market. The app researches public sources and returns:

- Owner / decision-maker clues
- Number of locations
- Rough revenue estimate
- Likely current POS system
- Online ordering and delivery platforms
- Website technology
- Review/pain-point clues
- Years in business
- Expansion signals
- Social media links
- Competitors
- Opportunity score
- Recommended sales pitch
- Source links

### New Opening Scanner
Enter a city/market and it searches for opening signals such as:

- Restaurants opening soon
- New retail openings
- Permits/construction clues
- Coffee shops/breweries opening soon

It outputs a CSV-friendly table of prospects.

## Install

1. Install Python 3.10+
2. Open Terminal / PowerShell in this folder
3. Run:

```bash
pip install -r requirements.txt
```

4. Optional but recommended: copy `.env.example` to `.env` and add your SerpAPI key:

```bash
SERPAPI_KEY=your_key_here
```

5. Start the app:

```bash
streamlit run app.py
```

## Notes

- This uses public web information only.
- POS detection is a best-effort inference based on website source code, online ordering links, delivery providers, snippets, job posts, and public clues.
- Always verify findings before outreach.
- Revenue estimates are rough prospecting estimates, not verified financial data.

## Suggested sales workflow

1. Run the New Opening Scanner every Monday for Kansas City.
2. Export the CSV.
3. Pick the highest-fit prospects.
4. Run each through Existing Business Intel.
5. Use the pitch bullets and source links to prepare outreach.
