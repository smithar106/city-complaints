"""
City Complaint Dashboard
Run:  python3 dashboard.py
Opens on http://localhost:5010
"""

import os, json, re, glob
from flask import Flask, render_template, jsonify

app = Flask(__name__)
REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

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

CAT_COLORS = {
    "Pothole / Road Damage":    "#E53E3E",
    "Sewer / Drainage":         "#805AD5",
    "Downed Tree / Debris":     "#38A169",
    "Street Lighting":          "#ECC94B",
    "Trash / Illegal Dumping":  "#DD6B20",
    "Graffiti / Vandalism":     "#E91E63",
    "Noise Complaint":          "#00BCD4",
    "Parks / Public Spaces":    "#4CAF50",
    "Water / Utilities":        "#2196F3",
    "Public Safety / Crime":    "#F44336",
    "Sidewalk / Curb Damage":   "#9C27B0",
    "Abandoned Vehicle":        "#FF5722",
    "Other City Service":       "#607D8B",
}


def parse_txt_report(path):
    """Parse a legacy .txt report into the same structure as .json."""
    txt = open(path).read()
    slug = os.path.basename(path).replace(".txt", "")

    # Header fields
    city_m      = re.search(r"CITY COMPLAINT ANALYSIS — (.+)", txt)
    gen_m       = re.search(r"Generated\s*:\s*(.+)", txt)
    total_m     = re.search(r"Articles analyzed\s*:\s*([\d,]+)", txt)
    complaints_m= re.search(r"Complaints found\s*:\s*([\d,]+)", txt)
    rate_m      = re.search(r"Complaint rate\s*:\s*([\d.]+)%", txt)

    city_name       = city_m.group(1).title() if city_m else slug.replace("_", " ").title()
    total_articles  = int(total_m.group(1).replace(",", "")) if total_m else 0
    total_complaints= int(complaints_m.group(1).replace(",", "")) if complaints_m else 0
    complaint_rate  = float(rate_m.group(1)) if rate_m else 0.0

    # Category table  "  Category Name                 COUNT   PCT%  ..."
    categories = {}
    for m in re.finditer(r"  (.+?)\s{2,}(\d+)\s+([\d.]+)%", txt):
        name = m.group(1).strip()
        if name.startswith("─") or name.startswith("CATEGORY"):
            continue
        count = int(m.group(2))
        pct   = float(m.group(3))
        if count > 0 and name not in ("Articles analyzed", "Complaints found"):
            categories[name] = {"count": count, "pct": pct, "samples": []}

    # Sample quotes per category
    current_cat = None
    for line in txt.splitlines():
        cat_m = re.match(r"\s+\[(.+?)\]", line)
        if cat_m:
            current_cat = cat_m.group(1)
        elif current_cat and line.strip().startswith("•"):
            quote = re.sub(r'^[•\s"]+|["]+$', "", line.strip()).strip()
            if current_cat in categories:
                categories[current_cat]["samples"].append({"quote": quote, "url": "", "date": ""})

    return {
        "city":             city_name,
        "slug":             slug,
        "generated":        gen_m.group(1).strip() if gen_m else "",
        "days":             365,
        "total_articles":   total_articles,
        "total_complaints": total_complaints,
        "complaint_rate":   complaint_rate,
        "categories":       categories,
        "source":           "txt",
    }


def load_all_cities():
    cities = {}

    # Prefer JSON (richer data), fall back to txt
    for jpath in sorted(glob.glob(os.path.join(REPORTS_DIR, "*.json"))):
        slug = os.path.basename(jpath).replace(".json", "")
        if slug.startswith("_"):
            continue
        try:
            data = json.load(open(jpath))
            data["source"] = "json"
            cities[slug] = data
        except Exception:
            pass

    for tpath in sorted(glob.glob(os.path.join(REPORTS_DIR, "*.txt"))):
        slug = os.path.basename(tpath).replace(".txt", "")
        if slug.startswith("_") or slug in cities:
            continue
        try:
            cities[slug] = parse_txt_report(tpath)
        except Exception:
            pass

    # Sort by total_complaints desc
    return dict(sorted(cities.items(), key=lambda x: x[1].get("total_complaints", 0), reverse=True))


@app.route("/")
def index():
    cities = load_all_cities()
    return render_template("index.html", cities=cities, categories=CATEGORIES, cat_colors=CAT_COLORS)


@app.route("/api/cities")
def api_cities():
    return jsonify(load_all_cities())


@app.route("/city/<slug>")
def city_detail(slug):
    cities = load_all_cities()
    city = cities.get(slug)
    if not city:
        return "City not found", 404
    return render_template("city.html", city=city, categories=CATEGORIES, cat_colors=CAT_COLORS)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5010))
    print(f"Dashboard → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
