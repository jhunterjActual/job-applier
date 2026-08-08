// API base url (empty since frontend is served from same origin)
const API_URL = "";

// State variables
let currentTab = "dashboard";
let selectedMaterialsJobId = null;
let cleanupPreview = null;
let cleanupActionsReady = false;
let cleanupActionEnableTimer = null;
let currentJobs = new Map();
let lifecycleJobId = null;
let geminiKeyConfigured = false;
let googleMapsKeyConfigured = false;
let loadedJobs = [];
let jobImportCanonicalUrl = null;
let sourceDiagnosticsOpener = null;
let activeLoadingOperation = null;

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
const pGeminiKeyStatus = document.getElementById("p-gemini-key-status");
const pGeminiKeyHelp = document.getElementById("p-gemini-key-help");
const pGoogleKeyStatus = document.getElementById("p-google-key-status");
const pGoogleKeyHelp = document.getElementById("p-google-key-help");
const pResume = document.getElementById("p-resume");
const pResumeMode = document.getElementById("p-resume-mode");
const pPreferUsHeadquarters = document.getElementById("p-prefer-us-headquarters");
const resumeFileUpload = document.getElementById("resume-file-upload");
const toggleApiVisibilityBtn = document.getElementById("toggle-api-visibility");
const toggleGoogleApiVisibilityBtn = document.getElementById("toggle-google-api-visibility");

// Search Form Elements
const searchForm = document.getElementById("search-form");
const sKeywords = document.getElementById("s-keywords");
const sLocation = document.getElementById("s-location");
const refreshJobsBtn = document.getElementById("refresh-jobs-btn");
const cleanupJobsBtn = document.getElementById("cleanup-jobs-btn");
const jobsTableBody = document.querySelector("#jobs-table tbody");
const savedSearchSelect = document.getElementById("saved-search-select");
const deleteSavedSearchBtn = document.getElementById("delete-saved-search-btn");
const saveSearchCheckbox = document.getElementById("save-search-checkbox");
const savedSearchName = document.getElementById("saved-search-name");
const savedSearchFrequency = document.getElementById("saved-search-frequency");
const jobMinScore = document.getElementById("job-min-score");
const jobStatusFilter = document.getElementById("job-status-filter");
const jobSortOrder = document.getElementById("job-sort-order");
const providerAlerts = document.getElementById("provider-alerts");
const savedSearchAlerts = document.getElementById("saved-search-alerts");
const openSourceDiagnosticsBtn = document.getElementById("open-source-diagnostics-btn");
const sourceDiagnosticsCount = document.getElementById("source-diagnostics-count");
const openJobImportBtn = document.getElementById("open-job-import-btn");
const jobImportModal = document.getElementById("job-import-modal");
const jobImportForm = document.getElementById("job-import-form");
const jobImportUrl = document.getElementById("job-import-url");
const jobImportFields = document.getElementById("job-import-fields");
const jobImportMessage = document.getElementById("job-import-message");
const previewJobImportBtn = document.getElementById("preview-job-import-btn");
const saveJobImportBtn = document.getElementById("save-job-import-btn");
const jobImportCompany = document.getElementById("job-import-company");
const jobImportTitle = document.getElementById("job-import-title");
const jobImportLocation = document.getElementById("job-import-location");
const jobImportCompensation = document.getElementById("job-import-compensation");
const jobImportWorkArrangement = document.getElementById("job-import-work-arrangement");
const jobImportEmploymentType = document.getElementById("job-import-employment-type");
const jobImportDescription = document.getElementById("job-import-description");

// Logs Elements
const refreshLogsBtn = document.getElementById("refresh-logs-btn");
const logsTableBody = document.querySelector("#logs-table tbody");

// Modals
const tailorModal = document.getElementById("tailor-modal");
const closeTailorModalBtns = [
    document.getElementById("close-tailor-modal"),
    document.getElementById("close-tailor-modal-btn")
].filter(Boolean);
const modalTabBtns = document.querySelectorAll(".modal-tab-btn");
const modalTabContents = document.querySelectorAll(".modal-tab-content");
const tailoredResumeDisplay = document.getElementById("tailored-resume-display");
const coverLetterDisplay = document.getElementById("cover-letter-display");
const saveMaterialsBtn = document.getElementById("save-materials-btn");
const downloadResumeBtn = document.getElementById("download-resume-btn");
const downloadCoverLetterBtn = document.getElementById("download-cover-letter-btn");
const openManualApplicationBtn = document.getElementById("open-manual-application-btn");

const loadingModal = document.getElementById("loading-modal");
const loadingTitle = document.getElementById("loading-title");
const loadingSubtitle = document.getElementById("loading-subtitle");
const loadingActions = document.getElementById("loading-actions");
const stopLoadingBtn = document.getElementById("stop-loading-btn");

const cleanupModal = document.getElementById("cleanup-modal");
const closeCleanupModalBtns = [
    document.getElementById("close-cleanup-modal"),
    document.getElementById("close-cleanup-modal-btn")
].filter(Boolean);
const archiveUntouchedBtn = document.getElementById("archive-untouched-btn");
const deleteUntouchedBtn = document.getElementById("delete-untouched-btn");
const restoreArchivedBtn = document.getElementById("restore-archived-btn");
const suppressionCount = document.getElementById("suppression-count");
const suppressionList = document.getElementById("suppression-list");
const suppressionEmpty = document.getElementById("suppression-empty");
const clearAllSuppressionsBtn = document.getElementById("clear-all-suppressions-btn");
const sourceDiagnosticsModal = document.getElementById("source-diagnostics-modal");
const sourceDiagnosticsList = document.getElementById("source-diagnostics-list");
const sourceDiagnosticsEmpty = document.getElementById("source-diagnostics-empty");
const clearSourceDiagnosticsBtn = document.getElementById("clear-source-diagnostics-btn");
const exportSourceDiagnosticsBtn = document.getElementById("export-source-diagnostics-btn");
const closeSourceDiagnosticsBtns = [
    document.getElementById("close-source-diagnostics-modal"),
    document.getElementById("close-source-diagnostics-modal-btn")
].filter(Boolean);
const lifecycleModal = document.getElementById("lifecycle-modal");
const lifecycleForm = document.getElementById("lifecycle-form");
const lifecycleStatus = document.getElementById("lifecycle-status");
const lifecycleAppliedOn = document.getElementById("lifecycle-applied-on");
const lifecycleMethod = document.getElementById("lifecycle-method");
const lifecycleFollowUp = document.getElementById("lifecycle-follow-up");
const lifecycleAppliedCalendar = document.getElementById("lifecycle-applied-calendar");
const lifecycleFollowUpCalendar = document.getElementById("lifecycle-follow-up-calendar");
const lifecycleNotes = document.getElementById("lifecycle-notes");
const undoLifecycleBtn = document.getElementById("undo-lifecycle-btn");

// Initialize on Load
document.addEventListener("DOMContentLoaded", () => {
    setupTabSwitching();
    setupPasswordToggle();

    // Attach form and click listeners
    bindEvent(profileForm, "submit", saveProfile);
    bindEvent(resumeFileUpload, "change", handleResumeUpload);
    bindEvent(searchForm, "submit", searchJobs);
    bindEvent(refreshJobsBtn, "click", loadJobs);
    bindEvent(cleanupJobsBtn, "click", showCleanupPreview);
    bindEvent(openJobImportBtn, "click", showJobImportModal);
    bindEvent(previewJobImportBtn, "click", previewJobImport);
    bindEvent(jobImportForm, "submit", saveJobImport);
    bindEvent(jobImportUrl, "input", invalidateJobImportPreview);
    bindEvent(document.getElementById("close-job-import-modal"), "click", hideJobImportModal);
    bindEvent(document.getElementById("cancel-job-import-btn"), "click", hideJobImportModal);
    bindEvent(refreshLogsBtn, "click", loadLogs);
    
    // Modal controls
    closeTailorModalBtns.forEach(btn => btn.addEventListener("click", hideTailorModal));
    setupModalTabs();
    bindEvent(saveMaterialsBtn, "click", saveTailoredMaterials);
    closeCleanupModalBtns.forEach(btn => btn.addEventListener("click", hideCleanupModal));
    bindEvent(archiveUntouchedBtn, "click", () => runCleanupAction("archive"));
    bindEvent(deleteUntouchedBtn, "click", () => runCleanupAction("delete"));
    bindEvent(restoreArchivedBtn, "click", () => runCleanupAction("restore"));
    bindEvent(clearAllSuppressionsBtn, "click", clearAllJobSuppressions);
    bindEvent(openSourceDiagnosticsBtn, "click", showSourceDiagnostics);
    closeSourceDiagnosticsBtns.forEach(btn => btn.addEventListener("click", hideSourceDiagnostics));
    bindEvent(clearSourceDiagnosticsBtn, "click", clearSourceDiagnostics);
    bindEvent(lifecycleForm, "submit", saveLifecycleChange);
    bindEvent(document.getElementById("close-lifecycle-modal"), "click", hideLifecycleModal);
    bindEvent(document.getElementById("cancel-lifecycle-btn"), "click", hideLifecycleModal);
    bindEvent(undoLifecycleBtn, "click", undoLifecycleChange);
    bindEvent(document.getElementById("lifecycle-applied-calendar-btn"), "click", () => openDatePicker(lifecycleAppliedCalendar));
    bindEvent(document.getElementById("lifecycle-follow-up-calendar-btn"), "click", () => openDatePicker(lifecycleFollowUpCalendar));
    bindEvent(lifecycleAppliedCalendar, "change", () => lifecycleAppliedOn.value = formatDisplayDate(lifecycleAppliedCalendar.value));
    bindEvent(lifecycleFollowUpCalendar, "change", () => lifecycleFollowUp.value = formatDisplayDate(lifecycleFollowUpCalendar.value));
    bindEvent(savedSearchSelect, "change", selectSavedSearch);
    bindEvent(deleteSavedSearchBtn, "click", deleteSelectedSavedSearch);
    bindEvent(jobMinScore, "change", renderFilteredJobs);
    bindEvent(jobStatusFilter, "change", renderFilteredJobs);
    bindEvent(jobSortOrder, "change", renderFilteredJobs);
    bindEvent(stopLoadingBtn, "click", stopActiveLoadingOperation);

    loadProfile();
    loadSavedSearches();
    checkDueSavedSearches();
    loadJobs();
    loadLogs();
    refreshSourceDiagnosticCount();
    document.addEventListener("keydown", event => {
        if (event.key === "Escape" && sourceDiagnosticsModal.classList.contains("active")) {
            hideSourceDiagnostics();
        }
    });
});

function bindEvent(element, eventName, handler) {
    if (element) element.addEventListener(eventName, handler);
}

function createElement(tag, className = "", textContent = null) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (textContent !== null) element.textContent = textContent;
    return element;
}

function createActionButton(label, iconClass, className, handler, title = "") {
    const button = createElement("button", className);
    button.type = "button";
    if (title) button.title = title;
    button.appendChild(createElement("i", iconClass));
    if (label) button.appendChild(document.createTextNode(` ${label}`));
    button.addEventListener("click", handler);
    return button;
}

function safeHttpUrl(value) {
    try {
        const parsed = new URL(value);
        return ["http:", "https:"].includes(parsed.protocol) ? parsed : null;
    } catch {
        return null;
    }
}

function todayIsoDate() {
    const now = new Date();
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    return local.toISOString().slice(0, 10);
}

function formatDisplayDate(isoDate) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate || "");
    return match ? `${match[2]}/${match[3]}/${match[1]}` : "";
}

function parseDateEntry(value, fieldLabel, required = false) {
    const text = String(value || "").trim();
    if (!text && !required) return null;
    let year, month, day;
    let match = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(text);
    if (match) [, year, month, day] = match;
    else {
        match = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text);
        if (match) [, month, day, year] = match;
    }
    if (!match) throw new Error(`${fieldLabel} must use MM/DD/YYYY or YYYY-MM-DD.`);
    const iso = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const parsed = new Date(`${iso}T00:00:00`);
    if (Number.isNaN(parsed.getTime()) || parsed.getFullYear() !== Number(year) || parsed.getMonth() + 1 !== Number(month) || parsed.getDate() !== Number(day)) {
        throw new Error(`${fieldLabel} is not a valid calendar date.`);
    }
    return iso;
}

function openDatePicker(input) {
    if (!input) return;
    if (typeof input.showPicker === "function") input.showPicker();
    else input.click();
}

function openLifecycleEditor(jobId) {
    const job = currentJobs.get(Number(jobId));
    if (!job) return;
    lifecycleJobId = Number(jobId);
    const isPreApplication = ["matched", "tailored", "form_filled", "submitted"].includes(job.status);
    document.getElementById("lifecycle-modal-title").innerText = isPreApplication ? "Mark Job as Applied" : "Update Application Status";
    document.getElementById("lifecycle-job-label").innerText = `${job.company} — ${job.title}`;
    lifecycleStatus.value = isPreApplication ? "applied" : job.status;
    lifecycleAppliedOn.value = formatDisplayDate(job.date_applied || todayIsoDate());
    lifecycleAppliedCalendar.value = job.date_applied || todayIsoDate();
    const storedMethod = (job.application_method || "").replace(/^manual:/, "");
    lifecycleMethod.value = ["company_site", "job_board", "email", "recruiter", "referral", "other"].includes(storedMethod)
        ? storedMethod : "company_site";
    lifecycleFollowUp.value = formatDisplayDate(job.follow_up_date || "");
    lifecycleFollowUpCalendar.value = job.follow_up_date || "";
    lifecycleNotes.value = job.notes || "";
    lifecycleModal.classList.add("active");
}

function hideLifecycleModal() {
    lifecycleModal.classList.remove("active");
    lifecycleJobId = null;
}

async function saveLifecycleChange(event) {
    event.preventDefault();
    if (!lifecycleJobId) return;
    const appliedStatuses = ["applied", "interview", "offer", "rejected", "withdrawn", "closed"];
    let payload;
    try {
        payload = {
            status: lifecycleStatus.value,
            applied_on: appliedStatuses.includes(lifecycleStatus.value) ? parseDateEntry(lifecycleAppliedOn.value, "Application date", true) : null,
            method: lifecycleMethod.value || null,
            notes: lifecycleNotes.value.trim() || null,
            follow_up_on: parseDateEntry(lifecycleFollowUp.value, "Follow-up date")
        };
    } catch (error) {
        alert(error.message);
        return;
    }
    showLoading("Saving Application Status...", "Recording the lifecycle change and audit history.");
    try {
        const response = await fetch(`${API_URL}/api/jobs/${lifecycleJobId}/lifecycle`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Could not update application status.");
        hideLoading();
        hideLifecycleModal();
        await Promise.all([loadJobs(), loadLogs(), updateDashboardStats()]);
        logActivity("Application Status Updated", `Status changed to ${result.status.replaceAll("_", " ")}.`, "success");
    } catch (error) {
        hideLoading();
        alert(error.message);
    }
}

async function undoLifecycleChange() {
    if (!lifecycleJobId || !confirm("Undo the most recent recorded status change for this job?")) return;
    try {
        const response = await fetch(`${API_URL}/api/jobs/${lifecycleJobId}/lifecycle/undo`, { method: "POST" });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "No status change is available to undo.");
        hideLifecycleModal();
        await Promise.all([loadJobs(), loadLogs(), updateDashboardStats()]);
        logActivity("Application Status Restored", `Restored status to ${result.status.replaceAll("_", " ")}.`, "success");
    } catch (error) {
        alert(error.message);
    }
}

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
        dashboard: { title: "Dashboard Overview", sub: "Track tailored materials and confirmed application progress." },
        profile: { title: "Profile & Resume", sub: "Setup your contact information and base resume." },
        search: { title: "Search & Match Jobs", sub: "Find Greenhouse, Lever, Ashby, and SmartRecruiters openings matching your skill set." },
        logs: { title: "Application Logs", sub: "View history and download tailored resumes and cover letters." }
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
    if (!toggleApiVisibilityBtn || !toggleGoogleApiVisibilityBtn) return;
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
function showLoading(title, subtitle = "Please wait while JobApplier prepares your request.", operation = null) {
    loadingTitle.innerText = title;
    loadingSubtitle.innerText = subtitle;
    activeLoadingOperation = operation;
    loadingActions.hidden = !operation;
    stopLoadingBtn.disabled = false;
    stopLoadingBtn.replaceChildren(
        createElement("i", "fa-solid fa-stop"),
        document.createTextNode(" Stop")
    );
    loadingModal.classList.add("active");
}

function hideLoading() {
    loadingModal.classList.remove("active");
    loadingActions.hidden = true;
    activeLoadingOperation = null;
}

function showCancellableLoading(title, subtitle) {
    const operation = {
        id: crypto.randomUUID(),
        stopRequested: false
    };
    showLoading(title, subtitle, operation);
    return operation;
}

function operationHeaders(operation, headers = {}) {
    return { ...headers, "X-JobApplier-Operation": operation.id };
}

async function stopActiveLoadingOperation() {
    const operation = activeLoadingOperation;
    if (!operation || operation.stopRequested) return;
    operation.stopRequested = true;
    stopLoadingBtn.disabled = true;
    stopLoadingBtn.replaceChildren(
        createElement("i", "fa-solid fa-spinner fa-spin"),
        document.createTextNode(" Stopping...")
    );
    loadingTitle.innerText = "Stopping...";
    loadingSubtitle.innerText = "Finishing the current step, then stopping without discarding completed work.";
    try {
        const response = await fetch(`${API_URL}/api/operations/${operation.id}/cancel`, { method: "POST" });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "The stop request could not be sent.");
        if (!result.active && activeLoadingOperation === operation) {
            loadingSubtitle.innerText = "The operation has already finished. Finalizing the result...";
        }
    } catch (error) {
        operation.stopRequested = false;
        stopLoadingBtn.disabled = false;
        stopLoadingBtn.replaceChildren(
            createElement("i", "fa-solid fa-stop"),
            document.createTextNode(" Stop")
        );
        loadingTitle.innerText = "Still working...";
        loadingSubtitle.innerText = error.message || "The stop request could not be sent.";
    }
}

function showTailorModal(resumeMarkdown, coverLetterText, jobId) {
    tailoredResumeDisplay.value = resumeMarkdown;
    coverLetterDisplay.value = coverLetterText;
    downloadResumeBtn.href = `${API_URL}/api/jobs/${jobId}/materials/resume`;
    downloadCoverLetterBtn.href = `${API_URL}/api/jobs/${jobId}/materials/cover-letter`;
    openManualApplicationBtn.href = `${API_URL}/api/jobs/${jobId}/apply-manually`;
    tailorModal.classList.add("active");
}

function hideTailorModal() {
    tailorModal.classList.remove("active");
}

function hideCleanupModal() {
    cleanupModal.classList.remove("active");
    cleanupActionsReady = false;
    if (cleanupActionEnableTimer) clearTimeout(cleanupActionEnableTimer);
}

function showJobImportMessage(message, isWarning = false) {
    jobImportMessage.replaceChildren(createElement("strong", "", isWarning ? "Review needed" : "Preview ready"));
    jobImportMessage.appendChild(createElement("p", "margin-top-sm", message));
    jobImportMessage.hidden = false;
}

function showJobImportModal() {
    jobImportForm.reset();
    jobImportCanonicalUrl = null;
    jobImportFields.hidden = true;
    jobImportMessage.hidden = true;
    saveJobImportBtn.disabled = true;
    jobImportModal.classList.add("active");
    jobImportUrl.focus();
}

function hideJobImportModal() {
    jobImportModal.classList.remove("active");
    jobImportCanonicalUrl = null;
}

function invalidateJobImportPreview() {
    if (!jobImportCanonicalUrl) return;
    jobImportCanonicalUrl = null;
    jobImportFields.hidden = true;
    jobImportMessage.hidden = true;
    saveJobImportBtn.disabled = true;
}

async function previewJobImport() {
    const url = jobImportUrl.value.trim();
    if (!url) {
        jobImportUrl.reportValidity();
        return;
    }
    const operation = showCancellableLoading("Previewing Job Posting...", "Validating the public URL and extracting available job details.");
    try {
        const response = await fetch(`${API_URL}/api/jobs/import/preview`, {
            method: "POST",
            headers: operationHeaders(operation, { "Content-Type": "application/json" }),
            body: JSON.stringify({ url })
        });
        const result = await response.json();
        hideLoading();
        if (!response.ok) throw new Error(result.detail || "Could not preview this posting.");
        if (result.cancelled) {
            showJobImportMessage(result.message || "Preview stopped.", true);
            return;
        }
        if (result.duplicate) {
            const existing = result.existing_job || {};
            jobImportCanonicalUrl = null;
            jobImportFields.hidden = true;
            saveJobImportBtn.disabled = true;
            showJobImportMessage(`This URL is already saved as job #${existing.id}: ${existing.company || "Unknown company"} — ${existing.title || "Untitled role"}.`, true);
            return;
        }
        if (result.suppressed) {
            jobImportCanonicalUrl = null;
            jobImportFields.hidden = true;
            saveJobImportBtn.disabled = true;
            showJobImportMessage(result.message, true);
            return;
        }

        const job = result.job || {};
        jobImportCanonicalUrl = job.url;
        jobImportUrl.value = job.url || url;
        jobImportCompany.value = job.company || "";
        jobImportTitle.value = job.title || "";
        jobImportLocation.value = job.location || "";
        jobImportCompensation.value = job.compensation || "";
        jobImportWorkArrangement.value = job.work_arrangement || "";
        jobImportEmploymentType.value = job.employment_type || "";
        jobImportDescription.value = job.description || "";
        jobImportFields.hidden = false;
        saveJobImportBtn.disabled = false;
        showJobImportMessage(result.message, !result.extraction_succeeded);
        const firstMissing = [jobImportCompany, jobImportTitle, jobImportDescription].find(field => !field.value.trim());
        if (firstMissing) firstMissing.focus();
    } catch (error) {
        hideLoading();
        jobImportCanonicalUrl = null;
        jobImportFields.hidden = true;
        saveJobImportBtn.disabled = true;
        showJobImportMessage(error.message, true);
    }
}

async function saveJobImport(event) {
    event.preventDefault();
    if (!jobImportCanonicalUrl || !jobImportForm.reportValidity()) return;
    const operation = showCancellableLoading("Saving Job Posting...", "Saving the reviewed details and analyzing the match when AI is configured.");
    try {
        const response = await fetch(`${API_URL}/api/jobs/import`, {
            method: "POST",
            headers: operationHeaders(operation, { "Content-Type": "application/json" }),
            body: JSON.stringify({
                url: jobImportCanonicalUrl,
                company: jobImportCompany.value.trim(),
                title: jobImportTitle.value.trim(),
                description: jobImportDescription.value.trim(),
                location: jobImportLocation.value.trim(),
                compensation: jobImportCompensation.value.trim(),
                work_arrangement: jobImportWorkArrangement.value,
                employment_type: jobImportEmploymentType.value
            })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Could not save this posting.");
        hideLoading();
        if (result.cancelled && !result.saved) {
            showJobImportMessage(result.message || "Job import stopped.", true);
            return;
        }
        hideJobImportModal();
        const [viewChange] = await Promise.all([
            loadJobs({ revealJobId: result.job_id }),
            updateDashboardStats()
        ]);
        logActivity("Job Imported", `Saved manually added job #${result.job_id}.`, "success");
        const viewMessage = viewChange ? `\n\n${viewChange}` : "";
        alert(`${result.message}${viewMessage}`);
    } catch (error) {
        hideLoading();
        showJobImportMessage(error.message, true);
    }
}

async function showCleanupPreview() {
    showLoading("Preparing Cleanup Preview...", "Counting only matched jobs with no application history or generated materials.");
    try {
        const res = await fetch(`${API_URL}/api/jobs/cleanup-preview`);
        const result = await res.json();
        if (!res.ok) throw new Error(result.detail || "Could not prepare cleanup preview.");

        cleanupPreview = result;
        document.getElementById("cleanup-definition").innerText = result.definition;
        document.getElementById("cleanup-archive-count").innerText = result.actions.archive.count;
        document.getElementById("cleanup-delete-count").innerText = result.actions.delete.count;
        document.getElementById("cleanup-protected-count").innerText = result.protected_count;
        document.getElementById("cleanup-restore-count").innerText = result.actions.restore.count;

        // Keep action buttons inert while the modal opens. This prevents the
        // click that opened the preview from landing on a newly overlaid action.
        cleanupActionsReady = false;
        archiveUntouchedBtn.disabled = true;
        deleteUntouchedBtn.disabled = true;
        restoreArchivedBtn.disabled = true;

        const sampleList = document.getElementById("cleanup-sample-list");
        sampleList.replaceChildren();
        result.sample.forEach(job => {
            const item = document.createElement("li");
            item.textContent = `${job.company || "Unknown company"} - ${job.title || "Untitled role"}`;
            sampleList.appendChild(item);
        });
        document.getElementById("cleanup-sample-container").style.display = result.sample.length ? "block" : "none";

        await loadJobSuppressions();
        hideLoading();
        cleanupModal.classList.add("active");
        cleanupActionEnableTimer = setTimeout(() => {
            cleanupActionsReady = true;
            archiveUntouchedBtn.disabled = result.actions.archive.count === 0;
            deleteUntouchedBtn.disabled = result.actions.delete.count === 0;
            restoreArchivedBtn.disabled = result.actions.restore.count === 0;
        }, 500);
    } catch (error) {
        hideLoading();
        console.error(error);
        alert(error.message || "Failed to prepare cleanup preview.");
    }
}

async function loadJobSuppressions() {
    const response = await fetch(`${API_URL}/api/job-suppressions`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not load suppressed postings.");

    suppressionCount.textContent = String(result.count || 0);
    suppressionList.replaceChildren();
    suppressionEmpty.hidden = result.items.length > 0;
    clearAllSuppressionsBtn.disabled = result.count === 0;
    result.items.forEach(item => {
        const row = createElement("li", "suppression-item");
        const details = createElement("div");
        details.appendChild(createElement("strong", "", `${item.company || "Unknown company"} — ${item.title || "Untitled role"}`));
        const source = item.deletion_source === "bulk_cleanup" ? "bulk cleanup" : "manual deletion";
        details.appendChild(createElement("span", "", `${item.hostname} · ${item.deleted_at} · ${source}`));
        const clearButton = createActionButton("Allow again", "fa-solid fa-rotate-left", "btn btn-secondary btn-sm", () => clearJobSuppression(item.id));
        row.append(details, clearButton);
        suppressionList.appendChild(row);
    });
}

async function clearJobSuppression(suppressionId) {
    const response = await fetch(`${API_URL}/api/job-suppressions/${suppressionId}`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) {
        alert(result.detail || "Could not clear the posting suppression.");
        return;
    }
    await loadJobSuppressions();
}

async function clearAllJobSuppressions() {
    if (!confirm("Allow all previously deleted postings to appear in future searches again?")) return;
    const response = await fetch(`${API_URL}/api/job-suppressions`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) {
        alert(result.detail || "Could not clear posting suppressions.");
        return;
    }
    await loadJobSuppressions();
}

async function runCleanupAction(action) {
    if (!cleanupPreview || !cleanupActionsReady) return;
    const preview = cleanupPreview.actions[action];
    if (!preview || preview.count === 0) return;

    const labels = { archive: "archive", delete: "permanently delete", restore: "restore" };
    if (!confirm(`This will ${labels[action]} exactly ${preview.count} untouched job(s). Continue?`)) return;
    if (action === "delete" && !confirm("Final confirmation: permanent deletion cannot be undone. Delete the previewed records?")) return;

    hideCleanupModal();
    showLoading(`${labels[action][0].toUpperCase()}${labels[action].slice(1)} Jobs...`, "Applying the previously previewed candidate set in one transaction.");
    try {
        const res = await fetch(`${API_URL}/api/jobs/cleanup`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action, preview_token: preview.preview_token })
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.detail || "Cleanup failed.");

        hideLoading();
        cleanupPreview = null;
        logActivity("Job Cleanup Complete", result.message, action === "delete" ? "warning" : "success");
        await loadJobs();
        await loadLogs();
        await updateDashboardStats();
        alert(result.message);
    } catch (error) {
        hideLoading();
        console.error(error);
        alert(`${error.message}\n\nRefresh the cleanup preview before trying again.`);
    }
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
    const icon = createElement("div", "timeline-icon");
    if (type === "success") icon.style.cssText = "border-color: var(--color-green); color: var(--color-green);";
    if (type === "error") icon.style.cssText = "border-color: var(--color-red); color: var(--color-red);";
    icon.appendChild(createElement("i", icons[type] || icons.info));
    const content = createElement("div", "timeline-content");
    content.appendChild(createElement("p", "timeline-title", String(title)));
    content.appendChild(createElement("p", "timeline-desc", String(desc)));
    content.appendChild(createElement("span", "timeline-time", new Date().toLocaleTimeString()));
    item.append(icon, content);
    
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

function updateSecretFieldStatus(input, status, help, configured, providerName, configuredHelp, missingHelp) {
    input.placeholder = configured ? "Saved — enter a new key to replace" : `Enter ${providerName} API key`;
    status.className = `secret-key-status ${configured ? "configured" : "missing"}`;
    status.textContent = configured ? "Saved" : "Not saved";
    help.textContent = configured ? configuredHelp : missingHelp;
}

function updateSecretStatuses() {
    updateSecretFieldStatus(
        pApiKey,
        pGeminiKeyStatus,
        pGeminiKeyHelp,
        geminiKeyConfigured,
        "Gemini",
        "Saved locally. Leave this field blank to keep the current key, or enter a new key to replace it.",
        "Required for AI matching and tailoring. The key is stored only on this machine.",
    );
    updateSecretFieldStatus(
        pGoogleApiKey,
        pGoogleKeyStatus,
        pGoogleKeyHelp,
        googleMapsKeyConfigured,
        "optional Google Maps",
        "Saved locally. Leave this field blank to keep the current key, or enter a new key to replace it.",
        "Optional. Used by Google Places to resolve headquarters street addresses.",
    );
    updateApiKeyStatus(geminiKeyConfigured);
}

function markSecretStatusesUnavailable() {
    [pGeminiKeyStatus, pGoogleKeyStatus].forEach(status => {
        status.className = "secret-key-status unavailable";
        status.textContent = "Status unavailable";
    });
}

function updateStartupActivity(profile) {
    const title = document.getElementById("startup-activity-title");
    const description = document.getElementById("startup-activity-description");
    if (!title || !description) return;

    const profileReady = !!(profile?.name && profile?.email && profile?.base_resume_text);
    if (profileReady) {
        title.textContent = "Profile Loaded";
        description.textContent = geminiKeyConfigured
            ? "Your saved profile is ready to search, tailor materials, and track applications."
            : "Your saved profile is loaded. Add a Gemini API key when you are ready to match and tailor jobs.";
    } else {
        title.textContent = "Profile Setup Needed";
        description.textContent = "Add your name, email, and base resume in Profile & Resume to get started.";
    }
}

// ----------------------------------------------------
// API Communication Logic
// ----------------------------------------------------

// Load Candidate Profile
async function loadProfile() {
    try {
        const res = await fetch(`${API_URL}/api/profile`);
        if (!res.ok) throw new Error(`Profile API returned HTTP ${res.status}`);
        const profile = await res.json();
        
        if (profile) {
            pName.value = profile.name || "";
            pEmail.value = profile.email || "";
            pPhone.value = profile.phone || "";
            pGithub.value = profile.github || "";
            pLinkedin.value = profile.linkedin || "";
            pWebsite.value = profile.website || "";
            pApiKey.value = "";
            pGoogleApiKey.value = "";
            geminiKeyConfigured = !!profile.gemini_api_key_configured;
            googleMapsKeyConfigured = !!profile.google_maps_api_key_configured;
            updateSecretStatuses();
            updateStartupActivity(profile);
            pResume.value = profile.base_resume_text || "";
            pResumeMode.value = profile.resume_mode || "general_professional";
            pPreferUsHeadquarters.checked = profile.prefer_us_headquarters !== 0;
            
            userDisplayName.innerText = profile.name || "Candidate";
            // Sync dashboard statistics
            updateDashboardStats();
        }
    } catch (e) {
        console.error("Failed to load profile", e);
        const indicator = apiStatusBadge?.querySelector(".status-indicator");
        const statusText = apiStatusBadge?.querySelector(".status-text");
        if (indicator) indicator.className = "status-indicator red";
        if (statusText) statusText.innerText = "Profile API Unavailable";
        markSecretStatusesUnavailable();
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
        resume_mode: pResumeMode.value,
        prefer_us_headquarters: pPreferUsHeadquarters.checked
    };
    
    try {
        const res = await fetch(`${API_URL}/api/profile`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const result = await res.json();
        if (!res.ok) throw new Error(result.detail || "Could not save profile.");

        const secretPayload = {};
        if (pApiKey.value.trim()) secretPayload.gemini_api_key = pApiKey.value.trim();
        if (pGoogleApiKey.value.trim()) secretPayload.google_maps_api_key = pGoogleApiKey.value.trim();
        if (Object.keys(secretPayload).length) {
            const secretResponse = await fetch(`${API_URL}/api/profile/secrets`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(secretPayload)
            });
            const secretResult = await secretResponse.json();
            if (!secretResponse.ok) throw new Error(secretResult.detail || "Could not update API keys.");
            geminiKeyConfigured = !!secretResult.gemini_api_key_configured;
            googleMapsKeyConfigured = !!secretResult.google_maps_api_key_configured;
            pApiKey.value = "";
            pGoogleApiKey.value = "";
        }
        
        if (result.success) {
            userDisplayName.innerText = payload.name || "Candidate";
            updateSecretStatuses();
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
    
    const operation = showCancellableLoading("Parsing Resume File...", `Reading ${file.name}`);
    
    const formData = new FormData();
    formData.append("file", file);
    
    try {
        const res = await fetch(`${API_URL}/api/profile/upload-resume`, {
            method: "POST",
            headers: operationHeaders(operation),
            body: formData
        });
        
        const result = await res.json();
        if (result.cancelled) {
            alert(result.message || "Resume import stopped.");
        } else if (result.success) {
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
async function loadJobs(options = {}) {
    try {
        const res = await fetch(`${API_URL}/api/jobs`);
        const jobs = await res.json();
        loadedJobs = jobs || [];
        currentJobs = new Map((jobs || []).map(job => [Number(job.id), job]));
        return renderFilteredJobs(options.revealJobId || null);
    } catch (e) {
        console.error(e);
        logActivity("Error Loading Jobs", "Could not query matched jobs list.", "error");
    }
}

function renderFilteredJobs(revealJobId = null) {
    jobsTableBody.replaceChildren();
    const revealedJob = revealJobId
        ? loadedJobs.find(job => Number(job.id) === Number(revealJobId))
        : null;
    const viewChanges = [];
    if (revealedJob) {
        const currentMinimum = Number(jobMinScore?.value || 40);
        const revealedScore = revealedJob.match_score === null ? null : Number(revealedJob.match_score);
        if (revealedScore !== null && revealedScore < currentMinimum) {
            let showAllOption = Array.from(jobMinScore.options).find(option => option.value === "0");
            if (!showAllOption) {
                showAllOption = new Option("All scores (imported job)", "0");
                jobMinScore.prepend(showAllOption);
            }
            jobMinScore.value = "0";
            viewChanges.push("The minimum-score filter was changed to All scores so the imported job is visible.");
        }
        const statusFilter = jobStatusFilter?.value || "";
        const statusMatches = !statusFilter
            || (statusFilter === "applied" && ["applied", "interview", "offer", "rejected", "withdrawn", "closed"].includes(revealedJob.status))
            || statusFilter === revealedJob.status;
        if (!statusMatches) {
            jobStatusFilter.value = "";
            viewChanges.push("The status filter was reset to show the imported job.");
        }
    }
    const minimumScore = Number(jobMinScore?.value || 40);
    const statusFilter = jobStatusFilter?.value || "";
    let jobs = loadedJobs.filter(job => job.match_score === null || Number(job.match_score) >= minimumScore);
    if (statusFilter === "applied") {
        jobs = jobs.filter(job => ["applied", "interview", "offer", "rejected", "withdrawn", "closed"].includes(job.status));
    } else if (statusFilter) {
        jobs = jobs.filter(job => job.status === statusFilter);
    }
    const order = jobSortOrder?.value || "score_desc";
    jobs.sort((a, b) => order === "company"
        ? String(a.company).localeCompare(String(b.company))
        : order === "newest"
            ? String(b.date_found || "").localeCompare(String(a.date_found || ""))
            : Number(b.match_score ?? -1) - Number(a.match_score ?? -1));
    const resultCount = document.getElementById("job-result-count");
    if (resultCount) resultCount.textContent = `${jobs.length} of ${loadedJobs.length} jobs`;

    if (jobs.length === 0) {
            jobsTableBody.innerHTML = `
                <tr>
                    <td colspan="7" class="table-empty-state">
                        <i class="fa-solid fa-briefcase"></i>
                        <p>No jobs match the current filters.</p>
                    </td>
                </tr>
            `;
        return viewChanges.join(" ");
    }
    jobs.forEach(job => jobsTableBody.appendChild(buildJobRow(job)));
    if (revealedJob) {
        requestAnimationFrame(() => {
            const row = jobsTableBody.querySelector(`[data-job-id="${Number(revealedJob.id)}"]`);
            if (!row) return;
            row.classList.add("job-row-revealed");
            row.focus({ preventScroll: true });
            row.scrollIntoView({
                behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
                block: "center"
            });
            window.setTimeout(() => row.classList.remove("job-row-revealed"), 5000);
        });
    }
    return viewChanges.join(" ");
}

function buildJobRow(job) {
    const jobId = Number(job.id);
    const tr = createElement("tr");
    tr.dataset.jobId = String(jobId);
    tr.tabIndex = -1;
    const companyCell = createElement("td");
    companyCell.appendChild(createElement("strong", "", String(job.company || "Unknown company")));
    tr.appendChild(companyCell);
    tr.appendChild(createElement("td", "", String(job.title || "Untitled role")));

    const detailsCell = createElement("td", "job-details-cell");
    const detailParts = [
        job.location || "Location not listed",
        [job.work_arrangement, job.employment_type].filter(Boolean).map(value => String(value).replaceAll("_", " ")).join(" · "),
        job.compensation || "",
        [job.source || "unknown source", job.date_found || "date unknown"].join(" · ")
    ].filter(Boolean);
    detailParts.forEach(part => detailsCell.appendChild(createElement("span", "", String(part))));
    tr.appendChild(detailsCell);

    const hasScore = job.match_score !== null && job.match_score !== undefined;
    const score = hasScore ? Number(job.match_score) : null;
    const scoreClass = !hasScore ? "unscored" : score >= 80 ? "high" : score >= 50 ? "medium" : "low";
    const scoreCell = createElement("td");
    scoreCell.appendChild(createElement("span", `match-badge ${scoreClass}`, hasScore ? `${score}%` : "Unscored"));
    tr.appendChild(scoreCell);

    const linkCell = createElement("td");
    const parsedUrl = safeHttpUrl(job.url);
    if (parsedUrl) {
        const link = createElement("a", "job-link");
        link.href = parsedUrl.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.append(createElement("i", "fa-solid fa-arrow-up-right-from-square"), document.createTextNode(` ${parsedUrl.hostname.replace("www.", "")}`));
        linkCell.appendChild(link);
    } else {
        linkCell.appendChild(createElement("span", "text-muted", "Invalid link"));
    }
    tr.appendChild(linkCell);

    const statusLabels = {
        matched: "Matched", tailored: "Tailored", form_filled: "Form Filled",
        submitted: "Submitted — Unverified", applied: "Applied", interview: "Interview",
        offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn", closed: "Closed"
    };
    const statusClass = job.status === "matched" ? "badge-matched"
        : ["tailored", "form_filled", "submitted"].includes(job.status) ? "badge-tailored" : "badge-applied";
    const statusCell = createElement("td");
    statusCell.appendChild(createElement("span", `badge ${statusClass}`, statusLabels[job.status] || String(job.status)));
    tr.appendChild(statusCell);

    const actionsCell = createElement("td", "actions-col");
    const actions = createElement("div", "table-actions");
    if (job.status === "matched") {
        actions.appendChild(createActionButton("Tailor Materials", "fa-solid fa-wand-magic-sparkles", "btn btn-secondary btn-sm", () => tailorResumeForJob(jobId)));
        actions.appendChild(createActionButton("Mark Applied", "fa-solid fa-circle-check", "btn btn-secondary btn-sm", () => openLifecycleEditor(jobId)));
    } else if (["tailored", "form_filled", "submitted"].includes(job.status)) {
        actions.appendChild(createActionButton("View Materials", "fa-solid fa-eye", "btn btn-secondary btn-sm", () => viewTailoredMaterials(jobId)));
        actions.appendChild(createActionButton("Apply Manually", "fa-solid fa-arrow-up-right-from-square", "btn btn-primary btn-sm", () => viewTailoredMaterials(jobId)));
        actions.appendChild(createActionButton("Mark Applied", "fa-solid fa-circle-check", "btn btn-secondary btn-sm", () => openLifecycleEditor(jobId)));
    } else {
        if (job.has_materials) {
            actions.appendChild(createActionButton("View Materials", "fa-solid fa-eye", "btn btn-secondary btn-sm", () => viewTailoredMaterials(jobId)));
        }
        actions.appendChild(createActionButton("Update Status", "fa-solid fa-pen", "btn btn-secondary btn-sm", () => openLifecycleEditor(jobId)));
    }
    actions.appendChild(createActionButton("", "fa-solid fa-rotate", "btn btn-secondary btn-sm btn-icon-only", () => verifyJobPosting(jobId), "Verify listing is active"));
    actions.appendChild(createActionButton("", "fa-solid fa-trash", "btn btn-danger btn-sm btn-icon-only", () => deleteJobRecord(jobId), "Delete Job"));
    actionsCell.appendChild(actions);
    tr.appendChild(actionsCell);
    return tr;
}

async function verifyJobPosting(jobId) {
    const operation = showCancellableLoading("Verifying Listing...", "Rechecking the employer posting without changing application history.");
    try {
        const response = await fetch(`${API_URL}/api/jobs/${jobId}/verify`, {
            method: "POST",
            headers: operationHeaders(operation)
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Could not verify listing.");
        hideLoading();
        if (result.cancelled) {
            logActivity("Listing Verification Stopped", result.message, "warning");
            alert(result.message);
            return;
        }
        await Promise.all([loadJobs(), loadLogs()]);
        logActivity("Listing Verification", result.message, result.expired ? "warning" : "success");
        alert(result.message);
    } catch (error) {
        hideLoading();
        alert(error.message);
    }
}

async function loadSavedSearches() {
    if (!savedSearchSelect) return;
    const response = await fetch(`${API_URL}/api/saved-searches`);
    if (!response.ok) return;
    const searches = await response.json();
    savedSearchSelect.replaceChildren(new Option("Select a saved search", ""));
    searches.forEach(search => {
        const option = new Option(search.name, String(search.id));
        option.dataset.keywords = search.keywords;
        option.dataset.location = search.location;
        option.dataset.frequency = search.schedule_frequency || "none";
        savedSearchSelect.appendChild(option);
    });
    deleteSavedSearchBtn.disabled = true;
}

function selectSavedSearch() {
    const option = savedSearchSelect.selectedOptions[0];
    const selected = !!option?.value;
    deleteSavedSearchBtn.disabled = !selected;
    if (selected) {
        sKeywords.value = option.dataset.keywords || "";
        sLocation.value = option.dataset.location || "";
        savedSearchFrequency.value = option.dataset.frequency || "none";
    }
}

async function checkDueSavedSearches() {
    if (!savedSearchAlerts) return;
    try {
        const response = await fetch(`${API_URL}/api/saved-searches/due`);
        if (!response.ok) return;
        const due = await response.json();
        savedSearchAlerts.replaceChildren();
        if (!due.length) {
            savedSearchAlerts.hidden = true;
            return;
        }
        savedSearchAlerts.hidden = false;
        savedSearchAlerts.appendChild(createElement("strong", "", "Saved searches are due"));
        const list = createElement("ul", "margin-top-sm");
        due.forEach(search => list.appendChild(createElement("li", "", `${search.name} (${search.schedule_frequency})`)));
        savedSearchAlerts.appendChild(list);
    } catch (error) {
        console.error("Could not check saved-search reminders", error);
    }
}

async function deleteSelectedSavedSearch() {
    const searchId = Number(savedSearchSelect.value);
    if (!searchId || !confirm("Remove this saved search? Existing jobs will not be affected.")) return;
    const response = await fetch(`${API_URL}/api/saved-searches/${searchId}`, { method: "DELETE" });
    if (response.ok) await loadSavedSearches();
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
    
    const operation = showCancellableLoading("Searching & Analyzing Jobs...", "AI is extracting keywords from your resume, crawling Yahoo, and scoring postings.");
    logActivity("Job Search Started", logMsg, "info");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/search`, {
            method: "POST",
            headers: operationHeaders(operation, { "Content-Type": "application/json" }),
            body: JSON.stringify({
                keywords,
                location,
                save_search: !!saveSearchCheckbox?.checked,
                saved_search_name: savedSearchName?.value.trim() || null,
                saved_search_id: Number(savedSearchSelect?.value) || null,
                schedule_frequency: savedSearchFrequency?.value || "none"
            })
        });
        const result = await res.json();
        
        if (result.cancelled) {
            hideLoading();
            logActivity("Job Search Stopped", result.message || "Search stopped.", "warning");
        } else if (result.success) {
            if (saveSearchCheckbox?.checked) await loadSavedSearches();
            await checkDueSavedSearches();
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
                        if (status.last_result?.cancelled) {
                            logActivity("Job Search Stopped", status.last_result.message, "warning");
                        } else if (status.last_result?.success === false) {
                            logActivity("Search Failed", status.last_result.error || "Search did not complete.", "error");
                            alert(status.last_result.error || "Search did not complete.");
                        } else {
                            logActivity("Job Search Complete", "Found and analyzed new job openings.", "success");
                            renderProviderAlerts(status.last_result);
                        }
                        await refreshSourceDiagnosticCount();
                    }
                } catch (err) {
                    console.error("Error polling search status:", err);
                }
            }, 3000);
        } else {
            hideLoading();
            alert(result.message || result.error || "Search failed.");
        }
    } catch (err) {
        hideLoading();
        console.error(err);
        logActivity("Search Failed", "An error occurred initiating the browser search crawl.", "error");
    }
}

function renderProviderAlerts(searchResult) {
    if (!providerAlerts) return;
    providerAlerts.replaceChildren();
    const alerts = searchResult?.provider_alerts || [];
    if (!alerts.length) {
        providerAlerts.hidden = true;
        return;
    }
    providerAlerts.hidden = false;
    const informationalCodes = new Set(["stale_postings", "partial_results"]);
    const needsAttention = alerts.some(alert => !informationalCodes.has(alert.code));
    const header = createElement("div", "provider-alerts-header");
    header.appendChild(createElement("strong", "", needsAttention ? "Some job sources may need attention" : "Search notes"));
    const actions = createElement("div", "provider-alert-actions");
    const reviewButton = createActionButton("Review history", "fa-solid fa-clock-rotate-left", "btn btn-secondary btn-sm", showSourceDiagnostics);
    const dismissButton = createActionButton("", "fa-solid fa-xmark", "provider-alert-dismiss", dismissProviderAlerts, "Dismiss this source notice");
    dismissButton.setAttribute("aria-label", "Dismiss this source notice");
    actions.append(reviewButton, dismissButton);
    header.appendChild(actions);
    providerAlerts.appendChild(header);
    const list = createElement("ul", "margin-top-sm");
    alerts.forEach(alert => list.appendChild(createElement("li", "", alert.message)));
    providerAlerts.appendChild(list);
}

function dismissProviderAlerts() {
    providerAlerts.hidden = true;
    openSourceDiagnosticsBtn.focus();
}

const DIAGNOSTIC_CODE_LABELS = {
    url_format_drift: "Job URL format changed",
    content_format_drift: "Posting data format changed",
    access_challenge: "Automated reading blocked",
    provider_error: "Provider request error",
    stale_postings: "Stale postings skipped",
    partial_results: "Partial search results"
};

const DIAGNOSTIC_COUNTER_LABELS = {
    raw_candidates: "Raw candidates",
    valid_discovered: "Valid posting URLs",
    new_candidates: "New candidates",
    accepted: "Accepted postings",
    rejected: "Rejected postings",
    skipped_active: "Already active",
    skipped_archived: "Already archived",
    skipped_suppressed: "Previously deleted",
    api_fallbacks: "API fallbacks",
    stale: "Stale postings",
    format_drift: "Format mismatches",
    access_challenge: "Access challenges",
    embedded_content: "Protected frames",
    provider_error: "Provider errors",
    oversized_responses: "Oversized responses",
    candidate_limit_hits: "Candidate limits reached",
    timeouts: "Timeouts",
    search_errors: "Search errors",
    candidate_budget_exhausted: "Run limit reached"
};

async function refreshSourceDiagnosticCount() {
    try {
        const response = await fetch(`${API_URL}/api/source-diagnostics?limit=1`);
        if (!response.ok) return;
        const result = await response.json();
        sourceDiagnosticsCount.textContent = String(result.count || 0);
        sourceDiagnosticsCount.hidden = result.count === 0;
    } catch {
        // Diagnostic history must never interfere with the primary workflow.
    }
}

async function showSourceDiagnostics(event) {
    sourceDiagnosticsOpener = event?.currentTarget || openSourceDiagnosticsBtn;
    sourceDiagnosticsEmpty.hidden = false;
    sourceDiagnosticsEmpty.textContent = "Loading source diagnostic history...";
    sourceDiagnosticsList.replaceChildren();
    sourceDiagnosticsModal.classList.add("active");
    document.getElementById("close-source-diagnostics-modal").focus();
    try {
        await loadSourceDiagnostics();
    } catch (error) {
        sourceDiagnosticsEmpty.hidden = false;
        sourceDiagnosticsEmpty.textContent = error.message || "Source diagnostic history could not be loaded.";
    }
}

function hideSourceDiagnostics() {
    sourceDiagnosticsModal.classList.remove("active");
    if (sourceDiagnosticsOpener?.isConnected) sourceDiagnosticsOpener.focus();
}

async function loadSourceDiagnostics() {
    const response = await fetch(`${API_URL}/api/source-diagnostics?limit=100`);
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Source diagnostic history could not be loaded.");

    sourceDiagnosticsList.replaceChildren();
    sourceDiagnosticsEmpty.hidden = result.items.length > 0;
    sourceDiagnosticsEmpty.textContent = "No source diagnostics have been recorded.";
    clearSourceDiagnosticsBtn.disabled = result.count === 0;
    exportSourceDiagnosticsBtn.hidden = result.count === 0;
    sourceDiagnosticsCount.textContent = String(result.count || 0);
    sourceDiagnosticsCount.hidden = result.count === 0;

    result.items.forEach(item => {
        const row = createElement("li", "source-diagnostic-item");
        const header = createElement("div", "source-diagnostic-item-header");
        const identity = createElement("div");
        identity.appendChild(createElement("strong", "", `${String(item.provider).toUpperCase()} — ${DIAGNOSTIC_CODE_LABELS[item.code] || "Source notice"}`));
        const timestamp = createElement("time", "", new Date(item.recorded_at).toLocaleString());
        timestamp.dateTime = item.recorded_at;
        identity.appendChild(timestamp);
        header.append(identity, createElement("span", `diagnostic-level ${item.level}`, item.level === "attention" ? "Needs attention" : "Note"));
        row.appendChild(header);

        const details = createElement("details", "diagnostic-counters");
        details.appendChild(createElement("summary", "", "Aggregate counters"));
        const counters = createElement("dl");
        Object.entries(DIAGNOSTIC_COUNTER_LABELS).forEach(([key, label]) => {
            const value = Number(item.counters?.[key] || 0);
            if (value === 0 && !["raw_candidates", "valid_discovered", "new_candidates", "accepted", "rejected"].includes(key)) return;
            counters.append(createElement("dt", "", label), createElement("dd", "", String(value)));
        });
        details.appendChild(counters);
        row.appendChild(details);
        sourceDiagnosticsList.appendChild(row);
    });
}

async function clearSourceDiagnostics() {
    if (!confirm("Clear all local source diagnostic history? This does not affect saved jobs or searches.")) return;
    const response = await fetch(`${API_URL}/api/source-diagnostics`, { method: "DELETE" });
    const result = await response.json();
    if (!response.ok) {
        alert(result.detail || "Source diagnostic history could not be cleared.");
        return;
    }
    await loadSourceDiagnostics();
}

// Trigger AI Resume Tailoring
async function tailorResumeForJob(jobId) {
    const operation = showCancellableLoading("Tailoring Application...", "AI is rewriting experience highlights and crafting a cover letter. Generating PDF resume...");
    logActivity("Tailoring Started", `Generating custom resume for Job ID #${jobId}...`, "magic");
    
    try {
        const res = await fetch(`${API_URL}/api/jobs/${jobId}/tailor`, {
            method: "POST",
            headers: operationHeaders(operation)
        });
        const result = await res.json();
        
        if (result.cancelled) {
            hideLoading();
            logActivity("Tailoring Stopped", result.message, "warning");
            alert(result.message);
        } else if (result.success) {
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
            selectedMaterialsJobId = jobId;
            
            const resumeText = result.tailored_resume || "This material was generated before editable source retention was enabled. Regenerate the tailored materials to edit the resume text.";
            showTailorModal(resumeText, result.cover_letter, jobId);
        } else {
            hideLoading();
            alert(result.message || "Failed to load materials.");
        }
    } catch (e) {
        hideLoading();
        console.error(e);
    }
}

async function saveTailoredMaterials() {
    if (!selectedMaterialsJobId) return;
    const tailoredResume = tailoredResumeDisplay.value.trim();
    const coverLetter = coverLetterDisplay.value.trim();
    if (!tailoredResume || !coverLetter) {
        alert("Both the tailored resume and cover letter must contain text.");
        return;
    }
    const operation = showCancellableLoading("Saving Reviewed Materials...", "Regenerating the attached PDF from your edits.");
    try {
        const response = await fetch(`${API_URL}/api/jobs/${selectedMaterialsJobId}/tailored`, {
            method: "PATCH",
            headers: operationHeaders(operation, { "Content-Type": "application/json" }),
            body: JSON.stringify({ tailored_resume: tailoredResume, cover_letter: coverLetter })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Could not save reviewed materials.");
        hideLoading();
        if (result.cancelled) {
            logActivity("PDF Regeneration Stopped", result.message, "warning");
            alert(result.message);
            return;
        }
        logActivity("Materials Saved", `Resume PDF regenerated at ${result.pdf_page_count} page(s).`, "success");
        alert("Your edits were saved and the resume PDF was regenerated.");
    } catch (error) {
        hideLoading();
        alert(error.message);
    }
}

// Load generated materials and manually maintained application history.
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
                        <p>No application materials yet. Tailor a job from the "Search & Match" tab.</p>
                    </td>
                </tr>
            `;
            return;
        }
        
        logs.forEach(log => {
            const tr = createElement("tr");
            const dateStr = log.date_applied || "Pending";
            const companyCell = createElement("td");
            companyCell.appendChild(createElement("strong", "", String(log.company || "Unknown company")));
            tr.append(companyCell, createElement("td", "", String(log.position || "Untitled role")), createElement("td", "", dateStr));

            const hqCell = createElement("td");
            hqCell.append(createElement("i", "fa-solid fa-location-dot text-muted"), document.createTextNode(` ${log.us_hq || "Unknown"}`));
            tr.appendChild(hqCell);
            const statusCell = createElement("td");
            const statusClass = ["applied", "interview", "offer"].includes(log.status) ? "badge-applied" : "badge-tailored";
            statusCell.appendChild(createElement("span", `badge ${statusClass}`, String(log.status || "unknown").replaceAll("_", " ")));
            tr.appendChild(statusCell);

            const fileCell = createElement("td");
            if (log.tailored_resume_path && log.job_id) {
                const fileActions = createElement("div", "file-actions");
                const resumeLink = createElement("a", "btn btn-secondary btn-sm");
                resumeLink.href = `${API_URL}/api/jobs/${log.job_id}/materials/resume`;
                resumeLink.download = "";
                resumeLink.append(createElement("i", "fa-solid fa-file-pdf"), document.createTextNode(" Resume PDF"));
                fileActions.appendChild(resumeLink);
                if (log.cover_letter || log.cover_letter_path) {
                    const coverLetterLink = createElement("a", "btn btn-secondary btn-sm");
                    coverLetterLink.href = `${API_URL}/api/jobs/${log.job_id}/materials/cover-letter`;
                    coverLetterLink.download = "";
                    coverLetterLink.append(createElement("i", "fa-solid fa-file-lines"), document.createTextNode(" Cover Letter TXT"));
                    fileActions.appendChild(coverLetterLink);
                }
                fileCell.appendChild(fileActions);
            } else {
                fileCell.appendChild(createElement("span", "text-muted", "No generated resume"));
            }
            tr.appendChild(fileCell);
            
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
            const totalTailored = jobs.filter(j => ["tailored", "form_filled", "submitted", "applied"].includes(j.status)).length;
            const totalApplied = logs.filter(l => ["applied", "interview", "offer", "rejected", "withdrawn", "closed"].includes(l.status)).length;
            
            document.getElementById("stat-found").innerText = totalMatched;
            document.getElementById("stat-tailored").innerText = totalTailored;
            document.getElementById("stat-applied").innerText = totalApplied;
        }
    } catch (err) {
        console.error("Error updating stats", err);
    }
}
