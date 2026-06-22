// API base url (empty since frontend is served from same origin)
const API_URL = "";

// State variables
let currentTab = "dashboard";
let selectedJobIdForApplying = null;

// DOM Elements
const navButtons = document.querySelectorAll(".nav-btn");
const tabPanes = document.querySelectorAll(".tab-pane");
const pageTitle = document.getElementById("page-title");
const pageSubtitle = document.getElementById("page-subtitle");
const userDisplayName = document.getElementById("user-display-name");
const apiStatusBadge = document.getElementById("api-status");

// Profile Form Elements
const profileForm = document.getElementById("profile-form");
const pName = document.getElementById("p-name");
const pEmail = document.getElementById("p-email");
const pPhone = document.getElementById("p-phone");
const pGithub = document.getElementById("p-github");
const pLinkedin = document.getElementById("p-linkedin");
const pWebsite = document.getElementById("p-website");
const pApiKey = document.getElementById("p-apikey");
const pGoogleApiKey = document.getElementById("p-google-apikey");
const pResume = document.getElementById("p-resume");
const resumeFileUpload = document.getElementById("resume-file-upload");
const toggleApiVisibilityBtn = document.getElementById("toggle-api-visibility");
const toggleGoogleApiVisibilityBtn = document.getElementById("toggle-google-api-visibility");

// Search Form Elements
const searchForm = document.getElementById("search-form");
const sKeywords = document.getElementById("s-keywords");
const sLocation = document.getElementById("s-location");
const refreshJobsBtn = document.getElementById("refresh-jobs-btn");
const jobsTableBody = document.querySelector("#jobs-table tbody");

// Logs Elements
const refreshLogsBtn = document.getElementById("refresh-logs-btn");
const logsTableBody = document.querySelector("#logs-table tbody");

// Modals
const tailorModal = document.getElementById("tailor-modal");
const closeTailorModalBtns = [
    document.getElementById("close-tailor-modal"),
    document.getElementById("close-tailor-modal-btn")
];
const modalTabBtns = document.querySelectorAll(".modal-tab-btn");
const modalTabContents = document.querySelectorAll(".modal-tab-content");
const tailoredResumeDisplay = document.getElementById("tailored-resume-display");
const coverLetterDisplay = document.getElementById("cover-letter-display");
const applyingHeadedCheckbox = document.getElementById("applying-headed-checkbox");
const applyJobConfirmBtn = document.getElementById("apply-job-confirm-btn");

const loadingModal = document.getElementById("loading-modal");
const loadingTitle = document.getElementById("loading-title");
const loadingSubtitle = document.getElementById("loading-subtitle");

// Initialize on Load
document.addEventListener("DOMContentLoaded", () => {
    setupTabSwitching();
    setupPasswordToggle();
    loadProfile();
    loadJobs();
    loadLogs();
    
    // Attach form and click listeners
    profileForm.addEventListener("submit", saveProfile);
    resumeFileUpload.addEventListener("change", handleResumeUpload);
    searchForm.addEventListener("submit", searchJobs);
    refreshJobsBtn.addEventListener("click", loadJobs);
    refreshLogsBtn.addEventListener("click", loadLogs);
    
    // Modal controls
    closeTailorModalBtns.forEach(btn => btn.addEventListener("click", hideTailorModal));
    setupModalTabs();
    applyJobConfirmBtn.addEventListener("click", triggerApplicationSubmission);
});

// Tab Switching Setup
function setupTabSwitching() {
    navButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            const tabId = btn.getAttribute("data-tab");
            switchTab(tabId);
        });
    });
}

function switchTab(tabId) {
    currentTab = tabId;
    
    // Update nav buttons active state
    navButtons.forEach(btn => {
        if (btn.getAttribute("data-tab") === tabId) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });
    
    // Update tab panes active state
    tabPanes.forEach(pane => {
        if (pane.id === `tab-${tabId}`) {
            pane.classList.add("active");
        } else {
            pane.classList.remove("active");
        }
    });
    
    // Update headers text
    const tabTitles = {
        dashboard: { title: "Dashboard Overview", sub: "Track your automated job application progress." },
        profile: { title: "Profile & Resume", sub: "Setup your contact information and base resume." },
        search: { title: "Search & Match Jobs", sub: "Find Greenhouse and Lever openings matching your skill set." },
        logs: { title: "Application Logs", sub: "View history and downloaded tailored resumes." }
    };
    
    pageTitle.innerText = tabTitles[tabId].title;
    pageSubtitle.innerText = tabTitles[tabId].sub;
    
    // Reload data contextually
    if (tabId === "dashboard") {
        updateDashboardStats();
    } else if (tabId === "search") {
        loadJobs();
    } else if (tabId === "logs") {
        loadLogs();
    }
}

// Password Visibility Toggle
function setupPasswordToggle() {
    toggleApiVisibilityBtn.addEventListener("click", () => {
        const type = pApiKey.getAttribute("type") === "password" ? "text" : "password";
        pApiKey.setAttribute("type", type);
        const icon = toggleApiVisibilityBtn.querySelector("i");
        icon.className = type === "password" ? "fa-solid fa-eye" : "fa-solid fa-eye-slash";
    });
    
    toggleGoogleApiVisibilityBtn.addEventListener("click", () => {
        const type = pGoogleApiKey.getAttribute("type") === "password" ? "text" : "password";
        pGoogleApiKey.setAttribute("type", type);
        const icon = toggleGoogleApiVisibilityBtn.querySelector("i");
        icon.className = type === "password" ? "fa-solid fa-eye" : "fa-solid fa-eye-slash";
    });
}

// Modal tab controls
function setupModalTabs() {
    modalTabBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            const target = btn.getAttribute("data-modal-tab");
            
            modalTabBtns.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            
            modalTabContents.forEach(content => {
                if (content.id === `modal-tab-${target}`) {
                    content.classList.add("active");
                } else {
                    content.classList.remove("active");
                }
            });
        });
    });
}

// Show/Hide Helpers
function showLoading(title, subtitle = "Please wait while the AI handles the paperwork.") {
    loadingTitle.innerText = title;
    loadingSubtitle.innerText = subtitle;
    loadingModal.classList.add("active");
}

function hideLoading() {
    loadingModal.classList.remove("active");
}

function showTailorModal(resumeMarkdown, coverLetterText) {
    tailoredResumeDisplay.value = resumeMarkdown;
    coverLetterDisplay.value = coverLetterText;
    tailorModal.classList.add("active");
}

function hideTailorModal() {
    tailorModal.classList.remove("active");
}

// Log Feed Update Helper
function logActivity(title, desc, type = "info") {
    const timeline = document.getElementById("activity-timeline");
    const icons = {
        info: "fa-solid fa-info-circle",
        success: "fa-solid fa-circle-check",
        warning: "fa-solid fa-circle-exclamation",
        error: "fa-solid fa-circle-xmark",
        magic: "fa-solid fa-wand-magic-sparkles",
        rocket: "fa-solid fa-rocket"
    };
    
    const item = document.createElement("div");
    item.className = "timeline-item";
    item.innerHTML = `
        <div class="timeline-icon" style="${type === 'success' ? 'border-color: var(--color-green); color: var(--color-green);' : ''} ${type === 'error' ? 'border-color: var(--color-red); color: var(--color-red);' : ''}"><i class="${icons[type]}"></i></div>
        <div class="timeline-content">
            <p class="timeline-title">${title}</p>
            <p class="timeline-desc">${desc}</p>
            <span class="timeline-time">${new Date().toLocaleTimeString()}</span>
        </div>
    `;
    
    timeline.insertBefore(item, timeline.firstChild);
}

// Fetch & Update API Key status indicator
function updateApiKeyStatus(hasKey) {
    const indicator = apiStatusBadge.querySelector(".status-indicator");
    const text = apiStatusBadge.querySelector(".status-text");
    
    if (hasKey) {
        indicator.className = "status-indicator green";
        text.innerText = "Gemini API Key Active";
    } else {
        indicator.className = "status-indicator red";
        text.innerText = "Gemini API Key Missing";
    }
}

// ----------------------------------------------------
// API Communication Logic
// ----------------------------------------------------

// Load Candidate Profile
async function loadProfile() {
    try {
        const res = await fetch(`${API_URL}/api/profile`);
        const profile = await res.json();
        
        if (profile) {
            pName.value = profile.name || "";
            pEmail.value = profile.email || "";
            pPhone.value = profile.phone || "";
            pGithub.value = profile.github || "";
            pLinkedin.value = profile.linkedin || "";
            pWebsite.value = profile.website || "";
            pApiKey.value = profile.gemini_api_key || "";
            pGoogleApiKey.value = profile.google_maps_api_key || "";
            pResume.value = profile.base_resume_text || "";
            
            userDisplayName.innerText = profile.name || "Candidate";
            updateApiKeyStatus(!!profile.gemini_api_key);
            
            // Sync dashboard statistics
            updateDashboardStats();
        }
    } catch (e) {
        console.error("Failed to load profile", e);
        logActivity("Error Loading Profile", "Could not fetch profile settings from database.", "error");
    }
}

// Save Candidate Profile
async function saveProfile(e) {
    e.preventDefault();
    showLoading("Saving Profile...", "Storing settings into your local database.");
    
    const payload = {
        name: pName.value.trim(),
        email: pEmail.value.trim(),
        phone: pPhone.value.trim(),
        github: pGithub.value.trim(),
        linkedin: pLinkedin.value.trim(),
        website: pWebsite.value.trim(),
        base_resume_text: pResume.value.trim(),
        gemini_api_key: pApiKey.value.trim(),
        google_maps_api_key: pGoogleApiKey.value.trim()
    };
    
    try {
        const res = await fetch(`${API_URL}/api/profile`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        
        if (result.success) {
            userDisplayName.innerText = payload.name || "Candidate";
            updateApiKeyStatus(!!payload.gemini_api_key);
            logActivity("Profile Saved", "Contact details and API key configured.", "success");
            hideLoading();
            alert("Profile settings saved successfully!");
        }
    } catch (err) {
        hideLoading();
        console.error(err);
        logActivity("Profile Save Failed", "Error storing profile adjustments.", "error");
    }
}

// Parse Resume Text Files
async function handleResumeUpload(e) {
    const file = e.target.files[0];
    if (!file) return;
    
    showLoading("Parsing Resume File...", `Reading ${file.name}`);
    
    const formData = new FormData();
    formData.append("file", file);
    
    try {
        const res = await fetch(`${API_URL}/api/profile/upload-resume`, {
            method: "POST",
            body: formData
        });
        
        const result = await res.json();
        if (result.success) {
            pResume.value = result.resume_text;
            logActivity("Resume File Uploaded", `Imported text from ${file.name}. Click 'Save Settings' to save.`, "success");
        } else {
            alert(result.detail || "Failed to upload resume file.");
        }
    } catch (err) {
        console.error(err);
        alert("Failed to upload file.");
    } finally {
        hideLoading();
    }
}

// Load Job Search Postings
async function loadJobs() {
    try {
        const res = await fetch(`${API_URL}/api/jobs`);
        const jobs = await res.json();
        
        // Clear table body
        jobsTableBody.innerHTML = "";
        
        if (!jobs || jobs.length === 0) {
            jobsTableBody.innerHTML = `
                <tr>
                    <td colspan="6" class="table-empty-state">
                        <i class="fa-solid fa-briefcase"></i>
                        <p>No job postings found yet. Run a search to match jobs with your resume.</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        jobs.forEach(job => {
            const tr = document.createElement("tr");
            
            // Match score badge color logic
            let scoreClass = "low";
            if (job.match_score >= 80) scoreClass = "high";
            else if (job.match_score >= 50) scoreClass = "medium";
            
            // Clean domain for URL display
            const domain = new URL(job.url).hostname.replace("www.", "");
            
            // Actions display based on job status
            let actionsHtml = "";
            let statusBadgeText = "";
            let statusClass = "text-muted";
            
            if (job.status === "matched") {
                statusBadgeText = "Matched";
                statusClass = "badge-matched";
                actionsHtml = `
                    <button class="btn btn-secondary btn-sm" onclick="tailorResumeForJob(${job.id})">
                        <i class="fa-solid fa-wand-magic-sparkles"></i> Tailor Materials
                    </button>
                `;
            } else if (job.status === "tailored") {
                statusBadgeText = "Tailored";
                statusClass = "badge-tailored";
                actionsHtml = `
                    <button class="btn btn-secondary btn-sm" onclick="viewTailoredMaterials(${job.id})">
                        <i class="fa-solid fa-eye"></i> View Materials
                    </button>
                    <button class="btn btn-primary btn-sm" onclick="openApplyConfirmation(${job.id})">
                        <i class="fa-solid fa-paper-plane"></i> Apply Now
                    </button>
                `;
            } else if (job.status === "applied") {
                statusBadgeText = "Applied";
                statusClass = "badge-applied";
                actionsHtml = `
                    <button class="btn btn-secondary btn-sm" disabled>
                        <i class="fa-solid fa-circle-check"></i> Applied
                    </button>
                `;
            }
            
            tr.innerHTML = `
                <td><strong>${job.company}</strong></td>
                <td>${job.title}</td>
                <td><span class="match-badge ${scoreClass}">${job.match_score}%</span></td>
                <td><a href="${job.url}" target="_blank" class="job-link"><i class="fa-solid fa-arrow-up-right-from-square"></i> ${domain}</a></td>
                <td><span class="badge ${statusClass}">${statusBadgeText}</span></td>
                <td class="actions-col">
                    <div class="table-actions">
                        ${actionsHtml}
                        <button class="btn btn-danger btn-sm btn-icon-only" onclick="deleteJobRecord(${job.id})" title="Delete Job">
                            <i class="fa-solid fa-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            
            jobsTableBody.appendChild(tr);
        });
    } catch (e) {
        console.error(e);
        logActivity("Error Loading Jobs", "Could not query matched jobs list.", "error");
    }
}

// Delete Job Record from Database
async function deleteJobRecord(jobId) {
    if (!confirm("Are you sure you want to delete this job posting from your list? Any tailored materials or logs associated with this job will also be removed.")) {
        return;
    }
    
    showLoading("Deleting Job...", "Removing the posting from your local database.");
    try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}`, {
            method: "DELETE"
        });
        const result = await res.json();
        hideLoading();
        
        if (result.success) {
            logActivity("Job Deleted", `Removed Job ID #${jobId} from listings.`, "warning");
            await loadJobs();
            await loadLogs();
            await updateDashboardStats();
        } else {
            const errorMsg = result.detail || result.error || "Unknown error";
            alert("Failed to delete job: " + errorMsg);
        }
    } catch (e) {
        hideLoading();
        console.error(e);
        logActivity("Error Deleting Job", `Failed to call delete API for Job ID #${jobId}`, "error");
        alert("An error occurred trying to delete the job.");
    }
}

// Search for jobs
async function searchJobs(e) {
    e.preventDefault();
    
    const keywords = sKeywords.value.trim();
    const location = sLocation.value.trim();
    
    const logMsg = keywords 
        ? `Searching for '${keywords}' ${location ? 'in ' + location : ''}...`
        : `Analyzing resume to suggest keywords and searching jobs...`;
    
    showLoading("Searching & Analyzing Jobs...", "AI is extracting keywords from your resume, crawling Yahoo, and scoring postings.");
    logActivity("Job Search Started", logMsg, "info");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/search`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ keywords, location })
        });
        const result = await res.json();
        
        if (result.success) {
            // Poll for background task status
            const pollInterval = setInterval(async () => {
                try {
                    const statusRes = await fetch(`${API_URL}/api/jobs/status`);
                    const status = await statusRes.json();
                    if (!status.searching) {
                        clearInterval(pollInterval);
                        await loadJobs();
                        await updateDashboardStats();
                        hideLoading();
                        logActivity("Job Search Complete", "Found and analyzed new job openings.", "success");
                    }
                } catch (err) {
                    console.error("Error polling search status:", err);
                }
            }, 3000);
        } else {
            hideLoading();
            alert(result.error || "Search failed.");
        }
    } catch (err) {
        hideLoading();
        console.error(err);
        logActivity("Search Failed", "An error occurred initiating the browser search crawl.", "error");
    }
}

// Trigger AI Resume Tailoring
async function tailorResumeForJob(jobId) {
    showLoading("Tailoring Application...", "AI is rewriting experience highlights and crafting a cover letter. Generating PDF resume...");
    logActivity("Tailoring Started", `Generating custom resume for Job ID #${jobId}...`, "magic");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}/tailor`, { method: "POST" });
        const result = await res.json();
        
        if (result.success) {
            hideLoading();
            logActivity("Tailoring Complete", `Resume and Cover Letter customized.`, "success");
            
            // Reload job table
            await loadJobs();
            
            // Show review materials
            viewTailoredMaterials(jobId);
        } else {
            hideLoading();
            const errorMsg = result.detail || result.error || "Unknown error";
            alert("Tailoring failed: " + errorMsg);
        }
    } catch (e) {
        hideLoading();
        console.error(e);
        logActivity("Tailoring Failed", "Could not call API to tailor resume details.", "error");
    }
}

// View tailored resume & cover letter details
async function viewTailoredMaterials(jobId) {
    showLoading("Fetching Materials...", "Loading customized files.");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}/tailored`);
        const result = await res.json();
        
        if (result.success) {
            hideLoading();
            selectedJobIdForApplying = jobId;
            
            // For display in modal, fetch the PDF path or display cover letter.
            // Since we save resume as PDF, the frontend can let the user read the cover letter text,
            // and we show the location info.
            
            // Generate simple mock markdown since PDF is compiled on backend
            const resumeMd = `### Tailored PDF Generated successfully!\n\nPath on disk: \n${result.tailored_resume_path}\n\nThis file will be automatically attached when applying.`;
            
            showTailorModal(resumeMd, result.cover_letter);
        } else {
            hideLoading();
            alert(result.message || "Failed to load materials.");
        }
    } catch (e) {
        hideLoading();
        console.error(e);
    }
}

// Open application confirmation
function openApplyConfirmation(jobId) {
    selectedJobIdForApplying = jobId;
    viewTailoredMaterials(jobId);
}

// Trigger Browser Automation Application
async function triggerApplicationSubmission() {
    if (!selectedJobIdForApplying) return;
    
    hideTailorModal();
    const headed = applyingHeadedCheckbox.checked;
    
    showLoading("Submitting Application...", headed 
        ? "Opening Chromium. Keep an eye on the browser window to watch the submission!" 
        : "Running background Chromium browser to input fields and upload resume."
    );
    
    logActivity("Automation Started", `Filing application for Job ID #${selectedJobIdForApplying} (Headed: ${headed})...`, "rocket");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/${selectedJobIdForApplying}/apply?headed=${headed}`, {
            method: "POST"
        });
        const result = await res.json();
        
        hideLoading();
        
        if (result.success) {
            logActivity("Application Filed", result.message, "success");
            alert(result.message);
            
            // Reload logs and jobs
            await loadJobs();
            await loadLogs();
            await updateDashboardStats();
        } else {
            const errorMsg = result.detail || result.error || "Unknown error";
            logActivity("Application Failed", errorMsg, "error");
            alert("Application filing failed: " + errorMsg);
        }
    } catch (e) {
        hideLoading();
        console.error(e);
        logActivity("Automation Error", "Could not complete browser automation sequence.", "error");
    }
}

// Load Application Submission History Logs
async function loadLogs() {
    try {
        const res = await fetch(`${API_URL}/api/applications`);
        const logs = await res.json();
        
        logsTableBody.innerHTML = "";
        
        if (!logs || logs.length === 0) {
            logsTableBody.innerHTML = `
                <tr>
                    <td colspan="6" class="table-empty-state">
                        <i class="fa-solid fa-paper-plane"></i>
                        <p>No jobs submitted yet. Tailor and apply from the "Search & Match" tab.</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        logs.forEach(log => {
            const tr = document.createElement("tr");
            
            const dateStr = log.date_applied || "Pending";
            
            // Downloadable resume link
            // We can download via /output/tailored_resume_X.pdf
            const pdfFilename = log.tailored_resume_path.split(/[\\/]/).pop();
            const downloadLink = `${API_URL}/output/${pdfFilename}`;
            
            tr.innerHTML = `
                <td><strong>${log.company}</strong></td>
                <td>${log.position}</td>
                <td>${dateStr}</td>
                <td><i class="fa-solid fa-location-dot text-muted"></i> ${log.us_hq || 'Unknown'}</td>
                <td><span class="badge ${log.status === 'applied' ? 'badge-applied' : 'badge-tailored'}">${log.status}</span></td>
                <td>
                    <a href="${downloadLink}" target="_blank" class="btn btn-secondary btn-sm" download>
                        <i class="fa-solid fa-file-pdf"></i> Resume
                    </a>
                </td>
            `;
            
            logsTableBody.appendChild(tr);
        });
    } catch (e) {
        console.error(e);
        logActivity("Error Loading Logs", "Could not query application logs database.", "error");
    }
}

// Update Overview Stats cards
async function updateDashboardStats() {
    try {
        const resJobs = await fetch(`${API_URL}/api/jobs`);
        const jobs = await resJobs.json();
        
        const resApps = await fetch(`${API_URL}/api/applications`);
        const logs = await resApps.json();
        
        if (jobs && logs) {
            const totalMatched = jobs.length;
            const totalTailored = jobs.filter(j => j.status === "tailored" || j.status === "applied").length;
            const totalApplied = logs.filter(l => l.status === "applied").length;
            
            document.getElementById("stat-found").innerText = totalMatched;
            document.getElementById("stat-tailored").innerText = totalTailored;
            document.getElementById("stat-applied").innerText = totalApplied;
        }
    } catch (err) {
        console.error("Error updating stats", err);
    }
}
