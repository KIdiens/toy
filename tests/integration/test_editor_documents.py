"""编辑器读取与实际下发必须使用同一个动作文档。"""
from __future__ import annotations

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from host.core.events import CommandKind
from host.web.app import create_app


def test_action_read_returns_actual_document_and_source_id(sim_context, wave_action):
    with TestClient(create_app(sim_context)) as client:
        response = client.get('/api/v1/actions/files/wave', params={'role_id': 1})
    assert response.status_code == 200
    data = response.json()['data']
    assert data['action_id'] == 'wave' and data['action'] == wave_action
    assert data['config_version'] == sim_context.roles.active().config_version


def test_editor_snapshot_reaches_queue_without_changing_disk(sim_context, wave_action):
    edited = deepcopy(wave_action)
    edited['frames'][1]['us'][1] = 1540
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'editor-snapshot', 'role_id': 1, 'action_id': 'wave',
            'action_version': sim_context.roles.active().config_version, 'action': edited,
        })
    assert response.status_code == 200
    commands = []
    while (item := sim_context.work.get(timeout=0)) is not None:
        commands.append(item)
    command = next(item for item in commands if item.kind is CommandKind.PLAY_ACTION)
    assert command.payload['action'] == edited, '服务端仍使用磁盘文件，丢失编辑内容'
    from tests.support import load_wave

    assert load_wave() == wave_action


def test_invalid_editor_snapshot_rejected_before_motion(sim_context, wave_action):
    queued = sim_context.work.qsize()
    wave_action['frames'][1]['us'][1] = 1601
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'invalid-editor', 'role_id': 1, 'action_id': 'wave',
            'action_version': sim_context.roles.active().config_version, 'action': wave_action,
        })
    assert response.status_code == 409
    assert sim_context.work.qsize() == queued and not sim_context.motion_busy


@pytest.mark.parametrize('version', [None, 'stale-version'])
def test_snapshot_requires_current_role_version(sim_context, wave_action, version):
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'version', 'role_id': 1, 'action_id': 'wave',
            'action_version': version, 'action': wave_action,
        })
    assert response.status_code == 409 and not sim_context.motion_busy


def test_new_draft_is_playable_without_creating_disk_file(sim_context, wave_action):
    before = sim_context.action_files()
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'new-draft', 'role_id': 1, 'action_id': 'draft',
            'action_version': sim_context.roles.active().config_version, 'action': wave_action,
        })
    assert response.status_code == 200
    assert sim_context.action_files() == before
    assert sim_context.roles.action_path(1, 'draft') is None


def test_invalid_replacement_does_not_stop_current_motion(sim_context, wave_action):
    sim_context.play_action(role_id=1, action_id='wave')
    token = sim_context._active_play_seq
    wave_action['role_id'] = 2
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'invalid-replace', 'role_id': 1, 'action_id': 'draft',
            'action_version': sim_context.roles.active().config_version, 'action': wave_action,
            'replace_current': True,
        })
    assert response.status_code == 409
    assert sim_context.motion_busy and sim_context._active_play_seq == token


def test_enqueued_snapshot_is_independent_of_callers_mutations(sim_context, wave_action):
    expected = deepcopy(wave_action)
    sim_context.play_action(role_id=1, action_id='draft', action=wave_action,
                            action_version=sim_context.roles.active().config_version)
    wave_action['frames'][1]['us'][1] = 1601
    while (command := sim_context.work.get(timeout=0)) is not None:
        if command.kind is CommandKind.PLAY_ACTION:
            assert command.payload['action'] == expected
            break
    else:
        pytest.fail('没有入队真实运动命令')


@pytest.mark.parametrize('path,params,status', [
    ('wave', {'role_id': 2}, 409), ('unknown', {'role_id': 1}, 409),
    ('wave', {}, 422), ('wave', {'role_id': 4}, 422),
])
def test_read_rejects_wrong_role_or_missing_source(sim_context, path, params, status):
    before = sim_context.action_files()
    with TestClient(create_app(sim_context)) as client:
        response = client.get(f'/api/v1/actions/files/{path}', params=params)
    assert response.status_code == status and sim_context.action_files() == before


def test_legacy_disk_play_still_requires_whitelist(sim_context):
    with TestClient(create_app(sim_context)) as client:
        response = client.post('/api/v1/actions/play', json={
            'request_id': 'legacy', 'role_id': 1, 'action_id': 'not-registered',
        })
    assert response.status_code == 409
