import sqlite3
from datetime import datetime
from config import DB_PATH

def get_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a connection to the local SQLite database.
    Sets the row factory to sqlite3.Row to allow key-based column lookups.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """
    Initializes the SQLite database tables (profile, jobs, applications)
    if they do not already exist, and performs schema migrations
    and automatic cleanup of expired job listings on startup.
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # User Profile table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        phone TEXT,
        github TEXT,
        linkedin TEXT,
        website TEXT,
        base_resume_text TEXT,
        gemini_api_key TEXT
    )
    """)
    
    # Jobs table (postings found/scraped)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        company TEXT,
        description TEXT,
        url TEXT UNIQUE,
        match_score INTEGER,
        match_analysis TEXT,
        date_found TEXT,
        status TEXT DEFAULT 'matched' -- matched, ignored, tailored, applied
    )
    """)
    
    # Applications table (jobs applied to)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER,
        company TEXT,
        position TEXT,
        date_applied TEXT,
        us_hq TEXT,
        tailored_resume_path TEXT,
        cover_letter TEXT,
        status TEXT DEFAULT 'applied',
        FOREIGN KEY (job_id) REFERENCES jobs(id)
    )
    """)
    
    # Check and add suggested_keywords column if it doesn't exist
    cursor.execute("PRAGMA table_info(profile)")
    columns = [row[1] for row in cursor.fetchall()]
    if "suggested_keywords" not in columns:
        cursor.execute("ALTER TABLE profile ADD COLUMN suggested_keywords TEXT DEFAULT ''")
    if "google_maps_api_key" not in columns:
        cursor.execute("ALTER TABLE profile ADD COLUMN google_maps_api_key TEXT DEFAULT ''")
        
    # Insert default empty profile if none exists
    cursor.execute("SELECT COUNT(*) FROM profile")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO profile (name, email, phone, github, linkedin, website, base_resume_text, gemini_api_key, suggested_keywords, google_maps_api_key)
        VALUES ('', '', '', '', '', '', '', '', '', '')
        """)
    # Clean up any previously stored junk/closed postings
    cursor.execute("""
    DELETE FROM jobs 
    WHERE title IS NULL 
       OR title = '' 
       OR title = 'Job not found' 
       OR title LIKE '%no longer active%'
       OR title LIKE '%no longer available%'
       OR title LIKE '%position closed%'
       OR title LIKE '%job is closed%'
       OR title LIKE 'jobs at%'
       OR title LIKE '%Internet Explorer 11%'
       OR title LIKE '%Consent to Cookies%'
    """)
    
    conn.commit()
    conn.close()

# Initialize DB on import
init_db()
