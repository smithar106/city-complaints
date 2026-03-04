#!/usr/bin/env python3
"""
City Complaint Analyzer
Searches Google News for city service complaints and classifies them
using Claude API. No API keys needed except Anthropic.

Usage:
  python3 main.py dallas
  python3 main.py "new york"
  python3 main.py --all                    # all cities from cities.csv
  python3 main.py --all cities.csv 50      # top 50 cities only
"""

import os, json, time, sys, csv, re, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from collections import defaultdict
from email.utils import parsedate_to_datetime
import requests
import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Categories ────────────────────────────────────────────────────────────────
CATEGORIES = [
    "Pothole / Road Damage",
    "Sewer / Drainage",
    "Downed Tree / Debris",
    "Street Lighting",
    "Trash / Illegal Dumping",
    "Graffiti / Vandalism",
    "Noise Complaint",
    "Parks / Public Spaces",
    "Water / Utilities",
    "Public Safety / Crime",
    "Sidewalk / Curb Damage",
    "Abandoned Vehicle",
    "Other City Service",
]

# Search terms that surface citizen complaint articles
SEARCH_QUERIES = [
    "{city} city pothole complaint residents",
    "{city} city sewer flooding complaint",
    "{city} trash pickup complaint residents",
    "{city} street light outage complaint",
    "{city} city services complaint residents",
    "{city} abandoned vehicle complaint",
    "{city} city park maintenance complaint",
    "{city} water main break residents",
    "{city} sidewalk repair complaint",
    "{city} graffiti vandalism complaint",
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/122.0.0.0 Safari/537.36"
})


# ── Fetch Google News RSS ─────────────────────────────────────────────────────
def fetch_articles(city_name, days=365):
    cutoff = datetime.now() - timedelta(days=days)
    seen   = set()
    articles = []

    for query_tpl in SEARCH_QUERIES:
        query = query_tpl.format(city=city_name)
        url   = (
            f"https://news.google.com/rss/search"
            f"?q={requests.utils.quote(query)}"
            f"&hl=en-US&gl=US&ceid=US:en"
        )
        try:
            resp = SESSION.get(url, timeout=12)
            if resp.status_code != 200:
                continue
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                title   = (item.findtext("title") or "").strip()
                link    = (item.findtext("link")  or "").strip()
                pub_str = (item.findtext("pubDate") or "").strip()
                desc    = (item.findtext("description") or "").strip()
                # Strip HTML tags from description
                desc = re.sub(r"<[^>]+>", " ", desc).strip()

                if not title or link in seen:
                    continue

                # Date filter
                try:
                    pub_dt = parsedate_to_datetime(pub_str).replace(tzinfo=None)
                    if pub_dt < cutoff:
                        continue
                except Exception:
                    pass

                seen.add(link)
                text = f"{title}. {desc}"[:600]
                articles.append({
                    "id":    link,
                    "text":  text,
                    "url":   link,
                    "date":  pub_str[:16],
                    "score": 0,
                })
        except Exception as e:
            print(f" [fetch error: {e}]", end="", flush=True)

        time.sleep(0.4)   # polite delay between queries

    return articles


# ── Claude classification ─────────────────────────────────────────────────────
def classify_batch(client, articles):
    numbered = "\n\n".join(
        f"[{i+1}] {a['text'][:400]}" for i, a in enumerate(articles)
    )
    cats = "\n".join(f"   - {c}" for c in CATEGORIES)

    prompt = f"""You are analyzing news articles about a city to find citizen complaints about city services or quality-of-life issues.

For each numbered article determine:
1. Is it reporting a citizen complaint about a city service or quality-of-life issue? (true/false)
2. If yes, which category best fits:
{cats}
3. A short quote or summary (max 15 words) capturing the complaint.

Respond ONLY with a valid JSON array, one object per article, in order:
[{{"complaint": true, "category": "Category Name", "quote": "short summary"}}, ...]

Articles:
{numbered}"""

    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        text  = resp.content[0].text.strip()
        start = text.index("[")
        end   = text.rindex("]") + 1
        result = json.loads(text[start:end])
        while len(result) < len(articles):
            result.append({"complaint": False, "category": None, "quote": None})
        return result
    except Exception as e:
        print(f" [classify error: {e}]", end="")
        return [{"complaint": False, "category": None, "quote": None}] * len(articles)


# ── Analyze one city ──────────────────────────────────────────────────────────
def analyze_city(city_name, days=365, client=None):
    if client is None:
        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    print(f"\n{'='*62}")
    print(f"  {city_name.title()}")
    print(f"{'='*62}")
    print(f"  Searching Google News ({len(SEARCH_QUERIES)} queries)...", end=" ", flush=True)

    articles = fetch_articles(city_name, days=days)
    print(f"{len(articles)} articles found")

    if not articles:
        print("  No articles found — skipping.")
        return None

    print(f"  Classifying with Claude...")

    BATCH   = 20
    results = []
    for i in range(0, len(articles), BATCH):
        batch = articles[i:i+BATCH]
        print(f"    batch {i//BATCH+1}/{(len(articles)-1)//BATCH+1}...", end=" ", flush=True)
        cls = classify_batch(client, batch)
        for art, c in zip(batch, cls):
            results.append({**art, **c})
        print("✓")
        time.sleep(0.2)

    complaints  = [r for r in results if r.get("complaint")]
    by_category = defaultdict(list)
    for c in complaints:
        by_category[c.get("category") or "Other City Service"].append(c)

    report = build_report(city_name, days, len(articles), complaints, by_category)

    os.makedirs("reports", exist_ok=True)
    slug  = city_name.lower().replace(' ', '_')
    fname = f"reports/{slug}.txt"
    with open(fname, "w") as f:
        f.write(report)

    # Save structured JSON for dashboard
    sorted_cats = sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True)
    json_data = {
        "city":             city_name.title(),
        "slug":             slug,
        "generated":        datetime.now().strftime("%Y-%m-%d"),
        "days":             days,
        "total_articles":   len(articles),
        "total_complaints": len(complaints),
        "complaint_rate":   round(len(complaints) / len(articles) * 100, 1) if articles else 0,
        "categories": {
            cat: {
                "count":    len(items),
                "pct":      round(len(items) / len(complaints) * 100, 1) if complaints else 0,
                "samples":  [
                    {"quote": i.get("quote",""), "url": i.get("url",""), "date": i.get("date","")}
                    for i in sorted(items, key=lambda x: x.get("score",0), reverse=True)[:5]
                ],
            }
            for cat, items in sorted_cats
        },
    }
    with open(f"reports/{slug}.json", "w") as f:
        json.dump(json_data, f, indent=2)

    print(report)
    print(f"\n  Saved → {fname}")
    return fname


# ── Report builder ────────────────────────────────────────────────────────────
def build_report(city_name, days, total, complaints, by_category):
    n    = len(complaints)
    rate = f"{n/total*100:.1f}%" if total else "—"
    lines = [
        "",
        "=" * 62,
        f"  CITY COMPLAINT ANALYSIS — {city_name.upper()}",
        f"  Generated : {datetime.now().strftime('%B %d, %Y')}",
        f"  Source    : Google News (past {days} days)",
        "=" * 62,
        "",
        f"  Articles analyzed : {total:,}",
        f"  Complaints found  : {n:,}",
        f"  Complaint rate    : {rate}",
        "",
        f"  {'CATEGORY':<32} {'COUNT':>5}  {'PCT':>6}  VOLUME",
        f"  {'─'*56}",
    ]

    sorted_cats = sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True)
    for cat, items in sorted_cats:
        pct = len(items) / n * 100 if n else 0
        bar = "█" * max(1, int(pct / 3))
        lines.append(f"  {cat:<32} {len(items):>5}  {pct:>5.1f}%  {bar}")

    lines += ["", f"  {'─'*56}", "  SAMPLE COMPLAINTS BY CATEGORY", f"  {'─'*56}"]
    for cat, items in sorted_cats[:8]:
        lines.append(f"\n  [{cat}]")
        top = sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:3]
        for item in top:
            quote = item.get("quote") or item["text"][:80]
            lines.append(f'    • "{quote}"')
            lines.append(f'      {item["url"]}')

    lines += [
        "",
        "=" * 62,
        "  PITCH SUMMARY",
        "=" * 62,
        "",
        f"  In the past year, Google News surfaced {n:,} news articles reporting",
        f"  citizen complaints about city services in {city_name.title()}.",
        "",
        "  Top issues:",
    ]
    for cat, items in sorted_cats[:3]:
        pct = len(items) / n * 100 if n else 0
        lines.append(f"    • {cat}: {pct:.0f}% of complaints ({len(items):,} articles)")

    lines += [
        "",
        "  MyCity311 turns scattered public complaints into structured,",
        "  trackable reports — giving city staff real data and giving residents",
        "  a direct, frictionless line to their local government.",
        "",
    ]
    return "\n".join(lines)


# ── Batch: all cities ─────────────────────────────────────────────────────────
def run_all_from_csv(csv_path, days=365, max_cities=None):
    cities = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            city = row.get("city", "").strip()
            if city:
                cities.append(city)
    if max_cities:
        cities = cities[:max_cities]

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    print(f"\nRunning analysis for {len(cities)} cities...")

    summary = []
    for i, city in enumerate(cities, 1):
        print(f"\n[{i}/{len(cities)}]", end="")
        try:
            fname = analyze_city(city, days=days, client=client)
            summary.append({"city": city, "status": "ok", "report": fname or ""})
        except Exception as e:
            print(f"  ERROR: {e}")
            summary.append({"city": city, "status": f"error: {e}", "report": ""})
        time.sleep(1)

    with open("reports/_summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["city", "status", "report"])
        w.writeheader()
        w.writerows(summary)
    print(f"\nDone. Summary → reports/_summary.csv")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    if args[0] == "--all":
        csv_path = args[1] if len(args) > 1 else "cities.csv"
        max_c    = int(args[2]) if len(args) > 2 else None
        run_all_from_csv(csv_path, max_cities=max_c)

    elif args[0] == "--csv":
        csv_path = args[1]
        max_c    = int(args[2]) if len(args) > 2 else None
        run_all_from_csv(csv_path, max_cities=max_c)

    else:
        city = " ".join(args)
        analyze_city(city)
