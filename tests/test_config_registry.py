from core.services.config_service import (
    build_default_config_rows,
    get_importable_setting_keys,
    get_setting_scopes,
    validate_setting_value,
)


def test_registry_contains_expected_scopes_and_keys():
    scopes = get_setting_scopes()
    assert 'general_template' in scopes
    assert 'dev' not in scopes
    assert 'default_extensions' in scopes['general_template']

    importable = get_importable_setting_keys()
    assert 'default_extensions' in importable
    assert 'dev_git_repo' not in importable
    assert 'version_check_cached_at' not in importable


def test_registry_default_rows_include_runtime_keys():
    rows = build_default_config_rows()
    keys = {k for k, _, _ in rows}
    assert 'default_extensions' in keys
    assert 'backup_dir' in keys
    assert 'backfill_deep_budget_seconds' in keys
    assert 'mapping_reset_cooldown_seconds' in keys
    assert 'backfill_failure_retention_days' in keys
    assert 'dev_mode' not in keys
    assert 'last_dev_apply_at' not in keys


def test_registry_validation_for_backfill_and_repo_keys():
    ok, val = validate_setting_value('backfill_file_fetch_workers', '6')
    assert ok is True
    assert val == '6'

    ok2, _ = validate_setting_value('backfill_file_fetch_workers', '99')
    assert ok2 is False

    ok3, val3 = validate_setting_value('backfill_deep_budget_seconds', '120')
    assert ok3 is True
    assert val3 == '120'

    ok4, _ = validate_setting_value('backfill_deep_budget_seconds', '5')
    assert ok4 is False

    ok5, val5 = validate_setting_value('github_repo', 'marod1m/HLM-Demo')
    assert ok5 is True
    assert val5 == 'marod1m/HLM-Demo'
