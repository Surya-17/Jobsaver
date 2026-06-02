from database import get_db

NON_US = [
    "india", "uk", "united kingdom", "canada", "australia", "germany",
    "france", "spain", "ireland", "poland", "luxembourg", "singapore",
    "netherlands", "sweden", "switzerland", "italy", "japan", "china",
    "brazil", "mexico", "sri lanka", "hyderabad", "bengaluru", "bangalore",
    "mumbai", "pune", "delhi", "chennai", "kolkata", "noida",
    "sydney", "toronto", "london", "paris", "berlin", "amsterdam",
    "madrid", "dublin", "warsaw", "zurich", "prague", "budapest",
    "greece", "thessaloniki", "athens", "belgium", "brussels", "austria",
    "vienna", "denmark", "copenhagen", "finland", "helsinki", "norway",
    "oslo", "portugal", "lisbon", "romania", "bucharest", "turkey",
    "istanbul", "israel", "tel aviv", "south korea", "seoul", "taiwan",
    "taipei", "hong kong", "dubai", "uae", "russia", "moscow",
]

conn = get_db()
rows = conn.execute("SELECT id, job_title, location, job_url FROM jobs").fetchall()

to_delete = []
for row in rows:
    check = ((row["location"] or "") + " " + (row["job_url"] or "")).lower()
    if any(kw in check for kw in NON_US):
        to_delete.append((row["id"], row["job_title"], row["location"], row["job_url"]))

for job_id, title, loc, url in to_delete:
    print(f"Deleting [{job_id}] {title[:50]} | loc={loc} | url={url[:70]}")
    conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

conn.commit()
conn.close()
print(f"\nTotal deleted: {len(to_delete)}")
