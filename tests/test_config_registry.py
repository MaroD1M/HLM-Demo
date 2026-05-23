from core.services.config_service import (
    build_default_config_rows,
    get_importable_setting_keys,
    get_setting_scopes,
    validate_setting_value,
)


def test_registry_contains_expected_scopes_and_keys():
    scopes = get_setting_scopes()
    assert 'general_template' in scopes
    assert 'dev' in scopes
    assert 'default_extensions' in scopes['general_template']
    assert 'dev_git_repo' in scopes['dev']

    importable = get_importable_setting_keys()
    assert 'default_extensions' in importable
    assert 'dev_git_repo' in importable
    assert 'version_check_cached_at' not in importable


def test_registry_default_rows_include_runtime_keys():
    rows = build_default_config_rows()
    keys = {k for k, _, _ in rows}
    assert 'default_extensions' in keys
    assert 'backup_dir' in keys
    assert 'dev_mode' in keys
    assert 'last_dev_apply_at' in keys


def test_registry_validation_for_backfill_and_dev_keys():
    ok, val = validate_setting_value('backfill_file_fetch_workers', '6')
    assert ok is True
    assert val == '6'

    ok2, _ = validate_setting_value('backfill_file_fetch_workers', '99')
    assert ok2 is False

    ok3, val3 = validate_setting_value('dev_git_branch', 'release/v1.2')
    assert ok3 is True
    assert val3 == 'release/v1.2'

    ok4, _ = validate_setting_value('dev_git_branch', 'bad branch')
    assert ok4 is False
