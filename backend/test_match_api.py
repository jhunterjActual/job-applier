from database import get_db_connection
from tailor import analyze_job_match
import sys

def test() -> None:
    """
    Test script to verify LLM job match analysis manually using stored profile details.
    """
    conn = get_db_connection()
    profile = conn.execute("SELECT base_resume_text, gemini_api_key FROM profile LIMIT 1").fetchone()
    conn.close()
    
    if not profile or not profile["base_resume_text"]:
        print("Resume or API Key missing!")
        return
        
    resume_text = profile["base_resume_text"]
    api_key = profile["gemini_api_key"]
    
    print(f"Resume text length: {len(resume_text)}")
    print(f"API Key starts with: {api_key[:10] if api_key else 'None'}")
    
    title = "Senior Data Engineer"
    company = "Versapay"
    desc = "We are looking for a Senior Data Engineer with experience in Python, SQL, and data pipelines."
    
    print("\nCalling analyze_job_match...")
    res = analyze_job_match(resume_text, title, company, desc, api_key)
    print("Result:", res)

if __name__ == "__main__":
    test()
