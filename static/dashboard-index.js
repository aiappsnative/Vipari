function asArray(value) {
    return Array.isArray(value) ? value : [];
}

const repoDashboardCache = new Map();
const repoDashboardInflightCache = new Map();
const previewState = {
    activeRepoFull: null,
    pendingRepoFull: null,
    pinnedRepoFull: null,
};
window.__overviewPendingRebaseline = null;
window.__overviewRebaselineBusy = false;

const HOVER_PREVIEW_DELAY_MS = 80;

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (!element) {
        return;
    }
    element.innerHTML = html;
    element.classList.remove("loading-shell");
    element.classList.remove("muted");
    element.removeAttribute("aria-busy");
}

function setText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
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

function dashboardShellState() {
    return String(document.body?.dataset?.dashboardShellState || "active").trim().toLowerCase() || "active";
}

function dashboardShellCopy() {
    return {
        title: String(document.body?.dataset?.dashboardShellTitle || "Dashboard access status"),
        body: String(document.body?.dataset?.dashboardShellBody || "Dashboard data is unavailable for this workspace."),
        ctaHref: String(document.body?.dataset?.dashboardShellCtaHref || ""),
        ctaLabel: String(document.body?.dataset?.dashboardShellCtaLabel || ""),
    };
}

function renderBlockedOverviewShell() {
    document.body.classList.add("dashboard-shell-obscured");
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function normalizeScore(value) {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
        return 0;
    }
    return clamp(numeric, 0, 1);
}

function averageNumeric(values) {
    const numeric = values.map((value) => Number(value)).filter((value) => Number.isFinite(value));
    if (!numeric.length) {
        return 0;
    }
    return numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#39;");
}

function severityForPriority(priority) {
    if (priority === "review_now") {
        return { label: "High Severity", className: "severity-high" };
    }
    if (priority === "watch") {
        return { label: "Medium Severity", className: "severity-medium" };
    }
    return { label: "Baseline Review", className: "severity-low" };
}

function profileEntries(repo) {
    const matched = repo._matchedRiskItem || null;
    return asArray(matched?.attribute_profile).filter((entry) => entry.attribute_key !== "control_surface_type");
}

function overviewAttributeLabel(key) {
    return {
        guardrail_robustness: "Guardrails",
        capability_risk: "Capability",
        autonomy_level: "Autonomy",
        governance_strength: "Governance",
        model_config_posture: "Model/config",
    }[key] || key;
}

function buildOverviewUnknownAttributeRow(key) {
    return {
        attribute_key: key,
        label: overviewAttributeLabel(key),
        baseline_value: "unknown",
        current_value: "unknown",
        reason: "No normalized attribute evidence is available yet.",
        state: "unknown",
    };
}

function normalizedOverviewAttributeRows(repo) {
    const preferredOrder = ["guardrail_robustness", "capability_risk", "autonomy_level", "governance_strength", "model_config_posture"];
    const entriesByKey = new Map(profileEntries(repo).map((entry) => [entry.attribute_key, entry]));
    const rows = preferredOrder
        .map((key) => entriesByKey.get(key))
        .filter((entry) => entry && entry.state !== "no_change");
    for (const key of preferredOrder) {
        if (rows.length >= 2) {
            break;
        }
        const entry = entriesByKey.get(key) || buildOverviewUnknownAttributeRow(key);
        if (!rows.some((candidate) => candidate.attribute_key === entry.attribute_key)) {
            rows.push(entry);
        }
    }
    if (!rows.length) {
        return [buildOverviewUnknownAttributeRow("guardrail_robustness"), buildOverviewUnknownAttributeRow("capability_risk")];
    }
    return rows.slice(0, 2);
}

function renderOverviewAttributeBlock(repo) {
    const rows = normalizedOverviewAttributeRows(repo);
    return `
        <div class="triage-attribute-block">
            ${rows.map((entry) => `
                <div class="triage-attribute-row">
                    <span class="triage-attribute-label">${escapeHtml(entry.label || overviewAttributeLabel(entry.attribute_key))}</span>
                    <span class="triage-attribute-transition">${escapeHtml(`${entry.baseline_value || "unknown"} -> ${entry.current_value || "unknown"}`)}</span>
                    <span class="triage-attribute-reason">${escapeHtml(entry.reason || "No normalized attribute evidence is available yet.")}</span>
                </div>
            `).join("")}
        </div>
    `;
}

function governanceFindingLabel(findingType) {
    return {
        missing_required_owner_review: "Owner review gap",
        weak_review_for_high_risk_change: "Weak review",
        repeated_high_risk_drift_same_surface: "Repeated high drift",
        baseline_stale_after_repeated_change: "Stale baseline",
        low_governance_confidence: "Low confidence",
    }[String(findingType || "")] || "Governance signal";
}

function governanceSeverityClass(severity) {
    if (severity === "high") {
        return "severity-high";
    }
    if (severity === "warning") {
        return "severity-medium";
    }
    return "severity-low";
}

function overviewGovernanceHeadline(summary) {
    const missingReviewCount = Number(summary?.high_risk_missing_review_count || 0);
    const repeatedSurfaces = asArray(summary?.repeated_drift_surfaces);
    const anomalyCount = Number(summary?.repos_with_anomalies_count || 0);
    if (missingReviewCount > 0 || repeatedSurfaces.length > 0) {
        return "Immediate governance follow-up";
    }
    if (anomalyCount > 0) {
        return "Governance watchlist active";
    }
    return "No governance escalations";
}

function overviewGovernanceCopy(summary) {
    const anomalyCount = Number(summary?.repos_with_anomalies_count || 0);
    const missingReviewCount = Number(summary?.high_risk_missing_review_count || 0);
    const repeatedSurfaces = asArray(summary?.repeated_drift_surfaces);
    if (!anomalyCount) {
        return "No repositories currently surface backend governance anomalies that need immediate follow-up.";
    }
    if (missingReviewCount > 0) {
        return `${missingReviewCount} high-risk change${missingReviewCount === 1 ? " lacks" : "s lack"} clear review coverage across ${anomalyCount} repo${anomalyCount === 1 ? "" : "s"}.`;
    }
    if (repeatedSurfaces.length > 0) {
        return `Repeated drift is clustering on ${repeatedSurfaces.slice(0, 2).join(", ")}, so baseline freshness needs attention.`;
    }
    return `${anomalyCount} repo${anomalyCount === 1 ? " has" : "s have"} normalized governance signals worth monitoring.`;
}

function renderOverviewGovernanceAttention(summary) {
    const anomalyCount = Number(summary?.repos_with_anomalies_count || 0);
    const missingReviewCount = Number(summary?.high_risk_missing_review_count || 0);
    const repeatedSurfaces = asArray(summary?.repeated_drift_surfaces).slice(0, 3);
    const rankedIssues = asArray(summary?.ranked_issues_now).slice(0, 4);
    const chips = [
        `${anomalyCount} repo${anomalyCount === 1 ? "" : "s"} flagged`,
        `${missingReviewCount} high-risk review gap${missingReviewCount === 1 ? "" : "s"}`,
        `${repeatedSurfaces.length} repeated surface${repeatedSurfaces.length === 1 ? "" : "s"}`,
    ];
    return `
        <div class="stack compact-stack">
            <div class="tag-row">
                ${chips.map((chip) => `<span class="drift-chip chip-governance">${escapeHtml(chip)}</span>`).join("")}
            </div>
            ${repeatedSurfaces.length ? `
                <div class="governance-surface-list">
                    ${repeatedSurfaces.map((surface) => `<span class="governance-surface-chip">${escapeHtml(surface)}</span>`).join("")}
                </div>
            ` : ""}
            ${rankedIssues.length ? `
                <div class="governance-issue-list">
                    ${rankedIssues.map((finding) => `
                        <div class="governance-issue-row">
                            <div class="governance-issue-copy">
                                <strong>${escapeHtml(governanceFindingLabel(finding.finding_type))}</strong>
                                <span>${escapeHtml(finding.repo || "unknown repo")}</span>
                                <span>${escapeHtml(finding.evidence_summary || "Governance evidence requires review.")}</span>
                            </div>
                            <span class="severity-badge ${governanceSeverityClass(finding.severity)}">${escapeHtml(String(finding.severity || "info").toUpperCase())}</span>
                        </div>
                    `).join("")}
                </div>
            ` : '<div class="muted">No ranked governance issues are active right now.</div>'}
        </div>
    `;
}

function attributeScore(entry, keyPrefix) {
    const scoreKey = keyPrefix === "baseline" ? "baseline_score" : "current_score";
    const value = Number(entry?.[scoreKey]);
    if (Number.isFinite(value)) {
        return clamp(value, 0, 1);
    }
    return 0;
}

function attributeValue(entries, key, mode) {
    return normalizeScore(averageNumeric(entries.filter((entry) => entry.attribute_key === key).map((entry) => attributeScore(entry, mode))));
}

function matchedRiskItem(repo) {
    return repo._matchedRiskItem || null;
}

function reviewScopeContext(repoLike) {
    const prNumber = repoLike?.highest_review_pr_number || repoLike?.review_pr_number || matchedRiskItem(repoLike)?.review_pr_number || "";
    const headSha = repoLike?.highest_review_head_sha || repoLike?.review_head_sha || matchedRiskItem(repoLike)?.review_head_sha || "";
    return {
        prNumber: prNumber ? String(prNumber) : "",
        headSha: headSha ? String(headSha) : "",
    };
}

function reviewScopeLabel(repoLike) {
    const { prNumber, headSha } = reviewScopeContext(repoLike);
    if (!prNumber && !headSha) {
        return "";
    }
    const shortHeadSha = headSha ? headSha.slice(0, 7) : "";
    if (prNumber && shortHeadSha) {
        return `PR #${prNumber} · ${shortHeadSha}`;
    }
    if (prNumber) {
        return `PR #${prNumber}`;
    }
    return `Commit ${shortHeadSha}`;
}

function repoScopedDashboardUrl(repoLike) {
    const repoFull = String(repoLike?.repo_full || "").trim();
    const url = new URL(`/dashboard/${encodeURIComponent(repoFull)}`, window.location.origin);
    const artifactPath = repoLike?.highest_insight_artifact_path || repoLike?.artifact_path || matchedRiskItem(repoLike)?.artifact_path || "";
    if (artifactPath) {
        url.searchParams.set("artifact", artifactPath);
    }
    const { prNumber, headSha } = reviewScopeContext(repoLike);
    if (prNumber) {
        url.searchParams.set("pr", String(prNumber));
    }
    if (headSha) {
        url.searchParams.set("head_sha", String(headSha));
    }
    return `${url.pathname}${url.search}`;
}

function repoDetailUrl(repo) {
    return repoScopedDashboardUrl(repo);
}

function reviewContext(repo) {
    return repo.highest_review_target || repo.highest_evidence_label || "latest signal";
}

function repoScopeLabel(repo) {
    const scope = String(repo.dashboard_scope || "allocated").toLowerCase();
    if (scope === "connected_history") {
        return "Connected History";
    }
    return "Workspace Repo";
}

function repoStatusLabel(repo) {
    const status = String(repo.onboarding_status || "").toLowerCase();
    if (status === "baseline_approved") {
        return "Baseline locked";
    }
    if (status === "pending_baseline_approval") {
        return "Awaiting baseline";
    }
    return "Discovery pending";
}

function baselineLabelForRepo(repo) {
    const status = String(repo.onboarding_status || "").toLowerCase();
    if (status === "baseline_approved") {
        return "Baseline: Approved";
    }
    if (status === "pending_baseline_approval") {
        return "Baseline: Pending approval";
    }
    return "Baseline: none yet";
}

function priorityRank(priority) {
    if (priority === "review_now") {
        return 0;
    }
    if (priority === "watch") {
        return 1;
    }
    return 2;
}

function compactRepoLabel(repoFull) {
    const tail = String(repoFull || "repo").split("/").pop() || "repo";
    return tail.length > 12 ? `${tail.slice(0, 12)}...` : tail;
}

function formatSnapshotType(snapshotType) {
    if (snapshotType === "baseline_approved") {
        return "Approved Baseline";
    }
    if (snapshotType === "branch_head") {
        return "Branch Head";
    }
    if (snapshotType === "historical_commit") {
        return "Historical Commit";
    }
    if (snapshotType === "merge") {
        return "Merged Change";
    }
    if (snapshotType === "current") {
        return "Current State";
    }
    return String(snapshotType || "milestone").replaceAll("_", " ");
}

function snapshotTag(snapshot) {
    const sourceRef = String(snapshot?.source_ref || "").trim();
    if (sourceRef) {
        return sourceRef.length > 18 ? `${sourceRef.slice(0, 18)}...` : sourceRef;
    }
    return formatSnapshotType(snapshot?.snapshot_type);
}

function snapshotCaption(snapshot) {
    const labels = asArray(snapshot?.change_labels).slice(0, 2).map((label) => String(label).replaceAll("_", " "));
    if (snapshot?.snapshot_type === "baseline_approved") {
        return "Approved posture for the repo";
    }
    if (labels.length) {
        return labels.join(" · ");
    }
    const riskSummary = snapshot?.risk_summary?.headline || snapshot?.risk_summary?.summary || "Repo posture checkpoint";
    return String(riskSummary);
}

function snapshotTitle(snapshot) {
    return formatSnapshotType(snapshot?.snapshot_type);
}

async function fetchRepoDashboard(repoFull) {
    if (repoDashboardCache.has(repoFull)) {
        return repoDashboardCache.get(repoFull);
    }
    if (repoDashboardInflightCache.has(repoFull)) {
        return repoDashboardInflightCache.get(repoFull);
    }
    const request = fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`)
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Repo dashboard request failed with ${response.status}`);
            }
            return response.json();
        })
        .then((payload) => {
            repoDashboardCache.set(repoFull, payload);
            return payload;
        })
        .finally(() => {
            repoDashboardInflightCache.delete(repoFull);
        });
    repoDashboardInflightCache.set(repoFull, request);
    return request;
}

function clearPreviewTimer(button) {
    const timerId = Number(button.dataset.previewTimer || "0");
    if (timerId) {
        window.clearTimeout(timerId);
        delete button.dataset.previewTimer;
    }
}

function journeyNodesFromRepoPayload(repo, repoPayload) {
    const snapshots = asArray(repoPayload?.journey_snapshots);
    const selectedBaselineSourceSnapshotId = Number(repoPayload?.selected_baseline_source_snapshot_id || 0);
    if (!snapshots.length) {
        return [
            {
                tag: repo.highest_baseline_label || "Approved baseline",
                title: "Approved Baseline",
                caption: "Repo-level baseline checkpoint",
                tone: "baseline",
                snapshotId: null,
                canRebaseline: false,
            },
            {
                tag: reviewContext(repo),
                title: "Latest Evidence",
                caption: triageSummary(repo),
                tone: "activity",
                snapshotId: null,
                canRebaseline: false,
            },
            {
                tag: "Current state",
                title: "Current State",
                caption: `${Number(repo.review_now_count || 0)} review now · ${Number(repo.watch_count || 0)} watch`,
                tone: "current",
                snapshotId: null,
                canRebaseline: false,
            },
        ];
    }

    const displaySnapshots = selectedBaselineSourceSnapshotId
        ? snapshots.filter((snapshot) => snapshot.snapshot_type !== "baseline_approved")
        : snapshots;
    const representativeSnapshots = selectRepresentativeJourneySnapshots(displaySnapshots, selectedBaselineSourceSnapshotId);
    return representativeSnapshots.map((snapshot) => {
        const isSelectedBaselineSource = selectedBaselineSourceSnapshotId > 0 && Number(snapshot.id) === selectedBaselineSourceSnapshotId;
        return {
            tag: snapshotTag(snapshot),
            title: isSelectedBaselineSource ? "Approved Baseline" : snapshotTitle(snapshot),
            caption: isSelectedBaselineSource ? "Current approved baseline checkpoint" : snapshotCaption(snapshot),
            tone: isSelectedBaselineSource
                ? "baseline"
                : snapshot.snapshot_type === "baseline_approved"
                    ? "baseline"
                    : (snapshot.snapshot_type === "current" || snapshot.snapshot_type === "branch_head")
                        ? "current"
                        : "activity",
            snapshotId: Number(snapshot.id || 0) || null,
            canRebaseline: Boolean(snapshot.commit_sha),
            sourceRef: snapshot.source_ref || "",
            createdAt: snapshot.created_at || 0,
        };
    });
}

function selectRepresentativeJourneySnapshots(snapshots, selectedBaselineSourceSnapshotId) {
    if (snapshots.length <= 4) {
        return snapshots;
    }

    const indices = new Set([0, snapshots.length - 1]);
    const baselineIndex = snapshots.findIndex((snapshot) => Number(snapshot.id) === selectedBaselineSourceSnapshotId);
    const currentIndex = snapshots.findIndex((snapshot) => snapshot.snapshot_type === "current") >= 0
        ? snapshots.findIndex((snapshot) => snapshot.snapshot_type === "current")
        : snapshots.findIndex((snapshot) => snapshot.snapshot_type === "branch_head");

    if (baselineIndex >= 0) {
        indices.add(baselineIndex);
    }
    if (currentIndex >= 0) {
        indices.add(currentIndex);
    }

    const fillers = [
        Math.floor((snapshots.length - 1) / 3),
        Math.floor((snapshots.length - 1) / 2),
        Math.floor(((snapshots.length - 1) * 2) / 3),
    ];
    for (const candidateIndex of fillers) {
        if (indices.size >= 4) {
            break;
        }
        indices.add(candidateIndex);
    }

    return Array.from(indices)
        .sort((left, right) => left - right)
        .slice(0, 4)
        .map((index) => snapshots[index]);
}

function renderJourney(repo, repoPayload = null) {
    const nodes = journeyNodesFromRepoPayload(repo, repoPayload);
    return `
        <div class="journey-line" aria-hidden="true"></div>
        <div class="journey-points">
            ${nodes.map((node, index) => `
                <div class="journey-point journey-point-${escapeHtml(node.tone || "activity")}" ${node.snapshotId ? `data-overview-snapshot="${escapeHtml(String(node.snapshotId))}"` : ""}>
                    <div class="journey-dot-wrap">
                        <span class="journey-dot"></span>
                        ${index < nodes.length - 1 ? '<span class="journey-arrow">↓</span>' : ""}
                    </div>
                    <div class="journey-pill">${escapeHtml(node.tag)}</div>
                    <div class="journey-point-title">${escapeHtml(node.title)}</div>
                    <div class="journey-point-caption">${escapeHtml(node.caption)}</div>
                    ${node.canRebaseline ? `<button type="button" class="journey-point-action" data-overview-rebaseline="${escapeHtml(String(node.snapshotId))}">Re-baseline here</button>` : ""}
                </div>
            `).join("")}
        </div>
    `;
}

function renderRepoAtlasCard(repo, index) {
    const reviewNowCount = Number(repo.review_now_count || 0);
    const watchCount = Number(repo.watch_count || 0);
    const insightCount = Number(repo.insight_count || 0);
    const driftTone = reviewNowCount > 0 ? "high" : watchCount > 0 ? "medium" : "steady";
    const checkpointCount = Number(repo.historical_version_count || 0);
    const summary = repo.highest_insight_title || triageSummary(repo) || "Repository posture available";
    const recentSignal = repo.highest_evidence_summary || repo.highest_change_summary || repo.highest_flag_summary || repo.highest_rationale || "Recent signal summary will appear here as audits accumulate.";
    const scopedReviewLabel = reviewScopeLabel(repo);
    return `
        <button type="button" class="repo-atlas-card-button" data-repo-atlas-index="${index}" data-repo-full="${escapeHtml(repo.repo_full)}">
            <div class="repo-atlas-topline">
                <span class="repo-atlas-scope">${escapeHtml(repoScopeLabel(repo))}</span>
                <span class="repo-atlas-status repo-atlas-status-${escapeHtml(driftTone)}">${escapeHtml(repoStatusLabel(repo))}</span>
            </div>
            <div class="repo-atlas-name">${escapeHtml(repo.repo_full)}</div>
            <div class="repo-atlas-summary">${escapeHtml(summary)}</div>
            <div class="repo-atlas-metrics">
                <span>${escapeHtml(String(insightCount))} audits</span>
                <span>${escapeHtml(String(reviewNowCount))} escalations</span>
                <span>${watchCount > 0 ? escapeHtml(`${String(watchCount)} watch`) : escapeHtml(`${String(checkpointCount)} checkpoints`)}</span>
            </div>
            <div class="repo-atlas-signal">${escapeHtml(recentSignal)}</div>
            <div class="repo-atlas-footer">
                <span class="repo-atlas-baseline">${escapeHtml(repo.highest_baseline_label || baselineLabelForRepo(repo))}</span>
                ${scopedReviewLabel ? `<span class="repo-atlas-context">Scoped ${escapeHtml(scopedReviewLabel)}</span>` : ""}
                <span class="repo-atlas-open">Preview</span>
            </div>
        </button>
    `;
}

function driftPercent(repo) {
    const raw = Number(repo.top_drift_magnitude || 0);
    if (!Number.isFinite(raw) || raw <= 0) {
        return 0;
    }
    return clamp(Math.round(raw * 100), 0, 100);
}

function drawDriftRing(percent) {
    const canvas = document.getElementById("drift-ring");
    if (!canvas) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }
    const center = canvas.width / 2;
    const radius = 58;
    const lineWidth = 10;
    const start = -Math.PI / 2;
    const end = start + ((Math.PI * 2) * clamp(percent, 0, 100)) / 100;
    const darkTheme = document.body?.dataset?.theme !== "light";
    const trackColor = darkTheme ? "rgba(255,255,255,0.08)" : "rgba(112, 98, 84, 0.20)";
    const activeColor = darkTheme ? "#52e0d5" : "#1f7d88";

    context.clearRect(0, 0, canvas.width, canvas.height);
    context.beginPath();
    context.strokeStyle = trackColor;
    context.lineWidth = lineWidth;
    context.arc(center, center, radius, 0, Math.PI * 2);
    context.stroke();

    context.beginPath();
    context.strokeStyle = activeColor;
    context.lineWidth = lineWidth;
    context.lineCap = "round";
    context.arc(center, center, radius, start, end);
    context.stroke();
}

function radarVectors(repo, repoPayload = null) {
    // If the server provided an authoritative payload with journey snapshots,
    // prefer those attribute vectors and coverage numbers so baseline/current
    // polygons are drawn from the same source the rest of the UI uses.
    if (repoPayload && Array.isArray(repoPayload.journey_snapshots) && repoPayload.journey_snapshots.length) {
        const snapshots = repoPayload.journey_snapshots;
        const selectedBaselineId = Number(repoPayload?.selected_baseline_source_snapshot_id || 0);
        let baselineSnapshot = (selectedBaselineId
            ? snapshots.find((s) => Number(s.id) === selectedBaselineId)
            : null) || snapshots.find((s) => s.snapshot_type === "baseline_approved") || snapshots[0];
        const currentSnapshot = snapshots.find((s) => s.snapshot_type === "current") || snapshots[snapshots.length - 1];

        const mapAttr = (snap) => {
            const vec = snap?.attribute_vector || {};
            const input = snap?.input_summary || {};
            return [
                Number(vec.governance || 0),
                Number(vec.change_velocity || 0),
                Number((Number(input.coverage_percent || 0) / 100) || 0),
                Number(vec.autonomy || 0),
                Number(vec.capability || 0),
                Number(vec.guardrails || 0),
            ];
        };

        const baselineValues = mapAttr(baselineSnapshot).map((value) => normalizeScore(value));
        const currentValues = mapAttr(currentSnapshot).map((value) => normalizeScore(value));

        return {
            labels: ["Governance", "Velocity", "Coverage", "Autonomy", "Capability", "Guardrails"],
            series: [
                {
                    color: "rgba(78, 103, 255, 0.28)",
                    stroke: "rgba(79, 106, 255, 0.9)",
                    values: baselineValues,
                },
                {
                    color: "rgba(73, 223, 217, 0.22)",
                    stroke: "rgba(85, 230, 222, 0.92)",
                    values: currentValues,
                },
            ],
        };
    }

    // Fallback to the legacy behavior derived from matched risk entries
    const entries = profileEntries(repo);
    if (!entries.length) {
        return null;
    }
    const approvedCoverage = (repo.highest_baseline_label || "").includes("Approved") ? 0.86 : 0.58;
    return {
        labels: ["Compliance", "Stability", "Coverage", "Efficiency", "Performance", "Security"],
        series: [
            {
                color: "rgba(78, 103, 255, 0.28)",
                stroke: "rgba(79, 106, 255, 0.9)",
                values: [
                    attributeValue(entries, "governance_strength", "baseline"),
                    attributeValue(entries, "stability_vs_creativity", "baseline"),
                    approvedCoverage,
                    attributeValue(entries, "autonomy_level", "baseline"),
                    0.62,
                    attributeValue(entries, "guardrail_robustness", "baseline"),
                ],
            },
            {
                color: "rgba(73, 223, 217, 0.22)",
                stroke: "rgba(85, 230, 222, 0.92)",
                values: [
                    attributeValue(entries, "governance_strength", "current"),
                    attributeValue(entries, "stability_vs_creativity", "current"),
                    normalizeScore((Number(repo.discovered_artifact_count || 0) + Number(repo.watch_count || 0)) / 6),
                    attributeValue(entries, "autonomy_level", "current"),
                    normalizeScore(0.42 + Number(repo.top_drift_magnitude || 0)),
                    attributeValue(entries, "guardrail_robustness", "current"),
                ],
            },
        ],
    };
}

function drawRadar(repo, repoPayload = null) {
    const canvas = document.getElementById("repo-posture-radar");
    if (!canvas) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }
    const vectors = radarVectors(repo, repoPayload);
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
            const x = centerX + Math.cos(angle) * radius * normalizeScore(value);
            const y = centerY + Math.sin(angle) * radius * normalizeScore(value);
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
}

function coverageBars(repo, repos) {
    const approved = (repo.highest_baseline_label || "").includes("Approved") ? 92 : 74;
    const promptCoverage = clamp(Math.round((Number(repo.discovered_artifact_count || 0) / Math.max(Number(repos.length || 1), 1)) * 100 * 0.7), 48, 89);
    const policyCoverage = clamp(Math.round((Number(repo.watch_count || 0) + Number(repo.review_now_count || 0) + 2) * 14), 58, 96);
    const testCoverage = clamp(Math.round((Number(repo.lower_confidence_count || 0) + 2) * 12), 42, 82);
    return [
        { label: "Prompt Coverage", value: promptCoverage },
        { label: "Policy Enforcement", value: approved },
        { label: "Test Suite", value: testCoverage },
    ];
}

function driftPercentFromPayload(repo, repoPayload) {
    const payloadDistance = Number(
        repoPayload?.journey_comparison?.drift_summary?.right_distance_from_selected_baseline
        ?? repoPayload?.journey_comparison?.drift_summary?.pair_distance
        ?? repoPayload?.journey_comparison?.drift_summary?.right_distance_from_baseline
    );
    if (Number.isFinite(payloadDistance) && payloadDistance >= 0) {
        return clamp(Math.round(payloadDistance * 100), 0, 100);
    }
    const leftVector = repoPayload?.journey_comparison?.left?.attribute_vector || null;
    const rightVector = repoPayload?.journey_comparison?.right?.attribute_vector || null;
    if (leftVector && rightVector) {
        const vectorKeys = Array.from(new Set([...Object.keys(leftVector), ...Object.keys(rightVector)]));
        const pairDistance = vectorKeys.reduce(
            (sum, key) => sum + Math.abs(Number(rightVector[key] || 0) - Number(leftVector[key] || 0)),
            0,
        );
        return clamp(Math.round(pairDistance * 100), 0, 100);
    }
    return driftPercent(repo);
}

function renderCoverageBars(repo, repos, repoPayload = null) {
    // Prefer authoritative payload values when available
    const snapshots = asArray(repoPayload?.journey_snapshots || []);
    const baselineSnapshot = snapshots.find((s) => s.snapshot_type === 'baseline_approved') || snapshots[0] || null;
    const input = baselineSnapshot?.input_summary || repo.input_summary || {};

    const tracked = Number(input.tracked_count || repo.discovered_artifact_count || 0);
    const approved = Number(input.approved_baseline_count || 0);
    const coveragePercent = Number(input.coverage_percent || 0);
    const criticalTotal = Number(input.critical_artifact_count || 0);
    const approvedCritical = Number(input.approved_critical_count || 0);
    const criticalPercent = Number(input.critical_coverage_percent || 0);

    // Primary coverage bar and a thin overlay representing critical coverage
    const rows = [
        {
            label: "Approved Baseline Coverage",
            value: clamp(Math.round(coveragePercent), 0, 100),
            meta: `${approved}/${tracked} approved`,
        },
        {
            label: "Critical Surface Coverage",
            value: clamp(Math.round(criticalPercent), 0, 100),
            meta: `${approvedCritical}/${criticalTotal} critical`,
        },
    ];

    return rows.map((item, idx) => `
        <div class="coverage-bar-row" data-coverage-type="${idx === 0 ? 'primary' : 'critical'}">
            <div class="coverage-bar-meta">
                <span>${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.meta)} · ${item.value}%</strong>
            </div>
            <div class="coverage-track">
                <div class="coverage-fill" style="width:${item.value}%"></div>
                ${idx === 0 ? `<div class="coverage-fill-critical" style="width:${clamp(Math.round(criticalPercent),0,100)}%"></div>` : ''}
            </div>
        </div>
    `).join("");
}

function triageSummary(repo) {
    return repo.highest_change_summary || repo.highest_flag_summary || repo.highest_rationale || "DriftGuard flagged this repository for review.";
}

function repoSubtitle(repo) {
    return repo.highest_insight_title || reviewContext(repo);
}

function issueHeadline(repo) {
    const title = String(repo.highest_insight_title || "").trim();
    if (title) {
        return title;
    }
    const artifact = String(repo.highest_insight_artifact_path || matchedRiskItem(repo)?.artifact_path || "main-v2").split("/").pop() || "main-v2";
    return `Model divergence in '${artifact}'`;
}

function onboardingStatusBadge(repo) {
    const status = String(repo.onboarding_status || "").toLowerCase();
    if (status === "baseline_approved") {
        return '<span class="baseline-status-badge baseline-status-approved">Baseline approved</span>';
    }
    if (status === "pending_baseline_approval") {
        return '<span class="baseline-status-badge baseline-status-pending">Awaiting baseline approval</span>';
    }
    return "";
}

function renderUrgentRow(repo, index) {
    const severity = severityForPriority(repo.highest_priority);
    const chips = [
        repo.highest_baseline_label || baselineLabelForRepo(repo),
        `${Number(repo.discovered_artifact_count || 0)} artifacts`,
        reviewContext(repo),
    ].filter(Boolean);
    return `
        <button type="button" class="triage-row" data-row-index="${index}">
            <div class="triage-row-top">
                <strong>${escapeHtml(issueHeadline(repo))}</strong>
                <span class="severity-badge ${severity.className}">${escapeHtml(severity.label)}</span>
            </div>
            <div class="triage-row-reason">${escapeHtml(repo.repo_full)}</div>
            <div class="triage-row-meta">
                <span>${escapeHtml(triageSummary(repo))}</span>
                <span>${escapeHtml(repoSubtitle(repo))}</span>
            </div>
            ${renderOverviewAttributeBlock(repo)}
            <div class="triage-row-chips">
                ${chips.map((chip) => `<span class="drift-chip chip-governance">${escapeHtml(chip)}</span>`).join("")}
            </div>
            <span class="triage-row-chevron" aria-hidden="true">Open</span>
        </button>
    `;
}

function selectUrgentRow(index) {
    document.querySelectorAll(".triage-row").forEach((item, itemIndex) => {
        item.classList.toggle("selected", itemIndex === index);
    });
}

function selectRepoAtlasCard(repoFull) {
    document.querySelectorAll(".repo-atlas-card-button").forEach((item) => {
        item.classList.toggle("active", item.getAttribute("data-repo-full") === repoFull);
    });
}

function applyRepoPreview(repo, repos, repoPayload = null) {
    previewState.activeRepoFull = repo.repo_full;
    const severity = severityForPriority(repo.highest_priority);
    const severityBadge = document.getElementById("detail-severity-badge");
    if (severityBadge) {
        severityBadge.textContent = severity.label;
        severityBadge.className = `severity-badge ${severity.className}`;
    }
    setText("detail-repo-name", repo.repo_full);
    setText("detail-subtitle", repoSubtitle(repo));
    setText("selected-repo-summary", triageSummary(repo));
    setText("repo-radar-title", compactRepoLabel(repo.repo_full));
    setText("journey-repo-title", compactRepoLabel(repo.repo_full));
    setText("journey-repo-name", repo.repo_full);
    setText("repo-radar-meta", triageSummary(repo));
    setHtml(
        "detail-summary",
        `
            <div class="stack compact-stack">
                <div>${escapeHtml(triageSummary(repo))}</div>
                <div class="tag-row">
                    <span class="drift-chip chip-governance">${escapeHtml(repoScopeLabel(repo))}</span>
                    <span class="drift-chip chip-governance">${escapeHtml(repo.highest_baseline_label || baselineLabelForRepo(repo))}</span>
                    <span class="drift-chip chip-governance">${escapeHtml(`${Number(repo.review_now_count || 0)} review now`)}</span>
                    <span class="drift-chip chip-governance">${escapeHtml(`${Number(repo.watch_count || 0)} watch`)}</span>
                </div>
            </div>
        `,
    );
    setSectionHtml("repo-journey-strip", renderJourney(repo, repoPayload));
    bindOverviewJourneyActions(repo, repoPayload);
    const repoStoryNote = repoPayload?.journey_comparison?.risk_summary?.headline || repoPayload?.featured_storyline?.summary || triageSummary(repo);
    setText("repo-journey-note", repoStoryNote);
    setSectionHtml("coverage-bars", renderCoverageBars(repo, repos, repoPayload));
    setText("coverage-note", `${Number(repo.discovered_artifact_count || 0)} tracked artifacts · ${repoScopeLabel(repo)}`);
    drawRadar(repo, repoPayload);
    const drift = driftPercentFromPayload(repo, repoPayload);
    drawDriftRing(drift);
    setText("drift-ring-value", `${drift}%`);
    selectRepoAtlasCard(repo.repo_full);
    const detailLink = document.getElementById("detail-escalate-btn");
    if (detailLink) {
        detailLink.setAttribute("href", repoDetailUrl(repo));
    }
    setText(
        "detail-recommendation-body",
        repo.highest_recommended_action || `Inspect ${repo.highest_insight_artifact_path || reviewContext(repo)} before merge.`,
    );
    const auditToggle = document.getElementById("audit-logs-toggle");
    if (auditToggle) {
        auditToggle.dataset.defaultHref = repoDetailUrl(repo);
    }
}

function buildSelectionItems(repos, attentionRepos, highestRiskItems) {
    const attentionByRepo = new Map(attentionRepos.map((repo) => [repo.repo_full, repo]));
    const riskByRepo = new Map(highestRiskItems.map((item) => [item.repo_full, item]));
    return repos.map((repo) => {
        const attention = attentionByRepo.get(repo.repo_full) || null;
        const matchedRiskItem = riskByRepo.get(repo.repo_full) || null;
        return {
            ...repo,
            ...(attention || {}),
            highest_priority: attention?.highest_priority || "baseline_review",
            highest_insight_title: attention?.highest_insight_title || matchedRiskItem?.title || null,
            highest_insight_artifact_path: attention?.highest_insight_artifact_path || matchedRiskItem?.artifact_path || null,
            highest_evidence_label: attention?.highest_evidence_label || matchedRiskItem?.evidence_label || null,
            highest_evidence_summary: attention?.highest_evidence_summary || matchedRiskItem?.evidence_summary || null,
            highest_change_summary: attention?.highest_change_summary || matchedRiskItem?.change_summary || null,
            highest_flag_summary: attention?.highest_flag_summary || matchedRiskItem?.flag_summary || null,
            highest_rationale: attention?.highest_rationale || matchedRiskItem?.rationale || null,
            highest_recommended_action: attention?.highest_recommended_action || matchedRiskItem?.recommended_action || null,
            highest_baseline_label: attention?.highest_baseline_label || matchedRiskItem?.baseline_label || baselineLabelForRepo(repo),
            highest_review_target: attention?.highest_review_target || matchedRiskItem?.review_target || null,
            highest_review_url: attention?.highest_review_url || matchedRiskItem?.review_url || null,
            highest_review_pr_number: attention?.highest_review_pr_number || matchedRiskItem?.review_pr_number || null,
            highest_review_head_sha: attention?.highest_review_head_sha || matchedRiskItem?.review_head_sha || null,
            insight_count: Number(attention?.insight_count || 0),
            lower_confidence_count: Number(attention?.lower_confidence_count || 0),
            review_now_count: Number(attention?.review_now_count || 0),
            watch_count: Number(attention?.watch_count || 0),
            baseline_review_count: Number(attention?.baseline_review_count || 0),
            top_drift_magnitude: Number(attention?.top_drift_magnitude || 0),
            avg_semantic_distance: Number(attention?.avg_semantic_distance || 0),
            discovered_artifact_count: Number(attention?.discovered_artifact_count || repo.discovered_artifact_count || 0),
            _matchedRiskItem: matchedRiskItem,
        };
    }).sort((left, right) => {
        const priorityDelta = priorityRank(left.highest_priority) - priorityRank(right.highest_priority);
        if (priorityDelta !== 0) {
            return priorityDelta;
        }
        const driftDelta = Number(right.top_drift_magnitude || 0) - Number(left.top_drift_magnitude || 0);
        if (driftDelta !== 0) {
            return driftDelta;
        }
        return String(left.repo_full || "").localeCompare(String(right.repo_full || ""));
    });
}

function overviewSections(payload) {
    return payload && typeof payload === "object" ? (payload.overview_sections || {}) : {};
}

async function previewRepoSelection(repo, repos, rowIndex = null) {
    previewState.pinnedRepoFull = repo.repo_full;
    previewState.pendingRepoFull = repo.repo_full;
    if (Number.isFinite(rowIndex)) {
        selectUrgentRow(rowIndex);
    }
    if (repoDashboardCache.has(repo.repo_full)) {
        applyRepoPreview(repo, repos, repoDashboardCache.get(repo.repo_full));
        return;
    }
    try {
        const repoPayload = await fetchRepoDashboard(repo.repo_full);
        if (previewState.pendingRepoFull !== repo.repo_full) {
            return;
        }
        applyRepoPreview(repo, repos, repoPayload);
    } catch {
        if (previewState.pendingRepoFull !== repo.repo_full) {
            return;
        }
        applyRepoPreview(repo, repos, null);
    }
}

function bindUrgentRows(items, repos) {
    const rows = Array.from(document.querySelectorAll(".triage-row"));
    rows.forEach((button) => {
        const preview = async () => {
            const index = Number(button.getAttribute("data-row-index"));
            if (!Number.isFinite(index) || !items[index]) {
                return;
            }
            await previewRepoSelection(items[index], repos, index);
        };
        button.addEventListener("mouseenter", () => {
            clearPreviewTimer(button);
            button.dataset.previewTimer = String(window.setTimeout(() => {
                void preview();
            }, HOVER_PREVIEW_DELAY_MS));
        });
        button.addEventListener("mouseleave", () => {
            clearPreviewTimer(button);
            previewState.pendingRepoFull = null;
        });
        button.addEventListener("blur", () => {
            clearPreviewTimer(button);
            previewState.pendingRepoFull = null;
        });
        button.addEventListener("focus", () => {
            clearPreviewTimer(button);
            void preview();
        });
        button.addEventListener("click", () => {
            clearPreviewTimer(button);
            const index = Number(button.getAttribute("data-row-index"));
            if (!Number.isFinite(index) || !items[index]) {
                return;
            }
            const repo = items[index];
            window.location.href = repoDetailUrl(repo);
        });
        button.addEventListener("keydown", (event) => {
            const index = Number(button.getAttribute("data-row-index"));
            if (!Number.isFinite(index)) {
                return;
            }
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                const delta = event.key === "ArrowDown" ? 1 : -1;
                const nextIndex = clamp(index + delta, 0, rows.length - 1);
                const nextRow = rows[nextIndex];
                if (nextRow instanceof HTMLButtonElement) {
                    nextRow.focus();
                    void previewRepoSelection(items[nextIndex], repos, nextIndex);
                }
            }
            if (event.key === "Home") {
                event.preventDefault();
                const firstRow = rows[0];
                if (firstRow instanceof HTMLButtonElement) {
                    firstRow.focus();
                    void previewRepoSelection(items[0], repos, 0);
                }
            }
            if (event.key === "End") {
                event.preventDefault();
                const lastIndex = rows.length - 1;
                const lastRow = rows[lastIndex];
                if (lastRow instanceof HTMLButtonElement) {
                    lastRow.focus();
                    void previewRepoSelection(items[lastIndex], repos, lastIndex);
                }
            }
            if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                window.location.href = repoDetailUrl(items[index]);
            }
        });
    });
}

function bindRepoAtlasCards(items, repos) {
    document.querySelectorAll(".repo-atlas-card-button").forEach((button) => {
        const preview = async () => {
            const index = Number(button.getAttribute("data-repo-atlas-index"));
            if (!Number.isFinite(index) || !items[index]) {
                return;
            }
            await previewRepoSelection(items[index], repos, null);
        };
        button.addEventListener("mouseenter", () => {
            clearPreviewTimer(button);
            button.dataset.previewTimer = String(window.setTimeout(() => {
                void preview();
            }, HOVER_PREVIEW_DELAY_MS));
        });
        button.addEventListener("mouseleave", () => {
            clearPreviewTimer(button);
            previewState.pendingRepoFull = null;
        });
        button.addEventListener("blur", () => {
            clearPreviewTimer(button);
            previewState.pendingRepoFull = null;
        });
        button.addEventListener("focus", () => {
            clearPreviewTimer(button);
            void preview();
        });
        button.addEventListener("click", () => {
            clearPreviewTimer(button);
            const index = Number(button.getAttribute("data-repo-atlas-index"));
            if (!Number.isFinite(index) || !items[index]) {
                return;
            }
            window.location.href = repoDetailUrl(items[index]);
        });
    });
}

function openOverviewRebaselineModal(repo, snapshot) {
    const modal = document.getElementById("overview-rebaseline-modal");
    const summary = document.getElementById("overview-rebaseline-modal-summary");
    const textarea = document.getElementById("overview-rebaseline-rationale");
    if (!modal || !summary || !(textarea instanceof HTMLTextAreaElement)) {
        return;
    }
    window.__overviewPendingRebaseline = { repo, snapshot };
    summary.innerHTML = `
        <div><strong>${escapeHtml(repo.repo_full)}</strong></div>
        <div class="detail-note">${escapeHtml(snapshotTitle(snapshot))} · ${escapeHtml(snapshot.commit_sha || snapshot.snapshot_key || "checkpoint")}</div>
        <div class="detail-note">${escapeHtml(`${Number(snapshot.change_breakdown?.critical_surfaces_changed || 0)} critical surfaces changed · this will create a candidate for approval.`)}</div>
    `;
    textarea.value = "";
    setOverviewRebaselineBusy(false);
    modal.hidden = false;
}

function closeOverviewRebaselineModal(force = false) {
    if (window.__overviewRebaselineBusy && !force) {
        return;
    }
    const modal = document.getElementById("overview-rebaseline-modal");
    if (modal) {
        modal.hidden = true;
    }
    window.__overviewPendingRebaseline = null;
}

function setOverviewRebaselineBusy(isBusy) {
    window.__overviewRebaselineBusy = Boolean(isBusy);
    const modal = document.getElementById("overview-rebaseline-modal");
    const card = modal?.querySelector(".modal-card");
    const progress = document.getElementById("overview-rebaseline-progress");
    const progressText = document.getElementById("overview-rebaseline-progress-text");
    const textarea = document.getElementById("overview-rebaseline-rationale");
    const confirmButton = document.getElementById("overview-rebaseline-confirm-btn");

    if (card) {
        card.classList.toggle("modal-card-busy", Boolean(isBusy));
    }
    if (progress) {
        progress.hidden = !isBusy;
    }
    if (progressText) {
        progressText.textContent = isBusy
            ? "Creating a baseline candidate from the selected checkpoint..."
            : "Creating a baseline candidate from the selected checkpoint...";
    }
    if (textarea instanceof HTMLTextAreaElement) {
        textarea.disabled = Boolean(isBusy);
    }
    if (confirmButton instanceof HTMLButtonElement) {
        confirmButton.disabled = Boolean(isBusy);
        confirmButton.textContent = isBusy ? "Working..." : "Confirm";
    }
    document.querySelectorAll("[data-close-overview-rebaseline]").forEach((button) => {
        if (button instanceof HTMLButtonElement) {
            button.disabled = Boolean(isBusy);
        }
    });
}

async function submitOverviewRebaseline() {
    const pending = window.__overviewPendingRebaseline;
    const textarea = document.getElementById("overview-rebaseline-rationale");
    if (!pending || !(textarea instanceof HTMLTextAreaElement)) {
        return;
    }
    setOverviewRebaselineBusy(true);
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(pending.repo.repo_full)}/baseline/rebaseline`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ snapshot_id: pending.snapshot.id, rationale: textarea.value.trim() || null }),
        });
        if (!response.ok) {
            throw new Error(`Re-baseline request failed with ${response.status}`);
        }
        const payload = await response.json();
        if (payload?.dashboard) {
            repoDashboardCache.set(pending.repo.repo_full, payload.dashboard);
        } else {
            repoDashboardCache.delete(pending.repo.repo_full);
        }
        repoDashboardInflightCache.delete(pending.repo.repo_full);
        closeOverviewRebaselineModal(true);
        await loadOverview(pending.repo.repo_full, payload?.dashboard || null);
    } finally {
        setOverviewRebaselineBusy(false);
    }
}

function bindOverviewJourneyActions(repo, repoPayload) {
    const snapshotById = new Map(asArray(repoPayload?.journey_snapshots).map((snapshot) => [String(snapshot.id), snapshot]));
    document.querySelectorAll("[data-overview-rebaseline]").forEach((button) => {
        if (button.dataset.boundOverviewRebaseline === "true") {
            return;
        }
        button.dataset.boundOverviewRebaseline = "true";
        button.addEventListener("click", (event) => {
            event.preventDefault();
            event.stopPropagation();
            const snapshot = snapshotById.get(String(button.getAttribute("data-overview-rebaseline") || ""));
            if (snapshot) {
                openOverviewRebaselineModal(repo, snapshot);
            }
        });
    });
}

function bindOverviewRebaselineModal() {
    document.querySelectorAll("[data-close-overview-rebaseline]").forEach((button) => {
        if (button.dataset.boundCloseOverviewRebaseline === "true") {
            return;
        }
        button.dataset.boundCloseOverviewRebaseline = "true";
        button.addEventListener("click", () => closeOverviewRebaselineModal());
    });
    const confirmButton = document.getElementById("overview-rebaseline-confirm-btn");
    if (confirmButton instanceof HTMLButtonElement && confirmButton.dataset.boundConfirmOverviewRebaseline !== "true") {
        confirmButton.dataset.boundConfirmOverviewRebaseline = "true";
        confirmButton.addEventListener("click", async () => {
            try {
                await submitOverviewRebaseline();
            } catch (error) {
                const message = error instanceof Error ? error.message : "Unable to re-baseline from this checkpoint.";
                window.alert(message);
            }
        });
    }
}

function populateOverviewStats(payload, attentionRepos, highestRiskItems, repos) {
    const approvedBaselineRepos = repos.filter((repo) => String(repo.onboarding_status || "") === "baseline_approved").length;
    setText("stat-needs-review", String(payload.risk_state?.review_now_repo_count || 0));
    setText("stat-high-risk", String(highestRiskItems.length));
    setText("stat-approved", String(approvedBaselineRepos));
    setText("repos-count", String(repos.length));
    setText("triage-count-summary", `${attentionRepos.length || highestRiskItems.length || repos.length} repositories in queue`);
}

async function fetchEscalationQueue(includeWatch = false) {
    const url = `/api/dashboard/escalation-queue${includeWatch ? "?include_watch=true" : ""}`;
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Escalation queue request failed with ${response.status}`);
    }
    return response.json();
}

function renderWorkspacePostureBar(queuePayload) {
    const posture = String(queuePayload?.workspace_posture || "healthy");
    const reasons = asArray(queuePayload?.workspace_posture_reasons);
    const escalationCount = Number(queuePayload?.escalation_count || 0);
    const watchCount = Number(queuePayload?.watch_count || 0);

    const postureClass = posture === "risk" ? "posture-bar-risk" : posture === "watch" ? "posture-bar-watch" : "posture-bar-healthy";
    const postureLabel = posture === "risk" ? "Risk" : posture === "watch" ? "Watch" : "Healthy";
    const reasonHtml = reasons.length
        ? `<span class="posture-bar-reasons">${reasons.map((r) => `<span class="posture-bar-reason">${escapeHtml(r)}</span>`).join("")}</span>`
        : "";
    const countsHtml = `<span class="posture-bar-counts"><strong>${escalationCount}</strong> escalation${escalationCount !== 1 ? "s" : ""} &middot; <strong>${watchCount}</strong> watch</span>`;

    return `
        <div class="posture-bar-inner ${postureClass}">
            <span class="posture-bar-indicator" aria-label="Workspace posture: ${escapeHtml(postureLabel)}">${escapeHtml(postureLabel)}</span>
            ${reasonHtml}
            ${countsHtml}
        </div>
    `;
}

function renderEscalationQueueRow(item, index) {
    const severity = severityForPriority(item.priority);
    const deltaHtml = asArray(item.attribute_deltas).slice(0, 2).map((delta) => `
        <span class="escalation-delta-chip">
            <span class="escalation-delta-label">${escapeHtml(delta.label || delta.attribute_key || "")}</span>
            <span class="escalation-delta-transition">${escapeHtml(`${delta.baseline_value || "?"} → ${delta.current_value || "?"}`)}</span>
        </span>
    `).join("");
    const artifactName = String(item.artifact_path || "").split("/").pop() || item.artifact_path || "artifact";
    const repoName = String(item.repo_full || "");
    const reviewHref = repoScopedDashboardUrl(item);
    const scopedReviewLabel = reviewScopeLabel(item);

    return `
        <div class="escalation-row" role="row" data-escalation-index="${index}" data-escalation-priority="${escapeHtml(item.priority)}">
            <div class="escalation-row-rank" role="cell"><span class="escalation-rank-num">${index + 1}</span></div>
            <div class="escalation-row-main" role="cell">
                <div class="escalation-row-top">
                    <strong class="escalation-title">${escapeHtml(item.title || artifactName)}</strong>
                    <span class="severity-badge ${severity.className}">${escapeHtml(severity.label)}</span>
                </div>
                <div class="escalation-row-repo">${escapeHtml(repoName)}</div>
                <div class="escalation-row-artifact">${escapeHtml(item.artifact_path || "")}</div>
                <div class="escalation-row-rationale">${escapeHtml(item.rationale || "")}</div>
                ${deltaHtml ? `<div class="escalation-deltas">${deltaHtml}</div>` : ""}
            </div>
            <div class="escalation-row-meta" role="cell">
                <span class="escalation-meta-label">${escapeHtml(item.evidence_label || "")}</span>
                <span class="escalation-meta-baseline">${escapeHtml(item.baseline_label || "")}</span>
                ${scopedReviewLabel ? `<span class="escalation-meta-context">Scoped ${escapeHtml(scopedReviewLabel)}</span>` : ""}
            </div>
            <div class="escalation-row-action" role="cell">
                <a class="escalation-action-btn" href="${escapeHtml(reviewHref)}" aria-label="Review ${escapeHtml(artifactName)}">${escapeHtml(item.recommended_action || "Review")}</a>
            </div>
        </div>
    `;
}

function renderEscalationQueueTable(queuePayload) {
    const items = asArray(queuePayload?.items);
    if (!items.length) {
        return '<div class="muted">No escalation items — workspace is healthy.</div>';
    }
    return `
        <div class="escalation-table-header" role="row">
            <span role="columnheader">#</span>
            <span role="columnheader">Finding</span>
            <span role="columnheader">Signal</span>
            <span role="columnheader">Action</span>
        </div>
        ${items.map((item, index) => renderEscalationQueueRow(item, index)).join("")}
    `;
}

async function loadEscalationQueue() {
    try {
        const queuePayload = await fetchEscalationQueue();
        const postureBarEl = document.getElementById("workspace-posture-bar");
        if (postureBarEl) {
            postureBarEl.innerHTML = renderWorkspacePostureBar(queuePayload);
            postureBarEl.classList.remove("loading-shell");
            postureBarEl.removeAttribute("aria-busy");
        }
        const countEl = document.getElementById("escalation-count-summary");
        if (countEl) {
            const escalationCount = Number(queuePayload?.escalation_count || 0);
            const watchCount = Number(queuePayload?.watch_count || 0);
            countEl.textContent = `${escalationCount} escalation${escalationCount !== 1 ? "s" : ""} · ${watchCount} watch`;
        }
        setSectionHtml("escalation-queue-table", renderEscalationQueueTable(queuePayload));
    } catch {
        const postureBarEl = document.getElementById("workspace-posture-bar");
        if (postureBarEl) {
            postureBarEl.innerHTML = '<div class="posture-bar-inner posture-bar-healthy"><span class="posture-bar-indicator">Healthy</span></div>';
            postureBarEl.classList.remove("loading-shell");
            postureBarEl.removeAttribute("aria-busy");
        }
        setSectionHtml("escalation-queue-table", '<div class="muted">Escalation queue unavailable.</div>');
    }
}

async function loadOverview(preferredRepoFull = null, preferredRepoPayload = null) {
    try {
        const page = document.body;
        const url = new URL("/api/dashboard/overview", window.location.origin);
        const activeOverviewRange = page?.dataset?.activeOverviewRange || "7d";
        const activeOverviewFilter = page?.dataset?.activeOverviewFilter || "all";
        url.searchParams.set("range", activeOverviewRange);
        url.searchParams.set("filter", activeOverviewFilter);
        const response = await fetch(`${url.pathname}${url.search}`);
        if (!response.ok) {
            throw new Error(`Overview request failed with ${response.status}`);
        }

        const payload = await response.json();
        const highestRiskItems = asArray(payload.highest_risk_items);
        const attentionRepos = asArray(payload.attention_repos);
        const repos = asArray(payload.repos);
        const navRepos = asArray(payload.nav_repos).length ? asArray(payload.nav_repos) : repos;
        const sections = overviewSections(payload);
        const governanceAttention = sections.governance_attention || null;
        const groupedUrgentRepos = sections.urgent_queue && Array.isArray(sections.urgent_queue.repos)
            ? asArray(sections.urgent_queue.repos)
            : null;
        const groupedRecentRepos = sections.recent_changes && Array.isArray(sections.recent_changes.repos)
            ? asArray(sections.recent_changes.repos)
            : null;
        const selectionItems = groupedRecentRepos || buildSelectionItems(repos, attentionRepos, highestRiskItems);
        const visibleSelectionItems = groupedUrgentRepos || selectionItems.slice(0, 4);
        const repoAtlasItems = groupedRecentRepos || selectionItems;

        populateOverviewStats(payload, attentionRepos, highestRiskItems, repos);
        setText("governance-attention-headline", overviewGovernanceHeadline(governanceAttention));
        setText("governance-attention-copy", overviewGovernanceCopy(governanceAttention));
        setSectionHtml("governance-attention-list", renderOverviewGovernanceAttention(governanceAttention));
        setSectionHtml(
            "triage-list",
            visibleSelectionItems.length
                ? visibleSelectionItems.map((repo, index) => renderUrgentRow(repo, index)).join("")
                : '<div class="muted">No urgent review items are available yet.</div>'
        );
        setSectionHtml(
            "repo-atlas-grid",
            repoAtlasItems.length
                ? repoAtlasItems.map((repo, index) => renderRepoAtlasCard(repo, index)).join("")
                : '<div class="muted">No repositories are available for overview preview yet.</div>'
        );
        bindUrgentRows(visibleSelectionItems, repos);
        bindRepoAtlasCards(repoAtlasItems, repos);

        // Populate Audit Logs repo list (collapsible nav)
        try {
            const auditListEl = document.getElementById("audit-logs-list");
            if (auditListEl) {
                if (navRepos.length === 0) {
                    setSectionHtml("audit-logs-list", '<div class="muted">No repositories available</div>');
                } else {
                    const items = navRepos.map((r) => `
                        <a class="sidebar-subitem" href="${repoDetailUrl(r)}">${escapeHtml(r.repo_full)}</a>
                    `).join("");
                    setSectionHtml("audit-logs-list", `<nav class=\"sidebar-sublist-nav\">${items}</nav>`);
                }
            }
        } catch (e) {
            // ignore failures populating nav
        }

        if (selectionItems.length) {
            const preferredIndex = preferredRepoFull
                ? selectionItems.findIndex((repo) => repo.repo_full === preferredRepoFull)
                : -1;
            const selectedIndex = preferredIndex >= 0 ? preferredIndex : 0;
            const selectedRepo = selectionItems[selectedIndex];
            previewState.pinnedRepoFull = selectedRepo.repo_full;
            previewState.pendingRepoFull = null;
            const urgentIndex = visibleSelectionItems.findIndex((repo) => repo.repo_full === selectedRepo.repo_full);
            if (urgentIndex >= 0) {
                selectUrgentRow(urgentIndex);
            }
            try {
                const firstPayload = preferredRepoPayload && selectedRepo.repo_full === preferredRepoFull
                    ? preferredRepoPayload
                    : await fetchRepoDashboard(selectedRepo.repo_full);
                if (previewState.activeRepoFull === selectedRepo.repo_full || previewState.pendingRepoFull === selectedRepo.repo_full || previewState.activeRepoFull === null) {
                    applyRepoPreview(selectedRepo, repos, firstPayload);
                }
            } catch {
                applyRepoPreview(selectedRepo, repos, null);
            }
        } else {
            setSectionHtml("repo-journey-strip", '<div class="muted">No version journey data is available.</div>');
            setSectionHtml("coverage-bars", '<div class="muted">No coverage data is available.</div>');
            setSectionHtml("repo-atlas-grid", '<div class="muted">No repositories are available for overview preview yet.</div>');
            setHtml("detail-summary", '<div class="muted">Populate the workspace with onboarded repositories to see drift context.</div>');
            setText("detail-repo-name", "No repository selected");
            setText("detail-subtitle", "Overview is ready once repositories are onboarded.");
            setText("detail-recommendation-body", "Onboard a repository to populate the triage queue.");
            setText("repo-radar-title", "No repository selected");
            setText("journey-repo-title", "No repository selected");
            setText("journey-repo-name", "No repository selected");
            setText("repo-radar-meta", "Populate the workspace with onboarded repositories to see posture tracking.");
            setText("coverage-note", "Coverage summary unavailable");
            setText("drift-ring-value", "--%");
            drawDriftRing(0);
            setText("selected-repo-summary", "No repository is currently selected.");
        }
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown overview error";
        const fallback = `<div class="muted">Unable to load dashboard overview. ${escapeHtml(message)}</div>`;
        setText("stat-needs-review", "-");
        setText("stat-high-risk", "-");
        setText("stat-approved", "-");
        setText("repos-count", "Unavailable");
        setSectionHtml("triage-list", fallback);
        setSectionHtml("repo-atlas-grid", fallback);
        setSectionHtml("repo-journey-strip", fallback);
        setSectionHtml("coverage-bars", fallback);
        setText("governance-attention-headline", "Governance attention unavailable");
        setText("governance-attention-copy", message);
        setSectionHtml("governance-attention-list", fallback);
        setHtml("detail-summary", fallback);
        setText("detail-repo-name", "Overview unavailable");
        setText("detail-subtitle", message);
        setText("detail-recommendation-body", message);
        setText("repo-radar-title", "Overview unavailable");
        setText("journey-repo-title", "Overview unavailable");
        setText("journey-repo-name", "Overview unavailable");
        setText("repo-radar-meta", message);
        setText("repo-journey-note", message);
        setText("coverage-note", message);
        setText("drift-ring-value", "--%");
        drawDriftRing(0);
        setText("selected-repo-summary", message);
    }
}

    bindOverviewRebaselineModal();
if (dashboardShellState() === "active") {
    loadOverview();
    loadEscalationQueue();
} else {
    renderBlockedOverviewShell();
}

// Audit Logs toggle behavior: expand/collapse the repo list
function bindAuditLogsToggle() {
    const toggle = document.getElementById("audit-logs-toggle");
    const list = document.getElementById("audit-logs-list");
    if (!toggle || !list || toggle.dataset.boundToggle === "true") {
        return;
    }
    toggle.dataset.boundToggle = "true";
    toggle.addEventListener("click", () => {
        const expanded = list.hasAttribute("hidden") ? false : true;
        if (expanded) {
            list.setAttribute("hidden", "true");
            toggle.setAttribute("aria-expanded", "false");
        } else {
            list.removeAttribute("hidden");
            toggle.setAttribute("aria-expanded", "true");
        }
    });
}
bindAuditLogsToggle();
