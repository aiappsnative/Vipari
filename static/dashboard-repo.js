const repoFull = document.querySelector('meta[name="promptdrift-repo-full"]')?.getAttribute("content") || "";

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
    }
}

function renderRiskTags(tags = []) {
    return tags.map((tag) => `<span class="tag">${tag}</span>`).join("");
}

function renderProfileMetric(label, baselineValue, currentValue) {
    return `
        <div class="profile-metric">
            <div class="profile-label">${label}</div>
            <div class="profile-values"><span>${baselineValue.toFixed(2)}</span><span class="muted">→</span><span>${currentValue.toFixed(2)}</span></div>
        </div>
    `;
}

function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
}

function renderLeaderboard(items = []) {
    if (!items.length) {
        return '<div class="muted">No pull-request drift samples have been recorded yet.</div>';
    }
    return `<table><thead><tr><th>Artifact</th><th>Type</th><th>Drift magnitude</th><th>Capability shift</th><th>Autonomy shift</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td>${item.artifact_path}</td>
                    <td>${item.artifact_type}</td>
                    <td>${item.drift_magnitude.toFixed(3)}</td>
                    <td>${item.capability_shift.toFixed(3)}</td>
                    <td>${item.autonomy_shift.toFixed(3)}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

function renderArtifacts(items = []) {
    if (!items.length) {
        return '<div class="muted">No onboarded artifacts were found for this repository yet.</div>';
    }
    return `<table><thead><tr><th>Artifact</th><th>Baseline lines</th><th>Historical versions</th><th>Historical drift</th><th>PR profiles</th><th>Latest PR semantic distance</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td><strong>${item.artifact_path}</strong><br><span class="muted">${item.artifact_type}</span></td>
                    <td>${item.baseline_line_count}</td>
                    <td>${item.historical_version_count}</td>
                    <td>${item.latest_historical_drift_magnitude.toFixed(3)}</td>
                    <td>${item.pr_profile_count}</td>
                    <td>${item.latest_pr_semantic_distance.toFixed(3)}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

function renderInsights(items = []) {
    if (!items.length) {
        return '<div class="muted">No prioritized insights yet. Once drift history grows, this panel will highlight what needs review first.</div>';
    }
    const designProfiles = Object.fromEntries(asArray(window.__designProfiles).map((item) => [item.artifact_path, item]));
    return `<div class="stack">${items
        .map(
            (item) => `
                <div>
                    <div class="priority priority-${item.priority}">${item.priority.replace("_", " ")}</div>
                    <div class="insight-title">${item.title}</div>
                    <div><strong>${item.artifact_path}</strong> <span class="muted">(${item.artifact_type})</span></div>
                    <div class="tag-row">${renderRiskTags(designProfiles[item.artifact_path]?.risk_tags || ["baseline only"])}</div>
                    <div class="meta-tight muted">${designProfiles[item.artifact_path]?.provenance?.label || "No PR or history provenance yet"}</div>
                    <div class="muted" style="margin-top:6px;">${item.rationale}</div>
                    <div style="margin-top:6px;">${item.recommended_action}</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaces(items = []) {
    if (!items.length) {
        return '<div class="muted">No grouped control surfaces yet.</div>';
    }
    return `<table><thead><tr><th>Group</th><th>Artifacts</th><th>High confidence</th><th>Top examples</th></tr></thead><tbody>${items
        .map(
            (item) => `
                <tr>
                    <td>${item.label}</td>
                    <td>${item.artifact_count}</td>
                    <td>${item.high_confidence_count}</td>
                    <td>${item.top_artifact_paths.join("<br>")}</td>
                </tr>`
        )
        .join("")}</tbody></table>`;
}

function renderHistoryTimelines(items = []) {
    if (!items.length) {
        return '<div class="muted">No historical timeline yet. Backfill or PR profile data will populate this view.</div>';
    }
    const maxDrift = Math.max(
        ...items.flatMap((item) => asArray(item.points).map((point) => asNumber(point.drift_magnitude))),
        1
    );
    return `<div class="stack">${items
        .map(
            (item) => `
                <div class="timeline-card">
                    <div class="timeline-header">
                        <div>
                            <div class="insight-title">${item.artifact_path}</div>
                            <div class="muted meta-tight">${item.artifact_type} · ${item.point_count} recorded points</div>
                        </div>
                        <div class="muted">Max drift ${item.max_drift_magnitude.toFixed(3)}</div>
                    </div>
                    <div class="timeline-points">${asArray(item.points)
                        .map(
                            (point) => `
                                <div class="timeline-point">
                                    <div class="timeline-label-row">
                                        <span class="timeline-label">${point.label}</span>
                                        <span class="timeline-meta">${point.source === "pull_request" ? "PR" : "History"} · drift ${point.drift_magnitude.toFixed(3)}</span>
                                    </div>
                                    <div class="bar-track"><div class="bar-fill" style="width: ${(point.drift_magnitude / maxDrift) * 100}%"></div></div>
                                    <div class="timeline-meta muted">Semantic ${point.semantic_distance.toFixed(3)} · Capability ${point.capability_shift.toFixed(3)} · Guardrail ${point.guardrail_shift.toFixed(3)}</div>
                                </div>
                            `
                        )
                        .join("")}</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderDesignProfiles(items = []) {
    if (!items.length) {
        return '<div class="muted">No design-profile comparisons yet. Onboarded baseline data is available, but no prioritized surfaces are ready for comparison.</div>';
    }
    return `<div class="stack">${items
        .map(
            (item) => `
                <div class="design-card">
                    <div class="timeline-header">
                        <div>
                            <div class="insight-title">${item.artifact_path}</div>
                            <div class="muted meta-tight">${item.artifact_type} · drift from baseline ${item.drift_from_baseline.toFixed(3)}</div>
                        </div>
                        <div class="muted">${item.provenance?.label || "Baseline only"}</div>
                    </div>
                    <div class="tag-row">${renderRiskTags(item.risk_tags)}</div>
                    <div class="profile-grid">
                        ${renderProfileMetric("Guardrails", item.baseline_profile.guardrail_robustness, item.current_profile.guardrail_robustness)}
                        ${renderProfileMetric("Capability", item.baseline_profile.capability_risk, item.current_profile.capability_risk)}
                        ${renderProfileMetric("Autonomy", item.baseline_profile.autonomy_level, item.current_profile.autonomy_level)}
                        ${renderProfileMetric("Stability", item.baseline_profile.stability_vs_creativity, item.current_profile.stability_vs_creativity)}
                        ${renderProfileMetric("Governance", item.baseline_profile.governance_strength, item.current_profile.governance_strength)}
                    </div>
                    <div class="meta-tight muted">${asArray(item.narrative).join(" ")}</div>
                </div>
            `
        )
        .join("")}</div>`;
}

async function loadDashboard() {
    try {
        const response = await fetch(`/api/repos/${encodeURIComponent(repoFull)}/dashboard`);
        if (!response.ok) {
            throw new Error(`Repo dashboard request failed with ${response.status}`);
        }
        const payload = await response.json();
        const onboarding = payload.onboarding || null;
        const backfill = payload.backfill || {};
        const driftSummary = payload.drift_summary || {};
        const designProfiles = asArray(payload.design_profiles);
        const insights = asArray(payload.insights);
        const controlSurfaces = asArray(payload.control_surface_groups);
        const leaderboard = asArray(payload.top_drifting_artifacts);
        const historyTimelines = asArray(payload.history_timelines);
        const artifacts = asArray(payload.artifacts);
        window.__designProfiles = designProfiles;

        setSectionHtml(
            "summary",
            [
                metricCard(
                    "Onboarded artifacts",
                    onboarding ? onboarding.discovered_artifact_count : 0,
                    onboarding ? `Default branch: ${onboarding.default_branch}` : "No onboarding yet"
                ),
                metricCard("Baseline versions", asNumber(payload.baseline_version_count), `Repo: ${payload.repo_full || repoFull}`),
                metricCard(
                    "Backfill jobs",
                    asNumber(backfill.job_count),
                    `Completed: ${asNumber(backfill.completed_job_count)} · Failed: ${asNumber(backfill.failed_job_count)}`
                ),
                metricCard(
                    "Historical versions",
                    asNumber(backfill.total_historical_versions),
                    `Historical profiles: ${asNumber(backfill.total_historical_profiles)}`
                ),
                metricCard("PR audits", asNumber(payload.pull_request_audit_count), `PR profiles: ${asNumber(driftSummary.profile_count)}`),
                metricCard(
                    "Avg semantic distance",
                    asNumber(driftSummary.avg_semantic_distance).toFixed(3),
                    `Highest capability artifact: ${driftSummary.highest_capability_artifact_path || "n/a"}`
                ),
            ].join("")
        );

        setSectionHtml("design-profiles", renderDesignProfiles(designProfiles));
        setSectionHtml("insights", renderInsights(insights));
        setSectionHtml("control-surfaces", renderControlSurfaces(controlSurfaces));
        setSectionHtml("leaderboard", renderLeaderboard(leaderboard));
        setSectionHtml("history-timelines", renderHistoryTimelines(historyTimelines));
        setSectionHtml("artifacts", renderArtifacts(artifacts));
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown repo dashboard error";
        const fallback = `<div class="muted">Unable to load repository dashboard. ${message}</div>`;
        setSectionHtml("summary", fallback);
        setSectionHtml("design-profiles", fallback);
        setSectionHtml("insights", fallback);
        setSectionHtml("control-surfaces", fallback);
        setSectionHtml("leaderboard", fallback);
        setSectionHtml("history-timelines", fallback);
        setSectionHtml("artifacts", fallback);
    }
}

loadDashboard();
