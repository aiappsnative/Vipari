from services.audit_jobs import init_db
from services.onboarding import onboard_repository, sync_on_pr_merge_artifact_changes
from services.onboarding_records import get_latest_repository_onboarding, list_onboarded_artifacts_for_onboarding


db_path='temp.db'
init_db(db_path)
files={'prompts/old.txt':'initial content'}
res=onboard_repository(
    db_path,
    repo_full='doria90/dummyAI',
    installation_id=123,
    token='token',
    get_default_branch_fn=lambda repo, token: 'main',
    list_repository_files_fn=lambda repo, token, ref: list(files.keys()),
    fetch_file_content_fn=lambda repo, path, token, ref: files[path],
)
print('onboarded before:', [a.artifact_path for a in list_onboarded_artifacts_for_onboarding(db_path, res.onboarding.id)])
added={'prompts/new.txt'}
removed={'prompts/old.txt'}
snapshots={'prompts/new.txt':'new content'}
sync_on_pr_merge_artifact_changes(db_path, repo_full='doria90/dummyAI', artifact_snapshots=snapshots, added_paths=added, removed_paths=removed)
latest=get_latest_repository_onboarding(db_path,'doria90/dummyAI')
print('onboarded after:', [a.artifact_path for a in list_onboarded_artifacts_for_onboarding(db_path, latest.id)])
