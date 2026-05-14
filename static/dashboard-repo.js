function resolveRepoFull() {
    const metaRepoFull = document.querySelector('meta[name="driftguard-repo-full"]')?.getAttribute("content")?.trim();
    if (metaRepoFull) {
        return metaRepoFull;
    }

    const pathname = String(window.location.pathname || "");
    const prefix = "/dashboard/";
    if (!pathname.startsWith(prefix)) {
        return "";
    }

    const encodedRepoFull = pathname
        .slice(prefix.length)
        .replace(/^\/+|\/+$/g, "")
        .replace(/\/audit$/i, "");
    if (!encodedRepoFull) {
        return "";
    }

    try {
        return decodeURIComponent(encodedRepoFull);
    } catch {
        return encodedRepoFull;
    }
}

const repoFull = resolveRepoFull();
const VALID_REPO_TABS = new Set(["audit", "drift", "version-control", "baseline", "compliance", "reports"]);
window.__storylineCache = new Map();
window.__selectedInsight = null;
window.__designProfiles = [];
window.__journeySnapshots = [];
window.__artifactEntries = [];
window.__artifactTypeFilter = "all";
window.__artifactQuery = "";
window.__artifactsCollapsed = false;
window.__artifactTypeOptions = [];
window.__artifactOptionEntries = [];
window.__artifactOptionsLoaded = false;
window.__artifactOptionsLoading = false;
window.__artifactOptionsError = "";
window.__artifactAddPath = "";
window.__artifactEditPath = "";
window.__artifactMutationBusyPath = "";
window.__artifactActionStatusMessage = "";
window.__artifactActionStatusTone = "info";
window.__pendingRebaselineSnapshot = null;
window.__rebaselineBusy = false;
window.__attributeProfileActiveTab = "guardrail_regressions";
window.__pendingProposalsLoadToken = 0;

function resolveRepoTab() {
    const metaTab = document.querySelector('meta[name="driftguard-active-repo-tab"]')?.getAttribute("content")?.trim().toLowerCase();
    if (metaTab && VALID_REPO_TABS.has(metaTab)) {
        return metaTab;
    }

    const params = new URLSearchParams(window.location.search);
    const requestedTab = (params.get("tab") || "").trim().toLowerCase();
    return VALID_REPO_TABS.has(requestedTab) ? requestedTab : "audit";
}

const activeRepoTab = resolveRepoTab();
window.__activeRepoTab = activeRepoTab;

function storylinePanelCopy() {
    if (activeRepoTab === "audit") {
        return {
            title: "Supporting history",
            itemLabel: "History summary",
            empty: "No supporting history is available for the selected artifact yet.",
            loading: "Loading supporting history...",
            error: "Unable to load supporting history",
            cta: "Open history",
            select: "Select an insight to load its supporting history.",
        };
    }
    return {
        title: "Drift storyline",
        itemLabel: "Story",
        empty: "No storyline is available for the selected artifact yet.",
        loading: "Loading selected artifact storyline...",
        error: "Unable to load storyline",
        cta: "Open storyline",
        select: "Select an insight to load its storyline.",
    };
}

function syncStorylinePanelCopy() {
    setText("repo-storyline-title", storylinePanelCopy().title);
}

const ATTRIBUTE_PROFILE_TAB_CONFIG = [
    { key: "guardrail_regressions", label: "Guardrail regressions", dimensionKeys: ["guardrail_robustness"] },
    { key: "capability_expansions", label: "Capability expansions", dimensionKeys: ["capability_risk"] },
    { key: "autonomy_increases", label: "Autonomy increases", dimensionKeys: ["autonomy_level"] },
    { key: "governance_anomalies", label: "Governance anomalies", dimensionKeys: ["governance_strength"] },
    { key: "model_config_changes", label: "Model/config changes", dimensionKeys: ["model_config_posture"] },
];

function asArray(value) {
    return Array.isArray(value) ? value : [];
}

function asNumber(value, fallback = 0) {
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = html;
        element.classList.remove("loading-shell");
        element.classList.remove("muted");
        element.removeAttribute("aria-busy");
    }
}

function setHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = html;
        element.classList.remove("loading-shell");
        element.classList.remove("muted");
        element.removeAttribute("aria-busy");
    }
}

function bindSidebarNavigation() {
    document.querySelectorAll('.sidebar-nav-item[href^="#"]').forEach((link) => {
        if (link.dataset.boundNav === "true") {
            return;
        }
        link.dataset.boundNav = "true";
        link.addEventListener("click", (event) => {
            const href = link.getAttribute("href") || "";
            const targetId = href.slice(1);
            const scrollRoot = document.querySelector(".page-scroll");
            const target = targetId ? document.getElementById(targetId) : null;
            if (!scrollRoot || !target) {
                return;
            }
            event.preventDefault();
            target.scrollIntoView({ behavior: "smooth", block: "start" });
            window.history.replaceState(null, "", `#${targetId}`);
        });
    });
}

function applyRepoTabVisibility() {
    document.body.dataset.activeRepoTab = activeRepoTab;

    document.querySelectorAll("[data-repo-tab-panel]").forEach((element) => {
        const supportedTabs = String(element.getAttribute("data-repo-tab-panel") || "")
            .split(/\s+/)
            .filter(Boolean);
        element.hidden = supportedTabs.length > 0 && !supportedTabs.includes(activeRepoTab);
    });

    document.querySelectorAll("[data-repo-tab-child]").forEach((element) => {
        const supportedTabs = String(element.getAttribute("data-repo-tab-child") || "")
            .split(/\s+/)
            .filter(Boolean);
        element.hidden = supportedTabs.length > 0 && !supportedTabs.includes(activeRepoTab);
    });

    document.querySelectorAll("[data-repo-tab-link]").forEach((element) => {
        const tab = element.getAttribute("data-repo-tab-link") || "";
        if (tab === activeRepoTab) {
            element.setAttribute("aria-current", "page");
            return;
        }
        element.removeAttribute("aria-current");
    });
}

function setText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
        element.removeAttribute("aria-busy");
    }
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function dashboardShellState() {
    return String(document.body?.dataset?.dashboardShellState || "active").trim().toLowerCase() || "active";
}

function dashboardShellCopy() {
    return {
        title: String(document.body?.dataset?.dashboardShellTitle || "Dashboard access status"),
        body: String(document.body?.dataset?.dashboardShellBody || "Repository dashboard data is unavailable for this workspace."),
        ctaHref: String(document.body?.dataset?.dashboardShellCtaHref || ""),
        ctaLabel: String(document.body?.dataset?.dashboardShellCtaLabel || ""),
    };
}

function deepLinkPullRequestNumber() {
    const metaValue = document.querySelector('meta[name="driftguard-deep-link-pr"]')?.getAttribute("content")?.trim();
    if (metaValue) {
        return metaValue;
    }
    const params = new URLSearchParams(window.location.search);
    return params.get("pr") || "";
}

function deepLinkHeadSha() {
    const metaValue = document.querySelector('meta[name="driftguard-deep-link-head-sha"]')?.getAttribute("content")?.trim();
    if (metaValue) {
        return metaValue;
    }
    const params = new URLSearchParams(window.location.search);
    return params.get("head_sha") || "";
}

function repoDetailUrl(repo) {
    const url = new URL(`/dashboard/${encodeURIComponent(repo.repo_full)}/audit`, window.location.origin);
    const artifactPath = repo.highest_insight_artifact_path || "";
    if (artifactPath) {
        url.searchParams.set("artifact", artifactPath);
    }
    return `${url.pathname}${url.search}`;
}

function repoTabUrl(tab, options = {}) {
    const normalizedTab = String(tab || "").trim().toLowerCase();
    const isAuditTab = normalizedTab === "audit";
    const url = new URL(isAuditTab ? `/dashboard/${encodeURIComponent(repoFull)}/audit` : `/dashboard/${encodeURIComponent(repoFull)}`, window.location.origin);
    if (normalizedTab && !isAuditTab) {
        url.searchParams.set("tab", tab);
    }
    const artifactPath = options.artifactPath || requestedArtifactPath();
    if (artifactPath) {
        url.searchParams.set("artifact", artifactPath);
    }
    const prNumber = options.prNumber || deepLinkPullRequestNumber();
    if (prNumber) {
        url.searchParams.set("pr", prNumber);
    }
    const headSha = options.headSha || deepLinkHeadSha();
    if (headSha) {
        url.searchParams.set("head_sha", headSha);
    }
    if (options.hash) {
        url.hash = options.hash;
    }
    return `${url.pathname}${url.search}${url.hash}`;
}

function renderAuditBrief(brief) {
    const badgeEl = document.getElementById("repo-audit-brief-posture");
    if (!brief) {
        if (badgeEl) {
            badgeEl.textContent = "Unavailable";
            badgeEl.className = "severity-badge severity-medium";
        }
        setText("repo-audit-brief-summary", "Audit brief is not available for this repository yet.");
        setSectionHtml("repo-audit-brief-why", '<div class="muted">No audit brief data is available.</div>');
        setSectionHtml("repo-audit-brief-actions", '<div class="muted">—</div>');
        setSectionHtml("repo-audit-brief-findings", '<div class="muted">No findings are currently available.</div>');
        return;
    }

    if (badgeEl) {
        const className = brief.severity_tone === "high"
            ? "severity-high"
            : brief.severity_tone === "medium"
                ? "severity-medium"
                : "severity-low";
        badgeEl.textContent = brief.recommendation_label || brief.severity_label || "Review";
        badgeEl.className = `severity-badge ${className}`;
    }

    setText(
        "repo-audit-brief-summary",
        `${brief.changed_artifact_count || 0} changed artifacts · ${brief.review_now_count || 0} review now · ${brief.watch_count || 0} watch · ${brief.lower_confidence_count || 0} lower-confidence`
    );
    setSectionHtml("repo-audit-brief-why", `
        <div class="stack compact-stack">
            <div>${escapeHtml(brief.why_now || "Review context is loading.")}</div>
            <div class="detail-note">${escapeHtml(brief.summary || "")}</div>
            <div class="tag-row">
                <span class="drift-chip chip-baseline">${escapeHtml(brief.baseline_reference || "Baseline: none yet")}</span>
                <span class="drift-chip chip-governance">${escapeHtml(`Baseline ${brief.baseline_status || "unknown"}`)}</span>
                <span class="drift-chip chip-model">${escapeHtml(brief.confidence_label || "mixed")}</span>
                ${asArray(brief.affected_dimensions).slice(0, 3).map((dimension) => `<span class="drift-chip chip-guardrails">${escapeHtml(dimension)}</span>`).join("")}
            </div>
        </div>
    `);
    setSectionHtml("repo-audit-brief-actions", `
        <div class="export-actions repo-actions-row">
            ${asArray(brief.actions).map((action) => `<a href="${escapeHtml(action.href || "#")}" class="${action.style === "primary" ? "export-submit-button" : "cue-action-button"}">${escapeHtml(action.label || "Open")}</a>`).join("")}
        </div>
    `);
    setSectionHtml("repo-audit-brief-findings", asArray(brief.findings).length ? asArray(brief.findings).map((finding) => `
        <div class="artifact-card">
            <strong>${escapeHtml(finding.title || finding.artifact_path || "Finding")}</strong>
            <div class="artifact-card-reason">${escapeHtml(finding.artifact_path || "")}${finding.evidence_label ? ` · ${escapeHtml(finding.evidence_label)}` : ""}${finding.review_target ? ` · ${escapeHtml(finding.review_target)}` : ""}</div>
            <div class="detail-note">${escapeHtml(finding.summary || "")}</div>
            ${asArray(finding.affected_dimensions).length ? `<div class="tag-row">${asArray(finding.affected_dimensions).slice(0, 3).map((dimension) => `<span class="drift-chip chip-governance">${escapeHtml(dimension)}</span>`).join("")}</div>` : ""}
        </div>
    `).join("") : '<div class="muted">No primary findings are currently available.</div>');
}

function profileMetricLabel(key) {
    return {
        guardrail_robustness: "Guardrails",
        capability_risk: "Capability",
        autonomy_level: "Autonomy",
        stability_vs_creativity: "Stability",
        governance_strength: "Governance",
        change_frequency: "Velocity",
        semantic_density: "Density",
    }[key] || key.replaceAll("_", " ");
}

function renderProfileMetricBars(profile = {}) {
    const keys = ["guardrail_robustness", "capability_risk", "autonomy_level", "stability_vs_creativity", "governance_strength", "change_frequency"];
    return keys.map((key) => {
        const value = clamp(Number(profile[key] || 0), 0, 1);
        return `
            <div class="baseline-metric-row">
                <span class="baseline-metric-label">${escapeHtml(profileMetricLabel(key))}</span>
                <div class="baseline-metric-track"><div class="baseline-metric-fill" style="width:${value * 100}%"></div></div>
                <span class="baseline-metric-value">${value.toFixed(2)}</span>
            </div>
        `;
    }).join("");
}

function baselineStatusBadge(status) {
    const normalized = String(status || "pending").toLowerCase();
    const className = normalized === "approved" ? "baseline-status-approved" : normalized === "rejected" ? "baseline-status-rejected" : "baseline-status-pending";
    const label = normalized === "approved" ? "Approved" : normalized === "rejected" ? "Rejected" : "Pending";
    return `<span class="baseline-status-badge ${className}">${label}</span>`;
}

function formatUnixTimestamp(ts) {
    const value = Number(ts);
    if (!Number.isFinite(value) || value <= 0) {
        return "Unknown time";
    }
    return new Date(value * 1000).toLocaleString();
}

function renderBaselineReviewPanel(panel) {
    if (!panel) {
        return '<div class="muted">No artifact sign-off data is available for this repository yet.</div>';
    }

    const recentDecisions = asArray(panel.recent_decisions);
    const artifactCards = asArray(panel.artifacts).slice(0, 6).map((artifact) => `
        <div class="artifact-card">
            <strong>${escapeHtml(artifact.artifact_path)}</strong>
            <div class="artifact-card-reason">${baselineStatusBadge(artifact.approval_status)} ${escapeHtml(artifact.artifact_type || "artifact")} · ${escapeHtml(artifact.provenance_label || "Supporting repository artifact")} · ${escapeHtml(`${artifact.line_count || 0} lines`)}</div>
            <div class="detail-note">${escapeHtml(artifact.approval_note || "No approval rationale recorded yet.")}</div>
        </div>
    `).join("");

    const decisionsHtml = recentDecisions.length
        ? `<div class="stack compact-stack">${recentDecisions.map((decision) => `
            <div class="artifact-card">
                <strong>${escapeHtml(decision.decision_type || decision.action || "decision")}</strong>
                <div class="artifact-card-reason">${escapeHtml(decision.actor_login || "system")} · ${escapeHtml(formatUnixTimestamp(decision.created_at))}${decision.artifact_path ? ` · ${escapeHtml(decision.artifact_path)}` : ""}</div>
                <div class="detail-note">${escapeHtml(decision.rationale || "No rationale recorded.")}</div>
                ${asArray(decision.linked_findings).length ? `<div class="tag-row">${asArray(decision.linked_findings).map((finding) => `<span class="drift-chip chip-governance">${escapeHtml(finding)}</span>`).join("")}</div>` : ""}
            </div>
        `).join("")}</div>`
        : '<div class="muted">No artifact sign-off decisions have been recorded yet.</div>';

    const actionsHtml = panel.is_pending_review
        ? `
            <div class="export-actions">
                <button type="button" class="export-submit-button" data-baseline-decision="approve">Approve artifact batch</button>
                <button type="button" class="cue-action-button" data-baseline-decision="reject">Reject artifact batch</button>
            </div>
        `
        : '<div class="detail-note">Artifact sign-off is not currently pending for this repository.</div>';

    return `
        <div class="stack compact-stack">
            <div class="detail-note">Artifact Sign-off records human approval for each individual artifact. It confirms whether the stored files are trusted evidence and does not change which snapshot DriftGuard uses as the reference baseline.</div>
            <div class="journey-strip">
                <div class="journey-node journey-tone-primary">
                    <span class="journey-node-value">${escapeHtml(String(panel.artifact_count || 0))}</span>
                    <span class="journey-node-label">Artifacts</span>
                    <span class="journey-node-caption">Tracked baseline candidates</span>
                </div>
                <div class="journey-node journey-tone-medium">
                    <span class="journey-node-value">${escapeHtml(String(panel.pending_count || 0))}</span>
                    <span class="journey-node-label">Pending sign-off</span>
                    <span class="journey-node-caption">Awaiting human approval per artifact</span>
                </div>
                <div class="journey-node journey-tone-primary">
                    <span class="journey-node-value">${escapeHtml(String(panel.approved_count || 0))}</span>
                    <span class="journey-node-label">Approved</span>
                    <span class="journey-node-caption">Artifacts explicitly trusted by a human reviewer</span>
                </div>
                <div class="journey-node journey-tone-gap">
                    <span class="journey-node-value">${escapeHtml(String(panel.rejected_count || 0))}</span>
                    <span class="journey-node-label">Rejected</span>
                    <span class="journey-node-caption">Artifacts that need follow-up or rework</span>
                </div>
            </div>
            ${actionsHtml}
            <div>
                <div class="detail-section-label">Recent artifact sign-off decisions</div>
                ${decisionsHtml}
            </div>
            <div>
                <div class="detail-section-label">Current artifact sign-off state</div>
                <div class="stack compact-stack">${artifactCards || '<div class="muted">No baseline artifacts are present.</div>'}</div>
            </div>
        </div>
    `;
}

function renderAiActAssessment(onboarding, artifacts = [], baselineReview = null) {
    const items = asArray(artifacts);
    if (!items.length) {
        return '<div class="muted">No stored onboarding artifacts are available yet for a repo-level relevance assessment.</div>';
    }

    const counts = {
        aiControl: 0,
        tool: 0,
        model: 0,
        governance: 0,
    };

    for (const artifact of items) {
        const kind = String(artifact?.provenance_kind || "");
        if (kind === "ai_control_surface") {
            counts.aiControl += 1;
        } else if (kind === "ai_tool_surface") {
            counts.tool += 1;
        } else if (kind === "model_behavior_surface") {
            counts.model += 1;
        } else if (kind === "human_governance_surface") {
            counts.governance += 1;
        }
    }

    const statusTags = [];
    if (counts.aiControl > 0) {
        statusTags.push(`<span class="drift-chip chip-capability">${escapeHtml(`${counts.aiControl} AI control surface${counts.aiControl === 1 ? "" : "s"}`)}</span>`);
    }
    if (counts.tool > 0) {
        statusTags.push(`<span class="drift-chip chip-model">${escapeHtml(`${counts.tool} tool surface${counts.tool === 1 ? "" : "s"}`)}</span>`);
    }
    if (counts.model > 0) {
        statusTags.push(`<span class="drift-chip chip-baseline">${escapeHtml(`${counts.model} model/config surface${counts.model === 1 ? "" : "s"}`)}</span>`);
    }
    if (counts.governance > 0) {
        statusTags.push(`<span class="drift-chip chip-governance">${escapeHtml(`${counts.governance} governance artifact${counts.governance === 1 ? "" : "s"}`)}</span>`);
    }
    statusTags.push(`<span class="drift-chip ${baselineReview?.is_pending_review ? "chip-baseline" : "chip-guardrails"}">${escapeHtml(baselineReview?.is_pending_review ? "Artifact sign-off pending" : "Human-approved artifacts")}</span>`);

    const reviewState = baselineReview?.is_pending_review
        ? "Artifact sign-off is still pending for part of the stored evidence."
        : "Stored evidence includes a reviewed baseline reference for this repository."
    const repoStatus = String(onboarding?.status || "onboarded repository").replaceAll("_", " ");

    return `
        <div class="stack compact-stack">
            <p class="detail-note">This repo view surfaces stored evidence that may require AI governance review. It does not classify the repository under the EU AI Act or make legal claims.</p>
            <div class="journey-strip">
                <div class="journey-node journey-tone-primary">
                    <span class="journey-node-value">${escapeHtml(String(counts.aiControl + counts.tool + counts.model))}</span>
                    <span class="journey-node-label">AI surfaces</span>
                    <span class="journey-node-caption">Prompt, tool, and model/config artifacts found in stored onboarding evidence.</span>
                </div>
                <div class="journey-node journey-tone-medium">
                    <span class="journey-node-value">${escapeHtml(String(counts.governance))}</span>
                    <span class="journey-node-label">Governance</span>
                    <span class="journey-node-caption">Policy and guardrail artifacts detected during onboarding.</span>
                </div>
                <div class="journey-node ${baselineReview?.is_pending_review ? "journey-tone-gap" : "journey-tone-primary"}">
                    <span class="journey-node-value">${escapeHtml(String(baselineReview?.pending_count || 0))}</span>
                    <span class="journey-node-label">Pending review</span>
                    <span class="journey-node-caption">Baseline entries still awaiting human approval.</span>
                </div>
            </div>
            <div class="tag-row">${statusTags.join("")}</div>
            <div class="artifact-card">
                <strong>Oversight summary</strong>
                <div class="artifact-card-reason">${escapeHtml(repoStatus)} · ${escapeHtml(`${items.length} stored artifacts`)}</div>
                <div class="detail-note">${escapeHtml(reviewState)}</div>
            </div>
        </div>
    `;
}

function renderPreAuditRelevancePanel(preAuditRelevance) {
    const scopedPrNumber = String(preAuditRelevance?.pr_number || "").trim();
    const scopedHeadSha = String(preAuditRelevance?.head_sha || "").trim();
    const decisions = asArray(preAuditRelevance?.decisions);
    if (!scopedPrNumber || !scopedHeadSha) {
        return '<div class="artifact-card"><strong>PR-scoped relevance</strong><div class="detail-note">Open this repo from a pull-request deep link to review the persisted pre-audit relevance gate for that exact change set.</div></div>';
    }
    if (!decisions.length) {
        return `<div class="artifact-card"><strong>PR-scoped relevance</strong><div class="artifact-card-reason">PR #${escapeHtml(scopedPrNumber)} · ${escapeHtml(scopedHeadSha.slice(0, 12))}</div><div class="detail-note">No persisted pre-audit relevance decisions were found for this pull request snapshot.</div></div>`;
    }
    return `
        <div class="stack compact-stack">
            <div class="artifact-card">
                <strong>PR-scoped relevance gate</strong>
                <div class="artifact-card-reason">PR #${escapeHtml(scopedPrNumber)} · ${escapeHtml(scopedHeadSha.slice(0, 12))} · ${escapeHtml(`${decisions.length} decision${decisions.length === 1 ? "" : "s"}`)}</div>
                <div class="detail-note">These rows show how the confidence-tiered relevance filter treated the incoming change set before audit creation.</div>
            </div>
            ${decisions.slice(0, 4).map((decision) => {
                const tier = String(decision?.confidence_tier || "uncertain").replaceAll("_", " ");
                const classifierStatus = String(decision?.classifier_status || "not_run").replaceAll("_", " ");
                const classifierVerdict = decision?.classifier_is_relevant === true
                    ? "Relevant"
                    : decision?.classifier_is_relevant === false
                        ? "Not relevant"
                        : "No classifier verdict";
                const rationale = decision?.classifier_reason || decision?.heuristic_reason || "No rationale recorded.";
                return `
                    <div class="artifact-card">
                        <strong>${escapeHtml(decision?.artifact_path || "Unknown artifact")}</strong>
                        <div class="artifact-card-reason">${escapeHtml(tier)} · heuristic ${escapeHtml(String(decision?.heuristic_score ?? "0"))} · ${escapeHtml(classifierStatus)}</div>
                        <div class="detail-note">${escapeHtml(rationale)}</div>
                        <div class="tag-row">
                            <span class="drift-chip chip-model">${escapeHtml(classifierVerdict)}</span>
                            ${decision?.changed_artifact_id ? `<span class="drift-chip chip-guardrails">Linked to audit artifact #${escapeHtml(String(decision.changed_artifact_id))}</span>` : '<span class="drift-chip chip-baseline">No audit artifact link yet</span>'}
                        </div>
                    </div>
                `;
            }).join("")}
        </div>
    `;
}

function governanceSurfaceCounts(artifacts = []) {
    const counts = {
        aiControl: 0,
        tool: 0,
        model: 0,
        governance: 0,
    };

    for (const artifact of asArray(artifacts)) {
        const kind = String(artifact?.provenance_kind || "");
        if (kind === "ai_control_surface") {
            counts.aiControl += 1;
        } else if (kind === "ai_tool_surface") {
            counts.tool += 1;
        } else if (kind === "model_behavior_surface") {
            counts.model += 1;
        } else if (kind === "human_governance_surface") {
            counts.governance += 1;
        }
    }

    return counts;
}

function renderGovernanceAttentionNote(onboarding, artifacts = [], baselineReview = null, journeySnapshots = []) {
    const posture = onboarding;
    const ownershipConfidence = String(posture?.ownership_confidence || "low confidence").replaceAll("_", " ");
    const reviewQuality = String(posture?.review_quality || "mixed").replaceAll("_", " ");
    const baselineFreshness = String(posture?.baseline_freshness_status || "current").replaceAll("_", " ");
    const repeatedCount = Number(posture?.repeated_drift_without_refresh_count || 0);
    const anomalies = asArray(posture?.top_governance_anomalies).slice(0, 3);

    let headline = "Moderate governance attention";
    if (reviewQuality === "weak for recent high-risk change" || baselineFreshness === "stale after repeated change" || repeatedCount > 0) {
        headline = "Higher governance attention";
    } else if (reviewQuality === "adequate" && ownershipConfidence === "established" && !anomalies.length) {
        headline = "Lower governance attention";
    }

    const body = [
        `Review quality is ${reviewQuality}.`,
        `Ownership confidence is ${ownershipConfidence}.`,
        `Baseline freshness is ${baselineFreshness}.`,
        repeatedCount > 0 ? `${repeatedCount} repeated drift signal${repeatedCount === 1 ? " is" : "s are"} still open.` : null,
    ].filter(Boolean).join(" ");

    const details = anomalies.length
        ? `
            <div class="stack compact-stack">
                <div class="tag-row">
                    <span class="drift-chip chip-governance">${escapeHtml(`${repeatedCount} repeated drift signal${repeatedCount === 1 ? "" : "s"}`)}</span>
                    <span class="drift-chip chip-governance">${escapeHtml(`Ownership: ${ownershipConfidence}`)}</span>
                    <span class="drift-chip chip-governance">${escapeHtml(`Baseline: ${baselineFreshness}`)}</span>
                </div>
            </div>
        `
        : '<div class="muted">No backend governance anomalies are ranked for this repo right now.</div>';

    return { headline, body, details };
}

async function mutateRepoBaselineDecision(action, note) {
    const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/baseline/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
    });
    if (!response.ok) {
        throw new Error(`Baseline ${action} failed with ${response.status}`);
    }
    return response.json();
}

function bindBaselineReviewActions() {
    document.querySelectorAll("[data-baseline-decision]").forEach((button) => {
        if (button.dataset.boundBaselineDecision === "true") {
            return;
        }
        button.dataset.boundBaselineDecision = "true";
        button.addEventListener("click", async () => {
            const action = button.getAttribute("data-baseline-decision");
            if (!action) {
                return;
            }
            const note = window.prompt(`Optional rationale for ${action}ing this baseline:`) || "";
            button.disabled = true;
            try {
                const payload = await mutateRepoBaselineDecision(action, note.trim() || null);
                if (payload?.dashboard) {
                    applyDashboardPayload(payload.dashboard);
                    return;
                }
                await loadDashboard();
            } catch (error) {
                const message = error instanceof Error ? error.message : `Baseline ${action} failed.`;
                window.alert(message);
            } finally {
                button.disabled = false;
            }
        });
    });
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function normalizeRadarValue(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    return clamp(numeric, 0, 1);
}

function compactRepoLabel(repoFullValue) {
    const segments = String(repoFullValue || "").split("/").filter(Boolean);
    return segments.length ? segments[segments.length - 1] : String(repoFullValue || "Repository posture");
}

function repoRadarVectors(repoPayload) {
    const snapshots = asArray(repoPayload?.journey_snapshots);
    if (!snapshots.length) {
        return null;
    }

    const selectedBaselineId = Number(repoPayload?.selected_baseline_source_snapshot_id || 0);
    const baselineSnapshot = (selectedBaselineId
        ? snapshots.find((snapshot) => Number(snapshot.id) === selectedBaselineId)
        : null) || snapshots.find((snapshot) => snapshot.snapshot_type === "baseline_approved") || snapshots[0];
    const currentSnapshot = snapshots.find((snapshot) => snapshot.snapshot_type === "current")
        || snapshots.find((snapshot) => snapshot.snapshot_type === "branch_head")
        || snapshots[snapshots.length - 1];
    if (!baselineSnapshot || !currentSnapshot) {
        return null;
    }

    const toSeries = (snapshot) => {
        const vector = snapshot?.attribute_vector || {};
        const inputSummary = snapshot?.input_summary || {};
        return [
            normalizeRadarValue(vector.governance),
            normalizeRadarValue(vector.change_velocity),
            normalizeRadarValue(Number(inputSummary.coverage_percent || 0) / 100),
            normalizeRadarValue(vector.autonomy),
            normalizeRadarValue(vector.capability),
            normalizeRadarValue(vector.guardrails),
        ];
    };

    return {
        labels: ["Governance", "Velocity", "Coverage", "Autonomy", "Capability", "Guardrails"],
        baselineKey: baselineSnapshot.snapshot_key || baselineSnapshot.source_ref || "Approved baseline",
        currentKey: currentSnapshot.snapshot_key || currentSnapshot.source_ref || "Current snapshot",
        series: [
            {
                color: "rgba(78, 103, 255, 0.28)",
                stroke: "rgba(79, 106, 255, 0.9)",
                values: toSeries(baselineSnapshot),
            },
            {
                color: "rgba(73, 223, 217, 0.22)",
                stroke: "rgba(85, 230, 222, 0.92)",
                values: toSeries(currentSnapshot),
            },
        ],
    };
}

function drawRepoRadar(repoPayload) {
    const canvas = document.getElementById("repo-posture-radar");
    if (!(canvas instanceof HTMLCanvasElement)) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }

    const vectors = repoRadarVectors(repoPayload);
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!vectors) {
        return;
    }

    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2 + 10;
    const radius = 118;
    const angleStep = (Math.PI * 2) / vectors.labels.length;
    const darkTheme = document.body?.dataset?.theme !== "light";
    const ringColor = darkTheme ? "rgba(255,255,255,0.10)" : "rgba(112, 98, 84, 0.22)";
    const axisColor = darkTheme ? "rgba(255,255,255,0.08)" : "rgba(112, 98, 84, 0.18)";
    const labelColor = darkTheme ? "rgba(221, 222, 225, 0.55)" : "rgba(77, 68, 60, 0.84)";

    for (let level = 1; level <= 4; level += 1) {
        const scale = level / 4;
        context.beginPath();
        vectors.labels.forEach((_, index) => {
            const angle = -Math.PI / 2 + index * angleStep;
            const x = centerX + Math.cos(angle) * radius * scale;
            const y = centerY + Math.sin(angle) * radius * scale;
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.closePath();
        context.strokeStyle = ringColor;
        context.lineWidth = 1;
        context.stroke();
    }

    vectors.labels.forEach((label, index) => {
        const angle = -Math.PI / 2 + index * angleStep;
        const axisX = centerX + Math.cos(angle) * radius;
        const axisY = centerY + Math.sin(angle) * radius;
        context.beginPath();
        context.moveTo(centerX, centerY);
        context.lineTo(axisX, axisY);
        context.strokeStyle = axisColor;
        context.stroke();

        const labelX = centerX + Math.cos(angle) * (radius + 22);
        const labelY = centerY + Math.sin(angle) * (radius + 22);
        context.fillStyle = labelColor;
        context.font = "500 13px Manrope";
        context.textAlign = labelX > centerX + 10 ? "left" : labelX < centerX - 10 ? "right" : "center";
        context.textBaseline = labelY > centerY + 10 ? "top" : labelY < centerY - 10 ? "bottom" : "middle";
        context.fillText(label, labelX, labelY);
    });

    vectors.series.forEach((series) => {
        context.beginPath();
        series.values.forEach((value, index) => {
            const angle = -Math.PI / 2 + index * angleStep;
            const x = centerX + Math.cos(angle) * radius * normalizeRadarValue(value);
            const y = centerY + Math.sin(angle) * radius * normalizeRadarValue(value);
            if (index === 0) {
                context.moveTo(x, y);
            } else {
                context.lineTo(x, y);
            }
        });
        context.closePath();
        context.fillStyle = series.color;
        context.strokeStyle = series.stroke;
        context.lineWidth = 2;
        context.fill();
        context.stroke();
    });

    setText("repo-radar-title", compactRepoLabel(repoFull));
    setText("repo-radar-meta", `${vectors.baselineKey} vs ${vectors.currentKey}`);
}

function severityForPriority(priority) {
    if (priority === "review_now") {
        return { label: "High", className: "severity-high" };
    }
    if (priority === "watch") {
        return { label: "Medium", className: "severity-medium" };
    }
    return { label: "Low", className: "severity-low" };
}

function detailButton() {
    return document.getElementById("detail-escalate-btn");
}

function findDesignProfile(artifactPath) {
    return asArray(window.__designProfiles).find((item) => item.artifact_path === artifactPath) || null;
}

function attributeScore(entry, keyPrefix) {
    const scoreKey = keyPrefix === "baseline" ? "baseline_score" : "current_score";
    const score = Number(entry?.[scoreKey]);
    if (Number.isFinite(score)) {
        return clamp(score, 0, 1);
    }
    const fallbackKey = keyPrefix === "baseline" ? "baseline_value" : "current_value";
    const fallback = Number(entry?.[fallbackKey]);
    if (Number.isFinite(fallback)) {
        return clamp(fallback, 0, 1);
    }
    return 0;
}

function renderInsightChips(item, profile) {
    const mergedReasons = [...new Set([...(item.risk_reasons || []), ...((profile?.risk_tags) || [])])];
    const reasons = mergedReasons.filter((reason) => {
        const normalized = String(reason).toLowerCase();
        if (normalized !== "baseline only") {
            return true;
        }
        return String(item?.evidence_label || "").toLowerCase() === "baseline only";
    });
    return reasons.slice(0, 4).map((reason) => {
        const normalized = String(reason).toLowerCase();
        const className = normalized.includes("guardrail")
            ? "chip-guardrails"
            : normalized.includes("capability")
                ? "chip-capability"
                : normalized.includes("autonomy")
                    ? "chip-autonomy"
                    : normalized.includes("governance")
                        ? "chip-governance"
                        : normalized.includes("baseline")
                            ? "chip-baseline"
                            : "chip-model";
        return `<span class="drift-chip ${className}">${escapeHtml(reason)}</span>`;
    }).join("");
}

function profileDimensionLabel(key) {
    return {
        guardrail_robustness: "Guardrails",
        capability_risk: "Capability",
        autonomy_level: "Autonomy",
        governance_strength: "Governance",
        model_config_posture: "Model/config",
        control_surface_type: "Control surface",
    }[key] || key.replaceAll("_", " ");
}

function fallbackProfileDimension(key) {
    return {
        attribute_key: key,
        label: profileDimensionLabel(key),
        baseline_value: "unknown",
        current_value: "unknown",
        direction: "unknown",
        state: "unknown",
        confidence_label: "low confidence",
        confidence_score: 0.4,
        reason: "No normalized attribute evidence was available for this dimension.",
    };
}

function normalizedProfileDimensions(profile) {
    const desiredOrder = [
        "guardrail_robustness",
        "capability_risk",
        "autonomy_level",
        "governance_strength",
        "model_config_posture",
        "control_surface_type",
    ];
    const entriesByKey = new Map(asArray(profile?.attribute_profile || []).map((entry) => [entry.attribute_key, entry]));
    return desiredOrder.map((key) => entriesByKey.get(key) || fallbackProfileDimension(key));
}

function profileStateClass(entry) {
    if (entry.state === "unknown") {
        return "attribute-profile-row-unknown";
    }
    if (entry.state === "no_change") {
        return "attribute-profile-row-neutral";
    }
    if (["weakened", "expanded", "increased", "more exploratory"].includes(String(entry.direction || "").toLowerCase())) {
        return "attribute-profile-row-regression";
    }
    return "attribute-profile-row-improvement";
}

function renderAttributeProfileSummaryCards(profile) {
    return normalizedProfileDimensions(profile).map((entry) => `
        <div class="attribute-profile-summary-card ${profileStateClass(entry)}">
            <span class="attribute-profile-summary-label">${escapeHtml(entry.label || profileDimensionLabel(entry.attribute_key))}</span>
            <strong class="attribute-profile-summary-transition">${escapeHtml(`${entry.baseline_value || "unknown"} -> ${entry.current_value || "unknown"}`)}</strong>
            <span class="attribute-profile-summary-confidence">${escapeHtml(entry.confidence_label || "low confidence")}</span>
        </div>
    `).join("");
}

function renderAttributeProfileTableRows(profile, activeTabKey) {
    const tab = ATTRIBUTE_PROFILE_TAB_CONFIG.find((item) => item.key === activeTabKey) || ATTRIBUTE_PROFILE_TAB_CONFIG[0];
    const rows = normalizedProfileDimensions(profile).filter((entry) => tab.dimensionKeys.includes(entry.attribute_key));
    return rows.map((entry) => `
        <tr class="${profileStateClass(entry)}">
            <td>${escapeHtml(entry.label || profileDimensionLabel(entry.attribute_key))}</td>
            <td>${escapeHtml(`${entry.baseline_value || "unknown"} -> ${entry.current_value || "unknown"}`)}</td>
            <td>${escapeHtml(entry.reason || "No normalized attribute evidence was available for this dimension.")}</td>
            <td>${escapeHtml(entry.confidence_label || "low confidence")}</td>
        </tr>
    `).join("");
}

function renderAttributeProfilePanel(profile, activeTabKey = window.__attributeProfileActiveTab || ATTRIBUTE_PROFILE_TAB_CONFIG[0].key) {
    const activeTab = ATTRIBUTE_PROFILE_TAB_CONFIG.find((item) => item.key === activeTabKey) || ATTRIBUTE_PROFILE_TAB_CONFIG[0];
    const controlSurface = normalizedProfileDimensions(profile).find((entry) => entry.attribute_key === "control_surface_type");
    return `
        <div class="attribute-profile-panel">
            <div class="attribute-profile-summary-grid">
                ${renderAttributeProfileSummaryCards(profile)}
            </div>
            <div class="attribute-profile-tabs" role="tablist" aria-label="Attribute profile filters">
                ${ATTRIBUTE_PROFILE_TAB_CONFIG.map((tab) => `
                    <button type="button" class="attribute-profile-tab ${tab.key === activeTab.key ? "active" : ""}" data-attribute-profile-tab="${escapeHtml(tab.key)}" role="tab" aria-selected="${tab.key === activeTab.key ? "true" : "false"}">${escapeHtml(tab.label)}</button>
                `).join("")}
            </div>
            <div class="attribute-profile-control-surface">Control surface: ${escapeHtml(controlSurface?.current_value || "unknown")}</div>
            <div class="attribute-profile-table-wrap">
                <table class="attribute-profile-table">
                    <thead>
                        <tr>
                            <th>Attribute</th>
                            <th>Baseline -> Current</th>
                            <th>Reason</th>
                            <th>Confidence</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${renderAttributeProfileTableRows(profile, activeTab.key)}
                    </tbody>
                </table>
            </div>
        </div>
    `;
}

function bindAttributeProfileTabs(profile) {
    document.querySelectorAll("[data-attribute-profile-tab]").forEach((button) => {
        if (button.dataset.boundAttributeProfileTab === "true") {
            return;
        }
        button.dataset.boundAttributeProfileTab = "true";
        button.addEventListener("click", () => {
            window.__attributeProfileActiveTab = button.getAttribute("data-attribute-profile-tab") || ATTRIBUTE_PROFILE_TAB_CONFIG[0].key;
            setSectionHtml("detail-attributes", renderAttributeProfilePanel(profile, window.__attributeProfileActiveTab));
            bindAttributeProfileTabs(profile);
        });
    });
}

function renderRepoTriageRow(item, index) {
    const severity = severityForPriority(item.priority);
    const profile = findDesignProfile(item.artifact_path);
    const meta = [item.baseline_label, item.review_target, item.evidence_label].filter(Boolean).join(" · ");
    return `
        <div class="triage-row" data-row-index="${index}" data-severity="${severity.label.toLowerCase()}" role="button" tabindex="0">
            <div class="triage-row-top">
                <span class="severity-badge ${severity.className}">${severity.label}</span>
                <span class="artifact-name">${escapeHtml(repoFull)} / ${escapeHtml(item.artifact_path)}</span>
                <span class="triage-row-chevron" aria-hidden="true">→</span>
            </div>
            <div class="triage-row-chips">${renderInsightChips(item, profile)}</div>
            <div class="triage-row-meta">${escapeHtml(meta || item.title)}</div>
            <div class="triage-row-reason">${escapeHtml(item.change_summary || item.flag_summary || item.rationale || item.title)}</div>
        </div>
    `;
}

function averageProfileScore(profile, key) {
    const attributes = asArray(profile?.attribute_profile || []).filter((entry) => entry.attribute_key !== "control_surface_type");
    if (!attributes.length) {
        return 0;
    }
    const values = attributes
        .map((entry) => attributeScore(entry, key))
        .filter((value) => Number.isFinite(value));
    if (!values.length) {
        return 0;
    }
    return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderEvidenceList(item, profile) {
    const evidence = [
        item.change_summary,
        item.flag_summary,
        item.evidence_summary,
        item.rationale,
        ...(profile?.narrative || []),
    ].filter(Boolean);
    if (!evidence.length) {
        return '<li>No detailed evidence summary is available yet.</li>';
    }
    return evidence.map((entry) => `<li>${escapeHtml(entry)}</li>`).join("");
}

function sourceUrlForInsight(item, profile) {
    return item.review_url || profile?.provenance?.source_url || profile?.baseline_provenance?.source_url || "";
}

function requestedArtifactPath() {
    const metaValue = document.querySelector('meta[name="driftguard-deep-link-artifact"]')?.getAttribute("content")?.trim();
    if (metaValue) {
        return metaValue;
    }
    const params = new URLSearchParams(window.location.search);
    return params.get("artifact") || "";
}

function insightMatchesPullRequest(item, prNumber) {
    const normalizedPr = String(prNumber || "").trim();
    if (!normalizedPr) {
        return false;
    }
    const markers = [
        String(item?.review_target || ""),
        String(item?.supporting_review_target || ""),
        String(item?.review_url || ""),
        String(item?.supporting_review_url || ""),
    ];
    return markers.some((value) => value.includes(`/pull/${normalizedPr}`) || value.includes(`PR #${normalizedPr}`) || value.includes(`pull ${normalizedPr}`));
}

function insightMatchesHeadSha(item, headSha) {
    const normalizedHeadSha = String(headSha || "").trim().toLowerCase();
    if (!normalizedHeadSha) {
        return false;
    }
    return String(item?.review_head_sha || "").trim().toLowerCase() === normalizedHeadSha;
}

function applyRepoDetail(item, options = {}) {
    const profile = findDesignProfile(item.artifact_path);
    const severity = severityForPriority(item.priority);
    const subtitle = [item.title, item.review_target, item.evidence_label].filter(Boolean).join(" · ") || item.artifact_path;
    const baselineScore = averageProfileScore(profile, "baseline");
    const currentScore = averageProfileScore(profile, "current");
    const delta = currentScore - baselineScore;

    window.__selectedInsight = item;

    setText("detail-artifact-name", `${repoFull} / ${item.artifact_path}`);
    const badge = document.getElementById("detail-severity-badge");
    if (badge) {
        badge.textContent = severity.label;
        badge.className = `severity-badge ${severity.className}`;
    }
    setText("detail-subtitle", subtitle);
    setText("detail-baseline-score", Math.round(baselineScore * 100).toString());
    setText("detail-current-score", Math.round(currentScore * 100).toString());
    setText("detail-baseline-label", "Baseline posture");
    setText("detail-current-label", "Current posture");
    setText("detail-score-delta", `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)}`);
    const deltaElement = document.getElementById("detail-score-delta");
    if (deltaElement) {
        deltaElement.className = `score-delta ${delta > 0.02 ? "score-delta-up" : delta < -0.02 ? "score-delta-down" : "score-delta-flat"}`;
    }

    setSectionHtml("detail-attributes", renderAttributeProfilePanel(profile));
    bindAttributeProfileTabs(profile);
    setSectionHtml("detail-evidence-list", renderEvidenceList(item, profile));
    setText("detail-recommendation-body", item.recommended_action || profile?.headline_summary || "Inspect the selected artifact before merge and confirm the changed control surface is still acceptable.");

    const button = detailButton();
    if (button) {
        const targetUrl = sourceUrlForInsight(item, profile);
        button.disabled = false;
        button.onclick = () => {
            if (targetUrl) {
                window.open(targetUrl, "_blank", "noopener,noreferrer");
                return;
            }
            window.location.href = `/dashboard/${encodeURIComponent(repoFull)}`;
        };
    }

    if (options.loadStoryline !== false) {
        loadArtifactStoryline(item.artifact_path);
    }
}

function activateRepoRow(row, items, options = {}) {
    document.querySelectorAll(".triage-row").forEach((candidate) => {
        candidate.classList.remove("selected");
        candidate.removeAttribute("aria-current");
    });
    row.classList.add("selected");
    row.setAttribute("aria-current", "true");
    const index = Number(row.getAttribute("data-row-index"));
    if (Number.isFinite(index) && items[index]) {
        applyRepoDetail(items[index], options);
    }
}

function bindRepoRows(items) {
    const rows = Array.from(document.querySelectorAll(".triage-row"));
    rows.forEach((row) => {
        const activate = () => activateRepoRow(row, items);
        row.addEventListener("click", activate);
        row.addEventListener("focus", activate);
        row.addEventListener("keydown", (event) => {
            const index = Number(row.getAttribute("data-row-index"));
            if (!Number.isFinite(index)) {
                return;
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const delta = event.key === "ArrowDown" ? 1 : -1;
                const nextIndex = clamp(index + delta, 0, rows.length - 1);
                const nextRow = rows[nextIndex];
                if (nextRow instanceof HTMLElement) {
                    nextRow.focus();
                }
                return;
            }
            if (event.key === "Home") {
                event.preventDefault();
                const firstRow = rows[0];
                if (firstRow instanceof HTMLElement) {
                    firstRow.focus();
                }
                return;
            }
            if (event.key === "End") {
                event.preventDefault();
                const lastRow = rows[rows.length - 1];
                if (lastRow instanceof HTMLElement) {
                    lastRow.focus();
                }
                return;
            }
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                activate();
            }
        });
    });
}

function autoSelectRepoRow(items, preferredArtifactPath = "", preferredPrNumber = "", preferredHeadSha = "") {
    const rows = Array.from(document.querySelectorAll(".triage-row"));
    if (!rows.length) {
        if (preferredArtifactPath || preferredPrNumber || preferredHeadSha) {
            renderMissingDeepLinkContext(preferredArtifactPath, preferredPrNumber, preferredHeadSha);
        }
        return false;
    }
    if (preferredHeadSha) {
        const preferredIndex = items.findIndex((item) => insightMatchesHeadSha(item, preferredHeadSha));
        if (preferredIndex >= 0 && rows[preferredIndex]) {
            activateRepoRow(rows[preferredIndex], items);
            return true;
        }
    }
    if (preferredArtifactPath) {
        const preferredIndex = items.findIndex((item) => item.artifact_path === preferredArtifactPath);
        if (preferredIndex >= 0 && rows[preferredIndex]) {
            activateRepoRow(rows[preferredIndex], items);
            return true;
        }
    }
    if (preferredPrNumber) {
        const preferredIndex = items.findIndex((item) => insightMatchesPullRequest(item, preferredPrNumber));
        if (preferredIndex >= 0 && rows[preferredIndex]) {
            activateRepoRow(rows[preferredIndex], items);
            return true;
        }
    }
    if (preferredArtifactPath || preferredPrNumber || preferredHeadSha) {
        renderMissingDeepLinkContext(preferredArtifactPath, preferredPrNumber, preferredHeadSha);
        return false;
    }
    activateRepoRow(rows[0], items, { loadStoryline: false });
    return true;
}

function renderMissingDeepLinkContext(preferredArtifactPath = "", preferredPrNumber = "", preferredHeadSha = "") {
    const requestedParts = [];
    if (preferredArtifactPath) {
        requestedParts.push(`artifact ${preferredArtifactPath}`);
    }
    if (preferredPrNumber) {
        requestedParts.push(`PR #${preferredPrNumber}`);
    }
    if (preferredHeadSha) {
        requestedParts.push(`review ${preferredHeadSha.slice(0, 7)}`);
    }
    const requestedLabel = requestedParts.length ? requestedParts.join(" and ") : "the requested review context";
    const message = `The linked dashboard context for ${requestedLabel} is not available in this repository view yet. Choose another item from the queue or refresh after the next audit finishes.`;
    const notice = `<div class="muted">${escapeHtml(message)}</div>`;

    setSectionHtml("featured-storyline", notice);
    setSectionHtml("detail-attributes", notice);
    setSectionHtml("detail-evidence-list", `<li>${escapeHtml(message)}</li>`);
    setText("detail-artifact-name", repoFull || "Requested dashboard context unavailable");
    setText("detail-subtitle", message);
    setText("detail-recommendation-body", message);

    const badge = document.getElementById("detail-severity-badge");
    if (badge) {
        badge.textContent = "Context unavailable";
        badge.className = "severity-badge severity-low";
    }

    const button = detailButton();
    if (button) {
        button.disabled = true;
    }
}

function filteredRepoItems(items, filter) {
    if (filter === "high") {
        return items.filter((item) => item.priority === "review_now");
    }
    if (filter === "medium") {
        return items.filter((item) => item.priority === "watch");
    }
    return items;
}

function renderRepoQueue(items, filter = "all", preferredArtifactPath = "", preferredPrNumber = "", preferredHeadSha = "") {
    const filtered = filteredRepoItems(items, filter);
    setText("repo-triage-count", `${filtered.length} item${filtered.length === 1 ? "" : "s"}`);
    setSectionHtml("triage-list", filtered.length ? filtered.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No repo insights match this filter.</div>');
    bindRepoRows(filtered);
    autoSelectRepoRow(filtered, preferredArtifactPath, preferredPrNumber, preferredHeadSha);
}

function bindRepoFilters(items, preferredArtifactPath = "", preferredPrNumber = "", preferredHeadSha = "") {
    document.querySelectorAll("[data-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            document.querySelectorAll("[data-filter]").forEach((candidate) => candidate.classList.remove("active"));
            button.classList.add("active");
            renderRepoQueue(items, button.getAttribute("data-filter") || "all", preferredArtifactPath, preferredPrNumber, preferredHeadSha);
        });
    });
}

function formatDateLabel(timestamp) {
    if (typeof timestamp !== "number" || !Number.isFinite(timestamp) || timestamp <= 0) {
        return "Unknown date";
    }
    try {
        return new Date(timestamp * 1000).toLocaleDateString();
    } catch {
        return "Unknown date";
    }
}

function journeyCardTimestamp(snapshot, isSelectedBaseline = false) {
    const approvedAt = Number(snapshot?.input_summary?.approved_at || 0);
    const lastBaselineAt = Number(snapshot?.input_summary?.last_baseline_at || 0);
    const createdAt = Number(snapshot?.created_at || 0);
    if (isSelectedBaseline || snapshot?.snapshot_type === "baseline_approved") {
        if (Number.isFinite(approvedAt) && approvedAt > 0) {
            return approvedAt;
        }
        if (Number.isFinite(lastBaselineAt) && lastBaselineAt > 0) {
            return lastBaselineAt;
        }
    }
    return createdAt;
}

function storylineRiskClass(episode) {
    if (episode?.episode_type === "baseline_milestone" || episode?.source_type === "baseline_promotion") {
        return "storyline-risk-baseline";
    }
    const severity = String(episode?.severity || "low").toLowerCase();
    if (severity === "high") {
        return "storyline-risk-high";
    }
    if (severity === "medium") {
        return "storyline-risk-medium";
    }
    return "storyline-risk-low";
}

function storylineRiskLabel(episode) {
    if (episode?.episode_type === "baseline_milestone" || episode?.source_type === "baseline_promotion") {
        return "Approved baseline";
    }
    const severity = String(episode?.severity || "low").toLowerCase();
    if (severity === "high") {
        return "High attention";
    }
    if (severity === "medium") {
        return "Medium attention";
    }
    return "Low attention";
}

function storylineEpisodeTypeLabel(episode) {
    if (episode?.episode_type === "baseline_milestone") {
        return "Baseline checkpoint";
    }
    if (episode?.episode_type === "current_posture") {
        return "Current posture";
    }
    return String(episode?.episode_type || "mixed").replaceAll("_", " ");
}

function renderStorylineEpisodeNode(episode, index) {
    const provenance = episode.source_url
        ? `<a class="link" href="${episode.source_url}" data-open-source-change="${episode.source_url}" target="_blank" rel="noreferrer noopener">${escapeHtml(episode.source_ref || "Open provenance")}</a>`
        : escapeHtml(episode.source_ref || "No provenance link");
    const riskClass = storylineRiskClass(episode);
    const laneClass = index % 2 === 0 ? "storyline-node-top" : "storyline-node-bottom";
    const attributeTags = asArray(episode.top_attributes).slice(0, 3);
    return `
        <article class="storyline-node ${laneClass} ${riskClass}">
            <div class="storyline-node-date">${escapeHtml(formatDateLabel(episode.episode_timestamp))}</div>
            <div class="storyline-node-rail">
                <span class="storyline-node-dot" aria-hidden="true"></span>
            </div>
            <div class="storyline-node-card">
                <div class="storyline-node-card-head">
                    <div>
                        <strong>${escapeHtml(episode.source_label)}</strong>
                        <div class="artifact-card-type">${escapeHtml(storylineEpisodeTypeLabel(episode))}</div>
                    </div>
                    <span class="storyline-risk-pill">${escapeHtml(storylineRiskLabel(episode))}</span>
                </div>
                ${attributeTags.length ? `<div class="tag-row">${attributeTags.map((item) => `<span class="tag tag-muted">${escapeHtml(item)}</span>`).join("")}</div>` : ""}
                <div class="artifact-card-reason">${escapeHtml(episode.episode_summary || "")}</div>
                <div class="storyline-node-footer muted">
                    <span>${escapeHtml(episode.confidence || "")}</span>
                    <span>${provenance}</span>
                </div>
            </div>
        </article>
    `;
}

function renderStoryline(storyline) {
    const copy = storylinePanelCopy();
    if (!storyline) {
        return `<div class="muted">${escapeHtml(copy.empty)}</div>`;
    }
    return `
        <div class="stack compact-stack">
            <div class="brief-panel">
                <div class="brief-row"><span class="brief-label">Artifact</span><span class="brief-copy"><strong>${escapeHtml(storyline.artifact_path)}</strong> <span class="muted">(${escapeHtml(storyline.artifact_type)})</span></span></div>
                <div class="brief-row"><span class="brief-label">${escapeHtml(copy.itemLabel)}</span><span class="brief-copy">${escapeHtml(storyline.summary || "")}</span></div>
                <div class="brief-row"><span class="brief-label">Posture</span><span class="brief-copy">${escapeHtml(storyline.current_posture_label || "")}</span></div>
            </div>
            ${storyline.limited_history_note ? `<div class="detail-note">${escapeHtml(storyline.limited_history_note)}</div>` : ""}
            <div class="storyline-timeline-scroll">
                <div class="storyline-timeline">${asArray(storyline.episodes).map((episode, index) => renderStorylineEpisodeNode(episode, index)).join("")}</div>
            </div>
        </div>
    `;
}

async function fetchArtifactStoryline(artifactPath) {
    if (window.__storylineCache.has(artifactPath)) {
        return window.__storylineCache.get(artifactPath);
    }
    const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${encodeURIComponent(artifactPath)}/episodes`);
    if (!response.ok) {
        throw new Error(`Artifact storyline request failed with ${response.status}`);
    }
    const payload = await response.json();
    window.__storylineCache.set(artifactPath, payload.storyline || null);
    return payload.storyline || null;
}

async function loadArtifactStoryline(artifactPath) {
    const copy = storylinePanelCopy();
    setSectionHtml("featured-storyline", `<div class="muted">${escapeHtml(copy.loading)}</div>`);
    try {
        const storyline = await fetchArtifactStoryline(artifactPath);
        setSectionHtml("featured-storyline", renderStoryline(storyline));
        bindOpenSourceChangeLinks(document.getElementById("featured-storyline") || document);
    } catch (error) {
        const message = error instanceof Error ? error.message : copy.error;
        setSectionHtml("featured-storyline", `<div class="muted">${escapeHtml(message)}</div>`);
    }
}

function focusStorylineSection() {
    const section = document.getElementById("repo-storyline-section");
    if (!section) {
        return;
    }
    const top = Math.max(0, window.scrollY + section.getBoundingClientRect().top - 24);
    const scrollingElement = document.scrollingElement || document.documentElement;
    scrollingElement.scrollTop = top;
    try {
        section.focus({ preventScroll: true });
    } catch {
        section.focus();
    }
}

function renderControlSurfaces(items = []) {
    if (!items.length) {
        return '<div class="muted">No grouped control surfaces yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => Number(item.artifact_count || 0)), 1);
    return items.map((item) => `
        <div class="drift-type-row">
            <span class="drift-type-label">${escapeHtml(item.label)}</span>
            <div class="drift-type-track"><div class="drift-type-fill" style="width:${(Number(item.artifact_count || 0) / maxArtifacts) * 100}%"></div></div>
            <span class="drift-type-count">${Number(item.artifact_count || 0)}</span>
        </div>
    `).join("");
}

function renderCueCards(items = []) {
    if (!items.length) {
        return '<div class="muted">No repo-level history cues yet.</div>';
    }
    return `<div class="stack compact-stack">${items.map((item) => `
        <div class="artifact-card">
            <div class="artifact-card-head">
                <strong>${escapeHtml(item.label)}</strong>
                ${asArray(item.artifact_paths).length ? `<span class="tag tag-muted">${asArray(item.artifact_paths).length}</span>` : ""}
            </div>
            <div class="artifact-card-reason">${escapeHtml(item.summary)}</div>
            ${asArray(item.artifact_paths).length ? `<div class="tag-row">${asArray(item.artifact_paths).map((artifactPath) => `<button type="button" class="cue-action-button" data-storyline-artifact="${encodeURIComponent(artifactPath)}">${escapeHtml(artifactPath)}</button>`).join("")}</div>` : ""}
        </div>
    `).join("")}</div>`;
}

function bindCueCards() {
    document.querySelectorAll("[data-storyline-artifact]").forEach((button) => {
        button.addEventListener("click", () => {
            const artifactPath = button.getAttribute("data-storyline-artifact");
            if (artifactPath) {
                if (button.closest("#repo-artifacts-section")) {
                    focusStorylineSection();
                }
                loadArtifactStoryline(decodeURIComponent(artifactPath));
            }
        });
    });
}

function artifactTypeOptions() {
    const optionSet = new Set();
    asArray(window.__artifactTypeOptions).forEach((value) => {
        const normalized = String(value || "").trim();
        if (normalized) {
            optionSet.add(normalized);
        }
    });
    asArray(window.__artifactEntries).forEach((item) => {
        const normalized = String(item?.artifact_type || "").trim();
        if (normalized) {
            optionSet.add(normalized);
        }
    });
    return [...optionSet].sort();
}

function artifactOptionEntries() {
    return asArray(window.__artifactOptionEntries).filter((item) => item && typeof item === "object");
}

function inferredArtifactTypeForPath(artifactPath) {
    const match = artifactOptionEntries().find((item) => String(item.path || "") === String(artifactPath || ""));
    return String(match?.inferred_artifact_type || "generic");
}

function replaceArtifactEntryLocal(artifactPath, updates = {}) {
    const path = String(artifactPath || "");
    window.__artifactEntries = asArray(window.__artifactEntries).map((item) => {
        if (String(item?.artifact_path || "") !== path) {
            return item;
        }
        return { ...item, ...updates };
    });
}

function removeArtifactEntryLocal(artifactPath) {
    const path = String(artifactPath || "");
    window.__artifactEntries = asArray(window.__artifactEntries).filter((item) => String(item?.artifact_path || "") !== path);
}

function setArtifactActionStatus(message = "", tone = "info") {
    window.__artifactActionStatusMessage = String(message || "").trim();
    window.__artifactActionStatusTone = tone;
    const status = document.getElementById("artifact-action-status");
    if (!status) {
        return;
    }
    if (!window.__artifactActionStatusMessage) {
        status.hidden = true;
        status.textContent = "";
        status.removeAttribute("data-tone");
        return;
    }
    status.hidden = false;
    status.textContent = window.__artifactActionStatusMessage;
    status.setAttribute("data-tone", tone);
}

async function readErrorMessage(response, fallbackMessage) {
    try {
        const payload = await response.json();
        if (payload && typeof payload.detail === "string" && payload.detail.trim()) {
            return payload.detail.trim();
        }
    } catch {
        // Ignore non-JSON responses and fall back to the provided message.
    }
    return fallbackMessage;
}

async function loadArtifactOptions(force = false) {
    if (!repoFull) {
        return;
    }
    if (window.__artifactOptionsLoading) {
        return;
    }
    if (window.__artifactOptionsLoaded && !force) {
        return;
    }

    window.__artifactOptionsLoading = true;
    refreshArtifactsSection();
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/options`);
        if (!response.ok) {
            throw new Error(await readErrorMessage(response, `Artifact options request failed with ${response.status}`));
        }
        const payload = await response.json();
        window.__artifactTypeOptions = asArray(payload.artifact_type_options);
        window.__artifactOptionEntries = asArray(payload.files);
        window.__artifactOptionsLoaded = true;
        window.__artifactOptionsError = "";

        const optionPaths = artifactOptionEntries().map((item) => String(item.path || ""));
        if (!optionPaths.includes(window.__artifactAddPath)) {
            window.__artifactAddPath = optionPaths[0] || "";
        }
    } catch (error) {
        window.__artifactOptionsError = error instanceof Error ? error.message : "Unable to load repository files.";
    } finally {
        window.__artifactOptionsLoading = false;
        refreshArtifactsSection();
    }
}

function renderArtifactTypeOptionTags(selectedType = "") {
    return artifactTypeOptions().map((type) => `
        <option value="${escapeHtml(type)}"${type === selectedType ? " selected" : ""}>${escapeHtml(artifactTypeLabel(type))}</option>
    `).join("");
}

function renderArtifactAddControls() {
    const isBusy = window.__artifactOptionsLoading || window.__artifactMutationBusyPath === "__artifact_add__";
    const options = artifactOptionEntries();

    if (window.__artifactOptionsLoading && !window.__artifactOptionsLoaded) {
        return '<div class="artifact-add-copy">Loading repository files that can be tracked...</div>';
    }

    if (window.__artifactOptionsError && !window.__artifactOptionsLoaded) {
        return `
            <div class="artifact-add-copy">Unable to load repository files. ${escapeHtml(window.__artifactOptionsError)}</div>
            <div class="artifact-add-row">
                <button type="button" class="cue-action-button" id="artifact-options-retry">Retry</button>
            </div>
        `;
    }

    if (!options.length) {
        return `
            <div class="artifact-add-copy">All visible repository files that can be added are already tracked, or no repository file inventory is available yet.</div>
            <div class="artifact-add-row">
                <button type="button" class="cue-action-button" id="artifact-options-refresh"${isBusy ? " disabled" : ""}>Refresh file list</button>
            </div>
        `;
    }

    const optionPaths = options.map((item) => String(item.path || ""));
    const selectedPath = optionPaths.includes(window.__artifactAddPath)
        ? window.__artifactAddPath
        : (optionPaths[0] || "");
    const inferredType = inferredArtifactTypeForPath(selectedPath);

    return `
        <div class="artifact-add-copy">Add a repository file to the tracked artifact registry. Vipari will infer its type from the selected path, fetch the file from the repo, and create a baseline immediately. If the inferred type is wrong, edit it after adding.</div>
        <div class="artifact-add-row">
            <select id="artifact-add-path-select" class="artifact-add-select" aria-label="Repository file to track"${isBusy ? " disabled" : ""}>
                ${options.map((item) => {
                    const path = String(item.path || "");
                    const type = String(item.inferred_artifact_type || "generic");
                    return `<option value="${escapeHtml(path)}"${path === selectedPath ? " selected" : ""}>${escapeHtml(path)} (${escapeHtml(artifactTypeLabel(type))})</option>`;
                }).join("")}
            </select>
            <div class="artifact-add-copy">Detected type: <strong>${escapeHtml(artifactTypeLabel(inferredType))}</strong></div>
            <button type="button" class="cue-action-button" id="artifact-options-refresh"${isBusy ? " disabled" : ""}>Refresh file list</button>
            <button type="button" class="cue-action-button" id="artifact-add-submit"${isBusy ? " disabled" : ""}>Add tracked artifact</button>
        </div>
    `;
}

function renderArtifactTable(items = []) {
    const storylineCallToAction = storylinePanelCopy().cta;
    if (!items.length) {
        return '<tr><td colspan="5" class="muted">No onboarded artifacts were found for this repository yet.</td></tr>';
    }
    return items.map((item, index) => {
        const encodedPath = encodeURIComponent(item.artifact_path);
        const isEditing = window.__artifactEditPath === item.artifact_path;
        const isBusy = window.__artifactMutationBusyPath === item.artifact_path;
        const typeCell = isEditing
            ? `
                <div class="artifact-inline-type">
                    <label class="visually-hidden" for="artifact-type-select-${index}">Artifact type for ${escapeHtml(item.artifact_path)}</label>
                    <select id="artifact-type-select-${index}" class="artifact-type-select" data-artifact-type-select="${encodedPath}"${isBusy ? " disabled" : ""}>
                        ${renderArtifactTypeOptionTags(String(item.artifact_type || ""))}
                    </select>
                    <div class="muted">${escapeHtml(item.provenance_label || "Supporting repository artifact")}</div>
                </div>
            `
            : `<div>${escapeHtml(item.artifact_type)}</div><div class="muted">${escapeHtml(item.provenance_label || "Supporting repository artifact")}</div>`;
        return `
        <tr data-artifact-row-path="${encodedPath}">
            <td>${escapeHtml(item.artifact_path)}</td>
            <td>${typeCell}</td>
            <td>${Number(item.historical_profile_count || 0)}</td>
            <td>${Math.max(Number(item.latest_historical_drift_magnitude || 0), Number(item.leaderboard_drift_magnitude || 0)).toFixed(3)}</td>
            <td>
                <div class="artifact-action-group">
                    <button type="button" class="cue-action-button" data-storyline-artifact="${encodedPath}"${isBusy ? " disabled" : ""}>${escapeHtml(storylineCallToAction)}</button>
                    <button type="button" class="cue-action-button artifact-icon-button" data-artifact-edit-path="${encodedPath}" aria-label="${isEditing ? "Confirm artifact type" : "Edit artifact type"}" title="${isEditing ? "Confirm artifact type" : "Edit artifact type"}"${isBusy ? " disabled" : ""}>${isEditing ? "✓" : "✎"}</button>
                    <button type="button" class="cue-action-button artifact-icon-button" data-artifact-remove-path="${encodedPath}" aria-label="Remove tracked artifact" title="Remove tracked artifact"${isBusy ? " disabled" : ""}>X</button>
                </div>
            </td>
        </tr>
    `;
    }).join("");
}

function artifactTypeLabel(value) {
    if (!value || value === "all") {
        return "All";
    }
    return String(value).replaceAll("_", " ");
}

function renderArtifactFilterChips(items = []) {
    const types = [...new Set(items.map((item) => String(item.artifact_type || "unknown")).filter(Boolean))].sort();
    const options = ["all", ...types];
    return options.map((type) => {
        const isActive = window.__artifactTypeFilter === type;
        return `<button type="button" class="triage-filter-btn${isActive ? " active" : ""}" data-artifact-type-filter="${escapeHtml(type)}">${escapeHtml(artifactTypeLabel(type))}</button>`;
    }).join("");
}

function filteredArtifactEntries(items = []) {
    const query = String(window.__artifactQuery || "").trim().toLowerCase();
    return items.filter((item) => {
        const typeMatches = window.__artifactTypeFilter === "all" || String(item.artifact_type || "") === window.__artifactTypeFilter;
        if (!typeMatches) {
            return false;
        }
        if (!query) {
            return true;
        }
        return String(item.artifact_path || "").toLowerCase().includes(query) || String(item.artifact_type || "").toLowerCase().includes(query);
    });
}

function renderArtifactResultsSummary(totalCount, filteredCount) {
    if (!totalCount) {
        return "No artifacts tracked yet";
    }
    if (filteredCount === totalCount) {
        return `${totalCount} artifact${totalCount === 1 ? "" : "s"}`;
    }
    return `${filteredCount} of ${totalCount} artifacts`;
}

function refreshArtifactsSection() {
    const items = asArray(window.__artifactEntries);
    const filtered = filteredArtifactEntries(items);
    setSectionHtml("artifacts-tbody", renderArtifactTable(filtered));
    setSectionHtml("artifact-add-controls", renderArtifactAddControls());
    const filterHost = document.getElementById("artifact-filter-chips");
    if (filterHost) {
        filterHost.innerHTML = renderArtifactFilterChips(items);
    }
    const summary = document.getElementById("artifact-results-summary");
    if (summary) {
        summary.textContent = renderArtifactResultsSummary(items.length, filtered.length);
    }
    const body = document.getElementById("artifacts-panel-body");
    if (body) {
        body.hidden = window.__artifactsCollapsed;
    }
    const toggle = document.getElementById("artifacts-collapse-toggle");
    if (toggle) {
        toggle.textContent = window.__artifactsCollapsed ? "Expand" : "Collapse";
        toggle.setAttribute("aria-expanded", window.__artifactsCollapsed ? "false" : "true");
    }
    setArtifactActionStatus(window.__artifactActionStatusMessage, window.__artifactActionStatusTone);
    bindCueCards();
    bindArtifactMutationControls();
}

function bindArtifactControls() {
    const searchInput = document.getElementById("artifact-search-input");
    if (searchInput && searchInput.dataset.boundArtifactSearch !== "true") {
        searchInput.dataset.boundArtifactSearch = "true";
        searchInput.addEventListener("input", () => {
            window.__artifactQuery = searchInput.value || "";
            refreshArtifactsSection();
        });
    }

    const collapseToggle = document.getElementById("artifacts-collapse-toggle");
    if (collapseToggle && collapseToggle.dataset.boundArtifactToggle !== "true") {
        collapseToggle.dataset.boundArtifactToggle = "true";
        collapseToggle.addEventListener("click", () => {
            window.__artifactsCollapsed = !window.__artifactsCollapsed;
            refreshArtifactsSection();
        });
    }

    const filterHost = document.getElementById("artifact-filter-chips");
    if (filterHost && filterHost.dataset.boundArtifactFilters !== "true") {
        filterHost.dataset.boundArtifactFilters = "true";
        filterHost.addEventListener("click", (event) => {
            const target = event.target instanceof HTMLElement ? event.target.closest("[data-artifact-type-filter]") : null;
            if (!target) {
                return;
            }
            window.__artifactTypeFilter = target.getAttribute("data-artifact-type-filter") || "all";
            refreshArtifactsSection();
        });
    }

    if (!window.__artifactOptionsLoaded && !window.__artifactOptionsLoading) {
        void loadArtifactOptions();
    }
}

async function addTrackedArtifact() {
    if (!window.__artifactAddPath) {
        setArtifactActionStatus("Choose a repository file before adding it.", "error");
        return;
    }

    window.__artifactMutationBusyPath = "__artifact_add__";
    setArtifactActionStatus("");
    refreshArtifactsSection();
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ artifact_path: window.__artifactAddPath }),
        });
        if (!response.ok) {
            throw new Error(await readErrorMessage(response, `Artifact add failed with ${response.status}`));
        }
        const payload = await response.json();
        const inferredType = payload?.artifact?.artifact_type || inferredArtifactTypeForPath(window.__artifactAddPath);
        window.__artifactEntries = [
            ...asArray(window.__artifactEntries),
            {
                artifact_path: window.__artifactAddPath,
                artifact_type: inferredType,
                historical_profile_count: 0,
                latest_historical_drift_magnitude: 0,
                leaderboard_drift_magnitude: 0,
                provenance_label: "Supporting repository artifact",
            },
        ].sort((left, right) => String(left?.artifact_path || "").localeCompare(String(right?.artifact_path || "")));
        refreshArtifactsSection();
        if (payload?.dashboard) {
            void Promise.resolve().then(() => applyDashboardPayload(payload.dashboard));
        } else {
            void loadDashboard();
        }
        setArtifactActionStatus(`Added ${window.__artifactAddPath} to the tracked artifact registry.`, "success");
        void loadArtifactOptions(true);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to add the selected artifact.";
        setArtifactActionStatus(message, "error");
    } finally {
        window.__artifactMutationBusyPath = "";
        refreshArtifactsSection();
    }
}

async function removeTrackedArtifact(artifactPath) {
    const confirmed = window.confirm(`Remove ${artifactPath} from tracked artifacts? This will also remove its stored onboarding baseline history for the tracked set.`);
    if (!confirmed) {
        return;
    }

    window.__artifactMutationBusyPath = artifactPath;
    setArtifactActionStatus("");
    refreshArtifactsSection();
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${encodeURIComponent(artifactPath)}`, {
            method: "DELETE",
        });
        if (!response.ok) {
            throw new Error(await readErrorMessage(response, `Artifact removal failed with ${response.status}`));
        }
        const payload = await response.json();
        window.__artifactEditPath = "";
        removeArtifactEntryLocal(artifactPath);
        refreshArtifactsSection();
        if (payload?.dashboard) {
            void Promise.resolve().then(() => applyDashboardPayload(payload.dashboard));
        } else {
            void loadDashboard();
        }
        setArtifactActionStatus(`Removed ${artifactPath} from tracked artifacts.`, "success");
        void loadArtifactOptions(true);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to remove the tracked artifact.";
        setArtifactActionStatus(message, "error");
    } finally {
        window.__artifactMutationBusyPath = "";
        refreshArtifactsSection();
    }
}

async function saveTrackedArtifactType(artifactPath, artifactType) {
    window.__artifactMutationBusyPath = artifactPath;
    setArtifactActionStatus("");
    refreshArtifactsSection();
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/artifacts/${encodeURIComponent(artifactPath)}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ artifact_type: artifactType }),
        });
        if (!response.ok) {
            throw new Error(await readErrorMessage(response, `Artifact type update failed with ${response.status}`));
        }
        const payload = await response.json();
        window.__artifactEditPath = "";
        replaceArtifactEntryLocal(artifactPath, { artifact_type: payload?.artifact?.artifact_type || artifactType });
        refreshArtifactsSection();
        if (payload?.dashboard) {
            void Promise.resolve().then(() => applyDashboardPayload(payload.dashboard));
        } else {
            void loadDashboard();
        }
        setArtifactActionStatus(`Updated ${artifactPath} to ${artifactTypeLabel(artifactType)}.`, "success");
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unable to update the tracked artifact type.";
        setArtifactActionStatus(message, "error");
    } finally {
        window.__artifactMutationBusyPath = "";
        refreshArtifactsSection();
    }
}

function bindArtifactMutationControls() {
    const addPathSelect = document.getElementById("artifact-add-path-select");
    if (addPathSelect instanceof HTMLSelectElement && addPathSelect.dataset.boundArtifactAddPath !== "true") {
        addPathSelect.dataset.boundArtifactAddPath = "true";
        addPathSelect.addEventListener("change", () => {
            window.__artifactAddPath = addPathSelect.value || "";
        });
    }

    const refreshButton = document.getElementById("artifact-options-refresh") || document.getElementById("artifact-options-retry");
    if (refreshButton && refreshButton.dataset.boundArtifactOptionsRefresh !== "true") {
        refreshButton.dataset.boundArtifactOptionsRefresh = "true";
        refreshButton.addEventListener("click", () => {
            void loadArtifactOptions(true);
        });
    }

    const addButton = document.getElementById("artifact-add-submit");
    if (addButton && addButton.dataset.boundArtifactAdd !== "true") {
        addButton.dataset.boundArtifactAdd = "true";
        addButton.addEventListener("click", () => {
            void addTrackedArtifact();
        });
    }

    const tbody = document.getElementById("artifacts-tbody");
    if (tbody && tbody.dataset.boundArtifactMutations !== "true") {
        tbody.dataset.boundArtifactMutations = "true";
        tbody.addEventListener("click", (event) => {
            const target = event.target instanceof HTMLElement ? event.target : null;
            if (!target) {
                return;
            }

            const editButton = target.closest("[data-artifact-edit-path]");
            if (editButton instanceof HTMLElement) {
                const encodedPath = editButton.getAttribute("data-artifact-edit-path") || "";
                const artifactPath = decodeURIComponent(encodedPath);
                if (!artifactPath) {
                    return;
                }
                if (window.__artifactEditPath === artifactPath) {
                    const select = document.querySelector(`[data-artifact-type-select="${encodedPath}"]`);
                    const selectedType = select instanceof HTMLSelectElement ? select.value : "";
                    if (selectedType) {
                        void saveTrackedArtifactType(artifactPath, selectedType);
                    }
                    return;
                }
                window.__artifactEditPath = artifactPath;
                refreshArtifactsSection();
                return;
            }

            const removeButton = target.closest("[data-artifact-remove-path]");
            if (removeButton instanceof HTMLElement) {
                const encodedPath = removeButton.getAttribute("data-artifact-remove-path") || "";
                const artifactPath = decodeURIComponent(encodedPath);
                if (artifactPath) {
                    void removeTrackedArtifact(artifactPath);
                }
            }
        });
    }
}

function bindOpenSourceChangeLinks(scope = document) {
    scope.querySelectorAll("[data-open-source-change]").forEach((link) => {
        if (link.dataset.boundOpenSourceChange === "true") {
            return;
        }
        link.dataset.boundOpenSourceChange = "true";
        link.addEventListener("click", (event) => {
            const url = link.getAttribute("data-open-source-change");
            if (!url) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            window.open(url, "_blank", "noopener,noreferrer");
        });
    });
}

function journeyToneForRisk(riskLevel) {
    const normalized = String(riskLevel || "").toLowerCase();
    if (normalized === "high") {
        return "journey-tone-medium";
    }
    if (normalized === "medium") {
        return "journey-tone-gap";
    }
    return "journey-tone-low";
}

function severityClassForRisk(riskLevel) {
    const normalized = String(riskLevel || "").toLowerCase();
    if (normalized === "high") {
        return "severity-high";
    }
    if (normalized === "medium") {
        return "severity-medium";
    }
    return "severity-low";
}

function snapshotTypeLabel(snapshotType) {
    const normalized = String(snapshotType || "checkpoint").replaceAll("_", " ");
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatSigned(value, digits = 3) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
        return "0.000";
    }
    return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function renderJourneySummary(snapshots = [], selectedBaselineSourceSnapshotId = null) {
    if (!snapshots.length) {
        return '<div class="muted">No repository checkpoints have been materialized yet.</div>';
    }
    const current = snapshots.find((item) => item.snapshot_type === "current") || snapshots.find((item) => item.snapshot_type === "branch_head") || snapshots[snapshots.length - 1];
    const selectedBaseline = selectedBaselineSourceSnapshotId
        ? snapshots.find((item) => Number(item.id) === Number(selectedBaselineSourceSnapshotId)) || null
        : null;
    const baseline = selectedBaseline || snapshots.find((item) => item.snapshot_type === "baseline_approved") || null;
    const mergedCount = snapshots.filter((item) => item.snapshot_type === "merge").length;
    const historicalCount = snapshots.filter((item) => item.snapshot_type === "historical_commit").length;
    const branchHeadCount = snapshots.filter((item) => item.snapshot_type === "branch_head").length;
    const riskLevel = current?.risk_summary?.risk_level || "low";
    const baselineValue = baseline ? snapshotTypeLabel(selectedBaseline ? baseline.snapshot_type : "baseline_approved") : "No";
    const baselineValueClass = baselineValue.length > 12 ? "journey-node-value journey-node-text" : "journey-node-value";
    return `
        <div class="journey-strip">
            <div class="journey-node journey-tone-primary">
                <span class="journey-node-value">${snapshots.length}</span>
                <span class="journey-node-label">Snapshots</span>
                <span class="journey-node-caption">${mergedCount} merged, ${historicalCount} historical, ${branchHeadCount} live</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node journey-tone-gap">
                <span class="${baselineValueClass}">${escapeHtml(baselineValue)}</span>
                <span class="journey-node-label">Reference baseline</span>
                <span class="journey-node-caption">${escapeHtml(baseline?.source_ref || "No reference baseline selected")}</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node ${journeyToneForRisk(riskLevel)}">
                <span class="journey-node-value">${escapeHtml(String(riskLevel).toUpperCase())}</span>
                <span class="journey-node-label">Current risk</span>
                <span class="journey-node-caption">score ${asNumber(current?.risk_summary?.score).toFixed(3)}</span>
                <span class="journey-node-link" aria-hidden="true"></span>
            </div>
            <div class="journey-node journey-tone-primary">
                <span class="journey-node-value">${asNumber(current?.distance_from_baseline).toFixed(3)}</span>
                <span class="journey-node-label">Drift from reference</span>
                <span class="journey-node-caption">${asNumber(current?.change_breakdown?.critical_surfaces_changed)} critical surfaces changed</span>
            </div>
        </div>
    `;
}

function renderJourneyTimelineCard(snapshot, selectedBaselineSourceSnapshotId = null) {
    const baselineVerified = snapshot?.input_summary?.baseline_verified !== false;
    const isSelectedBaseline = selectedBaselineSourceSnapshotId !== null && Number(snapshot?.id) === Number(selectedBaselineSourceSnapshotId);
    const displayTimestamp = journeyCardTimestamp(snapshot, isSelectedBaseline);
    const source = snapshot.source_url
        ? `<a class="link" href="${snapshot.source_url}" data-open-source-change="${snapshot.source_url}" target="_blank" rel="noreferrer noopener">${escapeHtml(snapshot.source_ref || "Open checkpoint")}</a>`
        : escapeHtml(snapshot.source_ref || "Stored checkpoint");
    const labels = asArray(snapshot.change_labels).slice(0, 3);
    const baselineMeta = isSelectedBaseline
        ? `<div class="detail-note">Current approved baseline checkpoint${snapshot?.input_summary?.approved_by ? ` · selected by @${escapeHtml(snapshot.input_summary.approved_by)} · ${escapeHtml(formatDateLabel(snapshot.input_summary.approved_at))}` : ""}</div>`
        : snapshot.snapshot_type === "baseline_approved" && snapshot?.input_summary?.approved_by
            ? `<div class="detail-note">Approved by @${escapeHtml(snapshot.input_summary.approved_by)} · ${escapeHtml(formatDateLabel(snapshot.input_summary.approved_at))}</div>`
            : "";
    const rebaselineButton = snapshot.commit_sha
        ? `<button type="button" class="journey-action-button" data-rebaseline-snapshot="${snapshot.id}">Set as reference baseline</button>`
        : "";
    return `
        <div class="artifact-card journey-card ${baselineVerified ? "" : "journey-card-muted"} ${isSelectedBaseline ? "journey-card-selected-baseline" : ""}" ${baselineVerified ? "" : 'title="Baseline not yet approved — drift scores are estimates."'}>
            <div class="artifact-card-head">
                <div>
                    <strong>${escapeHtml(isSelectedBaseline ? "Approved baseline" : snapshotTypeLabel(snapshot.snapshot_type))}</strong>
                    <div class="artifact-card-type">${escapeHtml(formatDateLabel(displayTimestamp))} · ${escapeHtml(snapshot.commit_sha || snapshot.snapshot_key)}</div>
                </div>
                <span class="severity-badge ${severityClassForRisk(snapshot.risk_summary?.risk_level)}">${escapeHtml(snapshot.risk_summary?.risk_level || "low")}</span>
            </div>
            <div class="journey-metrics-row">
                <span>drift ${asNumber(snapshot.distance_from_baseline).toFixed(3)}</span>
                <span>critical ${asNumber(snapshot.change_breakdown?.critical_surfaces_changed)}</span>
                <span>artifacts ${asNumber(snapshot.artifact_coverage?.artifact_count)}</span>
            </div>
            ${labels.length ? `<div class="tag-row">${labels.map((label) => `<span class="tag tag-muted">${escapeHtml(label)}</span>`).join("")}</div>` : ""}
            <div class="artifact-card-reason">${escapeHtml(snapshot.change_summary?.changed_artifact_count ? `${snapshot.change_summary.changed_artifact_count} changed, ${snapshot.change_summary.added_artifact_count} added, ${snapshot.change_summary.removed_artifact_count} removed.` : "No material artifact changes recorded for this checkpoint.")}</div>
            ${baselineMeta}
            <div class="storyline-episode-meta muted">
                <span>${escapeHtml(snapshot.default_branch || "")}</span>
                <span>${source}</span>
            </div>
            ${rebaselineButton}
        </div>
    `;
}

function openRebaselineModal(snapshot) {
    const modal = document.getElementById("rebaseline-modal");
    const summary = document.getElementById("rebaseline-modal-summary");
    const textarea = document.getElementById("rebaseline-rationale");
    if (!modal || !summary || !textarea) {
        return;
    }
    window.__pendingRebaselineSnapshot = snapshot;
    summary.innerHTML = `
        <div><strong>${escapeHtml(snapshotTypeLabel(snapshot.snapshot_type))}</strong> · ${escapeHtml(snapshot.commit_sha || snapshot.snapshot_key)}</div>
        <div class="detail-note">${escapeHtml(`${asNumber(snapshot.change_breakdown?.critical_surfaces_changed)} critical surfaces changed · this moves the snapshot DriftGuard compares future changes against.`)}</div>
        <div class="detail-note">Artifact Sign-off stays separate. If you change the reference baseline, reviewers may need to re-approve the artifacts attached to that newer snapshot.</div>
    `;
    textarea.value = "";
    setRebaselineBusy(false);
    modal.hidden = false;
}

function closeRebaselineModal(force = false) {
    if (window.__rebaselineBusy && !force) {
        return;
    }
    const modal = document.getElementById("rebaseline-modal");
    if (modal) {
        modal.hidden = true;
    }
    window.__pendingRebaselineSnapshot = null;
}

function setRebaselineBusy(isBusy) {
    window.__rebaselineBusy = Boolean(isBusy);
    const modal = document.getElementById("rebaseline-modal");
    const card = modal?.querySelector(".modal-card");
    const progress = document.getElementById("rebaseline-progress");
    const progressText = document.getElementById("rebaseline-progress-text");
    const textarea = document.getElementById("rebaseline-rationale");
    const confirmButton = document.getElementById("rebaseline-confirm-btn");

    if (card) {
        card.classList.toggle("modal-card-busy", Boolean(isBusy));
    }
    if (progress) {
        progress.hidden = !isBusy;
    }
    if (progressText) {
        progressText.textContent = isBusy
            ? "Setting a new reference baseline. This can take a few seconds for large repositories..."
            : "Preparing the reference baseline update...";
    }
    if (textarea instanceof HTMLTextAreaElement) {
        textarea.disabled = Boolean(isBusy);
    }
    if (confirmButton instanceof HTMLButtonElement) {
        confirmButton.disabled = Boolean(isBusy);
        confirmButton.textContent = isBusy ? "Working..." : "Confirm";
    }
    document.querySelectorAll("[data-close-rebaseline]").forEach((button) => {
        if (button instanceof HTMLButtonElement) {
            button.disabled = Boolean(isBusy);
        }
    });
}

function renderDecisionSection(insights, payload) {
    const topInsight = asArray(insights)[0] || null;
    if (!topInsight) {
        const badgeEl = document.getElementById("repo-decision-posture-badge");
        if (badgeEl) {
            badgeEl.textContent = "Healthy";
            badgeEl.className = "severity-badge severity-low";
        }
        setText("repo-decision-subtitle", "No audit findings require immediate action.");
        setSectionHtml("repo-decision-finding", '<div class="muted">No primary findings at this time.</div>');
        setSectionHtml("repo-decision-action", '<div class="muted">—</div>');
        return;
    }

    const severity = topInsight.priority === "review_now"
        ? { label: "Review Now", className: "severity-high" }
        : topInsight.priority === "watch"
            ? { label: "Watch", className: "severity-medium" }
            : { label: "Artifact Sign-off", className: "severity-low" };

    const badgeEl = document.getElementById("repo-decision-posture-badge");
    if (badgeEl) {
        badgeEl.textContent = severity.label;
        badgeEl.className = `severity-badge ${severity.className}`;
    }

    const reviewCount = asArray(insights).filter((i) => i.priority === "review_now").length;
    const watchCount = asArray(insights).filter((i) => i.priority === "watch").length;
    setText("repo-decision-subtitle", `${reviewCount} review now · ${watchCount} watch · ${asArray(payload?.lower_confidence_insights).length} lower-confidence`);

    const artifactName = String(topInsight.artifact_path || "").split("/").pop() || topInsight.artifact_path || "";
    const deltas = asArray(topInsight.attribute_profile)
        .filter((d) => d.state && d.state !== "no_change" && d.state !== "unknown")
        .slice(0, 2);

    setSectionHtml("repo-decision-finding", `
        <div class="stack compact-stack">
            <strong>${escapeHtml(topInsight.title || artifactName)}</strong>
            <div class="muted">${escapeHtml(topInsight.rationale || "")}</div>
            ${deltas.length ? `<div class="tag-row">${deltas.map((d) => `<span class="drift-chip chip-governance">${escapeHtml(d.label || d.attribute_key)}: ${escapeHtml(`${d.baseline_value || "?"} → ${d.current_value || "?"}`)}</span>`).join("")}</div>` : ""}
        </div>
    `);

    setSectionHtml("repo-decision-action", `
        <div class="stack compact-stack">
            <div>${escapeHtml(topInsight.recommended_action || "Inspect before merge.")}</div>
            ${topInsight.review_url ? `<a href="${escapeHtml(topInsight.review_url)}" class="escalation-action-btn">Open review</a>` : ""}
        </div>
    `);
}

function prReviewRouteSeverity(route) {
    const riskLevel = String(route?.risk_level || "unknown").toLowerCase();
    if (riskLevel === "high") {
        return { label: "High risk", className: "severity-high" };
    }
    if (riskLevel === "medium") {
        return { label: "Medium risk", className: "severity-medium" };
    }
    if (riskLevel === "low") {
        return { label: "Low risk", className: "severity-low" };
    }
    return { label: "Tracked", className: "severity-medium" };
}

function renderPrReviewRoutesSection(routePayload) {
    const routes = asArray(routePayload?.routes);
    const selectedRoute = routePayload?.selected_route || routes[0] || null;

    if (!selectedRoute) {
        setSectionHtml("repo-pr-review-selected", '<div class="muted">Vipari has not recorded a PR review episode for this repository yet.</div>');
        setSectionHtml("repo-pr-review-list", '<div class="muted">Recent PR review routes will appear here after the next PR review is posted.</div>');
        return;
    }

    const selectedSeverity = prReviewRouteSeverity(selectedRoute);
    const selectedAuditUrl = repoTabUrl("audit", {
        prNumber: selectedRoute.pr_number,
        headSha: selectedRoute.head_sha,
        hash: "repo-pr-review-routes-section",
    });
    const selectedFeedback = selectedRoute.feedback || {};
    const topFindings = asArray(selectedRoute.top_findings);
    const recentFeedback = asArray(selectedRoute.recent_feedback);
    const selectedTags = [
        selectedRoute.short_head_sha ? `head ${selectedRoute.short_head_sha}` : "",
        selectedRoute.output_mode ? String(selectedRoute.output_mode).replaceAll("_", " ") : "",
        selectedRoute.semantic_review_completed ? "semantic review complete" : "fallback review",
        selectedRoute.finding_count ? `${selectedRoute.finding_count} stored findings` : "",
        selectedFeedback.helpful_count ? `${selectedFeedback.helpful_count} helpful` : "",
        selectedFeedback.noisy_count ? `${selectedFeedback.noisy_count} noisy` : "",
        selectedFeedback.strongly_disagree_count ? `${selectedFeedback.strongly_disagree_count} strongly disagree` : "",
        selectedFeedback.reaction_count ? `${selectedFeedback.reaction_count} GitHub reactions` : "",
        selectedFeedback.outcome_count ? `${selectedFeedback.outcome_count} recorded outcomes` : "",
    ].filter(Boolean);

    setSectionHtml("repo-pr-review-selected", `
        <div class="stack compact-stack">
            <div class="repo-decision-title-row">
                <strong>PR #${escapeHtml(selectedRoute.pr_number)}</strong>
                <span class="severity-badge ${selectedSeverity.className}">${escapeHtml(selectedSeverity.label)}</span>
            </div>
            <div class="muted">${escapeHtml(`Head ${selectedRoute.short_head_sha || selectedRoute.head_sha || "unknown"} · reviewed ${formatDateLabel(selectedRoute.review_posted_at || selectedRoute.updated_at)}`)}</div>
            <div>${escapeHtml(selectedRoute.summary || "Vipari recorded this PR review episode.")}</div>
            ${selectedRoute.review_excerpt ? `<div class="artifact-card-reason">${escapeHtml(selectedRoute.review_excerpt)}</div>` : ""}
            ${selectedTags.length ? `<div class="tag-row">${selectedTags.map((tag) => `<span class="drift-chip chip-governance">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
            ${selectedRoute.review_body ? `<details class="artifact-card"><summary><strong>Full review note</strong></summary><pre class="artifact-card-reason" style="white-space: pre-wrap; margin-top: 0.75rem;">${escapeHtml(selectedRoute.review_body)}</pre></details>` : ""}
            ${topFindings.length ? `<div class="stack compact-stack"><div class="repo-decision-label">Top review findings</div>${topFindings.map((finding) => `<div class="artifact-card"><strong>${escapeHtml(finding.title || "Recorded finding")}</strong><div class="artifact-card-reason">${escapeHtml(finding.rationale || "Vipari stored a review finding for this episode.")}</div></div>`).join("")}</div>` : ""}
            ${recentFeedback.length ? `<div class="stack compact-stack"><div class="repo-decision-label">Feedback loop</div>${recentFeedback.map((event) => `<div class="artifact-card"><strong>${escapeHtml(event.title || "Recorded feedback")}</strong><div class="artifact-card-reason">${escapeHtml(event.summary || "Vipari stored feedback for this review episode.")}</div></div>`).join("")}</div>` : ""}
            <div class="export-actions repo-actions-row audit-step-actions">
                <a href="${escapeHtml(selectedAuditUrl)}" class="cue-action-button">Open selected route</a>
                <a href="${escapeHtml(selectedRoute.pull_request_url || "#")}" class="cue-action-button">Open GitHub PR</a>
            </div>
        </div>
    `);

    setSectionHtml("repo-pr-review-list", routes.map((route) => {
        const severity = prReviewRouteSeverity(route);
        const auditUrl = repoTabUrl("audit", {
            prNumber: route.pr_number,
            headSha: route.head_sha,
            hash: "repo-pr-review-routes-section",
        });
        const feedback = route.feedback || {};
        const detailBits = [
            route.short_head_sha ? `head ${route.short_head_sha}` : "",
            route.output_mode ? String(route.output_mode).replaceAll("_", " ") : "",
            feedback.helpful_count ? `${feedback.helpful_count} helpful` : "",
            feedback.reaction_count ? `${feedback.reaction_count} reactions` : "",
        ].filter(Boolean);
        return `
            <article class="artifact-card${route.selected ? " artifact-card-selected" : ""}">
                <div class="repo-decision-title-row">
                    <strong>PR #${escapeHtml(route.pr_number)}</strong>
                    <span class="severity-badge ${severity.className}">${escapeHtml(severity.label)}</span>
                </div>
                <div class="artifact-card-reason">${escapeHtml(route.summary || "Vipari recorded this PR review episode.")}</div>
                ${detailBits.length ? `<div class="tag-row">${detailBits.map((bit) => `<span class="drift-chip chip-baseline">${escapeHtml(bit)}</span>`).join("")}</div>` : ""}
                <div class="export-actions repo-actions-row audit-step-actions">
                    <a href="${escapeHtml(auditUrl)}" class="cue-action-button">Open route</a>
                    <a href="${escapeHtml(route.pull_request_url || "#")}" class="cue-action-button">GitHub PR</a>
                </div>
            </article>
        `;
    }).join("") || '<div class="muted">No PR review routes have been recorded for this repository yet.</div>');
}

function renderRepoActionsSection(insights) {
    const topInsight = asArray(insights)[0] || null;
    const reviewUrl = topInsight?.review_url || "";
    const reviewTarget = topInsight?.review_target || topInsight?.artifact_path || repoFull;
    const reviewTitle = topInsight?.title || "Open the highest-priority change first";
    const repoUrl = repoFull ? `https://github.com/${repoFull}` : "";
    const baselineReviewUrl = repoFull
        ? repoTabUrl("baseline", { hash: "baseline-review-panel" })
        : "#baseline-review-panel";
    const driftQueueUrl = repoFull
        ? repoTabUrl("drift", { artifactPath: topInsight?.artifact_path || "", hash: "repo-triage-section" })
        : "#repo-triage-section";
    const reviewArtifactUrl = repoFull
        ? repoTabUrl("audit", { artifactPath: topInsight?.artifact_path || "", hash: "repo-audit-brief-section" })
        : "#repo-audit-brief-section";
    const relatedAuditsUrl = repoFull
        ? repoTabUrl("audit", { hash: "repo-audit-brief-section" })
        : "#repo-audit-brief-section";
    const exportUrl = repoFull
        ? repoTabUrl("reports", { hash: "repo-export-section" })
        : "#repo-export-section";

    if (!topInsight) {
        setSectionHtml("repo-actions-review", `
            <div class="stack compact-stack repo-audit-workflow">
                <div class="detail-note repo-audit-workflow-intro">No urgent audit workflow is active for this repository right now.</div>
                <div class="artifact-card audit-workflow-step audit-workflow-step-clear">
                    <div class="audit-workflow-step-head">
                        <span class="audit-step-number">0</span>
                        <div class="stack compact-stack">
                            <span class="audit-step-eyebrow">Audit queue</span>
                            <strong>Review queue is clear</strong>
                        </div>
                    </div>
                    <div class="artifact-card-reason">Baseline posture already covers the current repository state. Use the actions below if you want a final governance confirmation or a shareable handoff artifact.</div>
                </div>
                <div class="export-actions repo-actions-row audit-step-actions">
                    <a href="${escapeHtml(baselineReviewUrl)}" class="cue-action-button">Open baseline review</a>
                    <a href="${escapeHtml(driftQueueUrl)}" class="cue-action-button">Open drift queue</a>
                    <a href="${escapeHtml(relatedAuditsUrl)}" class="cue-action-button">Recheck audit brief</a>
                    <a href="${escapeHtml(exportUrl)}" class="cue-action-button">Create export</a>
                </div>
            </div>
        `);
        return;
    }

    setSectionHtml("repo-actions-review", `
        <div class="stack compact-stack repo-audit-workflow">
            <div class="detail-note repo-audit-workflow-intro">Inspect the flagged change first, then resolve artifact sign-off and export actions from this same audit workspace.</div>
            <div class="artifact-card audit-workflow-step">
                <div class="audit-workflow-step-head">
                    <span class="audit-step-number">1</span>
                    <div class="stack compact-stack">
                        <span class="audit-step-eyebrow">Immediate review</span>
                        <strong>Review the flagged change</strong>
                    </div>
                </div>
                <div class="artifact-card-reason">${escapeHtml(reviewTitle)} · ${escapeHtml(reviewTarget)}</div>
                <div class="detail-note">${escapeHtml(topInsight?.recommended_action || "Inspect the current review target and confirm the changed control surface is acceptable.")}</div>
                <div class="export-actions repo-actions-row audit-step-actions">
                    <a href="${escapeHtml(reviewArtifactUrl)}" class="export-submit-button">Open flagged change</a>
                    ${reviewUrl && reviewUrl !== reviewArtifactUrl ? `<a href="${escapeHtml(reviewUrl)}" class="cue-action-button">Open source review</a>` : ""}
                </div>
            </div>
            <div class="artifact-card audit-workflow-step">
                <div class="audit-workflow-step-head">
                    <span class="audit-step-number">2</span>
                    <div class="stack compact-stack">
                        <span class="audit-step-eyebrow">Context check</span>
                        <strong>Compare repository context</strong>
                    </div>
                </div>
                <div class="artifact-card-reason">Use the audit brief and repository context before accepting or re-baselining the change.</div>
                <div class="export-actions repo-actions-row audit-step-actions">
                    <a href="${escapeHtml(driftQueueUrl)}" class="cue-action-button">Open drift queue</a>
                    <a href="${escapeHtml(relatedAuditsUrl)}" class="cue-action-button">Open audit brief</a>
                    ${repoUrl ? `<a href="${escapeHtml(repoUrl)}" class="cue-action-button">Inspect in GitHub</a>` : ""}
                </div>
            </div>
            <div class="artifact-card audit-workflow-step">
                <div class="audit-workflow-step-head">
                    <span class="audit-step-number">3</span>
                    <div class="stack compact-stack">
                        <span class="audit-step-eyebrow">Handoff</span>
                        <strong>Prepare the handoff</strong>
                    </div>
                </div>
                <div class="artifact-card-reason">Once the review is settled, capture the current evidence package for governance or customer handoff.</div>
                <div class="export-actions repo-actions-row audit-step-actions">
                    <a href="${escapeHtml(exportUrl)}" class="cue-action-button">Create export</a>
                </div>
            </div>
            <div class="export-actions repo-actions-row audit-workflow-footer">
                <a href="${escapeHtml(reviewArtifactUrl)}" class="cue-action-button">Jump back to flagged artifact</a>
            </div>
        </div>
    `);
}

function renderRepoAuditFallback(message) {
    const fallback = `<div class="muted">Unable to load repository dashboard. ${escapeHtml(message)}</div>`;
    const auditBadge = document.getElementById("repo-audit-brief-posture");
    if (auditBadge) {
        auditBadge.textContent = "Unavailable";
        auditBadge.className = "severity-badge severity-medium";
    }
    setText("repo-audit-brief-summary", "Audit brief unavailable");
    setSectionHtml("repo-audit-brief-why", fallback);
    setSectionHtml("repo-audit-brief-actions", '<div class="muted">Sign in or restore dashboard access to review this repository.</div>');
    setSectionHtml("repo-audit-brief-findings", fallback);

    const decisionBadge = document.getElementById("repo-decision-posture-badge");
    if (decisionBadge) {
        decisionBadge.textContent = "Unavailable";
        decisionBadge.className = "severity-badge severity-medium";
    }
    setText("repo-decision-subtitle", message);
    setSectionHtml("repo-decision-finding", fallback);
    setSectionHtml("repo-decision-action", '<div class="muted">Review actions become available once the repository dashboard can be loaded.</div>');
    setSectionHtml("repo-decision-proposals", '<div class="detail-note">Pending proposals are unavailable until repository dashboard access is restored.</div>');
}

async function loadPendingProposals() {
    if (!repoFull) {
        return;
    }
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/proposals/pending`);
        if (!response.ok) {
            setSectionHtml("repo-decision-proposals", '<div class="detail-note">Unable to load pending proposals for this repository right now.</div>');
            return;
        }
        const payload = await response.json();
        const proposals = asArray(payload?.proposals || []);
        const pendingCount = Number(payload?.pending_count || proposals.length);

        if (!pendingCount) {
            setSectionHtml("repo-decision-proposals", '<div class="detail-note">No baseline or disposition proposals are waiting on this repository right now.</div>');
            return;
        }

        const items = proposals.slice(0, 5).map((p) => {
            const agentLabel = p.is_agent_proposal ? '<span class="drift-chip chip-model">Agent</span>' : '<span class="drift-chip chip-governance">Human</span>';
            return `
                <div class="repo-proposal-row">
                    ${agentLabel}
                    <span class="repo-proposal-artifact">${escapeHtml(p.artifact_path || String(p.artifact_id || ""))}</span>
                    <span class="repo-proposal-rationale">${escapeHtml(String(p.rationale || "").slice(0, 80))}</span>
                </div>
            `;
        }).join("");

        setSectionHtml("repo-decision-proposals", `
            <div>
                <strong>${pendingCount}</strong> pending proposal${pendingCount !== 1 ? "s" : ""}
                <div class="stack compact-stack" style="margin-top: 0.5rem;">${items}</div>
            </div>
        `);
    } catch {
        setSectionHtml("repo-decision-proposals", '<div class="detail-note">Unable to load pending proposals for this repository right now.</div>');
    }
}

function schedulePendingProposalsLoad() {
    const loadToken = ++window.__pendingProposalsLoadToken;
    const run = () => {
        if (loadToken !== window.__pendingProposalsLoadToken) {
            return;
        }
        void loadPendingProposals();
    };
    window.setTimeout(run, 1200);
}

function applyDashboardPayload(payload) {
    const onboarding = payload.onboarding || null;
    const insights = asArray(payload.insights);
    const lowerConfidenceInsights = asArray(payload.lower_confidence_insights);
    const controlSurfaces = asArray(payload.control_surface_groups);
    const baselineReview = payload.baseline_review || null;
    const historyCues = asArray(payload.history_cues);
    const artifacts = asArray(payload.artifacts);
    const historyTimelines = asArray(payload.history_timelines);
    const journeySnapshots = asArray(payload.journey_snapshots);
    const selectedBaselineSourceSnapshotId = payload.selected_baseline_source_snapshot_id || null;
    const featuredStoryline = payload.featured_storyline || null;
    const preferredArtifactPath = requestedArtifactPath();
    const preferredPrNumber = deepLinkPullRequestNumber();
    const preferredHeadSha = deepLinkHeadSha();
    const governanceAttention = renderGovernanceAttentionNote(payload.governance_posture || null);
    window.__designProfiles = asArray(payload.design_profiles);
    window.__journeySnapshots = journeySnapshots;
    const comparison = payload.journey_comparison || null;
    renderAuditBrief(payload.audit_brief || null);
    renderPrReviewRoutesSection(payload.pr_review_routes || null);

    setText("repo-stat-artifacts", String(onboarding ? onboarding.discovered_artifact_count : artifacts.length));
    setText("repo-stat-review", String(insights.length));
    setText("repo-stat-baselines", String(asNumber(payload.baseline_version_count)));
    setText("repo-stat-history", String(historyTimelines.reduce((sum, item) => sum + Number(item.point_count || 0), 0)));
    setText("repo-governance-attention-headline", governanceAttention.headline);
    setHtml("repo-governance-attention-copy", escapeHtml(governanceAttention.body));
    setSectionHtml("repo-governance-attention-details", governanceAttention.details);

    setSectionHtml("triage-list", insights.length ? insights.map((item, index) => renderRepoTriageRow(item, index)).join("") : '<div class="muted">No primary repo insights are available yet.</div>');
    bindRepoRows(insights);
    bindRepoFilters(insights, preferredArtifactPath, preferredPrNumber, preferredHeadSha);
    autoSelectRepoRow(insights, preferredArtifactPath, preferredPrNumber, preferredHeadSha);

    if (featuredStoryline?.artifact_path) {
        window.__storylineCache.set(featuredStoryline.artifact_path, featuredStoryline);
        setSectionHtml("featured-storyline", renderStoryline(featuredStoryline));
    } else {
        setSectionHtml("featured-storyline", `<div class="muted">${escapeHtml(storylinePanelCopy().select)}</div>`);
    }
    setSectionHtml("control-surfaces", renderControlSurfaces(controlSurfaces));
    setSectionHtml("repo-ai-act-assessment", `${renderAiActAssessment(onboarding, artifacts, baselineReview)}${renderPreAuditRelevancePanel(payload.pre_audit_relevance)}`);
    setSectionHtml("history-cues", renderCueCards(historyCues));
    setSectionHtml("baseline-review-panel", renderBaselineReviewPanel(baselineReview));
    setSectionHtml("repo-journey-summary", renderJourneySummary(journeySnapshots, selectedBaselineSourceSnapshotId));
    setSectionHtml("repo-journey-timeline", renderJourneyTimeline(journeySnapshots, selectedBaselineSourceSnapshotId));
    setSectionHtml("repo-journey-compare", renderJourneyCompare(comparison));
    drawRepoRadar(payload);
    const repoRadarCaption = comparison
        ? `${comparison.left?.snapshot_key || "baseline"} -> ${comparison.right?.snapshot_key || "current"} with drift delta ${formatSigned(comparison.drift_summary?.drift_delta)}.`
        : "Version-control posture appears once DriftGuard has both an approved baseline snapshot and a current repository snapshot to compare.";
    setText("repo-radar-caption", repoRadarCaption);
    setSectionHtml("lower-confidence-insights", lowerConfidenceInsights.length
        ? `<div class="stack compact-stack">${lowerConfidenceInsights.slice(0, 4).map((item) => `<div class="artifact-card"><strong>${escapeHtml(item.artifact_path)}</strong><div class="artifact-card-reason">${escapeHtml(item.title || item.rationale || item.flag_summary || "Lower-confidence lead")}</div></div>`).join("")}</div>`
        : '<div class="muted">No lower-confidence findings are competing for attention right now.</div>');
    window.__artifactEntries = artifacts;
    if (window.__artifactEditPath && !artifacts.some((item) => item.artifact_path === window.__artifactEditPath)) {
        window.__artifactEditPath = "";
    }
    const types = artifactTypeOptions();
    refreshArtifactsSection();
    bindArtifactControls();
    bindBaselineReviewActions();
    bindRebaselineButtons(journeySnapshots);
    bindOpenSourceChangeLinks(document);
    void loadArtifactOptions();

    renderDecisionSection(insights, payload);
    renderRepoActionsSection(insights);
    schedulePendingProposalsLoad();
}

async function submitRebaseline() {
    const snapshot = window.__pendingRebaselineSnapshot;
    const textarea = document.getElementById("rebaseline-rationale");
    if (!snapshot || !(textarea instanceof HTMLTextAreaElement)) {
        return;
    }
    const rationale = textarea.value.trim();
    setRebaselineBusy(true);
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/baseline/rebaseline`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ snapshot_id: snapshot.id, rationale: rationale || null }),
        });
        if (!response.ok) {
            throw new Error(`Re-baseline request failed with ${response.status}`);
        }
        const payload = await response.json();
        closeRebaselineModal(true);
        if (payload?.dashboard) {
            applyDashboardPayload(payload.dashboard);
            return;
        }
        await loadDashboard();
    } finally {
        setRebaselineBusy(false);
    }
}

function bindRebaselineButtons(snapshots = []) {
    const snapshotById = new Map(asArray(snapshots).map((snapshot) => [String(snapshot.id), snapshot]));
    document.querySelectorAll("[data-rebaseline-snapshot]").forEach((button) => {
        if (button.dataset.boundRebaseline === "true") {
            return;
        }
        button.dataset.boundRebaseline = "true";
        button.addEventListener("click", () => {
            const snapshot = snapshotById.get(String(button.getAttribute("data-rebaseline-snapshot") || ""));
            if (snapshot) {
                openRebaselineModal(snapshot);
            }
        });
    });
}

function bindRebaselineModal() {
    document.querySelectorAll("[data-close-rebaseline]").forEach((button) => {
        if (button.dataset.boundCloseRebaseline === "true") {
            return;
        }
        button.dataset.boundCloseRebaseline = "true";
        button.addEventListener("click", closeRebaselineModal);
    });
    const confirmButton = document.getElementById("rebaseline-confirm-btn");
    if (confirmButton && confirmButton.dataset.boundConfirmRebaseline !== "true") {
        confirmButton.dataset.boundConfirmRebaseline = "true";
        confirmButton.addEventListener("click", async () => {
            try {
                await submitRebaseline();
            } catch (error) {
                const message = error instanceof Error ? error.message : "Unable to create a new baseline candidate.";
                window.alert(message);
            }
        });
    }
}

function renderJourneyTimeline(snapshots = [], selectedBaselineSourceSnapshotId = null) {
    if (!snapshots.length) {
        return '<div class="muted">No timeline is available yet.</div>';
    }
    const displaySnapshots = selectedBaselineSourceSnapshotId
        ? snapshots.filter((snapshot) => snapshot.snapshot_type !== "baseline_approved")
        : snapshots;
    const timeline = displaySnapshots.slice(-6).map((snapshot) => renderJourneyTimelineCard(snapshot, selectedBaselineSourceSnapshotId)).join("");
    return `<div class="stack compact-stack">${timeline}</div>`;
}

function renderJourneyCompare(comparison) {
    if (!comparison) {
        return '<div class="muted">Baseline and current snapshots are required before DriftGuard can render a repository-level comparison.</div>';
    }
    const deltas = Object.entries(comparison.vector_delta || {})
        .sort((left, right) => Math.abs(Number(right[1]) || 0) - Math.abs(Number(left[1]) || 0))
        .slice(0, 6);
    const labels = asArray(comparison.change_labels);
    return `
        <div class="stack compact-stack">
            <div class="journey-strip">
                <div class="journey-node journey-tone-gap">
                    <span class="journey-node-value">${escapeHtml(String(comparison.comparison_kind || "arbitrary").replaceAll("_", " "))}</span>
                    <span class="journey-node-label">Comparison</span>
                    <span class="journey-node-caption">${escapeHtml(comparison.left?.snapshot_key || "left")} → ${escapeHtml(comparison.right?.snapshot_key || "right")}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node journey-tone-primary">
                    <span class="journey-node-value">${formatSigned(comparison.drift_summary?.drift_delta)}</span>
                    <span class="journey-node-label">Drift delta</span>
                    <span class="journey-node-caption">pair ${asNumber(comparison.drift_summary?.right_distance_from_selected_baseline ?? comparison.drift_summary?.pair_distance ?? comparison.drift_summary?.right_distance_from_baseline).toFixed(3)}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node ${journeyToneForRisk(comparison.risk_summary?.risk_level)}">
                    <span class="journey-node-value">${escapeHtml(String(comparison.risk_summary?.risk_level || "low").toUpperCase())}</span>
                    <span class="journey-node-label">Risk level</span>
                    <span class="journey-node-caption">score ${asNumber(comparison.risk_summary?.score).toFixed(3)}</span>
                    <span class="journey-node-link" aria-hidden="true"></span>
                </div>
                <div class="journey-node journey-tone-medium">
                    <span class="journey-node-value">${asNumber(comparison.change_breakdown?.critical_surfaces_changed)}</span>
                    <span class="journey-node-label">Critical surfaces</span>
                    <span class="journey-node-caption">${asNumber(comparison.change_breakdown?.changed_artifact_count)} changed artifacts</span>
                </div>
            </div>
            ${labels.length ? `<div class="tag-row">${labels.map((label) => `<span class="drift-chip chip-model">${escapeHtml(label)}</span>`).join("")}</div>` : ""}
            <div class="journey-compare-grid">
                ${deltas.map(([key, value]) => `
                    <div class="journey-compare-row">
                        <span class="journey-compare-label">${escapeHtml(key.replaceAll("_", " "))}</span>
                        <span class="journey-compare-value ${Number(value) > 0 ? "journey-compare-up" : Number(value) < 0 ? "journey-compare-down" : ""}">${formatSigned(value, 4)}</span>
                    </div>
                `).join("")}
            </div>
            <div class="detail-note">${escapeHtml(`${asNumber(comparison.change_breakdown?.added_artifact_count)} added, ${asNumber(comparison.change_breakdown?.removed_artifact_count)} removed, and ${asNumber(comparison.change_breakdown?.changed_artifact_count)} changed artifacts between the approved baseline and the current landed posture.`)}</div>
        </div>
    `;
}

function populateAuditRepoLists(repos = []) {
    const items = asArray(repos);
    if (!items.length) {
        setSectionHtml("audit-logs-list", '<div class="muted">No repositories available</div>');
        return;
    }

    const navItems = items.map((repo) => {
        const repoFullValue = String(repo.repo_full || "");
        const currentClass = repoFullValue === repoFull ? " sidebar-subitem-active" : "";
        return `<a class="sidebar-subitem${currentClass}" href="${repoDetailUrl(repo)}">${escapeHtml(repoFullValue)}</a>`;
    }).join("");
    setSectionHtml("audit-logs-list", `<nav class="sidebar-sublist-nav">${navItems}</nav>`);
}

function bindAuditLogsToggle() {
    const toggle = document.getElementById("audit-logs-toggle");
    const list = document.getElementById("audit-logs-list");
    if (!toggle || !list || toggle.dataset.boundToggle === "true") {
        return;
    }
    toggle.dataset.boundToggle = "true";
    toggle.addEventListener("click", () => {
        const expanded = !list.hasAttribute("hidden");
        if (expanded) {
            list.setAttribute("hidden", "true");
            toggle.setAttribute("aria-expanded", "false");
        } else {
            list.removeAttribute("hidden");
            toggle.setAttribute("aria-expanded", "true");
        }
    });
}

async function loadAvailableRepos() {
    try {
        if (dashboardShellState() !== "active") {
            const shell = dashboardShellCopy();
            setSectionHtml("audit-logs-list", `<div class="muted">${escapeHtml(shell.body)}</div>`);
            return;
        }
        const response = await fetch("/api/repos");
        if (!response.ok) {
            throw new Error(`Repo inventory request failed with ${response.status}`);
        }
        const payload = await response.json();
        populateAuditRepoLists(asArray(payload.repos));
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown repository inventory error";
        const fallback = `<div class="muted">Unable to load workspace repositories. ${escapeHtml(message)}</div>`;
        setSectionHtml("audit-logs-list", fallback);
    }
}

function renderBlockedRepoShell() {
    document.body.classList.add("dashboard-shell-obscured");
    const button = detailButton();
    if (button) {
        button.disabled = true;
    }
}

async function loadDashboard() {
    try {
        if (!repoFull) {
            throw new Error("Repository context is missing from this page.");
        }
        const dashboardResponse = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard${window.location.search || ""}`);
        if (!dashboardResponse.ok) {
            throw new Error(`Repo dashboard request failed with ${dashboardResponse.status}`);
        }

        const payload = await dashboardResponse.json();
        applyDashboardPayload(payload);
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown repo dashboard error";
        const fallback = `<div class="muted">Unable to load repository dashboard. ${escapeHtml(message)}</div>`;
        renderRepoAuditFallback(message);
        setText("repo-stat-artifacts", "-");
        setText("repo-stat-review", "-");
        setText("repo-stat-baselines", "-");
        setText("repo-stat-history", "-");
        setText("repo-triage-count", "Unavailable");
        setText("repo-governance-attention-headline", "Governance attention unavailable");
        setHtml("repo-governance-attention-copy", escapeHtml(message));
        setSectionHtml("repo-governance-attention-details", fallback);
        setSectionHtml("triage-list", fallback);
        setSectionHtml("featured-storyline", fallback);
        setSectionHtml("detail-attributes", fallback);
        setSectionHtml("detail-evidence-list", `<li>${escapeHtml(message)}</li>`);
        setSectionHtml("control-surfaces", fallback);
        setSectionHtml("repo-ai-act-assessment", fallback);
        setSectionHtml("history-cues", fallback);
        setSectionHtml("baseline-review-panel", fallback);
        setSectionHtml("repo-journey-summary", fallback);
        setSectionHtml("repo-journey-timeline", fallback);
        setSectionHtml("repo-journey-compare", fallback);
        setText("repo-radar-title", "Version posture unavailable");
        setText("repo-radar-meta", message);
        setText("repo-radar-caption", message);
        drawRepoRadar(null);
        setSectionHtml("lower-confidence-insights", fallback);
        setSectionHtml("repo-actions-review", fallback);
        setSectionHtml("artifacts-tbody", `<tr><td colspan="5" class="muted">${escapeHtml(message)}</td></tr>`);
        setText("detail-artifact-name", repoFull || "Repository unavailable");
        setText("detail-subtitle", message);
        setText("detail-recommendation-body", message);
        const button = detailButton();
        if (button) {
            button.disabled = true;
        }
    }
}

bindAuditLogsToggle();

bindSidebarNavigation();
applyRepoTabVisibility();
syncStorylinePanelCopy();
bindRebaselineModal();
bindExportForm();
loadAvailableRepos();
if (dashboardShellState() === "active") {
    loadDashboard();
} else {
    renderBlockedRepoShell();
}

function bindExportForm() {
    const form = document.getElementById('export-form');
    if (!form) return;

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const submitButton = document.getElementById('export-submit-button');
        const formData = new FormData(form);
        const data = {
            from_date: formData.get('from_date'),
            to_date: formData.get('to_date'),
            export_mode: formData.get('export_mode'),
            include_artifact_content: formData.has('include_artifact_content'),
        };

        const statusDiv = document.getElementById('export-status');
        statusDiv.hidden = false;
        statusDiv.className = 'export-status export-status-progress';
        statusDiv.textContent = 'Generating export package…';
        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = 'Generating…';
        }

        try {
            const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/export/compliance`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });

            if (!response.ok) {
                let detail = `Export request failed: ${response.status}`;
                try {
                    const errorPayload = await response.json();
                    if (errorPayload && typeof errorPayload.detail === 'string') {
                        detail = errorPayload.detail;
                    }
                } catch (error) {
                }
                throw new Error(detail);
            }

            const result = await response.json();
            statusDiv.className = 'export-status export-status-success';
            if (result.download_url) {
                statusDiv.innerHTML = `Export job <strong>#${escapeHtml(String(result.job_id))}</strong> is ready. <a class="link" href="${escapeHtml(result.download_url)}">Download ZIP</a>.`;
            } else {
                statusDiv.textContent = `Export job #${result.job_id} was created.`;
            }
            await loadExportHistory();
        } catch (error) {
            const message = error instanceof Error ? error.message : 'Unknown export error';
            statusDiv.className = 'export-status export-status-error';
            statusDiv.textContent = message;
        } finally {
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = 'Generate Export Package';
            }
        }
    });
}

async function loadExportHistory() {
    const tbody = document.getElementById('export-history-tbody');
    if (!tbody) {
        return;
    }
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/export/history`);
        if (!response.ok) throw new Error('Failed to load export history');

        const payload = await response.json();
        const exportJobs = asArray(payload.jobs || []);
        if (exportJobs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="muted">No exports found</td></tr>';
            return;
        }

        tbody.innerHTML = exportJobs.map(job => `
            <tr>
                <td>${escapeHtml(job.export_mode)}</td>
                <td>${new Date(job.from_ts * 1000).toLocaleDateString()} - ${new Date(job.to_ts * 1000).toLocaleDateString()}</td>
                <td>${escapeHtml(job.status)}</td>
                <td>${job.created_at ? new Date(job.created_at * 1000).toLocaleString() : '-'}</td>
                <td>${job.result_size_bytes ? `${(job.result_size_bytes / 1024).toFixed(1)} KB` : '-'}</td>
                <td>
                    ${job.status === 'completed' && job.download_url ? `<a href="${escapeHtml(job.download_url)}" class="btn btn-sm">Download</a>` : '-'}
                </td>
            </tr>
        `).join('');
    } catch (error) {
        tbody.innerHTML = '<tr><td colspan="6" class="muted">Error loading export history</td></tr>';
    }
}
