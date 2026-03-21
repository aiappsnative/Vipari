async function loadRepos() {
    const container = document.getElementById("repos");
    container.innerHTML = '<div class="card">Loading repositories...</div>';

    const response = await fetch("/api/repos");
    const payload = await response.json();
    if (!payload.repos.length) {
        container.innerHTML = '<div class="card">No onboarded repositories yet. Use the onboarding API first.</div>';
        return;
    }

    container.innerHTML = payload.repos
        .map(
            (repo) => `
                <div class="card">
                    <a class="repo-link" href="/dashboard/${encodeURIComponent(repo.repo_full)}">${repo.repo_full}</a>
                    <div class="meta">Default branch: ${repo.default_branch}</div>
                    <div class="meta">Discovered artifacts: ${repo.discovered_artifact_count}</div>
                    <div class="pill">${repo.onboarding_status}</div>
                </div>
            `
        )
        .join("");
}

loadRepos();
