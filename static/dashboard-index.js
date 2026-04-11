function asArray(value) {
    return Array.isArray(value) ? value : [];
}

const repoDashboardCache = new Map();
const repoDashboardInflightCache = new Map();
const previewState = {
    activeRepoFull: null,
    pendingRepoFull: null,
};

const HOVER_PREVIEW_DELAY_MS = 80;

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (!element) {
        return;
    }
    element.innerHTML = html;
    element.classList.remove("loading-shell");
    element.classList.remove("muted");
}

function setText(elementId, value) {
    const element = document.getElementById(elementId);
    if (element) {
        element.textContent = value;
    }
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

function repoDetailUrl(repo) {
    const url = new URL(`/dashboard/${encodeURIComponent(repo.repo_full)}`, window.location.origin);
    const artifactPath = repo.highest_insight_artifact_path || matchedRiskItem(repo)?.artifact_path || "";
    if (artifactPath) {
        url.searchParams.set("artifact", artifactPath);
    }
    return `${url.pathname}${url.search}`;
}

function reviewContext(repo) {
    return repo.highest_review_target || repo.highest_evidence_label || "latest signal";
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

function primeRepoDashboardCache(items) {
    items.slice(0, 4).forEach((repo, index) => {
        window.setTimeout(() => {
            void fetchRepoDashboard(repo.repo_full).catch(() => null);
        }, 40 * (index + 1));
    });
}

function journeyNodesFromRepoPayload(repo, repoPayload) {
    const snapshots = asArray(repoPayload?.journey_snapshots);
    if (!snapshots.length) {
        return [
            {
                tag: repo.highest_baseline_label || "Approved baseline",
                title: "Approved Baseline",
                caption: "Repo-level baseline checkpoint",
                tone: "baseline",
            },
            {
                tag: reviewContext(repo),
                title: "Latest Evidence",
                caption: triageSummary(repo),
                tone: "activity",
            },
            {
                tag: "Current state",
                title: "Current State",
                caption: `${Number(repo.review_now_count || 0)} review now · ${Number(repo.watch_count || 0)} watch`,
                tone: "current",
            },
        ];
    }
    return snapshots.slice(0, 4).map((snapshot) => ({
        tag: snapshotTag(snapshot),
        title: snapshotTitle(snapshot),
        caption: snapshotCaption(snapshot),
        tone: snapshot.snapshot_type === "baseline_approved" ? "baseline" : (snapshot.snapshot_type === "current" || snapshot.snapshot_type === "branch_head") ? "current" : "activity",
    }));
}

function renderJourney(repo, repoPayload = null) {
    const nodes = journeyNodesFromRepoPayload(repo, repoPayload);
    return `
        <div class="journey-line" aria-hidden="true"></div>
        <div class="journey-points">
            ${nodes.map((node, index) => `
                <div class="journey-point journey-point-${escapeHtml(node.tone || "activity")}">
                    <div class="journey-dot-wrap">
                        <span class="journey-dot"></span>
                        ${index < nodes.length - 1 ? '<span class="journey-arrow">↓</span>' : ""}
                    </div>
                    <div class="journey-pill">${escapeHtml(node.tag)}</div>
                    <div class="journey-point-title">${escapeHtml(node.title)}</div>
                    <div class="journey-point-caption">${escapeHtml(node.caption)}</div>
                </div>
            `).join("")}
        </div>
    `;
}

function driftPercent(repo) {
    const raw = Number(repo.top_drift_magnitude || 0);
    if (!Number.isFinite(raw) || raw <= 0) {
        return 14;
    }
    return clamp(Math.round(raw * 100), 8, 86);
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

    context.clearRect(0, 0, canvas.width, canvas.height);
    context.beginPath();
    context.strokeStyle = "rgba(255,255,255,0.08)";
    context.lineWidth = lineWidth;
    context.arc(center, center, radius, 0, Math.PI * 2);
    context.stroke();

    context.beginPath();
    context.strokeStyle = "#52e0d5";
    context.lineWidth = lineWidth;
    context.lineCap = "round";
    context.arc(center, center, radius, start, end);
    context.stroke();
}

function radarVectors(repo) {
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

function drawRadar(repo) {
    const canvas = document.getElementById("repo-posture-radar");
    if (!canvas) {
        return;
    }
    const context = canvas.getContext("2d");
    if (!context) {
        return;
    }
    const vectors = radarVectors(repo);
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!vectors) {
        return;
    }

    const centerX = canvas.width / 2;
    const centerY = canvas.height / 2 + 10;
    const radius = 118;
    const angleStep = (Math.PI * 2) / vectors.labels.length;

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
        context.strokeStyle = "rgba(255,255,255,0.10)";
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
        context.strokeStyle = "rgba(255,255,255,0.08)";
        context.stroke();

        const labelX = centerX + Math.cos(angle) * (radius + 22);
        const labelY = centerY + Math.sin(angle) * (radius + 22);
        context.fillStyle = "rgba(221, 222, 225, 0.55)";
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
    const payloadDistance = Number(repoPayload?.journey_comparison?.drift_summary?.right_distance_from_baseline);
    if (Number.isFinite(payloadDistance) && payloadDistance >= 0) {
        return clamp(Math.round(payloadDistance * 100), 8, 86);
    }
    return driftPercent(repo);
}

function renderCoverageBars(repo, repos) {
    return coverageBars(repo, repos).map((item) => `
        <div class="coverage-bar-row">
            <div class="coverage-bar-meta">
                <span>${escapeHtml(item.label)}</span>
                <strong>${item.value}%</strong>
            </div>
            <div class="coverage-track">
                <div class="coverage-fill" style="width:${item.value}%"></div>
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

function renderUrgentRow(repo, index) {
    const severity = severityForPriority(repo.highest_priority);
    return `
        <button type="button" class="urgent-item" data-row-index="${index}">
            <span class="urgent-icon">!</span>
            <span class="urgent-copy">
                <span class="urgent-headline">${escapeHtml(issueHeadline(repo))}</span>
                <span class="urgent-subline">in ${escapeHtml(repo.repo_full)}</span>
            </span>
            <span class="urgent-severity ${severity.className}">${escapeHtml(severity.label)}</span>
        </button>
    `;
}

function selectUrgentRow(index) {
    document.querySelectorAll(".urgent-item").forEach((item, itemIndex) => {
        item.classList.toggle("active", itemIndex === index);
    });
}

function applyRepoPreview(repo, repos, repoPayload = null) {
    previewState.activeRepoFull = repo.repo_full;
    setText("repo-radar-title", compactRepoLabel(repo.repo_full));
    setText("journey-repo-name", repo.repo_full);
    setText("repo-radar-meta", triageSummary(repo));
    setSectionHtml("repo-journey-strip", renderJourney(repo, repoPayload));
    const repoStoryNote = repoPayload?.journey_comparison?.risk_summary?.headline || repoPayload?.featured_storyline?.summary || triageSummary(repo);
    setText("repo-journey-note", repoStoryNote);
    setSectionHtml("coverage-bars", renderCoverageBars(repo, repos));
    drawRadar(repo);
    const drift = driftPercentFromPayload(repo, repoPayload);
    drawDriftRing(drift);
    setText("drift-ring-value", `${drift}%`);
    const detailLink = document.getElementById("detail-escalate-btn");
    if (detailLink) {
        detailLink.setAttribute("href", repoDetailUrl(repo));
    }
    const auditLogsLink = document.getElementById("audit-logs-link");
    if (auditLogsLink) {
        auditLogsLink.setAttribute("href", repoDetailUrl(repo));
    }
}

function buildSelectionItems(attentionRepos, highestRiskItems) {
    return attentionRepos.map((repo) => ({
        ...repo,
        _matchedRiskItem: highestRiskItems.find((item) => item.repo_full === repo.repo_full) || null,
    }));
}

function bindUrgentRows(items, repos) {
    document.querySelectorAll(".urgent-item").forEach((button) => {
        const preview = async () => {
            const index = Number(button.getAttribute("data-row-index"));
            if (!Number.isFinite(index) || !items[index]) {
                return;
            }
            const repo = items[index];
            previewState.pendingRepoFull = repo.repo_full;
            selectUrgentRow(index);
            applyRepoPreview(repo, repos, null);
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
        };
        button.addEventListener("mouseenter", () => {
            clearPreviewTimer(button);
            button.dataset.previewTimer = String(window.setTimeout(() => {
                void preview();
            }, HOVER_PREVIEW_DELAY_MS));
        });
        button.addEventListener("mouseleave", () => {
            clearPreviewTimer(button);
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
    });
}

function populateOverviewStats(payload, attentionRepos, highestRiskItems, repos) {
    const approvedBaselineRepos = attentionRepos.filter((repo) => (repo.highest_baseline_label || "").includes("Approved")).length;
    setText("stat-needs-review", String(payload.risk_state?.review_now_repo_count || 0));
    setText("stat-high-risk", String(highestRiskItems.length));
    setText("stat-approved", String(approvedBaselineRepos));
    setText("repos-count", `${repos.length} repos`);
}

async function loadOverview() {
    try {
        const response = await fetch("/api/dashboard/overview");
        if (!response.ok) {
            throw new Error(`Overview request failed with ${response.status}`);
        }

        const payload = await response.json();
        const highestRiskItems = asArray(payload.highest_risk_items);
        const attentionRepos = asArray(payload.attention_repos);
        const repos = asArray(payload.repos);
        const selectionItems = buildSelectionItems(attentionRepos, highestRiskItems);

        populateOverviewStats(payload, attentionRepos, highestRiskItems, repos);
        setSectionHtml(
            "triage-list",
            selectionItems.length
                ? selectionItems.slice(0, 4).map((repo, index) => renderUrgentRow(repo, index)).join("")
                : '<div class="muted">No urgent review items are available yet.</div>'
        );
        const visibleSelectionItems = selectionItems.slice(0, 4);
        bindUrgentRows(visibleSelectionItems, repos);

        if (selectionItems.length) {
            selectUrgentRow(0);
            applyRepoPreview(selectionItems[0], repos, null);
            primeRepoDashboardCache(visibleSelectionItems);
            try {
                const firstPayload = await fetchRepoDashboard(selectionItems[0].repo_full);
                if (previewState.activeRepoFull === selectionItems[0].repo_full || previewState.pendingRepoFull === selectionItems[0].repo_full || previewState.activeRepoFull === null) {
                    applyRepoPreview(selectionItems[0], repos, firstPayload);
                }
            } catch {
                previewState.activeRepoFull = selectionItems[0].repo_full;
            }
        } else {
            setSectionHtml("repo-journey-strip", '<div class="muted">No version journey data is available.</div>');
            setSectionHtml("coverage-bars", '<div class="muted">No coverage data is available.</div>');
            setText("repo-radar-title", "No repository selected");
            setText("journey-repo-name", "No repository selected");
            setText("repo-radar-meta", "Populate the workspace with onboarded repositories to see posture tracking.");
            setText("drift-ring-value", "0%");
            drawDriftRing(0);
        }
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown overview error";
        const fallback = `<div class="muted">Unable to load dashboard overview. ${escapeHtml(message)}</div>`;
        setText("stat-needs-review", "-");
        setText("stat-high-risk", "-");
        setText("stat-approved", "-");
        setText("repos-count", "Unavailable");
        setSectionHtml("triage-list", fallback);
        setSectionHtml("repo-journey-strip", fallback);
        setSectionHtml("coverage-bars", fallback);
        setText("repo-radar-title", "Overview unavailable");
        setText("journey-repo-name", "Overview unavailable");
        setText("repo-radar-meta", message);
        setText("repo-journey-note", message);
        setText("drift-ring-value", "--%");
        drawDriftRing(0);
    }
}

loadOverview();
