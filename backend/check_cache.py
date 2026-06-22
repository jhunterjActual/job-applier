import sqlite3
import config

def check() -> None:
    """
    Utility script to inspect the cached profile search keywords and found jobs in the SQLite database.
    """
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    print("--- CACHED KEYWORDS ---")
    profile = cursor.execute("SELECT suggested_keywords FROM profile LIMIT 1").fetchone()
    if profile:
        print("Cached keywords:", profile["suggested_keywords"])
    else:
        print("Profile not found")
        
    print("\n--- CRAWLED JOBS ---")
    jobs = cursor.execute("SELECT company, title, url, match_score, match_analysis FROM jobs").fetchall()
    print(f"Total jobs: {len(jobs)}")
    for j in jobs:
        print(dict(j))
        
    conn.close()

if __name__ == "__main__":
    check()
