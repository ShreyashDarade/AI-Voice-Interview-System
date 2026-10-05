"""Small built-in word lists shared by several extractors (no external gazetteer)."""
from __future__ import annotations

import re
from typing import FrozenSet

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
    "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
    "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}
CA_PROVINCES = {"ON": "Ontario", "BC": "British Columbia", "AB": "Alberta", "QC": "Quebec", "MB": "Manitoba",
                "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland"}
INDIAN_STATES = [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "New Delhi", "Chandigarh", "Puducherry",
    "Jammu and Kashmir", "Ladakh",
]
COUNTRIES = [
    "India", "United States", "USA", "United States of America", "US", "United Kingdom", "UK", "Canada", "Australia",
    "Germany", "France", "Spain", "Italy", "Netherlands", "Belgium", "Switzerland", "Austria", "Sweden", "Norway",
    "Denmark", "Finland", "Ireland", "Poland", "Portugal", "Czech Republic", "Romania", "Hungary", "Greece",
    "Turkey", "Russia", "Ukraine", "Israel", "UAE", "United Arab Emirates", "Saudi Arabia", "Qatar", "Kuwait",
    "Bahrain", "Oman", "Egypt", "Nigeria", "Kenya", "South Africa", "Ghana", "Morocco", "Pakistan", "Bangladesh",
    "Sri Lanka", "Nepal", "China", "Japan", "South Korea", "Korea", "Taiwan", "Hong Kong", "Singapore", "Malaysia",
    "Indonesia", "Thailand", "Vietnam", "Philippines", "New Zealand", "Brazil", "Argentina", "Chile", "Colombia",
    "Mexico", "Peru",
]
CITIES = [
    "Bangalore", "Bengaluru", "Hyderabad", "Pune", "Mumbai", "Chennai", "Delhi", "New Delhi", "Gurgaon", "Gurugram",
    "Noida", "Kolkata", "Ahmedabad", "Jaipur", "Chandigarh", "Indore", "Kochi", "Coimbatore", "Trivandrum",
    "Thiruvananthapuram", "Lucknow", "Nagpur", "Bhubaneswar", "Surat", "Vadodara", "Mysore", "Mysuru",
    "London", "Manchester", "Edinburgh", "Birmingham", "Dublin", "Paris", "Berlin", "Munich", "Hamburg", "Frankfurt",
    "Amsterdam", "Rotterdam", "Madrid", "Barcelona", "Lisbon", "Rome", "Milan", "Zurich", "Geneva", "Vienna",
    "Stockholm", "Copenhagen", "Oslo", "Helsinki", "Warsaw", "Prague", "Budapest", "Bucharest", "Athens", "Istanbul",
    "Dubai", "Abu Dhabi", "Doha", "Riyadh", "Tel Aviv", "Cairo", "Lagos", "Nairobi", "Johannesburg", "Cape Town",
    "Singapore", "Kuala Lumpur", "Jakarta", "Bangkok", "Manila", "Hanoi", "Ho Chi Minh City", "Tokyo", "Osaka",
    "Seoul", "Beijing", "Shanghai", "Shenzhen", "Hong Kong", "Taipei", "Sydney", "Melbourne", "Brisbane", "Perth",
    "Auckland", "Toronto", "Vancouver", "Montreal", "Ottawa", "Calgary", "New York", "San Francisco", "Los Angeles",
    "San Diego", "San Jose", "Seattle", "Austin", "Dallas", "Houston", "Boston", "Chicago", "Atlanta", "Denver",
    "Miami", "Phoenix", "Philadelphia", "Washington", "Portland", "Raleigh", "Charlotte", "Minneapolis", "Detroit",
    "Sao Paulo", "Buenos Aires", "Mexico City", "Bogota", "Santiago", "Lima", "Karachi", "Lahore", "Dhaka", "Colombo",
    "Kathmandu", "Palo Alto", "Mountain View", "Sunnyvale", "Cupertino", "Redmond", "Bellevue", "Cambridge",
    "Sofia", "Kyiv", "Kiev", "Belgrade", "Zagreb", "Tallinn", "Riga", "Vilnius", "Bratislava", "Ljubljana", "Reykjavik",
    "Brussels", "Luxembourg", "Lyon", "Marseille", "Toulouse", "Cologne", "Stuttgart", "Dusseldorf", "Gothenburg", "Malmo",
    "Antwerp", "Valencia", "Seville", "Porto", "Naples", "Turin", "Krakow", "Wroclaw", "Gdansk", "Brno", "Cluj", "Basel", "Bern",
    "Tbilisi", "Yerevan", "Baku", "Minsk", "Casablanca", "Accra", "Addis Ababa", "Kampala", "Kigali", "Tunis", "Amman", "Beirut",
    "Muscat", "Islamabad", "Chittagong", "Kandy", "Yangon", "Phnom Penh", "Almaty", "Tashkent", "Guangzhou", "Chengdu", "Hangzhou",
    "Nanjing", "Wuhan", "Nagoya", "Yokohama", "Kyoto", "Fukuoka", "Busan", "Pittsburgh", "Salt Lake City", "Columbus", "Indianapolis",
    "Nashville", "Orlando", "Tampa", "Las Vegas", "Sacramento", "Kansas City", "St. Louis", "Baltimore", "Cleveland", "Cincinnati",
    "Honolulu", "Boulder", "Ann Arbor", "Madison", "Princeton", "New Haven", "Providence", "Buffalo", "Rochester", "Albany", "Richmond",
    "Louisville", "Memphis", "Milwaukee", "Omaha", "San Antonio", "Albuquerque", "Tucson", "Oakland", "Irvine", "Santa Clara",
    "Menlo Park", "Redwood City", "Fremont", "Santa Monica", "Pasadena", "Edmonton", "Winnipeg", "Halifax", "Waterloo", "Mississauga",
    "Adelaide", "Canberra", "Wellington", "Christchurch", "Pretoria", "Durban", "Visakhapatnam", "Vizag", "Bhopal", "Patna", "Ranchi",
    "Guwahati", "Mangalore", "Mangaluru", "Nashik", "Kanpur", "Varanasi", "Agra", "Ludhiana", "Amritsar", "Dehradun", "Faridabad",
    "Ghaziabad", "Thane", "Navi Mumbai", "Madurai", "Vellore", "Warangal", "Vijayawada", "Rajkot", "Jodhpur", "Udaipur", "Raipur",
    "Kozhikode", "Thrissur", "Cuttack", "Siliguri", "Hubli", "Belgaum", "Tiruchirappalli", "Aurangabad", "Jalandhar",
]
LOCATION_WORDS: FrozenSet[str] = frozenset(
    {w.lower() for w in COUNTRIES + CITIES + INDIAN_STATES + list(US_STATES.values()) + list(CA_PROVINCES.values())}
    | {"remote", "hybrid", "onsite", "on-site", "worldwide"}
)

TITLE_WORDS = {
    "engineer", "engineers", "engineering", "developer", "developers", "programmer", "architect", "analyst",
    "scientist", "consultant", "manager", "lead", "director", "head", "vp", "president", "officer", "cto",
    "ceo", "cfo", "coo", "intern", "trainee", "associate", "assistant", "administrator", "admin", "specialist",
    "technician", "designer", "researcher", "executive", "coordinator", "supervisor", "representative", "advisor",
    "adviser", "strategist", "tester", "qa", "sdet", "devops", "sre", "owner", "founder", "co-founder", "cofounder",
    "partner", "principal", "fellow", "apprentice", "freelancer", "contractor", "instructor", "teacher", "tutor",
    "professor", "lecturer", "mentor", "editor", "writer", "accountant", "auditor", "clerk", "recruiter",
    "operator", "technologist", "evangelist", "scrum", "master", "owner", "generalist", "agent", "controller",
    "dba", "swe", "sde", "sdet", "pm", "tl", "developer.", "staff", "senior", "junior", "sr", "jr", "sr.", "jr.",
    "graduate", "volunteer", "member", "chair", "chairman", "secretary", "treasurer", "ambassador", "lead.",
    "nurse", "doctor", "physician", "pharmacist", "surgeon", "therapist", "counselor", "counsellor", "attorney",
    "lawyer", "paralegal", "banker", "broker", "trader", "underwriter", "actuary", "economist", "statistician",
    "architect.", "evaluator", "inspector", "surveyor", "foreman", "mechanic", "electrician", "welder", "driver",
    "cashier", "barista", "chef", "cook", "waiter", "bartender", "salesperson", "merchandiser", "buyer", "planner",
    "annotator", "moderator", "translator", "interpreter", "producer", "animator", "illustrator", "photographer",
}
TITLE_PHRASES = ("product owner", "scrum master", "team lead", "tech lead", "human resources", "business analyst",
                 "data scientist", "full stack", "full-stack", "front end", "front-end", "back end", "back-end",
                 "machine learning", "software", "research assistant", "teaching assistant", "sales", "marketing")

COMPANY_LEGAL = {"inc", "llc", "ltd", "limited", "pvt", "corp", "corporation", "co", "gmbh", "ag", "plc", "sa", "bv",
                 "oy", "srl", "pty", "llp", "lp", "pvt.", "ltd.", "inc.", "corp.", "co.", "private", "ab", "kg", "nv", "spa"}
COMPANY_STRONG = {
    "technologies", "solutions", "labs", "lab", "systems", "consulting", "services", "group", "partners", "industries",
    "university", "college", "institute", "school", "hospital", "bank", "ventures", "enterprises", "associates",
    "holdings", "capital", "infotech", "infosystems", "softech", "foundation", "agency", "laboratories", "airlines",
    "motors", "pharma", "healthcare", "insurance", "logistics", "telecom", "robotics", "dynamics", "innovations",
    "academy", "polytechnic", "corporation", "company", "studios", "networks", "technology", "infoway",
}
COMPANY_WEAK = {"software", "tech", "digital", "global", "international", "media", "research", "analytics", "interactive",
                "studio", "ai", "io", "communications", "financial", "retail", "works", "data", "cloud", "labs", "health"}
KNOWN_COMPANIES = {
    "infosys", "wipro", "tcs", "cognizant", "accenture", "capgemini", "deloitte", "pwc", "kpmg", "ibm", "oracle", "google",
    "microsoft", "amazon", "meta", "apple", "netflix", "uber", "airbnb", "stripe", "adobe", "intel", "nvidia", "cisco",
    "salesforce", "flipkart", "paytm", "zomato", "swiggy", "ola", "hcl", "mindtree", "mphasis", "ltimindtree", "zoho",
    "freshworks", "razorpay", "facebook", "twitter", "linkedin", "spotify", "atlassian", "vmware", "sap", "siemens",
    "bosch", "samsung", "sony", "dell", "hp", "qualcomm", "paypal", "walmart", "target", "jpmorgan", "goldman", "sachs",
    "morgan", "stanley", "citi", "barclays", "hsbc", "mckinsey", "bcg", "bain", "ey", "tesla", "spacex", "slack",
    "shopify", "twilio", "okta", "datadog", "snowflake", "databricks", "palantir", "booking", "expedia", "lyft",
}


def company_score(text: str) -> float:
    toks = [t.rstrip(",.").strip() for t in re.findall(r"[A-Za-z][A-Za-z.&\-]*", text.lower())]
    sc = 0.0
    for t in toks:
        if t in COMPANY_LEGAL or t.rstrip(".") in COMPANY_LEGAL:
            sc = max(sc, 2.0)
        elif t in KNOWN_COMPANIES:
            sc = max(sc, 1.5)
        elif t in COMPANY_STRONG:
            sc = max(sc, 1.0)
        elif t in COMPANY_WEAK:
            sc = max(sc, 0.3)
    return sc


ACTION_VERBS = {
    "developed", "built", "designed", "implemented", "led", "managed", "created", "worked", "responsible",
    "collaborated", "improved", "reduced", "increased", "optimized", "optimised", "delivered", "launched",
    "maintained", "analyzed", "analysed", "wrote", "migrated", "automated", "deployed", "integrated", "architected",
    "mentored", "supported", "conducted", "performed", "coordinated", "established", "drove", "spearheaded",
    "engineered", "owned", "handled", "assisted", "contributed", "participated", "executed", "streamlined",
    "refactored", "configured", "tested", "documented", "researched", "trained", "reviewed", "organized",
    "organised", "presented", "negotiated", "generated", "achieved", "utilized", "utilised", "leveraged", "using",
    "supervised", "monitored", "resolved", "ensured", "provided", "prepared", "planned", "oversaw", "enhanced",
    "experience", "proficient", "strong", "passionate", "seeking", "looking", "motivated", "skilled", "expertise",
}


def has_title_word(text: str) -> bool:
    low = text.lower()
    toks = re.findall(r"[a-z][a-z.\-]*", low)
    if any(t in TITLE_WORDS or t.rstrip(".") in TITLE_WORDS for t in toks):
        return True
    return any(p in low for p in TITLE_PHRASES) and any(
        t in {"engineer", "developer", "analyst", "manager", "scientist", "architect", "consultant", "lead", "intern", "designer"}
        for t in toks
    )


def has_company_cue(text: str) -> bool:
    return company_score(text) >= 1.0
