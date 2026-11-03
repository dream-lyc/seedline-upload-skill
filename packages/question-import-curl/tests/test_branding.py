def test_brand_rename_preserves_existing_profile_and_pending_connection(tmp_path):
    from question_import_sdk.config import default_home
    preferred = tmp_path / '.seedline-import'
    legacy = tmp_path / '.acebro-import'
    assert default_home(tmp_path) == preferred
    pending = legacy / '.profile.json.connection' / 'state.json'
    pending.parent.mkdir(parents=True)
    pending.write_text('{}')
    assert default_home(tmp_path) == legacy
    pending.unlink()
    profile = legacy / 'profile.json'
    profile.write_text('{}')
    assert default_home(tmp_path) == legacy
    preferred.mkdir()
    (preferred / 'profile.json').write_text('{}')
    assert default_home(tmp_path) == preferred
    assert profile.read_text() == '{}'
