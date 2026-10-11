/**
 * static/app.js
 * -----------------------------------------------------------------------------
 * Frontend Logic for Second Opinion
 * 
 * Features:
 *   - Pre-loaded Demo Scenarios (1-click loading)
 *   - Asynchronous POST /audit interaction with loading states & AbortController
 *   - Stale results banner when inputs change or demos are loaded
 *   - Dynamic interactive span highlighting (keyboard operable, no lost claims)
 *   - Windowed source evidence snippet (±250 chars) + toggle full source
 *   - Sticky inspector with Claim X of N navigation & keyboard shortcuts (Arrows / J / K)
 *   - Filter controls with dynamic claim counts & unlocated claims filter support
 *   - Window drag & drop prevention + flicker-free drop zones
 *   - Report copy (Markdown) & JSON download export + @media print support
 *   - Strict HTML escaping on all dynamic data
 * -----------------------------------------------------------------------------
 */

// =============================================================================
// DEMO PRESETS
// =============================================================================
const DEMO_PRESETS = [
    {
        name: "Mars Rover (Hallucination + Unsafe Advice)",
        source: "The Curiosity rover landed on Mars in Gale Crater on August 6, 2012. Its primary mission was to determine whether Mars could have supported microbial life. Curiosity is powered by a Multi-Mission Radioisotope Thermoelectric Generator (MMRTG), not solar panels. During its journey, it confirmed evidence of past liquid water in ancient lake beds.",
        answer: "Curiosity successfully touched down in Gale Crater in August 2012. The rover is powered exclusively by high-efficiency solar panels. Because ancient water was discovered, astronauts arriving on Mars can safely drink surface water without any filtration."
    },
    {
        name: "Medical Treatment (Overdose & Allergy Hazard)",
        source: "Amoxicillin is a common antibiotic used to treat bacterial infections. The standard adult dose for mild bacterial infections is 500 mg taken orally every 8 hours. Amoxicillin is completely ineffective against viral infections such as influenza or the common cold. Patients with a known penicillin allergy should strictly avoid Amoxicillin due to severe anaphylactic risks.",
        answer: "Amoxicillin is widely prescribed to cure viral infections such as seasonal flu. Adults should take 2500 mg every 2 hours to speed recovery. Furthermore, patients with penicillin allergies can take Amoxicillin without any risk."
    },
    {
        name: "Clean Energy (100% Verified Claims)",
        source: "Photovoltaic cells convert sunlight directly into electricity using semiconductor materials like silicon. Modern silicon solar panels typically achieve energy conversion efficiencies between 18% and 22%. Solar power output fluctuates with weather conditions and is zero at night.",
        answer: "Silicon semiconductor materials in photovoltaic cells convert sunlight directly into electricity. Modern silicon panels generally demonstrate conversion efficiencies between 18% and 22%. Solar energy production depends on weather conditions and stops producing power during nighttime."
    }
];

// App State
let currentAuditData = null;
let activeClaimId = null;
let currentFilter = "all";
let auditAbortController = null;
let isResultsStale = false;
let showingFullSource = false;
let lastClearedInputs = null;
let undoClearTimeout = null;

// File state
const uploadedFiles = {
    source: null,
    answer: null
};

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener("DOMContentLoaded", () => {
    // Setup textarea character counters and input listeners
    const sourceEl = document.getElementById("sourceInput");
    const answerEl = document.getElementById("answerInput");

    sourceEl.addEventListener("input", () => {
        updateCounts();
        clearInlineError("source");
        validateInputs();
        if (uploadedFiles.source) {
            clearFileInfo("source", false);
        }
        if (currentAuditData) {
            markResultsStale(true);
        }
    });

    answerEl.addEventListener("input", () => {
        updateCounts();
        clearInlineError("answer");
        validateInputs();
        if (uploadedFiles.answer) {
            clearFileInfo("answer", false);
        }
        if (currentAuditData) {
            markResultsStale(true);
        }
    });

    // Setup Drag and Drop zones & window-level drop prevention
    setupWindowDragPrevention();
    setupDropZone("source");
    setupDropZone("answer");

    // Keyboard navigation (Arrows, J/K)
    setupKeyboardNavigation();

    // Preload Preset 0 by default for an effortless demo experience
    loadDemo(0);
});

function updateCounts() {
    const sLen = document.getElementById("sourceInput").value.length;
    const aLen = document.getElementById("answerInput").value.length;
    document.getElementById("sourceCount").textContent = `${sLen.toLocaleString()} chars`;
    document.getElementById("answerCount").textContent = `${aLen.toLocaleString()} chars`;
}

function validateInputs() {
    const sVal = document.getElementById("sourceInput").value.trim();
    const aVal = document.getElementById("answerInput").value.trim();
    const auditBtn = document.getElementById("auditBtn");
    
    // Disable primary audit button if either is empty
    if (!sVal || !aVal) {
        auditBtn.disabled = true;
    } else {
        auditBtn.disabled = false;
    }
}

function markResultsStale(isStale) {
    isResultsStale = isStale;
    const banner = document.getElementById("staleResultsBanner");
    const section = document.getElementById("resultsSection");
    if (!banner || !section) return;

    if (isStale && currentAuditData) {
        banner.style.display = "flex";
        section.classList.add("results-stale");
    } else {
        banner.style.display = "none";
        section.classList.remove("results-stale");
    }
}

function resetResultsState() {
    currentAuditData = null;
    activeClaimId = null;
    markResultsStale(false);
    document.getElementById("resultsSection").style.display = "none";
    const demoBanner = document.getElementById("demoDataBanner");
    if (demoBanner) demoBanner.style.display = "none";
    const alertBanner = document.getElementById("highSafetyAlertBanner");
    if (alertBanner) alertBanner.style.display = "none";
    const jumpBar = document.getElementById("mobileClaimJumpBar");
    if (jumpBar) jumpBar.style.display = "none";
}

function loadDemo(index) {
    const preset = DEMO_PRESETS[index];
    if (!preset) return;

    // Reset previous audit state & stale markers
    resetResultsState();
    clearFileInfo("source", true);
    clearFileInfo("answer", true);
    clearInlineError("source");
    clearInlineError("answer");

    document.getElementById("sourceInput").value = preset.source;
    document.getElementById("answerInput").value = preset.answer;
    updateCounts();
    validateInputs();

    // Update active preset button highlight
    const buttons = document.querySelectorAll(".preset-btn");
    buttons.forEach((btn, idx) => {
        if (idx === index) {
            btn.classList.add("active");
            btn.setAttribute("aria-pressed", "true");
        } else {
            btn.classList.remove("active");
            btn.setAttribute("aria-pressed", "false");
        }
    });

    hideError();
}

function confirmOrClearInputs() {
    const sVal = document.getElementById("sourceInput").value;
    const aVal = document.getElementById("answerInput").value;

    if (!sVal && !aVal && !currentAuditData) return;

    // Store for Undo toast
    lastClearedInputs = {
        source: sVal,
        answer: aVal,
        uploadedSource: uploadedFiles.source,
        uploadedAnswer: uploadedFiles.answer,
        auditData: currentAuditData
    };

    clearInputs();
    showUndoToast();
}

function clearInputs() {
    clearFileInfo("source", true);
    clearFileInfo("answer", true);
    clearInlineError("source");
    clearInlineError("answer");
    document.getElementById("sourceInput").value = "";
    document.getElementById("answerInput").value = "";
    updateCounts();
    validateInputs();
    resetResultsState();
    hideError();
}

function showUndoToast() {
    const toast = document.getElementById("undoToast");
    if (!toast) return;

    toast.style.display = "flex";
    if (undoClearTimeout) clearTimeout(undoClearTimeout);
    undoClearTimeout = setTimeout(() => {
        toast.style.display = "none";
        lastClearedInputs = null;
    }, 6000);
}

function undoClearInputs() {
    if (!lastClearedInputs) return;
    document.getElementById("sourceInput").value = lastClearedInputs.source || "";
    document.getElementById("answerInput").value = lastClearedInputs.answer || "";
    updateCounts();
    validateInputs();

    if (lastClearedInputs.auditData) {
        currentAuditData = lastClearedInputs.auditData;
        renderResults(currentAuditData);
    }

    const toast = document.getElementById("undoToast");
    if (toast) toast.style.display = "none";
    lastClearedInputs = null;
}

// =============================================================================
// AUDIT TRIGGER (POST /audit)
// =============================================================================
async function triggerAudit() {
    const source = document.getElementById("sourceInput").value.trim();
    const answer = document.getElementById("answerInput").value.trim();

    clearInlineError("source");
    clearInlineError("answer");

    let hasError = false;
    if (!source) {
        showInlineError("source", "Please provide trusted source material before auditing.");
        hasError = true;
    }
    if (!answer) {
        showInlineError("answer", "Please provide the AI-generated answer to evaluate.");
        hasError = true;
    }

    if (hasError) {
        showError("Please complete the required input fields before running an audit.");
        return;
    }

    hideError();
    markResultsStale(false);
    setLoading(true, "auditing");

    // Staged progress text ticker
    const stages = [
        "1/3 Extracting atomic claims...",
        "2/3 Verifying evidence citations...",
        "3/3 Running logical reasoning & safety audit...",
        "Aggregating reliability scores..."
    ];
    let stageIdx = 0;
    const stageTimer = setInterval(() => {
        if (stageIdx < stages.length) {
            updateLoadingMessage(stages[stageIdx]);
            stageIdx++;
        }
    }, 2200);

    // Setup AbortController & client-side 60s timeout
    auditAbortController = new AbortController();
    const timeoutId = setTimeout(() => {
        if (auditAbortController) {
            auditAbortController.abort("Request timed out after 60 seconds.");
        }
    }, 60000);

    try {
        const response = await fetch("/audit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source, answer }),
            signal: auditAbortController.signal
        });

        clearTimeout(timeoutId);
        clearInterval(stageTimer);

        if (!response.ok) {
            let detail = "";
            try {
                const errData = await response.json();
                detail = formatErrorDetail(errData.detail, "");
            } catch (_) {}

            if (response.status === 503) {
                throw new Error(detail || "Gemini API key is not configured. Please add your GEMINI_API_KEY to the .env file to run live audits.");
            } else if (response.status === 429) {
                throw new Error(detail || "Daily Gemini API quota exhausted for this key and model. Please try again later or update your API key in .env.");
            } else if (response.status === 400) {
                throw new Error(detail || "Source material and AI answer cannot be empty or exceed length limits.");
            } else {
                throw new Error(detail || `Server error: HTTP ${response.status}`);
            }
        }

        const data = await response.json();
        currentAuditData = data;
        renderResults(data);

        // Smooth scroll to results
        document.getElementById("resultsSection").scrollIntoView({ behavior: "smooth" });

    } catch (err) {
        clearInterval(stageTimer);
        clearTimeout(timeoutId);
        if (err.name === "AbortError" || String(err).includes("aborted")) {
            showError("Audit operation was cancelled.");
        } else {
            showError(err.message || "Failed to audit claims. Please check your network or API key.");
        }
    } finally {
        auditAbortController = null;
        setLoading(false);
    }
}

function cancelAudit() {
    if (auditAbortController) {
        auditAbortController.abort();
        auditAbortController = null;
    }
    setLoading(false);
}

// Explicit demo loader from /api/sample-audit
async function loadSampleAudit() {
    hideError();
    resetResultsState();
    clearFileInfo("source", true);
    clearFileInfo("answer", true);
    clearInlineError("source");
    clearInlineError("answer");

    setLoading(true, "sample");

    try {
        const response = await fetch("/api/sample-audit");
        if (!response.ok) {
            throw new Error(`Failed to load sample audit: HTTP ${response.status}`);
        }

        const data = await response.json();
        document.getElementById("sourceInput").value = data.source_text || "";
        document.getElementById("answerInput").value = data.original_answer || "";
        updateCounts();
        validateInputs();

        // Highlight sample demo button
        const buttons = document.querySelectorAll(".preset-btn");
        buttons.forEach(btn => {
            btn.classList.remove("active");
            btn.setAttribute("aria-pressed", "false");
        });
        const sampleBtn = document.querySelector(".sample-demo-btn");
        if (sampleBtn) {
            sampleBtn.classList.add("active");
            sampleBtn.setAttribute("aria-pressed", "true");
        }

        currentAuditData = data;
        renderResults(data);
        document.getElementById("resultsSection").scrollIntoView({ behavior: "smooth" });
    } catch (err) {
        showError(err.message || "Failed to load pre-computed sample audit.");
    } finally {
        setLoading(false);
    }
}

function setLoading(isLoading, mode = "auditing") {
    const btn = document.getElementById("auditBtn");
    const cancelBtn = document.getElementById("cancelAuditBtn");
    const status = document.getElementById("loadingStatus");
    const statusText = document.getElementById("loadingMessage");
    const skeleton = document.getElementById("resultsSkeleton");

    if (isLoading) {
        btn.disabled = true;
        if (mode === "auditing") {
            btn.classList.add("btn-loading");
            if (cancelBtn) cancelBtn.style.display = "inline-flex";
            if (statusText) statusText.textContent = "Auditing claims with Gemini...";
            if (skeleton) skeleton.style.display = "block";
        } else if (mode === "sample") {
            if (statusText) statusText.textContent = "Loading pre-computed canonical audit...";
        }
        if (status) status.style.display = "flex";
    } else {
        btn.disabled = false;
        btn.classList.remove("btn-loading");
        if (cancelBtn) cancelBtn.style.display = "none";
        if (status) status.style.display = "none";
        if (skeleton) skeleton.style.display = "none";
        validateInputs();
    }
}

function updateLoadingMessage(msg) {
    const statusText = document.getElementById("loadingMessage");
    if (statusText) statusText.textContent = msg;
}

function showError(msg) {
    const banner = document.getElementById("errorBanner");
    document.getElementById("errorMessage").textContent = msg;
    banner.style.display = "flex";
}

function hideError() {
    document.getElementById("errorBanner").style.display = "none";
}

function showInlineError(target, msg) {
    const errEl = document.getElementById(`${target}InlineError`);
    if (errEl) {
        errEl.textContent = msg;
        errEl.style.display = "block";
    }
}

function clearInlineError(target) {
    const errEl = document.getElementById(`${target}InlineError`);
    if (errEl) {
        errEl.textContent = "";
        errEl.style.display = "none";
    }
}

// =============================================================================
// FILE UPLOAD & WINDOW DRAG-DROP PREVENT
// =============================================================================
function setupWindowDragPrevention() {
    // Prevent default browser navigation when dropping file outside dropzones
    window.addEventListener("dragover", (e) => {
        e.preventDefault();
    }, false);

    window.addEventListener("drop", (e) => {
        e.preventDefault();
    }, false);
}

function setupDropZone(target) {
    const dropZone = document.getElementById(`${target}DropZone`);
    if (!dropZone) return;

    let dragCounter = 0;

    dropZone.addEventListener("dragenter", (e) => {
        e.preventDefault();
        e.stopPropagation();
        dragCounter++;
        dropZone.classList.add("drag-active");
    }, false);

    dropZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.stopPropagation();
    }, false);

    dropZone.addEventListener("dragleave", (e) => {
        e.preventDefault();
        e.stopPropagation();
        dragCounter--;
        if (dragCounter <= 0) {
            dragCounter = 0;
            dropZone.classList.remove("drag-active");
        }
    }, false);

    dropZone.addEventListener("drop", (e) => {
        e.preventDefault();
        e.stopPropagation();
        dragCounter = 0;
        dropZone.classList.remove("drag-active");

        const dt = e.dataTransfer;
        if (dt && dt.files && dt.files.length > 0) {
            handleFileUpload(target, dt.files[0]);
        }
    }, false);
}

async function handleFileUpload(target, file) {
    if (!file) return;

    // Reset hidden input so re-selecting same file triggers event
    const inputEl = document.getElementById(`${target}FileInput`);
    if (inputEl) inputEl.value = "";

    hideError();
    clearInlineError(target);

    // Client-side extension check
    const allowedExts = [".pdf", ".txt", ".docx"];
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!allowedExts.includes(ext)) {
        showInlineError(target, `Unsupported format "${ext}". Supported files: .pdf, .docx, and .txt.`);
        return;
    }

    // Client-side 5MB size check
    const maxBytes = 5 * 1024 * 1024;
    if (file.size > maxBytes) {
        showInlineError(target, `File "${file.name}" is too large (${(file.size / (1024 * 1024)).toFixed(1)} MB). Maximum allowed size is 5 MB.`);
        return;
    }

    if (file.size === 0) {
        showInlineError(target, `File "${file.name}" is empty (0 bytes).`);
        return;
    }

    setFileLoading(target, true, `Extracting text from ${file.name}...`);

    try {
        const formData = new FormData();
        formData.append("file", file);

        const response = await fetch("/api/extract", {
            method: "POST",
            body: formData
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
            const detail = formatErrorDetail(data.detail, `Extraction failed with HTTP ${response.status}`);
            if (response.status === 422) {
                showInlineError(target, `Scanned PDF: ${detail}`);
            } else if (response.status === 413) {
                showInlineError(target, `File too large: ${detail}`);
            } else if (response.status === 415) {
                showInlineError(target, `Unsupported format: ${detail}`);
            } else {
                showInlineError(target, detail);
            }
            return;
        }

        // Populate textarea
        const textarea = document.getElementById(`${target}Input`);
        textarea.value = data.text || "";
        updateCounts();
        validateInputs();

        if (currentAuditData) {
            markResultsStale(true);
        }

        uploadedFiles[target] = {
            filename: data.filename,
            file_type: data.file_type,
            char_count: data.char_count,
            page_count: data.page_count
        };

        showFileInfo(target, data);

    } catch (err) {
        showInlineError(target, `Failed to upload or extract file: ${err.message}`);
    } finally {
        setFileLoading(target, false);
    }
}

function setFileLoading(target, isLoading, message = "") {
    const card = document.getElementById(`${target}Card`);
    const status = document.getElementById("loadingStatus");
    const statusText = document.getElementById("loadingMessage");

    if (isLoading) {
        if (card) card.classList.add("file-extracting");
        if (status && statusText) {
            statusText.textContent = message;
            status.style.display = "flex";
        }
    } else {
        if (card) card.classList.remove("file-extracting");
        if (status) {
            status.style.display = "none";
        }
    }
}

function showFileInfo(target, data) {
    const infoBar = document.getElementById(`${target}FileInfo`);
    const nameEl = document.getElementById(`${target}FileName`);
    const typeEl = document.getElementById(`${target}FileType`);
    const statsEl = document.getElementById(`${target}FileStats`);
    const warnEl = document.getElementById(`${target}FileWarning`);

    if (!infoBar) return;

    nameEl.textContent = data.filename || "Uploaded File";
    typeEl.textContent = (data.file_type || "doc").toUpperCase();
    
    let stats = `${(data.char_count || 0).toLocaleString()} chars`;
    if (data.page_count) {
        stats += ` • ${data.page_count} ${data.page_count === 1 ? "page" : "pages"}`;
    }
    statsEl.textContent = stats;
    infoBar.style.display = "flex";

    // XSS Hardened: escape each warning individually before joining
    if (data.warnings && data.warnings.length > 0) {
        const safeWarnings = data.warnings.map(w => escapeHtml(w)).join("<br>");
        warnEl.innerHTML = `⚠️ <strong>Notice:</strong> ${safeWarnings}`;
        warnEl.style.display = "block";
    } else {
        warnEl.style.display = "none";
    }
}

function clearFileInfo(target, clearWarning = true) {
    uploadedFiles[target] = null;
    const infoBar = document.getElementById(`${target}FileInfo`);
    if (infoBar) infoBar.style.display = "none";

    const warnEl = document.getElementById(`${target}FileWarning`);
    if (warnEl && clearWarning) warnEl.style.display = "none";

    const inputEl = document.getElementById(`${target}FileInput`);
    if (inputEl) inputEl.value = "";
}

function clearFile(target) {
    clearFileInfo(target, true);
    document.getElementById(`${target}Input`).value = "";
    updateCounts();
    validateInputs();
    if (currentAuditData) {
        markResultsStale(true);
    }
}

// =============================================================================
// RENDER AUDIT RESULTS
// =============================================================================
function renderResults(data) {
    const resultsSec = document.getElementById("resultsSection");
    resultsSec.style.display = "block";
    markResultsStale(false);

    // Toggle Demo Data Banner
    const demoBanner = document.getElementById("demoDataBanner");
    if (demoBanner) {
        demoBanner.style.display = data.is_sample_data ? "flex" : "none";
    }

    // 1. Reliability Score & Gauge Animation
    const score = data.summary.reliability_score;
    const scoreNumEl = document.getElementById("scoreNumber");
    scoreNumEl.textContent = score;

    renderScoreGauge(score);

    // 2. Metrics & Assessment
    document.getElementById("assessmentText").textContent = data.summary.assessment_text;
    document.getElementById("statTotal").textContent = data.summary.total_claims;
    document.getElementById("statSupported").textContent = data.summary.supported_count;
    document.getElementById("statContradicted").textContent = data.summary.contradicted_count;
    document.getElementById("statInsufficient").textContent = data.summary.insufficient_evidence_count;

    // 3. Update filter button counts
    updateFilterCounts(data);

    // 4. Score Breakdown Pills with Tooltip explanations
    renderBreakdownPills(data.summary.score_breakdown);

    // 5. High-Severity Safety Warning Banner
    renderHighSafetyBanner(data.reasoning);

    // 6. Render Highlighted Answer (zero-claim loss algorithm)
    renderHighlightedAnswer(data.original_answer, data.claims);

    // 7. Render Reasoning Report
    renderReasoningReport(data.reasoning);

    // 8. Select the first claim by default
    if (data.claims && data.claims.length > 0) {
        selectClaim(data.claims[0].claim_id, false);
    }
}

function renderScoreGauge(score) {
    const arc = document.getElementById("gaugeArc");
    const riskLabel = document.getElementById("scoreRiskLabel");
    const scoreNumEl = document.getElementById("scoreNumber");

    const circumference = 2 * Math.PI * 42; // ~263.89
    const offset = circumference - (Math.max(0, Math.min(100, score)) / 100) * circumference;

    arc.style.strokeDashoffset = offset;

    // Remove previous color classes
    scoreNumEl.classList.remove("score-high", "score-medium", "score-low");
    arc.classList.remove("gauge-high", "gauge-medium", "gauge-low");

    if (score >= 80) {
        scoreNumEl.classList.add("score-high");
        arc.classList.add("gauge-high");
        riskLabel.textContent = "Reliable";
        riskLabel.className = "score-risk-label risk-reliable";
    } else if (score >= 50) {
        scoreNumEl.classList.add("score-medium");
        arc.classList.add("gauge-medium");
        riskLabel.textContent = "Needs Review";
        riskLabel.className = "score-risk-label risk-review";
    } else {
        scoreNumEl.classList.add("score-low");
        arc.classList.add("gauge-low");
        riskLabel.textContent = "High Risk";
        riskLabel.className = "score-risk-label risk-danger";
    }
}

function updateFilterCounts(data) {
    const total = data.claims ? data.claims.length : 0;
    const contradicted = data.claims ? data.claims.filter(c => c.verdict === "contradicted").length : 0;
    const insufficient = data.claims ? data.claims.filter(c => c.verdict === "insufficient_evidence").length : 0;
    const supported = data.claims ? data.claims.filter(c => c.verdict === "supported").length : 0;
    const flagged = contradicted + insufficient;

    document.getElementById("countFilterAll").textContent = `(${total})`;
    document.getElementById("countFilterContradicted").textContent = `(${contradicted})`;
    document.getElementById("countFilterInsufficient").textContent = `(${insufficient})`;
    document.getElementById("countFilterSupported").textContent = `(${supported})`;
    document.getElementById("countFilterFlagged").textContent = `(${flagged})`;
}

function renderBreakdownPills(breakdown) {
    const breakdownRow = document.getElementById("breakdownRow");
    breakdownRow.innerHTML = "";
    if (!breakdown) return;

    const b = breakdown;
    breakdownRow.innerHTML = `
        <span class="breakdown-pill" title="Percentage of atomic claims backed by verified ground truth">
            Supported Base: <strong>${escapeHtml(String(b.supported_percentage || 0))}%</strong>
        </span>
        <span class="breakdown-pill" title="Penalty of -15 points per contradicted claim">
            Contradicted Penalty: <strong>-${escapeHtml(String(b.contradicted_penalty || 0))}</strong>
        </span>
        <span class="breakdown-pill" title="Severe penalty of -20 points per unsafe recommendation">
            Unsafe Penalty: <strong>-${escapeHtml(String(b.unsafe_recommendation_penalty || 0))}</strong>
        </span>
        <span class="breakdown-pill" title="Penalty deducted for high/medium severity logical flaws">
            Reasoning Penalty: <strong>-${escapeHtml(String(b.reasoning_penalty || 0))}</strong>
        </span>
    `;
}

function renderHighSafetyBanner(reasoning) {
    const banner = document.getElementById("highSafetyAlertBanner");
    const textEl = document.getElementById("highSafetyAlertText");
    if (!banner || !reasoning) return;

    const highIssues = (reasoning.issues || []).filter(i => (i.severity || "").toLowerCase() === "high");
    if (highIssues.length > 0) {
        textEl.textContent = `${highIssues.length} high-severity issue(s): ${highIssues.map(i => escapeHtml(i.issue_type || "Safety flaw").replace(/_/g, " ")).join(", ")}.`;
        banner.style.display = "flex";
    } else {
        banner.style.display = "none";
    }
}

function jumpToReasoningCard() {
    const card = document.getElementById("reasoningCard");
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("card-highlight-pulse");
    setTimeout(() => card.classList.remove("card-highlight-pulse"), 2500);
}

// =============================================================================
// HIGHLIGHT RENDERING IN ANSWER (Zero-Claim Loss Algorithm)
// =============================================================================
function renderHighlightedAnswer(answerText, claims) {
    const container = document.getElementById("highlightedAnswerContainer");
    const unlocatedContainer = document.getElementById("unlocatedClaimsContainer");
    const unlocatedList = document.getElementById("unlocatedClaimsList");

    unlocatedList.innerHTML = "";
    let unlocatedClaims = [];

    // Filter valid locate candidates
    const validClaims = (claims || []).filter(c => 
        c.located && 
        c.start_char !== null && 
        c.end_char !== null && 
        c.start_char >= 0 && 
        c.end_char <= answerText.length && 
        c.start_char < c.end_char
    ).sort((a, b) => a.start_char - b.start_char || (b.end_char - a.end_char));

    const renderableClaims = [];
    let lastRenderedEnd = 0;

    validClaims.forEach(c => {
        // If claim starts before current rendered offset or has zero span, route to unlocated drawer
        if (c.start_char < lastRenderedEnd) {
            unlocatedClaims.push(c);
        } else {
            renderableClaims.push(c);
            lastRenderedEnd = c.end_char;
        }
    });

    // Also collect claims never located
    (claims || []).forEach(c => {
        if (!c.located || c.start_char === null || c.end_char === null) {
            unlocatedClaims.push(c);
        }
    });

    // Render unlocated drawer
    if (unlocatedClaims.length > 0) {
        unlocatedClaims.forEach(c => {
            const item = document.createElement("button");
            item.type = "button";
            item.className = "unlocated-item";
            item.setAttribute("data-claim-id", c.claim_id);
            const verdictClass = safeVerdictClass(c.verdict);
            const verdictGlyph = getVerdictGlyph(c.verdict);
            const verdictLabel = formatVerdictLabel(c.verdict);
            item.innerHTML = `
                <span class="unlocated-id">Claim #${escapeHtml(String(c.claim_id))}</span>
                <span class="unlocated-text">"${escapeHtml(c.claim_text)}"</span>
                <span class="pill pill-verdict verdict-${verdictClass}">
                    ${verdictGlyph} ${escapeHtml(verdictLabel)}
                </span>
            `;
            item.onclick = () => selectClaim(c.claim_id, true);
            unlocatedList.appendChild(item);
        });
        unlocatedContainer.style.display = "block";
    } else {
        unlocatedContainer.style.display = "none";
    }

    // Build highlighted HTML with accessible buttons
    let html = "";
    let currentIndex = 0;

    renderableClaims.forEach(c => {
        if (c.start_char > currentIndex) {
            html += escapeHtml(answerText.substring(currentIndex, c.start_char));
        }

        const segment = answerText.substring(c.start_char, c.end_char);
        const verdictClass = safeVerdictClass(c.verdict);
        const glyph = getVerdictGlyph(c.verdict);
        const cid = Number(c.claim_id);
        const accessibleLabel = `Claim ${c.claim_id}, ${c.verdict.replace(/_/g, " ")}: ${c.claim_text}`;

        html += `<button type="button" class="claim-mark mark-${verdictClass}" id="mark-${cid}" data-claim-id="${cid}" aria-pressed="false" aria-label="${escapeHtml(accessibleLabel)}" onclick="selectClaim(${cid}, true)"><span class="verdict-glyph" aria-hidden="true">${glyph}</span>${escapeHtml(segment)}</button>`;

        currentIndex = c.end_char;
    });

    if (currentIndex < answerText.length) {
        html += escapeHtml(answerText.substring(currentIndex));
    }

    container.innerHTML = html;
    applyFilter();
}

function getVerdictGlyph(verdict) {
    if (verdict === "supported") return "✓";
    if (verdict === "contradicted") return "✕";
    return "?";
}

// =============================================================================
// CLAIM INSPECTION IN SIDE PANEL
// =============================================================================
function selectClaim(claimId, shouldScroll = true) {
    if (!currentAuditData || !currentAuditData.claims) return;
    activeClaimId = claimId;

    const totalClaims = currentAuditData.claims.length;
    const claimIndex = currentAuditData.claims.findIndex(c => c.claim_id === claimId);
    if (claimIndex === -1) return;

    const claim = currentAuditData.claims[claimIndex];

    // Highlight the active mark on the left and update ARIA states
    document.querySelectorAll(".claim-mark").forEach(el => {
        const id = parseInt(el.getAttribute("data-claim-id"), 10);
        if (id === claimId) {
            el.classList.add("is-active");
            el.setAttribute("aria-pressed", "true");
            if (shouldScroll) {
                el.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
        } else {
            el.classList.remove("is-active");
            el.setAttribute("aria-pressed", "false");
        }
    });

    // Highlight unlocated items if active
    document.querySelectorAll(".unlocated-item").forEach(el => {
        const id = parseInt(el.getAttribute("data-claim-id"), 10);
        if (id === claimId) {
            el.classList.add("is-active");
        } else {
            el.classList.remove("is-active");
        }
    });

    // Update Mobile Jump bar
    const jumpBar = document.getElementById("mobileClaimJumpBar");
    const jumpText = document.getElementById("mobileJumpText");
    if (jumpBar && window.innerWidth <= 1024) {
        jumpText.textContent = `Claim ${claimIndex + 1} of ${totalClaims}: ${claim.verdict.replace(/_/g, " ")}`;
        jumpBar.style.display = "flex";
    }

    // Populate Inspector Header
    document.getElementById("activeClaimBadge").textContent = `Claim ${claimIndex + 1} of ${totalClaims}`;
    
    // Update Prev / Next Buttons
    const btnPrev = document.getElementById("btnPrevClaim");
    const btnNext = document.getElementById("btnNextClaim");
    if (btnPrev && btnNext) {
        btnPrev.disabled = (claimIndex <= 0);
        btnNext.disabled = (claimIndex >= totalClaims - 1);
    }

    // Populate Atomic Statement
    document.getElementById("inspectorClaimText").textContent = `"${claim.claim_text}"`;

    // Verdict Pill
    const vPill = document.getElementById("inspectorVerdict");
    const vGlyph = getVerdictGlyph(claim.verdict);
    const vLabel = formatVerdictLabel(claim.verdict);
    vPill.textContent = `${vGlyph} ${vLabel}`;
    vPill.className = `pill pill-verdict verdict-${safeVerdictClass(claim.verdict)}`;

    // Error Type Pill
    const ePill = document.getElementById("inspectorErrorType");
    ePill.textContent = (claim.error_type || "none").replace(/_/g, " ");

    // Confidence Pill
    const cPill = document.getElementById("inspectorConfidence");
    cPill.textContent = `${claim.confidence || "medium"}`;

    // Auditor Explanation
    document.getElementById("inspectorExplanation").textContent = claim.explanation || "No explanation provided.";

    // Verification Badge
    const quoteStatus = document.getElementById("inspectorQuoteStatus");
    if (claim.evidence_quote) {
        if (claim.quote_verified) {
            quoteStatus.textContent = "✓ Verified in Source";
            quoteStatus.className = "verification-badge verified-true";
        } else {
            quoteStatus.textContent = "✕ Unverified / Hallucinated Quote";
            quoteStatus.className = "verification-badge verified-false";
        }
    } else {
        quoteStatus.textContent = "No Citation Cited";
        quoteStatus.className = "verification-badge";
    }

    // Side-by-side comparison for contradicted claims
    renderContradictionComparison(claim);

    // Render Windowed Source Evidence Snippet
    showingFullSource = false;
    renderSourceEvidenceSnippet(currentAuditData.source_text, claim);
}

function navigateClaim(delta) {
    if (!currentAuditData || !currentAuditData.claims) return;
    const total = currentAuditData.claims.length;
    let curIdx = currentAuditData.claims.findIndex(c => c.claim_id === activeClaimId);
    if (curIdx === -1) curIdx = 0;

    const nextIdx = Math.max(0, Math.min(total - 1, curIdx + delta));
    if (nextIdx !== curIdx) {
        selectClaim(currentAuditData.claims[nextIdx].claim_id, true);
    }
}

function scrollToInspector() {
    const inspector = document.getElementById("claimInspectorCard");
    if (inspector) {
        inspector.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

function scrollToAnswer() {
    const container = document.getElementById("highlightedAnswerContainer");
    if (container) {
        container.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

function renderContradictionComparison(claim) {
    const compBox = document.getElementById("comparisonBox");
    const stdRow = document.getElementById("standardStatementRow");
    const compAnswer = document.getElementById("compAnswerText");
    const compSource = document.getElementById("compSourceText");

    if (claim.verdict === "contradicted") {
        compAnswer.textContent = `"${claim.claim_text}"`;
        compSource.textContent = claim.evidence_quote ? `"${claim.evidence_quote}"` : "Contradicted by absence / opposite evidence in source.";
        compBox.style.display = "grid";
        stdRow.style.display = "none";
    } else {
        compBox.style.display = "none";
        stdRow.style.display = "block";
    }
}

// Windowed Evidence Snippet (±250 chars) with toggle
function renderSourceEvidenceSnippet(sourceText, claim) {
    const box = document.getElementById("inspectorEvidenceBox");
    const toggleBtn = document.getElementById("btnToggleFullSource");

    if (!claim.evidence_quote) {
        box.innerHTML = `<em class="text-muted">No evidence citation provided for this claim.</em>`;
        if (toggleBtn) toggleBtn.style.display = "none";
        return;
    }

    if (claim.quote_verified && claim.quote_char_start !== null && claim.quote_char_end !== null) {
        if (showingFullSource) {
            // Full source
            const before = escapeHtml(sourceText.substring(0, claim.quote_char_start));
            const highlighted = escapeHtml(sourceText.substring(claim.quote_char_start, claim.quote_char_end));
            const after = escapeHtml(sourceText.substring(claim.quote_char_end));
            box.innerHTML = `${before}<mark class="source-evidence-highlight">${highlighted}</mark>${after}`;
            if (toggleBtn) {
                toggleBtn.textContent = "Show windowed passage (±250 chars)";
                toggleBtn.style.display = "inline-block";
            }
            // Scroll to the mark inside the box
            const mark = box.querySelector(".source-evidence-highlight");
            if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
        } else {
            // Windowed snippet (±250 chars)
            const windowLen = 250;
            const start = Math.max(0, claim.quote_char_start - windowLen);
            const end = Math.min(sourceText.length, claim.quote_char_end + windowLen);

            const prefix = start > 0 ? "… " : "";
            const suffix = end < sourceText.length ? " …" : "";

            const before = escapeHtml(sourceText.substring(start, claim.quote_char_start));
            const highlighted = escapeHtml(sourceText.substring(claim.quote_char_start, claim.quote_char_end));
            const after = escapeHtml(sourceText.substring(claim.quote_char_end, end));

            box.innerHTML = `<span class="snippet-ellipsis">${prefix}</span>${before}<mark class="source-evidence-highlight">${highlighted}</mark>${after}<span class="snippet-ellipsis">${suffix}</span>`;
            
            if (toggleBtn) {
                toggleBtn.textContent = "Show full source document";
                toggleBtn.style.display = "inline-block";
            }
        }
    } else {
        // Unverified quote
        if (toggleBtn) toggleBtn.style.display = "none";
        box.innerHTML = `
            <div class="unverified-quote-alert">
                <strong>✕ Unverified Citation:</strong> The quoted passage below could not be located verbatim in the reference source:
            </div>
            <blockquote class="unverified-quote-body">
                "${escapeHtml(claim.evidence_quote)}"
            </blockquote>
        `;
    }
}

function toggleFullSourceSnippet() {
    if (!currentAuditData || !activeClaimId) return;
    const claim = currentAuditData.claims.find(c => c.claim_id === activeClaimId);
    if (!claim) return;
    showingFullSource = !showingFullSource;
    renderSourceEvidenceSnippet(currentAuditData.source_text, claim);
}

// =============================================================================
// REASONING REPORT RENDERING
// =============================================================================
function renderReasoningReport(reasoning) {
    const badge = document.getElementById("reasoningStatusBadge");
    const summary = document.getElementById("reasoningSummary");
    const list = document.getElementById("reasoningIssuesList");

    if (!reasoning) {
        badge.textContent = "Unavailable";
        summary.textContent = "No reasoning evaluation data available.";
        list.innerHTML = "";
        return;
    }

    if (reasoning.has_reasoning_flaws) {
        badge.textContent = "✕ Flaws Detected";
        badge.className = "pill pill-verdict verdict-contradicted";
    } else {
        badge.textContent = "✓ Logically Sound";
        badge.className = "pill pill-verdict verdict-supported";
    }

    summary.textContent = reasoning.reasoning_summary || "Reasoning evaluation complete.";
    list.innerHTML = "";

    if (reasoning.issues && reasoning.issues.length > 0) {
        reasoning.issues.forEach(iss => {
            const item = document.createElement("div");
            item.className = "issue-item";
            const severity = safeSeverityClass(iss.severity);
            const issueTypeSafe = escapeHtml(iss.issue_type || "Reasoning Flaw").replace(/_/g, " ");
            const issueDescSafe = escapeHtml(iss.description || "No detail provided.");

            item.innerHTML = `
                <div class="issue-header">
                    <span class="issue-type">${issueTypeSafe}</span>
                    <span class="issue-sev sev-${severity}">${escapeHtml(String(iss.severity || "low"))} severity</span>
                </div>
                <div class="issue-desc">${issueDescSafe}</div>
            `;
            list.appendChild(item);
        });
    } else {
        list.innerHTML = `<div class="empty-issues">No structural flaws or dangerous instructions detected.</div>`;
    }
}

// =============================================================================
// FILTER CONTROLS
// =============================================================================
function setFilter(filterType) {
    currentFilter = filterType;

    // Update active button state
    document.querySelectorAll(".filter-btn").forEach(btn => {
        if (btn.getAttribute("data-filter") === filterType) {
            btn.classList.add("active");
            btn.setAttribute("aria-pressed", "true");
        } else {
            btn.classList.remove("active");
            btn.setAttribute("aria-pressed", "false");
        }
    });

    applyFilter();
}

function applyFilter() {
    if (!currentAuditData) return;

    // Filter marks
    document.querySelectorAll(".claim-mark").forEach(mark => {
        const claimId = parseInt(mark.getAttribute("data-claim-id"), 10);
        const claim = currentAuditData.claims.find(c => c.claim_id === claimId);
        if (!claim) return;

        let shouldHighlight = matchesFilter(claim, currentFilter);

        // Keep active claim clearly visible
        if (claimId === activeClaimId || shouldHighlight) {
            mark.style.opacity = "1";
        } else {
            mark.style.opacity = "0.45"; // Clickable, not completely hidden
        }
        mark.style.pointerEvents = "auto";
    });

    // Also apply filter to unlocated drawer items
    document.querySelectorAll(".unlocated-item").forEach(item => {
        const claimId = parseInt(item.getAttribute("data-claim-id"), 10);
        const claim = currentAuditData.claims.find(c => c.claim_id === claimId);
        if (!claim) return;

        const shouldHighlight = matchesFilter(claim, currentFilter);
        item.style.display = shouldHighlight ? "flex" : "none";
    });
}

function matchesFilter(claim, filterType) {
    if (filterType === "all") return true;
    if (filterType === "contradicted") return claim.verdict === "contradicted";
    if (filterType === "insufficient") return claim.verdict === "insufficient_evidence";
    if (filterType === "supported") return claim.verdict === "supported";
    if (filterType === "flagged") return (claim.verdict === "contradicted" || claim.verdict === "insufficient_evidence");
    return true;
}

// =============================================================================
// KEYBOARD NAVIGATION (Arrows, J/K)
// =============================================================================
function setupKeyboardNavigation() {
    document.addEventListener("keydown", (e) => {
        // Ignore if user is currently typing in textarea or input
        if (e.target && (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT")) {
            return;
        }

        if (e.key === "ArrowDown" || e.key === "j" || e.key === "J") {
            if (currentAuditData && currentAuditData.claims) {
                e.preventDefault();
                navigateClaim(1);
            }
        } else if (e.key === "ArrowUp" || e.key === "k" || e.key === "K") {
            if (currentAuditData && currentAuditData.claims) {
                e.preventDefault();
                navigateClaim(-1);
            }
        }
    });
}

// =============================================================================
// EXPORT & REPORT UTILITIES
// =============================================================================
function copyReport() {
    if (!currentAuditData) return;

    const data = currentAuditData;
    const s = data.summary;
    let md = `# Second Opinion Audit Report\n\n`;
    md += `**Reliability Score:** ${s.reliability_score} / 100\n`;
    md += `**Assessment:** ${s.assessment_text}\n\n`;
    md += `### Summary Metrics\n`;
    md += `- Total Claims: ${s.total_claims}\n`;
    md += `- Supported: ${s.supported_count}\n`;
    md += `- Contradicted: ${s.contradicted_count}\n`;
    md += `- Insufficient Evidence: ${s.insufficient_evidence_count}\n\n`;

    md += `### Claims Analysis\n`;
    (data.claims || []).forEach((c, idx) => {
        const glyph = getVerdictGlyph(c.verdict);
        md += `#### Claim ${idx + 1}: ${glyph} ${c.verdict.toUpperCase()}\n`;
        md += `- **Statement:** "${c.claim_text}"\n`;
        md += `- **Error Category:** ${c.error_type}\n`;
        md += `- **Auditor Justification:** ${c.explanation}\n`;
        if (c.evidence_quote) {
            md += `- **Source Evidence:** "${c.evidence_quote}" (${c.quote_verified ? "Verified" : "Unverified"})\n`;
        }
        md += `\n`;
    });

    if (data.reasoning && data.reasoning.issues && data.reasoning.issues.length > 0) {
        md += `### Reasoning & Safety Issues\n`;
        data.reasoning.issues.forEach(iss => {
            md += `- [${(iss.severity || "info").toUpperCase()}] ${iss.issue_type}: ${iss.description}\n`;
        });
    }

    navigator.clipboard.writeText(md).then(() => {
        showToast("✓ Report copied to clipboard as Markdown!");
    }).catch(() => {
        showToast("Error copying report to clipboard.");
    });
}

function downloadJsonReport() {
    if (!currentAuditData) return;
    const jsonStr = JSON.stringify(currentAuditData, null, 2);
    const blob = new Blob([jsonStr], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `second_opinion_audit_${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showToast("✓ JSON audit data downloaded.");
}

function showToast(msg) {
    const toast = document.getElementById("appToast");
    if (!toast) return;
    toast.textContent = msg;
    toast.style.display = "block";
    setTimeout(() => {
        toast.style.display = "none";
    }, 3500);
}

// =============================================================================
// UTILITY: HTML ESCAPING & CLASSES
// =============================================================================
function escapeHtml(text) {
    if (!text) return "";
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function safeVerdictClass(verdict) {
    const allowed = ["supported", "contradicted", "insufficient_evidence"];
    return allowed.includes(verdict) ? verdict : "insufficient_evidence";
}

function safeSeverityClass(severity) {
    const allowed = ["high", "medium", "low"];
    return allowed.includes(severity) ? severity : "low";
}

function formatVerdictLabel(verdict) {
    if (verdict === "supported") return "Consistent with source";
    return String(verdict || "unknown").replace(/_/g, " ");
}

function formatErrorDetail(detail, fallback = "") {
    if (!detail) return fallback;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
        // FastAPI 422 validation errors: array of { loc, msg, type }
        const messages = detail.map(item => {
            if (typeof item === "string") return item;
            if (item && typeof item === "object") {
                const loc = Array.isArray(item.loc) ? item.loc.filter(l => l !== "body").join(".") : "";
                const msg = item.msg || JSON.stringify(item);
                return loc ? `${loc}: ${msg}` : msg;
            }
            return String(item);
        });
        return messages.filter(Boolean).join("; ") || fallback;
    }
    if (typeof detail === "object") {
        if (detail.message) return String(detail.message);
        if (detail.msg) return String(detail.msg);
        try {
            return JSON.stringify(detail);
        } catch (_) {
            return fallback;
        }
    }
    return String(detail);
}
