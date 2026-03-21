function metricCard(label, value, detail) {
    return `<div class="card"><div class="muted">${label}</div><div class="metric">${value}</div><div class="muted">${detail}</div></div>`;
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

function renderRiskState(riskState) {
    if (!riskState) {
        return '<div class="hero-panel-inner hero-status-baseline"><div><div class="priority priority-baseline_review">baseline</div><h2 class="hero-title">Portfolio risk state unavailable</h2><p class="hero-copy">The overview payload is missing a risk-state summary. Refresh after the API finishes loading.</p></div></div>';
    }
    const statusLabel = riskState.status.replace("_", " ");
    return `
        <div class="hero-panel-inner hero-status-${riskState.status}">
            <div>
                <div class="priority priority-${riskState.review_now_repo_count > 0 ? "review_now" : riskState.watch_repo_count > 0 ? "watch" : "baseline_review"}">${statusLabel}</div>
                <h2 class="hero-title">${riskState.headline}</h2>
                <p class="hero-copy">${riskState.summary}</p>
            </div>
            <div class="hero-stats">
                <div class="hero-stat"><span class="hero-stat-value">${riskState.review_now_repo_count}</span><span class="muted">review now repos</span></div>
                <div class="hero-stat"><span class="hero-stat-value">${riskState.watch_repo_count}</span><span class="muted">watch repos</span></div>
                <div class="hero-stat"><span class="hero-stat-value">${riskState.baseline_review_repo_count}</span><span class="muted">baseline repos</span></div>
            </div>
            <div class="hero-focus muted">
                <strong>Current highest-risk focus:</strong>
                ${riskState.highest_risk_repo_full || "n/a"}
                ${riskState.highest_risk_title ? `· ${riskState.highest_risk_title}` : ""}
                ${riskState.highest_risk_artifact_path ? `<br>${riskState.highest_risk_artifact_path}` : ""}
                ${riskState.highest_drift_magnitude ? `<br>Top recorded drift magnitude ${riskState.highest_drift_magnitude.toFixed(3)}` : ""}
            </div>
        </div>
    `;
}

function renderAttentionRepos(items = []) {
    if (!items.length) {
        return '<div class="muted">No repo priorities yet. Onboard a repository to populate the decision queue.</div>';
    }
    return `<div class="stack">${items
        .map(
            (repo) => `
                <a class="attention-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">
                    <div class="priority priority-${repo.highest_priority}">${repo.highest_priority.replace("_", " ")}</div>
                    <div class="insight-title">${repo.repo_full}</div>
                    <div>${repo.highest_insight_title || "No prioritized repo insight yet"}</div>
                    <div class="muted meta-tight">${repo.highest_insight_artifact_path || "No lead artifact yet"}</div>
                    <div class="meta-tight muted">Review now: ${repo.review_now_count} · Watch: ${repo.watch_count} · Baseline review: ${repo.baseline_review_count}</div>
                </a>
            `
        )
        .join("")}</div>`;
}

function renderHighestRiskItems(items = []) {
    if (!items.length) {
        return '<div class="muted">No cross-repo regressions yet.</div>';
    }
    return `<div class="stack">${items
        .map(
            (item) => `
                <a class="attention-link" href="/dashboard/${encodeURIComponent(item.repo_full)}">
                    <div class="priority priority-${item.priority}">${item.priority.replace("_", " ")}</div>
                    <div class="insight-title">${item.title}</div>
                    <div>${item.repo_full}</div>
                    <div class="muted meta-tight">${item.artifact_path} · ${item.artifact_type}</div>
                    <div class="meta-tight muted">Drift ${item.drift_magnitude.toFixed(3)} · Capability ${item.capability_shift.toFixed(3)} · Guardrail ${item.guardrail_shift.toFixed(3)}</div>
                </a>
            `
        )
        .join("")}</div>`;
}

function renderRegressionPatterns(items = []) {
    if (!items.length) {
        return '<div class="muted">No portfolio-level regression patterns yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count), 1);
    return `<div class="stack">${items
        .map(
            (item) => `
                <div class="risk-surface-card">
                    <div class="coverage-row">
                        <strong>${item.label}</strong>
                        <span class="muted">${item.artifact_count} artifacts · ${item.repo_count} repos</span>
                    </div>
                    <div class="bar-track"><div class="bar-fill" style="width: ${(item.artifact_count / maxArtifacts) * 100}%"></div></div>
                    <div class="meta-tight muted">${item.summary}</div>
                    <div class="meta-tight muted">Review now: ${item.review_now_artifact_count} · Max drift ${item.max_drift_magnitude.toFixed(3)}</div>
                    ${item.example_repo_full ? `<div class="meta-tight muted">Lead example: ${item.example_repo_full} · ${item.example_title || item.example_artifact_path}${item.example_artifact_path ? ` · ${item.example_artifact_path}` : ""}</div>` : ""}
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaceRisk(items = []) {
    if (!items.length) {
        return '<div class="muted">No control-surface risk distribution yet.</div>';
    }
    const maxRisk = Math.max(...items.map((item) => item.weighted_risk), 1);
    return `<div class="stack">${items
        .map(
            (item) => `
                <div class="risk-surface-card">
                    <div class="coverage-row">
                        <strong>${item.label}</strong>
                        <span class="muted">risk ${item.weighted_risk.toFixed(3)}</span>
                    </div>
                    <div class="bar-track"><div class="bar-fill" style="width: ${(item.weighted_risk / maxRisk) * 100}%"></div></div>
                    <div class="meta-tight muted">${item.repo_count} repos · ${item.artifact_count} artifacts · ${item.review_now_artifact_count} review-now artifacts</div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderControlSurfaceCoverage(items = []) {
    if (!items.length) {
        return '<div class="muted">No control surface coverage yet.</div>';
    }
    const maxArtifacts = Math.max(...items.map((item) => item.artifact_count), 1);
    return `<div class="stack">${items
        .map(
            (item) => `
                <div>
                    <div class="coverage-row">
                        <strong>${item.label}</strong>
                        <span class="muted">${item.repo_count} repos · ${item.artifact_count} artifacts</span>
                    </div>
                    <div class="bar-track"><div class="bar-fill" style="width: ${(item.artifact_count / maxArtifacts) * 100}%"></div></div>
                </div>
            `
        )
        .join("")}</div>`;
}

function renderRepoTable(items = []) {
    if (!items.length) {
        return '<div class="muted">No onboarded repositories yet. Use the onboarding API first.</div>';
    }
    return `<table><thead><tr><th>Repository</th><th>Default branch</th><th>Artifacts</th><th>Status</th></tr></thead><tbody>${items
        .map(
            (repo) => `
                <tr>
                    <td><a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${repo.repo_full}</a></td>
                    <td>${repo.default_branch}</td>
                    <td>${repo.discovered_artifact_count}</td>
                    <td><span class="pill">${repo.onboarding_status}</span></td>
                </tr>
            `
        )
        .join("")}</tbody></table>`;
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
            metrics.map((metric) => metricCard(metric.label, metric.value, metric.detail)).join("")
        );
        setSectionHtml("regression-patterns", renderRegressionPatterns(regressionPatterns));
        setSectionHtml("highest-risk-items", renderHighestRiskItems(highestRiskItems));
        setSectionHtml("control-surface-risk", renderControlSurfaceRisk(controlSurfaceRisk));
        setSectionHtml("attention-repos", renderAttentionRepos(attentionRepos));
        setSectionHtml("control-surface-coverage", renderControlSurfaceCoverage(controlSurfaceCoverage));
        setSectionHtml("repos", renderRepoTable(repos));
    } catch (error) {
        const message = error instanceof Error ? error.message : "Unknown overview error";
        const fallback = `<div class="muted">Unable to load dashboard overview. ${message}</div>`;
        setSectionHtml("portfolio-risk-state", fallback);
        setSectionHtml("overview-metrics", fallback);
        setSectionHtml("regression-patterns", fallback);
        setSectionHtml("highest-risk-items", fallback);
        setSectionHtml("control-surface-risk", fallback);
        setSectionHtml("attention-repos", fallback);
        setSectionHtml("control-surface-coverage", fallback);
        setSectionHtml("repos", fallback);
    }
}

loadOverview();
