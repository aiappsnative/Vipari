function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
}

function metricGlyph(label) {
    const normalized = label.toLowerCase();
    if (normalized.includes("review") || normalized.includes("risk")) {
        return "⚑";
    }
    if (normalized.includes("baseline")) {
        return "◆";
    }
    if (normalized.includes("artifact")) {
        return "◈";
    }
    if (normalized.includes("pr")) {
        return "↗";
    }
    if (normalized.includes("semantic") || normalized.includes("distance")) {
        return "≈";
    }
    return "●";
}

function renderMetricGlyphCard(metric) {
    return `
        <div class="metric-glyph-card">
            <div class="metric-glyph-badge">${metricGlyph(metric.label)}</div>
            <div class="metric-glyph-copy">
                <div class="metric-glyph-label">${metric.label}</div>
                <div class="metric-glyph-value">${metric.value}</div>
                <div class="metric-glyph-detail">${metric.detail}</div>
            </div>
        </div>
    `;
}

function asArray(value) {
    return Array.isArray(value) ? value : [];
}

function setSectionHtml(elementId, html) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = html;
    }
}

function setMode(mode) {
    document.querySelectorAll("[data-dashboard-mode]").forEach((button) => {
        const active = button.getAttribute("data-dashboard-mode") === mode;
        button.classList.toggle("mode-button-active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
    });

    document.querySelectorAll("[data-mode-panel]").forEach((panel) => {
        panel.hidden = panel.getAttribute("data-mode-panel") !== mode;
    });
}

function bindModeSwitcher() {
    document.querySelectorAll("[data-dashboard-mode]").forEach((button) => {
        button.addEventListener("click", () => {
            setMode(button.getAttribute("data-dashboard-mode") || "triage");
        });
    });
}

function renderReasonPills(labels = []) {
    return labels.map((label) => `<span class="tag tag-muted">${label}</span>`).join("");
}

function priorityGlyph(priority) {
    if (priority === "review_now") {
        return "⚑";
    }
    if (priority === "watch") {
        return "◉";
    }
    return "◆";
}

function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function priorityWeight(priority) {
    return priority === "review_now" ? 1 : priority === "watch" ? 0.62 : 0.34;
}

function createRingMeter({ value = 0, label = "", tone = "accent", size = 136, stroke = 12, centerLabel = "" }) {
    const normalized = clamp(value, 0, 1);
    const radius = (size - stroke) / 2;
    const circumference = 2 * Math.PI * radius;
    const dash = circumference * normalized;
    return `
        <svg viewBox="0 0 ${size} ${size}" class="ring-meter ring-meter-${tone}" aria-label="${label}">
            <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" class="ring-meter-track"></circle>
            <circle cx="${size / 2}" cy="${size / 2}" r="${radius}" class="ring-meter-value" stroke-dasharray="${dash} ${circumference - dash}" stroke-dashoffset="${circumference * 0.25}"></circle>
            <text x="50%" y="48%" text-anchor="middle" class="ring-meter-center">${centerLabel}</text>
            <text x="50%" y="63%" text-anchor="middle" class="ring-meter-caption">${label}</text>
        </svg>
    `;
}

function linkedText(label, href) {
    if (!label) {
        return "";
    }
    return href ? `<a class="link" href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>` : label;
}

function renderBriefRows({ changeSummary, flagSummary, whereLabel, whereUrl, allowLinks = true }) {
    const rows = [
        changeSummary ? `<div class="brief-row"><span class="brief-label">What changed</span><span class="brief-copy">${changeSummary}</span></div>` : "",
        flagSummary ? `<div class="brief-row"><span class="brief-label">Why flagged</span><span class="brief-copy">${flagSummary}</span></div>` : "",
        whereLabel ? `<div class="brief-row"><span class="brief-label">Where</span><span class="brief-copy">${allowLinks ? linkedText(whereLabel, whereUrl) : whereLabel}</span></div>` : "",
    ].filter(Boolean);
    if (!rows.length) {
        return "";
    }
    return `<div class="brief-panel">${rows.join("")}</div>`;
}

function renderMiniSignalRow(repo, rank) {
    const intensity = clamp(priorityWeight(repo.highest_priority) + Math.min(repo.review_now_count * 0.08, 0.24), 0.12, 1);
    const barWidth = Math.round(intensity * 100);
    return `
        <a class="signal-row" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
            <div class="signal-rank">#${rank}</div>
            <div class="signal-main">
                <div class="signal-title-row">
                    <strong><span class="signal-glyph signal-glyph-${repo.highest_priority}">${priorityGlyph(repo.highest_priority)}</span>${repo.repo_full}</strong>
                    <span class="priority priority-${repo.highest_priority}">${repo.highest_priority.replace("_", " ")}</span>
                </div>
                <div class="signal-bar"><span style="width:${barWidth}%"></span></div>
                <div class="signal-subline">${repo.highest_insight_title || "No prioritized insight yet"}</div>
            </div>
            <div class="signal-meta">${repo.highest_evidence_label || repo.highest_review_target || "Open"}</div>
        </a>
    `;
}

function renderHotspotField(items = []) {
    if (!items.length) {
        return '<div class="muted">No hotspot field yet.</div>';
    }
    const maxDrift = Math.max(...items.map((item) => item.drift_magnitude || 0), 0.001);
    return `
        <div class="hotspot-field">
            ${items.slice(0, 6).map((item, index) => {
                const size = 56 + ((item.drift_magnitude || 0) / maxDrift) * 54;
                const x = [12, 56, 32, 74, 18, 64][index] || 40;
                const y = [12, 18, 54, 58, 78, 84][index] || 50;
                return `
                    <a class="hotspot-bubble hotspot-priority-${item.priority}" href="/dashboard/${encodeURIComponent(item.repo_full)}" style="left:${x}%; top:${y}%; width:${size}px; height:${size}px;">
                        <span class="hotspot-bubble-label">${item.repo_full.split("/").pop()}</span>
                    </a>
                `;
            }).join("")}
        </div>
    `;
}

function renderTrustRadar({ attentionRepos = [], controlSurfaceCoverage = [], repos = [] }) {
    const reviewNowRepos = attentionRepos.filter((repo) => repo.review_now_count > 0).length;
    const lowerConfidenceCount = attentionRepos.reduce((sum, repo) => sum + (repo.lower_confidence_count || 0), 0);
    const highConfidenceArtifacts = controlSurfaceCoverage.reduce((sum, group) => sum + (group.high_confidence_count || 0), 0);
    const baselineReady = clamp(attentionRepos.filter((repo) => (repo.highest_baseline_label || "").includes("Approved")).length / Math.max(repos.length, 1), 0, 1);
    const confidenceRatio = clamp(1 - lowerConfidenceCount / Math.max(attentionRepos.length + lowerConfidenceCount, 1), 0, 1);
    const coverageRatio = clamp(highConfidenceArtifacts / Math.max(controlSurfaceCoverage.reduce((sum, group) => sum + (group.artifact_count || 0), 0), 1), 0, 1);
    const urgencyRatio = clamp(reviewNowRepos / Math.max(repos.length, 1), 0, 1);
    return `
        <div class="trust-radar-grid">
            <div class="trust-radar-tile">
                ${createRingMeter({ value: urgencyRatio, label: "Urgency", tone: "warning", centerLabel: String(reviewNowRepos) })}
            </div>
            <div class="trust-radar-tile">
                ${createRingMeter({ value: confidenceRatio, label: "Confidence", tone: "success", centerLabel: `${Math.round(confidenceRatio * 100)}%` })}
            </div>
            <div class="trust-radar-tile">
                ${createRingMeter({ value: coverageRatio, label: "Coverage", tone: "accent", centerLabel: String(highConfidenceArtifacts) })}
            </div>
            <div class="trust-radar-tile">
                ${createRingMeter({ value: baselineReady, label: "Baseline", tone: "violet", centerLabel: `${Math.round(baselineReady * 100)}%` })}
            </div>
        </div>
    `;
}

function renderSignalSkyline(items = []) {
    if (!items.length) {
        return '<div class="muted">No repo skyline yet.</div>';
    }
    const maxReviewNow = Math.max(...items.map((item) => item.review_now_count || 0), 1);
    return `
        <div class="signal-skyline">
            ${items.slice(0, 8).map((item) => {
                const height = 26 + ((item.review_now_count || 0) / maxReviewNow) * 86;
                return `
                    <a class="skyline-bar-wrap" href="/dashboard/${encodeURIComponent(item.repo_full)}">
                        <div class="skyline-bar skyline-bar-${item.highest_priority}" style="height:${height}px"></div>
                        <div class="skyline-label">${item.repo_full.split("/").pop()}</div>
                    </a>
                `;
            }).join("")}
        </div>
    `;
}

function renderLaneSpectrum(items = []) {
    const reviewNow = items.filter((item) => item.highest_priority === "review_now").length;
    const watch = items.filter((item) => item.highest_priority === "watch").length;
    const baseline = Math.max(items.length - reviewNow - watch, 0);
    const total = Math.max(items.length, 1);
    return `
        <div class="spectrum-wrap">
            <div class="lane-spectrum">
                <span class="lane-segment lane-segment-review" style="width:${(reviewNow / total) * 100}%"></span>
                <span class="lane-segment lane-segment-watch" style="width:${(watch / total) * 100}%"></span>
                <span class="lane-segment lane-segment-baseline" style="width:${(baseline / total) * 100}%"></span>
            </div>
            <div class="lane-spectrum-legend">
                <span><strong>${reviewNow}</strong> review now</span>
                <span><strong>${watch}</strong> watch</span>
                <span><strong>${baseline}</strong> baseline</span>
            </div>
        </div>
    `;
}

function renderReviewFlow({ attentionRepos = [], highestRiskItems = [], repos = [] }) {
    const reviewNow = attentionRepos.filter((item) => item.highest_priority === "review_now").length;
    const totalRepos = repos.length;
    const hotspotCount = highestRiskItems.length;
    return `
        <div class="review-flow">
            <div class="flow-node">
                <div class="flow-node-value">${totalRepos}</div>
                <div class="flow-node-label">tracked repos</div>
            </div>
            <div class="flow-link"></div>
            <div class="flow-node flow-node-emphasis">
                <div class="flow-node-value">${reviewNow}</div>
                <div class="flow-node-label">urgent lanes</div>
            </div>
            <div class="flow-link"></div>
            <div class="flow-node">
                <div class="flow-node-value">${hotspotCount}</div>
                <div class="flow-node-label">cross-repo hotspots</div>
            </div>
        </div>
    `;
}

function renderPortfolioPulse({ attentionRepos = [], highestRiskItems = [], controlSurfaceCoverage = [], repos = [], riskState = null }) {
    return `
        <div class="pulseboard-grid">
            <div class="card pulse-panel">
                <div class="section-kicker">Portfolio skyline</div>
                <h2>Where attention stacks up</h2>
                ${renderSignalSkyline(attentionRepos)}
            </div>
            <div class="card pulse-panel">
                <div class="section-kicker">Lane mix</div>
                <h2>How the queue is distributed</h2>
                ${renderLaneSpectrum(attentionRepos)}
                ${riskState ? `<div class="meta-tight muted">${riskState.summary}</div>` : ""}
            </div>
            <div class="card pulse-panel">
                <div class="section-kicker">Flow</div>
                <h2>What needs filtering next</h2>
                ${renderReviewFlow({ attentionRepos, highestRiskItems, repos })}
                <div class="meta-tight muted">${controlSurfaceCoverage.length} control-surface groups currently contribute monitored evidence.</div>
            </div>
        </div>
    `;
}

function renderCoverageAtlas(items = []) {
    if (!items.length) {
        return '<div class="muted">No coverage atlas yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count || 0), 1);
    return `<div class="atlas-grid">${items.map((item) => {
        const intensity = (item.artifact_count || 0) / maxArtifacts;
        const confidence = clamp((item.high_confidence_count || 0) / Math.max(item.artifact_count || 1, 1), 0, 1);
        return `
            <div class="atlas-tile">
                <div class="atlas-tile-head">
                    <strong>${item.label}</strong>
                    <span class="muted">${item.repo_count} repos</span>
                </div>
                <div class="atlas-tile-glow" style="opacity:${0.25 + intensity * 0.75}"></div>
                <div class="atlas-meter"><span style="width:${confidence * 100}%"></span></div>
                <div class="atlas-caption">${item.artifact_count} tracked · ${item.high_confidence_count} high confidence</div>
            </div>
        `;
    }).join("")}</div>`;
}

function renderRiskState(riskState) {
    if (!riskState) {
        return '<div class="hero-panel-inner hero-status-baseline"><div><div class="priority priority-baseline_review">baseline</div><h2 class="hero-title">Portfolio risk state unavailable</h2><p class="hero-copy">The overview payload is missing a risk-state summary. Refresh after the API finishes loading.</p></div></div>';
    }
    const statusLabel = riskState.status.replace("_", " ");
    const focus = [riskState.highest_risk_repo_full, riskState.highest_risk_title, riskState.highest_risk_artifact_path]
        .filter(Boolean)
        .join(" · ");
    const total = Math.max(
        (riskState.review_now_repo_count || 0) + (riskState.watch_repo_count || 0) + (riskState.baseline_review_repo_count || 0),
        1,
    );
    const heat = clamp(((riskState.review_now_repo_count || 0) * 1 + (riskState.watch_repo_count || 0) * 0.55 + (riskState.baseline_review_repo_count || 0) * 0.2) / total, 0, 1);
    return `
        <div class="hero-panel-inner hero-status-${riskState.status}">
            <div class="hero-visual-grid">
                <div>
                    <div class="priority priority-${riskState.review_now_repo_count > 0 ? "review_now" : riskState.watch_repo_count > 0 ? "watch" : "baseline_review"}">${statusLabel}</div>
                    <h2 class="hero-title">${riskState.headline}</h2>
                    <p class="hero-copy">${riskState.summary}</p>
                    <div class="hero-state-strip">
                        <span class="hero-state-chip hero-state-chip-review"><strong>${riskState.review_now_repo_count}</strong> review now</span>
                        <span class="hero-state-chip hero-state-chip-watch"><strong>${riskState.watch_repo_count}</strong> watch</span>
                        <span class="hero-state-chip hero-state-chip-baseline"><strong>${riskState.baseline_review_repo_count}</strong> baseline</span>
                    </div>
                </div>
                <div class="hero-orbit">
                    ${createRingMeter({ value: heat, label: "Portfolio heat", tone: riskState.review_now_repo_count > 0 ? "warning" : riskState.watch_repo_count > 0 ? "accent" : "success", size: 160, stroke: 14, centerLabel: `${Math.round(heat * 100)}%` })}
                </div>
            </div>
            <div class="hero-beam">
                <span class="hero-beam-segment hero-beam-review" style="width:${(riskState.review_now_repo_count / total) * 100}%"></span>
                <span class="hero-beam-segment hero-beam-watch" style="width:${(riskState.watch_repo_count / total) * 100}%"></span>
                <span class="hero-beam-segment hero-beam-baseline" style="width:${(riskState.baseline_review_repo_count / total) * 100}%"></span>
            </div>
            <div class="hero-focus muted">
                <strong>Open next:</strong><br>
                ${focus || "No immediate hotspot yet"}
                ${riskState.highest_drift_magnitude ? `<br>Top recorded drift ${riskState.highest_drift_magnitude.toFixed(3)}` : ""}
            </div>
        </div>
    `;
}

function renderAttentionRepos(items = [], startRank = 1) {
    if (!items.length) {
        return '<div class="muted">No repo priorities yet. Onboard a repository to populate the decision queue.</div>';
    }
    return `<div class="stack">${items
        .map(
            (repo, index) => `
                <a class="attention-link triage-card ${index === 0 ? "triage-card-featured" : index < 3 ? "triage-card-priority" : ""}" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
                    <div class="triage-card-header">
                        <div>
                            <div class="triage-rank-row">
                                <span class="rank-badge">#${startRank + index}</span>
                                <div class="priority priority-${repo.highest_priority}">${repo.highest_priority.replace("_", " ")}</div>
                            </div>
                            <div class="insight-title">${repo.repo_full}</div>
                        </div>
                        <div class="meta-tight muted">${repo.highest_review_target || "open repo detail"}</div>
                    </div>
                    <div class="triage-card-body">
                        <div class="triage-summary">${repo.highest_insight_title || "No prioritized repo insight yet"}</div>
                        <div class="meta-tight muted">${repo.highest_insight_artifact_path || "No lead artifact yet"}</div>
                        ${repo.highest_evidence_label ? `<div class="meta-tight"><strong>${repo.highest_evidence_label}</strong></div>` : ""}
                        ${repo.highest_baseline_label ? `<div class="meta-tight"><strong>${repo.highest_baseline_label}</strong></div>` : ""}
                        ${renderBriefRows({
                            changeSummary: repo.highest_change_summary,
                            flagSummary: repo.highest_flag_summary,
                            whereLabel: repo.highest_review_target,
                            whereUrl: repo.highest_review_url,
                            allowLinks: false,
                        })}
                        ${repo.highest_evidence_summary ? `<div class="meta-tight muted">${repo.highest_evidence_summary}</div>` : ""}
                        ${repo.highest_rationale ? `<div class="meta-tight muted">${repo.highest_rationale}</div>` : ""}
                        ${repo.highest_recommended_action ? `<div class="meta-tight">${repo.highest_recommended_action}</div>` : ""}
                    </div>
                    <div class="triage-card-footer triage-card-footer-split">
                        <span class="muted">Review now: ${repo.review_now_count} · Watch: ${repo.watch_count} · Lower confidence: ${repo.lower_confidence_count}</span>
                        <span class="cta-link">Open repo triage →</span>
                    </div>
                </a>
            `
        )
        .join("")}</div>`;
}

function renderPrimaryReviewFocus(repo) {
    if (!repo) {
        return '<div class="muted">No primary review target yet.</div>';
    }
    const score = clamp(priorityWeight(repo.highest_priority) + Math.min(repo.review_now_count * 0.1, 0.3), 0.15, 1);
    return `
        <a class="attention-link focus-card" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
            <div class="focus-grid">
                <div>
                    <div class="section-kicker">#1 next repo</div>
                    <div class="focus-title"><span class="signal-glyph signal-glyph-${repo.highest_priority}">${priorityGlyph(repo.highest_priority)}</span>${repo.repo_full}</div>
                    <div class="focus-summary">${repo.highest_insight_title || "No prioritized insight yet"}</div>
                </div>
                <div class="focus-meter-wrap">
                    ${createRingMeter({ value: score, label: repo.highest_priority.replace("_", " "), tone: repo.highest_priority === "review_now" ? "warning" : repo.highest_priority === "watch" ? "accent" : "success", centerLabel: "GO" })}
                </div>
            </div>
            <div class="glance-strip">
                <div class="glance-chip">
                    <span class="glance-chip-label">Artifact</span>
                    <strong>${repo.highest_insight_artifact_path || "No lead artifact yet"}</strong>
                </div>
                ${repo.highest_evidence_label ? `<div class="glance-chip"><span class="glance-chip-label">Evidence</span><strong>${repo.highest_evidence_label}</strong></div>` : ""}
                ${repo.highest_baseline_label ? `<div class="glance-chip"><span class="glance-chip-label">Baseline</span><strong>${repo.highest_baseline_label}</strong></div>` : ""}
                ${repo.highest_review_target ? `<div class="glance-chip"><span class="glance-chip-label">Open next</span><strong>${repo.highest_review_target}</strong></div>` : ""}
            </div>
            ${renderBriefRows({
                changeSummary: repo.highest_change_summary,
                flagSummary: repo.highest_flag_summary,
                whereLabel: repo.highest_review_target,
                whereUrl: repo.highest_review_url,
                allowLinks: false,
            })}
            ${repo.highest_evidence_summary ? `<div class="meta-tight muted">${repo.highest_evidence_summary}</div>` : ""}
            ${repo.highest_rationale ? `<div class="meta-tight muted">${repo.highest_rationale}</div>` : ""}
            ${repo.highest_recommended_action ? `<div class="focus-action">${repo.highest_recommended_action}</div>` : ""}
            <div class="focus-footer">
                <span class="muted">Review now ${repo.review_now_count} · Watch ${repo.watch_count} · Lower confidence ${repo.lower_confidence_count}</span>
                <span class="cta-link">Open repo triage →</span>
            </div>
        </a>
    `;
}

function renderRepoQueue(items = []) {
    if (!items.length) {
        return '<div class="muted">No additional queue items yet.</div>';
    }
    return `<div class="queue-list">${items.map((repo, index) => renderMiniSignalRow(repo, index + 2)).join("")}</div>`;
}

function renderHighestRiskItems(items = []) {
    if (!items.length) {
        return '<div class="muted">No cross-repo regressions yet.</div>';
    }
    return `${renderHotspotField(items)}<div class="stack compact-stack hotspot-legend-list">${items.slice(0, 4)
        .map(
            (item) => `
                <div class="hotspot-legend-item">
                    <span class="hotspot-dot hotspot-dot-${item.priority}"></span>
                    <div>
                        <div><strong>${item.repo_full}</strong> · ${item.title}</div>
                        <div class="meta-tight muted">${item.artifact_path}</div>
                        ${item.evidence_label ? `<div class="meta-tight"><strong>${item.evidence_label}</strong></div>` : ""}
                        ${renderAttributeProfileChips(item.attribute_profile || [])}
                        ${renderBriefRows({
                            changeSummary: item.change_summary,
                            flagSummary: item.flag_summary,
                            whereLabel: item.review_target,
                            whereUrl: item.review_url,
                        })}
                        ${item.evidence_summary ? `<div class="meta-tight muted">${item.evidence_summary}</div>` : ""}
                    </div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderAttributeProfileChips(dimensions = []) {
    const items = asArray(dimensions);
    if (!items.length) {
        return "";
    }
    const controlSurface = items.find((dimension) => dimension.attribute_key === "control_surface_type");
    const changed = items.filter((dimension) => dimension.attribute_key !== "control_surface_type" && dimension.state !== "no_change");
    const primary = (changed.length ? changed : items.filter((dimension) => dimension.attribute_key !== "control_surface_type")).slice(0, 3);
    return `
        <div class="tag-row">
            ${controlSurface ? `<span class="tag tag-muted">${controlSurface.current_value}</span>` : ""}
            ${primary.map((dimension) => `<span class="tag tag-muted">${dimension.label}: ${dimension.baseline_value} → ${dimension.current_value}</span>`).join("")}
        </div>
    `;
}

function renderRegressionPatterns(items = []) {
    if (!items.length) {
        return '<div class="muted">No portfolio-level regression patterns yet.</div>';
    }
    const maxDrift = Math.max(...items.map((item) => item.max_drift_magnitude || 0), 0.001);
    return `<div class="stack compact-stack">${items
        .map(
            (item) => `
                <div class="compact-card analytics-card viz-row-card">
                    <div class="analytics-row">
                        <strong>${item.label}</strong>
                        <span class="muted">${item.artifact_count} artifacts · ${item.repo_count} repos</span>
                    </div>
                    <div class="viz-meter"><span style="width:${(item.max_drift_magnitude / maxDrift) * 100}%"></span></div>
                    <div class="meta-tight muted">${item.summary}</div>
                    <div class="tag-row">
                        <span class="tag tag-muted">review now ${item.review_now_artifact_count}</span>
                        <span class="tag tag-muted">max drift ${item.max_drift_magnitude.toFixed(3)}</span>
                    </div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaceRisk(items = []) {
    if (!items.length) {
        return '<div class="muted">No control-surface risk distribution yet.</div>';
    }
    const maxRisk = Math.max(...items.map((item) => item.weighted_risk || 0), 0.001);
    return `<div class="stack compact-stack">${items
        .map(
            (item) => `
                <div class="compact-card analytics-card viz-row-card">
                    <div class="analytics-row">
                        <strong>${item.label}</strong>
                        <span class="pill ${item.weighted_risk >= 3 ? "pill-high" : item.weighted_risk >= 1.5 ? "pill-medium" : "pill-low"}">risk concentration</span>
                    </div>
                    <div class="meta-tight muted">${item.repo_count} repos · ${item.artifact_count} artifacts</div>
                    <div class="viz-meter viz-meter-warning"><span style="width:${(item.weighted_risk / maxRisk) * 100}%"></span></div>
                    <div class="meta-tight">Weighted risk score ${item.weighted_risk.toFixed(3)} with ${item.review_now_artifact_count} review-now artifacts in this group.</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaceCoverage(items = []) {
    if (!items.length) {
        return '<div class="muted">No control surface coverage yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count || 0), 1);
    return `<div class="stack compact-stack">${items
        .map(
            (item) => `
                <div class="compact-card analytics-card viz-row-card">
                    <div class="analytics-row">
                        <strong>${item.label}</strong>
                        <span class="muted">${item.repo_count} repos</span>
                    </div>
                    <div class="viz-meter"><span style="width:${((item.artifact_count || 0) / maxArtifacts) * 100}%"></span></div>
                    <div class="meta-tight muted">${item.artifact_count} tracked artifacts</div>
                    <div class="meta-tight">${item.high_confidence_count} high-confidence artifacts currently anchor this control-surface category.</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderRepoTable(items = []) {
    if (!items.length) {
        return '<div class="muted">No onboarded repositories yet. Use the onboarding API first.</div>';
    }
    return `<div class="repo-capsule-grid">${items
        .map(
            (repo) => `
                <a class="repo-capsule" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
                    <div class="repo-capsule-head">
                        <strong>${repo.repo_full}</strong>
                        <span class="pill">${repo.onboarding_status}</span>
                    </div>
                    <div class="repo-capsule-meta">${repo.default_branch}</div>
                    <div class="repo-capsule-bar"><span style="width:${Math.min(repo.discovered_artifact_count * 8, 100)}%"></span></div>
                    <div class="repo-capsule-meta">${repo.discovered_artifact_count} tracked artifacts</div>
                </a>
            `
        )
        .join("")}</div>`;
}

async function loadOverview() {
    try {
        const response = await fetch("/api/dashboard/overview");
        if (!response.ok) {
            throw new Error(`Overview request failed with ${response.status}`);
        }
        const payload = await response.json();
        const metrics = asArray(payload.metrics);
        const regressionPatterns = asArray(payload.regression_patterns);
        const highestRiskItems = asArray(payload.highest_risk_items);
        const controlSurfaceRisk = asArray(payload.control_surface_risk);
        const attentionRepos = asArray(payload.attention_repos);
        const controlSurfaceCoverage = asArray(payload.control_surface_coverage);
        const repos = asArray(payload.repos);

        setSectionHtml("portfolio-risk-state", renderRiskState(payload.risk_state));
        setSectionHtml(
            "overview-metrics",
            metrics.map((metric) => renderMetricGlyphCard(metric)).join("")
        );
        setSectionHtml("portfolio-pulse", renderPortfolioPulse({ attentionRepos, highestRiskItems, controlSurfaceCoverage, repos, riskState: payload.risk_state }));
        setSectionHtml("top-review-focus", renderPrimaryReviewFocus(attentionRepos[0]));
        setSectionHtml("review-queue", renderRepoQueue(attentionRepos.slice(1)));
        setSectionHtml("highest-risk-items", renderHighestRiskItems(highestRiskItems));
        setSectionHtml("coverage-trust", renderTrustRadar({ attentionRepos, controlSurfaceCoverage, repos }));
        setSectionHtml("coverage-atlas", renderCoverageAtlas(controlSurfaceCoverage));
        setSectionHtml("control-surface-coverage", renderControlSurfaceCoverage(controlSurfaceCoverage));
        setSectionHtml("control-surface-risk", renderControlSurfaceRisk(controlSurfaceRisk));
        setSectionHtml("regression-patterns", renderRegressionPatterns(regressionPatterns));
        setSectionHtml("repos", renderRepoTable(repos));
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown overview error";
        const fallback = `<div class="muted">Unable to load dashboard overview. ${message}</div>`;
        setSectionHtml("portfolio-risk-state", fallback);
        setSectionHtml("portfolio-pulse", fallback);
        setSectionHtml("overview-metrics", fallback);
        setSectionHtml("top-review-focus", fallback);
        setSectionHtml("review-queue", fallback);
        setSectionHtml("highest-risk-items", fallback);
        setSectionHtml("coverage-trust", fallback);
        setSectionHtml("coverage-atlas", fallback);
        setSectionHtml("control-surface-coverage", fallback);
        setSectionHtml("control-surface-risk", fallback);
        setSectionHtml("regression-patterns", fallback);
        setSectionHtml("repos", fallback);
    }
}

bindModeSwitcher();
loadOverview();
