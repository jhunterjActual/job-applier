import os
import json
import re
from pathlib import Path
from config import get_gemini_api_key

SENSITIVE_FIELD_PATTERNS = (
    r"citizen(ship)?", r"work authori[sz]ation", r"visa|sponsor(ship)?",
    r"salary|compensation|pay expectation", r"race|ethnic|gender|sex(ual)?|pronoun",
    r"disab|veteran|military status", r"criminal|conviction|background check",
    r"drug test|credit check", r"agree|consent|certif(y|ication)|signature",
)

CONFIRMATION_PATTERNS = (
    r"thank you for (applying|your application)",
    r"application (has been )?(submitted|received)",
    r"we('ve| have) received your application",
    r"submission (confirmed|complete|successful)",
)


def field_requires_review(field: dict) -> bool:
    """Return True for questions that automation must not infer or guess."""
    text = " ".join(str(field.get(key, "")) for key in ("label", "name", "placeholder"))
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in SENSITIVE_FIELD_PATTERNS)


def build_cover_letter_upload_path(tailored_resume_path: str) -> Path:
    """Build a cover-letter path that can never alias the generated resume."""
    resume_path = Path(tailored_resume_path)
    cover_path = resume_path.with_name(f"{resume_path.stem}_cover_letter.txt")
    if cover_path.resolve() == resume_path.resolve():
        raise ValueError("Cover-letter upload path must differ from resume path.")
    return cover_path


def _confirmation_markers(page) -> set[str]:
    try:
        body_text = page.locator("body").inner_text(timeout=5000)[:30000]
    except Exception:
        return set()
    return {
        pattern for pattern in CONFIRMATION_PATTERNS
        if re.search(pattern, body_text, flags=re.IGNORECASE)
    }


def detect_submission_confirmation(
    page,
    previous_url: str,
    previous_markers: set[str] | None = None,
) -> tuple[bool, str]:
    """Look for destination-provided evidence that a submission was accepted."""
    current_url = page.url
    baseline = previous_markers or set()
    for pattern in _confirmation_markers(page):
        if pattern not in baseline:
            return True, f"confirmation_text:{pattern}"
    if current_url != previous_url and re.search(r"thank|confirm|success|submitted", current_url, re.IGNORECASE):
        return True, f"confirmation_url:{current_url}"
    return False, ""

def extract_form_fields(page) -> list:
    """
    Scrapes the loaded webpage using custom JavaScript to find form input,
    select, and textarea elements along with their associated text labels
    and options (for select dropdowns).
    
    Args:
        page: The Playwright Page instance.
        
    Returns:
        list: A list of dictionaries detailing the extracted form fields.
    """
    fields = []
    
    # We will run a script in the browser to extract clean field information
    js_script = """
    () => {
        const results = [];
        
        // Find all labels on the page and map them to their elements
        const labelsMap = {};
        document.querySelectorAll('label').forEach(label => {
            const forAttr = label.getAttribute('for');
            if (forAttr) {
                labelsMap[forAttr] = label.innerText.trim();
            }
        });

        // Query all form inputs, selects, textareas
        const inputs = document.querySelectorAll('input, select, textarea');
        inputs.forEach((el, index) => {
            // Ignore hidden fields or submit buttons
            const type = el.getAttribute('type') || '';
            const tag = el.tagName.toLowerCase();
            
            if (type === 'hidden' || type === 'submit' || type === 'button') {
                return;
            }
            
            // Get label text
            let labelText = '';
            // 1. Check mapped label by ID
            const id = el.getAttribute('id');
            if (id && labelsMap[id]) {
                labelText = labelsMap[id];
            }
            // 2. Check parent element label
            if (!labelText) {
                const parentLabel = el.closest('label');
                if (parentLabel) {
                    labelText = parentLabel.innerText.trim();
                }
            }
            // 3. Check preceding element label
            if (!labelText && el.previousElementSibling && el.previousElementSibling.tagName === 'LABEL') {
                labelText = el.previousElementSibling.innerText.trim();
            }
            // 4. Use aria-label or placeholder
            if (!labelText) {
                labelText = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
            }
            
            // If still no label, check parent container for text
            if (!labelText) {
                const parent = el.parentElement;
                if (parent) {
                    // Get first text node of parent
                    labelText = parent.innerText.split('\\n')[0].trim();
                }
            }
            
            // Gather options if select
            const options = [];
            if (tag === 'select') {
                el.querySelectorAll('option').forEach(opt => {
                    options.push({
                        text: opt.innerText.strip ? opt.innerText.strip() : opt.innerText,
                        value: opt.getAttribute('value') || ''
                    });
                });
            }
            
            // Create a unique CSS selector for this element
            let selector = tag;
            if (id) {
                selector = `#${id}`;
            } else {
                const name = el.getAttribute('name');
                if (name) {
                    selector = `${tag}[name="${name}"]`;
                } else {
                    selector = `${tag}:nth-of-type(${index + 1})`;
                }
            }

            results.push({
                tag: tag,
                type: type,
                id: id || '',
                name: el.getAttribute('name') || '',
                placeholder: el.getAttribute('placeholder') || '',
                label: labelText.replace(/\\s+/g, ' ').trim(),
                options: options,
                selector: selector
            });
        });
        
        return results;
    }
    """
    
    try:
        fields = page.evaluate(js_script)
    except Exception as e:
        print(f"Error evaluating form fields extraction: {e}")
        
    return fields

def fill_application_form(url: str, candidate_profile: dict, tailored_resume_path: str, cover_letter_text: str, api_key: str = None, headed: bool = True) -> dict:
    """
    Launches a browser via Playwright, navigates to the application URL,
    handles any auto-navigation or clicking required, maps the candidate
    details to the inputs using Gemini, and interacts with the browser
    to fill in the application and upload files.
    
    Args:
        url (str): The job application web page URL.
        candidate_profile (dict): A dictionary containing candidate contact details.
        tailored_resume_path (str): The local path to the tailored resume PDF file.
        cover_letter_text (str): The tailored cover letter text content.
        api_key (str, optional): The Gemini API key. Defaults to None.
        headed (bool, optional): Whether to run the browser in Headed mode. Defaults to True.
        
    Returns:
        dict: A dictionary containing success status, auto_submitted flag, and messages.
    """
    key = api_key or get_gemini_api_key()
    if not key:
        return {"success": False, "error": "Gemini API key is required to fill out forms."}

    from google import genai
    from google.genai import types
    from playwright.sync_api import sync_playwright

    client = genai.Client(api_key=key)
    temporary_files: list[Path] = []
    
    with sync_playwright() as p:
        # Open browser in headed mode by default so the user can watch the automation!
        browser = p.chromium.launch(headless=not headed)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        try:
            page.goto(url, timeout=40000)
            # Give page a second to load dynamic contents
            page.wait_for_timeout(3000)
            
            # Check if there is an application form (e.g. visible inputs)
            # If no input elements are detected, try to click 'Apply' buttons or redirect
            inputs_count = page.evaluate("() => document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
            
            if inputs_count == 0:
                print("No form fields detected. Checking for 'Apply' buttons or links...")
                
                # 1. Lever-specific quick-redirect
                if "lever.co" in page.url and not page.url.endswith("/apply"):
                    apply_url = page.url.split('?')[0].rstrip("/") + "/apply"
                    print(f"Lever posting detected. Redirecting to application form: {apply_url}")
                    page.goto(apply_url, timeout=30000)
                    page.wait_for_timeout(3000)
                    inputs_count = page.evaluate("() => document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
                
                # 2. Try clicking common text-based Apply buttons using Playwright locators
                if inputs_count == 0:
                    for text_query in ["text=Apply for this job", "text=Apply Now", "text=Apply", "text=Submit Application"]:
                        try:
                            locator = page.locator(text_query).first
                            if locator and locator.is_visible():
                                print(f"Found Apply element with query '{text_query}'. Clicking it...")
                                locator.click()
                                page.wait_for_timeout(4000)
                                inputs_count = page.evaluate("() => document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
                                if inputs_count > 0:
                                    print("Form fields successfully loaded after text click.")
                                    break
                        except Exception as click_err:
                            print(f"Error clicking text locator '{text_query}': {click_err}")
                            
                # 3. Try clicking standard CSS selectors
                if inputs_count == 0:
                    css_selectors = ["a[href*='/apply']", "a[href*='/application']", ".postings-btn", ".template-btn-primary"]
                    for sel in css_selectors:
                        try:
                            btn = page.query_selector(sel)
                            if btn and btn.is_visible():
                                print(f"Found CSS Apply button: '{sel}'. Clicking...")
                                btn.click()
                                page.wait_for_timeout(4000)
                                inputs_count = page.evaluate("() => document.querySelectorAll('input:not([type=hidden]), textarea, select').length")
                                if inputs_count > 0:
                                    print("Form fields successfully loaded after CSS click.")
                                    break
                        except Exception as css_err:
                            print(f"Error clicking CSS selector '{sel}': {css_err}")
            
            # Extract form structure
            fields = extract_form_fields(page)
            if not fields:
                return {
                    "success": False,
                    "error": "No application form fields were found. The application was not submitted.",
                }
            
            # Construct AI Prompt to map form fields to candidates details
            prompt = f"""
            You are a Browser Form Filler Agent. Your goal is to map the fields extracted from a job application web page to the candidate's personal profile, tailored resume, and cover letter.
            
            Candidate Profile:
            {json.dumps(candidate_profile, indent=2)}
            
            Tailored Resume PDF Path: "{tailored_resume_path}" (Upload this file where a resume/CV is requested)
            Cover Letter Content: "{cover_letter_text}" (Input this text if a cover letter textbox is provided, otherwise upload the cover letter if an upload file option is found)
            
            Form Fields:
            {json.dumps(fields, indent=2)}
            
            Determine the filling action for each form field.
            The actions can be:
            1. "type": Input text. Must provide a "value" string.
            2. "select": Choose a dropdown option. Must provide a "value" string (the option value to select) or "text" (the text of the option to select).
            3. "check": Check a checkbox or radio. Provide "value" as boolean true.
            4. "upload_resume": Upload the resume file.
            5. "upload_cover_letter": Upload a cover letter file (or fill the text area if it's a cover letter textbox).
            6. "review": Leave the field untouched for human review.
            7. "ignore": Do not interact (e.g. for already filled fields, read-only labels, or sections we don't have details for).
            
            Safety rules:
            - Never infer or guess answers about work authorization, citizenship, sponsorship, salary, protected demographics, disability, veteran status, criminal history, consent, certifications, or signatures. Use "review" for those fields unless an exact answer is explicitly present in the candidate profile.
            - Never make a fallback dropdown choice. Use "review" when no exact option matches.
            
            Output a JSON list of objects:
            [
              {{
                "selector": "CSS selector of the field",
                "label": "The label of the field",
                "action": "type | select | check | upload_resume | upload_cover_letter | review | ignore",
                "value": "the string/bool value to input, or null"
              }},
              ...
            ]
            
            Return ONLY valid JSON.
            """
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                ),
            )
            
            actions = json.loads(response.text.strip(), strict=False)
            
            fields_by_selector = {field.get("selector"): field for field in fields}
            fields_needing_review = []

            # Execute actions in the browser
            for act in actions:
                selector = act["selector"]
                action = act["action"]
                val = act.get("value")
                label = act.get("label", selector)

                field = fields_by_selector.get(selector, {"label": label})
                if action == "review" or field_requires_review(field):
                    fields_needing_review.append(label)
                    print(f"Review required: {label}")
                    continue
                
                print(f"Action: {action} on {label} ({selector})")
                
                try:
                    # Scroll to element to make it visible
                    elem = page.query_selector(selector)
                    if not elem:
                        print(f"  Element not found: {selector}")
                        fields_needing_review.append(label)
                        continue
                        
                    elem.scroll_into_view_if_needed()
                    page.wait_for_timeout(300)
                    
                    if action == "ignore":
                        continue
                        
                    elif action == "type":
                        # Clear existing value if possible
                        elem.focus()
                        page.keyboard.press("Control+A")
                        page.keyboard.press("Backspace")
                        elem.fill(val)
                        
                    elif action == "select":
                        # If value matches option value or option text
                        # Locate correct value
                        options_list = page.evaluate(f"sel => Array.from(document.querySelector('{selector}').options).map(o => ({{text: o.text, val: o.value}}))")
                        matching_val = None
                        
                        # Look for matching value or text
                        for opt in options_list:
                            # Match ignoring case and whitespace
                            if val and (val.lower().strip() == opt["val"].lower().strip() or val.lower().strip() == opt["text"].lower().strip()):
                                matching_val = opt["val"]
                                break
                                
                        if matching_val is not None:
                            page.select_option(selector, value=matching_val)
                        else:
                            fields_needing_review.append(label)
                            print(f"  No exact dropdown match; review required: {label}")
                            
                    elif action == "check":
                        if val:
                            page.check(selector)
                            
                    elif action == "upload_resume":
                        if os.path.exists(tailored_resume_path):
                            # Set file input
                            # Greenhouse and Lever files are input[type=file]
                            # Sometimes they are hidden, so we need to set it directly
                            page.set_input_files(selector, tailored_resume_path)
                            print(f"  Uploaded resume PDF: {tailored_resume_path}")
                        else:
                            print(f"  Resume PDF not found: {tailored_resume_path}")
                            fields_needing_review.append(label)
                            
                    elif action == "upload_cover_letter":
                        # Check if this is a file upload or a text area
                        elem_tag = page.evaluate(f"el => document.querySelector('{selector}').tagName.toLowerCase()")
                        elem_type = page.evaluate(f"el => document.querySelector('{selector}').getAttribute('type') || ''")
                        
                        if elem_tag == "textarea":
                            elem.fill(cover_letter_text)
                            print("  Filled cover letter text area.")
                        elif elem_type == "file":
                            # Use a distinct temporary path; never overwrite the resume PDF.
                            temp_cl_path = build_cover_letter_upload_path(tailored_resume_path)
                            with temp_cl_path.open("w", encoding="utf-8") as f:
                                f.write(cover_letter_text)
                            temporary_files.append(temp_cl_path)
                            page.set_input_files(selector, str(temp_cl_path))
                            print(f"  Uploaded cover letter file: {temp_cl_path}")
                            
                except Exception as ex:
                    print(f"  Failed to execute action on {label}: {ex}")
                    fields_needing_review.append(label)
            
            # Let the browser pause briefly for user satisfaction or captcha checks
            page.wait_for_timeout(5000)
            
            # Submit only when every requested action completed without requiring
            # human judgment. Any review item makes this a fill-only run.
            submit_selectors = ["button[type=submit]", "#submit-button", "#submit_app", "input[type=submit]"]
            clicked_submit = False
            submission_confirmed = False
            submission_evidence = ""
            
            # Auto-submit
            for sel in submit_selectors:
                if fields_needing_review:
                    break
                submit_btn = page.query_selector(sel)
                if submit_btn:
                    # Let's wait 2 seconds before clicking
                    page.wait_for_timeout(2000)
                    previous_url = page.url
                    previous_markers = _confirmation_markers(page)
                    submit_btn.click()
                    clicked_submit = True
                    print(f"Clicked submit button: {sel}")
                    # Wait for redirect/success page
                    page.wait_for_timeout(5000)
                    submission_confirmed, submission_evidence = detect_submission_confirmation(
                        page, previous_url, previous_markers
                    )
                    break
            
            return {
                "success": True,
                "auto_submitted": clicked_submit,
                "submission_attempted": clicked_submit,
                "submission_confirmed": submission_confirmed,
                "submission_evidence": submission_evidence,
                "fields_needing_review": sorted(set(fields_needing_review)),
                "msg": (
                    "Application submission was confirmed by the destination site."
                    if submission_confirmed
                    else (
                        "The form was filled but not submitted because one or more fields require review."
                        if fields_needing_review
                        else "The form was filled, but submission was not confirmed. Review the application status before treating it as applied."
                    )
                )
            }
            
        except Exception as e:
            return {"success": False, "error": f"Error running browser automation: {str(e)}"}
        finally:
            # We close the browser if it was headless. If headed, let's close it after a brief wait
            if not headed:
                browser.close()
            else:
                # Keep it open for 5 more seconds then close
                page.wait_for_timeout(5000)
                browser.close()
            for temp_path in temporary_files:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
