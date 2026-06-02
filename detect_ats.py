"""
Auto-detect Greenhouse / Ashby ATS for Fortune 500 companies.
Run:  python detect_ats.py
Outputs Python config entries ready to paste into config.py.
"""
import asyncio, re, json
from urllib.parse import quote
import aiohttp

ALREADY_ADDED = {
    "Walmart","Amazon","Apple","CVS Health","UnitedHealth Group","Berkshire Hathaway",
    "Exxon Mobil","Alphabet","McKesson","AmerisourceBergen","Cigna","AT&T","Chevron",
    "Costco Wholesale","Microsoft","Cardinal Health","Walgreens Boots Alliance",
    "JPMorgan Chase","Kroger","Verizon Communications","Raytheon Technologies",
    "Goldman Sachs Group","Tesla","Meta Platforms","Bank of America","Target",
    "Ford Motor","Home Depot","General Motors","Elevance Health","Centene",
    "Dell Technologies","Citigroup","Johnson & Johnson","FedEx","Energy Transfer",
    "State Farm Insurance","PepsiCo","Wells Fargo","Walt Disney","ConocoPhillips",
    "Procter & Gamble","General Electric","Albertsons","Marathon Petroleum",
    "Phillips 66","Valero Energy","Fannie Mae","Freddie Mac","Humana","Pfizer",
    "Sysco","Archer Daniels Midland","UPS","Lowe's","MetLife","Comcast",
    "Edison Scientific",
}

FORTUNE_500 = [
    "Lockheed Martin","Boeing","Intel","IBM","Honeywell International",
    "Abbott Laboratories","Caterpillar","American Express","Charter Communications",
    "Merck","3M","General Dynamics","Northrop Grumman","Deere & Company",
    "Allstate","Progressive","Danaher","Thermo Fisher Scientific","Starbucks","Nike",
    "Marriott International","Hilton Worldwide Holdings","United Airlines Holdings",
    "American Airlines Group","Delta Air Lines","Southwest Airlines",
    "Norfolk Southern","CSX","Union Pacific","Tyson Foods","Conagra Brands",
    "Kraft Heinz","Mondelez International","General Mills","Kellanova",
    "Colgate-Palmolive","Kimberly-Clark","Estee Lauder",
    "Bristol-Myers Squibb","Eli Lilly","AbbVie","Amgen","Biogen",
    "Gilead Sciences","Regeneron Pharmaceuticals","Vertex Pharmaceuticals",
    "Moderna","Medtronic","Boston Scientific","Becton Dickinson","Stryker",
    "Zimmer Biomet","Edwards Lifesciences","Baxter International","Danaher",
    "HCA Healthcare","Tenet Healthcare","Universal Health Services",
    "Accenture","Oracle","Salesforce","Adobe","Intuit","ServiceNow","Workday",
    "PayPal","Visa","Mastercard","Capital One Financial","Discover Financial Services",
    "US Bancorp","PNC Financial Services Group","Truist Financial","KeyCorp",
    "Ally Financial","Regions Financial","Fifth Third Bancorp","Huntington Bancshares",
    "BlackRock","Charles Schwab","Ameriprise Financial","Raymond James Financial",
    "Fiserv","Fidelity National Information Services","Global Payments",
    "Chubb","Travelers Companies","Hartford Financial Services","Lincoln National",
    "Principal Financial Group","Aflac","Unum Group",
    "Duke Energy","NextEra Energy","Dominion Energy","Southern Company","Exelon",
    "American Electric Power","Entergy","Consolidated Edison",
    "Public Service Enterprise Group","Xcel Energy","DTE Energy","Sempra Energy",
    "Halliburton","Baker Hughes","SLB","Pioneer Natural Resources","EOG Resources",
    "Devon Energy","Diamondback Energy","Coterra Energy","APA Corporation",
    "Hewlett Packard Enterprise","HP","Qualcomm","Texas Instruments","Broadcom",
    "Applied Materials","Lam Research","KLA Corporation","Micron Technology",
    "Western Digital","Seagate Technology","Corning","Emerson Electric",
    "Parker Hannifin","Illinois Tool Works","Dover Corporation","Rockwell Automation",
    "Masco","Mohawk Industries","Owens Corning","Martin Marietta Materials",
    "Vulcan Materials","Nucor","Steel Dynamics","Alcoa","Freeport-McMoRan",
    "Newmont","Mosaic","International Paper","WestRock",
    "Waste Management","Republic Services","Clean Harbors","Cintas","Aramark",
    "CBRE Group","Jones Lang LaSalle","Cushman & Wakefield","Prologis",
    "American Tower","Crown Castle","Equinix","Digital Realty Trust",
    "Cognizant Technology Solutions","Leidos Holdings","Booz Allen Hamilton",
    "L3Harris Technologies","Textron","Motorola Solutions","Zebra Technologies",
    "Keysight Technologies","Verisk Analytics","S&P Global","Moody's",
    "Gartner","TransUnion","Equifax","Automatic Data Processing","Paychex",
    "Robert Half International","ManpowerGroup","Korn Ferry",
    "Interpublic Group","Omnicom Group",
    "Warner Bros Discovery","Paramount Global","Netflix",
    "Uber Technologies","Lyft","DoorDash","Airbnb",
    "Booking Holdings","Expedia Group","eBay","Etsy","Wayfair","Chewy",
    "CarMax","AutoZone","O'Reilly Automotive","Advance Auto Parts",
    "Dollar General","Dollar Tree","Ross Stores","TJX Companies","Burlington Stores",
    "Nordstrom","Macy's","Kohl's","Gap","PVH Corp","Hanesbrands","Under Armour",
    "VF Corporation","Carter's",
    "Yum! Brands","Darden Restaurants","Chipotle Mexican Grill","Domino's Pizza",
    "Sprouts Farmers Market",
    "Ahold Delhaize USA","Publix Super Markets","Rite Aid",
    "ResMed","Insulet","Align Technology","Hologic","Zoetis","Elanco Animal Health",
    "WW International","Peloton Interactive","Planet Fitness",
    "Live Nation Entertainment",
    "Freeport-McMoRan","Celanese","Eastman Chemical","PPG Industries",
    "Sherwin-Williams","RPM International","Huntsman Corporation","Cabot Corporation",
    "Olin Corporation","Trinseo","Innospec",
    "Nvidia","Advanced Micro Devices","Marvell Technology","Analog Devices",
    "Microchip Technology","ON Semiconductor","Skyworks Solutions","Qorvo",
    "Lattice Semiconductor","Silicon Laboratories","Wolfspeed",
    "Fortive","Roper Technologies","IDEX Corporation","Watts Water Technologies",
    "Xylem","Graco","Chart Industries","Kadant","CIRCOR International",
    "Cognex","Zebra Technologies","Trimble","Teledyne Technologies",
    "Curtiss-Wright","TransDigm Group","Spirit AeroSystems","Moog",
    "Heico","Ducommun","Kaman Aerospace",
    "Akamai Technologies","VeriSign","GoDaddy","Rackspace Technology",
    "Fastly","Cloudflare","Zscaler","CrowdStrike","Palo Alto Networks",
    "Fortinet","Check Point Software","Proofpoint","Mimecast",
    "Splunk","Dynatrace","New Relic","Elastic","Sumo Logic",
    "Twilio","SendGrid","Bandwidth","8x8","RingCentral",
    "Zendesk","Freshworks","Sprinklr","Medallia","Qualtrics",
    "HubSpot","Marketo","Pardot","Braze","Klaviyo",
    "DocuSign","Adobe Sign","HelloSign",
    "Dropbox","Box","Egnyte","Citrix Systems",
    "MongoDB","Couchbase","Datastax","InfluxData","SingleStore",
    "Snowflake","Databricks","dbt Labs","Fivetran","Airbyte",
    "Confluent","MuleSoft","Boomi","Informatica","Talend",
    "HashiCorp","Puppet","Chef","Ansible","Red Hat",
    "GitLab","GitHub","Atlassian","JFrog","Sonatype",
    "UiPath","Automation Anywhere","Blue Prism","NICE Systems",
    "Veeva Systems","Medidata Solutions","Definitive Healthcare",
    "nCino","Q2 Holdings","Blend Labs","Blend","Plaid",
    "Marqeta","Stripe","Brex","Ramp","Divvy",
    "Gusto","Rippling","Deel","Remote","Papaya Global",
    "Lattice","Leapsome","Culture Amp","Betterworks","Reflektive",
    "Greenhouse Software","Lever","iCIMS","SmartRecruiters","Jobvite",
    "Cornerstone OnDemand","Saba Software","SumTotal Systems",
    "Workiva","Anaplan","OneStream Software","Host Analytics","Planful",
    "Coupa Software","Jaggaer","GEP","Ivalua","Basware",
    "Netsuite","Sage Group","Epicor Software","Infor","Unit4",
    "PROS Holdings","Zilliant","Vendavo","Model N",
    "Verint Systems","NICE inContact","Genesys","Five9","Talkdesk",
    "Sprout Social","Hootsuite","Buffer","Semrush","Similarweb",
    "Bazaarvoice","PowerReviews","Yotpo","Trustpilot",
    "Contentsquare","Heap","FullStory","Quantum Metric","Glassbox",
    "Amplitude","Mixpanel","Segment","mParticle","Tealium",
]

# Remove already-added companies
COMPANIES = [c for c in FORTUNE_500 if c not in ALREADY_ADDED]

CONCURRENCY = 30

def gh_slugs(name):
    b = name.lower()
    return list({
        re.sub(r'[^a-z0-9]', '', b),
        re.sub(r'[^a-z0-9-]', '', b.replace(' & ', '-').replace(' ', '-')).strip('-'),
        re.sub(r'[^a-z0-9-]', '', b.replace(' ', '-')).strip('-'),
        re.sub(r'\s+', '-', re.sub(r'[^a-z0-9 ]', '', b)).strip('-'),
    })

def ashby_slugs(name):
    return list({
        name,
        re.sub(r'[^a-zA-Z0-9 ]', '', name).strip(),
        name.replace(' & ', ' ').strip(),
    })

async def try_greenhouse(session, name):
    for slug in gh_slugs(name):
        if not slug:
            continue
        try:
            async with session.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    d = await r.json(content_type=None)
                    if 'jobs' in d:
                        return slug
        except Exception:
            pass
    return None

async def try_ashby(session, name):
    for slug in ashby_slugs(name):
        try:
            async with session.get(
                f"https://api.ashbyhq.com/posting-api/job-board/{quote(slug)}",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as r:
                if r.status == 200:
                    d = await r.json(content_type=None)
                    if 'jobs' in d:
                        return slug
        except Exception:
            pass
    return None

async def detect(session, sem, name):
    async with sem:
        gh, ashby = await asyncio.gather(
            try_greenhouse(session, name),
            try_ashby(session, name),
        )
    if gh:
        return {"company_name": name, "ats_type": "greenhouse", "greenhouse_token": gh,
                "career_url": f"https://boards.greenhouse.io/{gh}",
                "search_url_template": None, "enabled": True}
    if ashby:
        return {"company_name": name, "ats_type": "ashby", "greenhouse_token": None,
                "career_url": f"https://jobs.ashbyhq.com/{quote(ashby)}",
                "search_url_template": None, "enabled": True}
    return {"company_name": name, "ats_type": None}

async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(limit=CONCURRENCY, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(*[detect(session, sem, c) for c in COMPANIES])

    found    = [r for r in results if r["ats_type"]]
    not_found = [r["company_name"] for r in results if not r["ats_type"]]

    print(f"# Auto-detected: {len(found)} companies")
    print("DETECTED = [")
    for c in found:
        entry = {k: v for k, v in c.items() if k != "ats_type" or True}
        print(f"    {entry},")
    print("]")

    print(f"\n# Not auto-detected ({len(not_found)}) - needs manual Workday/other URL:")
    for n in not_found:
        print(f"#   {n}")

    # Save to JSON for easy review
    with open("detected_companies.json", "w") as f:
        json.dump({"found": found, "not_found": not_found}, f, indent=2)
    print(f"\nSaved to detected_companies.json")

if __name__ == "__main__":
    asyncio.run(main())
