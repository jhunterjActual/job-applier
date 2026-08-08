from database import get_db_connection
from ai_providers import provider_label, settings_from_profile
from tailor import analyze_job_match

def test() -> None:
    """
    Test script to verify LLM job match analysis manually using stored profile details.
    """
    conn = get_db_connection()
    profile = conn.execute("SELECT * FROM profile LIMIT 1").fetchone()
    conn.close()
    
    if not profile or not profile["base_resume_text"]:
        print("Resume or API Key missing!")
        return
        
    resume_text = profile["base_resume_text"]
    settings = settings_from_profile(profile)
    
    print(f"Resume text length: {len(resume_text)}")
    print(f"AI provider: {provider_label(settings.provider)}; model: {settings.model}; key configured: {bool(settings.api_key)}")
    
    title = "Senior Data Engineer"
    company = "Versapay"
    desc = "We are looking for a Senior Data Engineer with experience in Python, SQL, and data pipelines."
    
    print("\nCalling analyze_job_match...")
    res = analyze_job_match(
        resume_text,
        title,
        company,
        desc,
        settings.api_key,
        ai_provider=settings.provider,
        ai_model=settings.model,
    )
    print("Result:", res)

if __name__ == "__main__":
    test()
