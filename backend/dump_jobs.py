import sqlite3
import config

def dump_jobs() -> None:
    """
    Utility script to print all crawled jobs in the database.
    """
    conn = sqlite3.connect(str(config.DB_PATH))
    cursor = conn.cursor()
    rows = cursor.execute("SELECT company, title, url, match_score FROM jobs").fetchall()
    print(f"Total jobs: {len(rows)}")
    for r in rows:
        print(f"Company: {r[0]} | Title: {r[1]} | URL: {r[2]} | Score: {r[3]}")
    conn.close()

if __name__ == "__main__":
    dump_jobs()
