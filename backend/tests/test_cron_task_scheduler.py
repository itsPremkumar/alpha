from pathlib import Path

from alpha.scheduler.cron_manager import CronManager
from alpha.tools.builtins.cronjob_manage_tool import cronjob_manage


def test_cron_manager_lifecycle(tmp_path: Path):
    manager = CronManager(root_dir=tmp_path)
    assert len(manager.list_jobs()) == 0

    # 1. Add job
    job = manager.add_job(
        name="daily_lint",
        cron_expression="0 0 * * *",
        command_or_prompt="flake8 . --count",
    )
    assert job.name == "daily_lint"
    assert job.is_enabled is True
    assert len(manager.list_jobs()) == 1

    # 2. Run due
    results = manager.run_due()
    assert len(results) == 1
    assert results[0]["name"] == "daily_lint"
    assert results[0]["status"] == "success"

    updated = manager.get_job(job.job_id)
    assert updated.run_count == 1

    # 3. Pause and Resume
    manager.pause_job(job.job_id)
    assert manager.get_job(job.job_id).is_enabled is False

    manager.resume_job(job.job_id)
    assert manager.get_job(job.job_id).is_enabled is True

    # 4. Remove
    removed = manager.remove_job(job.job_id)
    assert removed is True
    assert len(manager.list_jobs()) == 0


def test_cronjob_manage_tool(tmp_path: Path, monkeypatch):
    manager = CronManager(root_dir=tmp_path)
    monkeypatch.setattr("alpha.scheduler.cron_manager.get_cron_manager", lambda: manager)
    monkeypatch.setattr("alpha.tools.builtins.cronjob_manage_tool.get_cron_manager", lambda: manager)

    # Add
    add_out = cronjob_manage.invoke({
        "action": "add",
        "name": "backup_db",
        "schedule": "0 2 * * *",
        "command_or_prompt": "pg_dump production_db",
    })
    assert "Scheduled job 'backup_db' added successfully" in add_out

    # List
    list_out = cronjob_manage.invoke({"action": "list"})
    assert "backup_db" in list_out

    # Run due
    run_out = cronjob_manage.invoke({"action": "run_due"})
    assert "backup_db" in run_out
