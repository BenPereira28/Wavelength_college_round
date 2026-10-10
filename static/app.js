/**
 * static/app.js
 * -----------------------------------------------------------------------------
 * Frontend Logic for Second Opinion
 * 
 * Features:
 *   - Pre-loaded Demo Scenarios (1-click loading)
 *   - Asynchronous POST /audit interaction with loading states
 *   - Dynamic interactive span highlighting (Green, Yellow, Red)
 *   - Side-panel evidence inspector with source-quote highlighting
 *   - Filter controls (All, Flagged Errors Only, Supported Only)
 *   - Honest confidence indicators and reasoning report
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

// Current State
let currentAuditData = null;
let activeClaimId = null;
let currentFilter = "all";

// =============================================================================
// INITIALIZATION
// =============================================================================
document.addEventListener("DOMContentLoaded", () => {
    // Setup textarea character counters
    const sourceEl = document.getElementById("sourceInput");
    const answerEl = document.getElementById("answerInput");

    sourceEl.addEventListener("input", updateCounts);
    answerEl.addEventListener("input", updateCounts);

    // Preload Preset 0 by default for an effortless demo experience
    loadDemo(0);
});

function updateCounts() {
    const sLen = document.getElementById("sourceInput").value.length;
    const aLen = document.getElementById("answerInput").value.length;
    document.getElementById("sourceCount").textContent = `${sLen.toLocaleString()} chars`;
    document.getElementById("answerCount").textContent = `${aLen.toLocaleString()} chars`;
}

function loadDemo(index) {
    const preset = DEMO_PRESETS[index];
    if (!preset) return;

    document.getElementById("sourceInput").value = preset.source;
    document.getElementById("answerInput").value = preset.answer;
    updateCounts();

    // Update active preset button highlight
    const buttons = document.querySelectorAll(".preset-btn");
    buttons.forEach((btn, idx) => {
        if (idx === index) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    // Clear previous error
    hideError();
}

function clearInputs() {
    document.getElementById("sourceInput").value = "";
    document.getElementById("answerInput").value = "";
    updateCounts();
    document.getElementById("resultsSection").style.display = "none";
    hideError();
}

// =============================================================================
// AUDIT TRIGGER (POST /audit)
// =============================================================================
async function triggerAudit() {
    const source = document.getElementById("sourceInput").value.trim();
    const answer = document.getElementById("answerInput").value.trim();

    if (!source) {
        showError("Please enter or paste trusted source material.");
        return;
    }
    if (!answer) {
        showError("Please enter or paste the AI-generated answer to audit.");
        return;
    }

    hideError();
    setLoading(true);

    try {
        const response = await fetch("/audit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ source, answer })
        });

        if (!response.ok) {
            const errData = await response.json().catch(() => ({}));
            throw new Error(errData.detail || `Server error: HTTP ${response.status}`);
        }

        const data = await response.json();
        currentAuditData = data;
        renderResults(data);

        // Smooth scroll to results
        document.getElementById("resultsSection").scrollIntoView({ behavior: "smooth" });

    } catch (err) {
        showError(err.message || "Failed to audit claims. Please check your network or API key.");
    } finally {
        setLoading(false);
    }
}

function setLoading(isLoading) {
    const btn = document.getElementById("auditBtn");
    const status = document.getElementById("loadingStatus");
    const text = document.getElementById("auditBtnText");
    const icon = document.getElementById("auditBtnIcon");

    if (isLoading) {
        btn.disabled = true;
        status.style.display = "flex";
        text.textContent = "Auditing in Progress...";
        icon.textContent = "⏳";
    } else {
        btn.disabled = false;
        status.style.display = "none";
        text.textContent = "Run Comprehensive Audit";
        icon.textContent = "⚡";
    }
}

function showError(msg) {
    const banner = document.getElementById("errorBanner");
    document.getElementById("errorMessage").textContent = msg;
    banner.style.display = "flex";
}

function hideError() {
    document.getElementById("errorBanner").style.display = "none";
}

// =============================================================================
// RENDER AUDIT RESULTS
// =============================================================================
function renderResults(data) {
    const resultsSec = document.getElementById("resultsSection");
    resultsSec.style.display = "block";

    // 1. Reliability Score
    const score = data.summary.reliability_score;
    const scoreNumEl = document.getElementById("scoreNumber");
    scoreNumEl.textContent = score;

    scoreNumEl.className = "score-number";
    if (score >= 80) {
        scoreNumEl.classList.add("score-high");
    } else if (score >= 50) {
        scoreNumEl.classList.add("score-medium");
    } else {
        scoreNumEl.classList.add("score-low");
    }

    // 2. Metrics & Assessment
    document.getElementById("assessmentText").textContent = data.summary.assessment_text;
    document.getElementById("statTotal").textContent = data.summary.total_claims;
    document.getElementById("statSupported").textContent = data.summary.supported_count;
    document.getElementById("statContradicted").textContent = data.summary.contradicted_count;
    document.getElementById("statInsufficient").textContent = data.summary.insufficient_evidence_count;

    // 3. Score Breakdown Pills
    const breakdownRow = document.getElementById("breakdownRow");
    breakdownRow.innerHTML = "";
    if (data.summary.score_breakdown) {
        const b = data.summary.score_breakdown;
        breakdownRow.innerHTML = `
            <span class="breakdown-pill">Supported: <strong>${b.supported_percentage || 0}%</strong></span>
            <span class="breakdown-pill">Contradicted Penalty: <strong>-${b.contradicted_penalty || 0}</strong></span>
            <span class="breakdown-pill">Unsafe Penalty: <strong>-${b.unsafe_recommendation_penalty || 0}</strong></span>
            <span class="breakdown-pill">Reasoning Penalty: <strong>-${b.reasoning_penalty || 0}</strong></span>
        `;
    }

    // 4. Render Highlighted Answer
    renderHighlightedAnswer(data.original_answer, data.claims);

    // 5. Render Reasoning Report
    renderReasoningReport(data.reasoning);

    // 6. Select the first claim in the inspector by default
    if (data.claims && data.claims.length > 0) {
        selectClaim(data.claims[0].claim_id);
    }
}

// =============================================================================
// HIGHLIGHT RENDERING IN ANSWER
// =============================================================================
function renderHighlightedAnswer(answerText, claims) {
    const container = document.getElementById("highlightedAnswerContainer");
    const unlocatedContainer = document.getElementById("unlocatedClaimsContainer");
    const unlocatedList = document.getElementById("unlocatedClaimsList");

    unlocatedList.innerHTML = "";
    let hasUnlocated = false;

    // Filter claims that have valid positions in the text
    const locatedClaims = claims
        .filter(c => c.located && c.start_char !== null && c.end_char !== null && c.start_char >= 0 && c.end_char <= answerText.length && c.start_char < c.end_char)
        .sort((a, b) => a.start_char - b.start_char);

    // Find any unlocated claims
    claims.forEach(c => {
        if (!c.located || c.start_char === null || c.end_char === null) {
            hasUnlocated = true;
            const item = document.createElement("div");
            item.className = "unlocated-item";
            item.innerHTML = `<strong>Claim #${c.claim_id}:</strong> ${escapeHtml(c.claim_text)} <span class="pill pill-verdict verdict-${c.verdict}" style="font-size:0.7rem;">${c.verdict}</span>`;
            item.onclick = () => selectClaim(c.claim_id);
            unlocatedList.appendChild(item);
        }
    });

    unlocatedContainer.style.display = hasUnlocated ? "block" : "none";

    // Non-overlapping interval reconstruction
    let html = "";
    let currentIndex = 0;

    locatedClaims.forEach(c => {
        if (c.start_char > currentIndex) {
            html += escapeHtml(answerText.substring(currentIndex, c.start_char));
        }

        // Avoid overlap errors if models return intersecting spans
        const actualStart = Math.max(currentIndex, c.start_char);
        const actualEnd = Math.max(actualStart, c.end_char);
        const segment = answerText.substring(actualStart, actualEnd);

        if (segment) {
            html += `<mark class="claim-mark mark-${c.verdict}" id="mark-${c.claim_id}" data-claim-id="${c.claim_id}" onclick="selectClaim(${c.claim_id})">${escapeHtml(segment)}</mark>`;
            currentIndex = actualEnd;
        }
    });

    if (currentIndex < answerText.length) {
        html += escapeHtml(answerText.substring(currentIndex));
    }

    container.innerHTML = html;
    applyFilter();
}

// =============================================================================
// CLAIM INSPECTION IN SIDE PANEL
// =============================================================================
function selectClaim(claimId) {
    if (!currentAuditData) return;
    activeClaimId = claimId;

    // Highlight the active mark on the left
    document.querySelectorAll(".claim-mark").forEach(el => {
        if (parseInt(el.getAttribute("data-claim-id")) === claimId) {
            el.classList.add("is-active");
        } else {
            el.classList.remove("is-active");
        }
    });

    const claim = currentAuditData.claims.find(c => c.claim_id === claimId);
    if (!claim) return;

    // Populate Inspector Fields
    document.getElementById("activeClaimBadge").textContent = `Claim #${claim.claim_id}`;
    document.getElementById("inspectorClaimText").textContent = `"${claim.claim_text}"`;

    // Verdict Pill
    const vPill = document.getElementById("inspectorVerdict");
    vPill.textContent = claim.verdict.replace("_", " ");
    vPill.className = `pill pill-verdict verdict-${claim.verdict}`;

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
            quoteStatus.textContent = "✗ Unverified / Hallucinated Quote";
            quoteStatus.className = "verification-badge verified-false";
        }
    } else {
        quoteStatus.textContent = "No Citation Cited";
        quoteStatus.className = "verification-badge";
    }

    // Highlighted Source Evidence in Side Panel
    renderSourceEvidenceSnippet(currentAuditData.source_text, claim);
}

function renderSourceEvidenceSnippet(sourceText, claim) {
    const box = document.getElementById("inspectorEvidenceBox");

    if (!claim.evidence_quote) {
        box.innerHTML = `<em style="color:#64748b;">No evidence quote cited for this statement.</em>`;
        return;
    }

    if (claim.quote_verified && claim.quote_char_start !== null && claim.quote_char_end !== null) {
        const before = escapeHtml(sourceText.substring(0, claim.quote_char_start));
        const highlighted = escapeHtml(sourceText.substring(claim.quote_char_start, claim.quote_char_end));
        const after = escapeHtml(sourceText.substring(claim.quote_char_end));

        box.innerHTML = `${before}<mark class="source-evidence-highlight">${highlighted}</mark>${after}`;
    } else {
        // Unverified quote
        box.innerHTML = `
            <div style="color:#fda4af; margin-bottom:0.5rem; font-weight:600;">
                [Alert] Cited quote was not found verbatim in source:
            </div>
            <div style="background:rgba(244,63,94,0.1); padding:0.5rem; border-radius:6px; color:#fecdd3; font-style:italic;">
                "${escapeHtml(claim.evidence_quote)}"
            </div>
        `;
    }
}

// =============================================================================
// REASONING REPORT RENDERING
// =============================================================================
function renderReasoningReport(reasoning) {
    const badge = document.getElementById("reasoningStatusBadge");
    const summary = document.getElementById("reasoningSummary");
    const list = document.getElementById("reasoningIssuesList");

    if (reasoning.has_reasoning_flaws) {
        badge.textContent = "Flaws Detected";
        badge.className = "pill pill-verdict verdict-contradicted";
    } else {
        badge.textContent = "Logically Sound";
        badge.className = "pill pill-verdict verdict-supported";
    }

    summary.textContent = reasoning.reasoning_summary || "Reasoning check completed.";
    list.innerHTML = "";

    if (reasoning.issues && reasoning.issues.length > 0) {
        reasoning.issues.forEach(iss => {
            const item = document.createElement("div");
            item.className = "issue-item";
            item.innerHTML = `
                <div class="issue-header">
                    <span class="issue-type">${escapeHtml(iss.issue_type.replace(/_/g, " "))}</span>
                    <span class="issue-sev sev-${iss.severity}">${iss.severity} severity</span>
                </div>
                <div class="issue-desc">${escapeHtml(iss.description)}</div>
            `;
            list.appendChild(item);
        });
    } else {
        list.innerHTML = `<div style="color:#64748b; font-size:0.82rem; font-style:italic;">No logical inconsistencies or dangerous advice detected in the answer.</div>`;
    }
}

// =============================================================================
// FILTER CONTROLS
// =============================================================================
function setFilter(filterType) {
    currentFilter = filterType;

    // Update active button
    document.querySelectorAll(".filter-btn").forEach(btn => {
        if (btn.getAttribute("data-filter") === filterType) {
            btn.classList.add("active");
        } else {
            btn.classList.remove("active");
        }
    });

    applyFilter();
}

function applyFilter() {
    if (!currentAuditData) return;

    document.querySelectorAll(".claim-mark").forEach(mark => {
        const claimId = parseInt(mark.getAttribute("data-claim-id"));
        const claim = currentAuditData.claims.find(c => c.claim_id === claimId);
        if (!claim) return;

        let shouldShow = true;
        if (currentFilter === "flagged") {
            shouldShow = (claim.verdict === "contradicted" || claim.verdict === "insufficient_evidence");
        } else if (currentFilter === "supported") {
            shouldShow = (claim.verdict === "supported");
        }

        if (shouldShow) {
            mark.style.opacity = "1";
            mark.style.pointerEvents = "auto";
        } else {
            mark.style.opacity = "0.2";
            mark.style.pointerEvents = "none";
        }
    });
}

// =============================================================================
// UTILITY: HTML ESCAPING
// =============================================================================
function escapeHtml(text) {
    if (!text) return "";
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
