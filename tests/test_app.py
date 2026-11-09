"""UI 스모크 테스트 (offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from mde import app as mde_app


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda _parent, _title, text: messages.append(text))
    win = mde_app.MovieDurationExtender()
    win.messages = messages
    yield win
    win.close()


def _make_sequence(folder, count=5):
    for frame in range(1001, 1001 + count):
        (folder / f'shot.{frame}.png').write_bytes(f'frame{frame}'.encode())


def _wait_until_idle(qapp, window, timeout_ms=10000):
    from PySide6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while window._is_running() and not deadline.hasExpired():
        qapp.processEvents()
    assert not window._is_running()


def test_add_sequence_row_and_default_output(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 1
    assert window.table.item(0, window.COL_TYPE).text() == 'PNG SEQ'
    assert window._output_path(0) == str(tmp_path / 'shot_hold_10s' / 'shot.%04d.png')
    assert window.table.item(0, window.COL_STATUS).text() == '대기'

    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 1


def test_format_change_updates_output_and_shorter_target_is_error(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    window.table.cellWidget(0, window.COL_FORMAT).setCurrentText('MOV')
    assert window._output_path(0) == str(tmp_path / 'shot_hold_10s.mov')

    window.table.cellWidget(0, window.COL_TARGET).setValue(0.1)
    assert window.table.item(0, window.COL_STATUS).text().startswith('목표 길이가 원본보다 짧습니다')


def test_start_extends_sequence_in_worker_thread(qapp, window, tmp_path):
    _make_sequence(tmp_path, count=5)
    window._add_paths([str(tmp_path)])
    window.table.cellWidget(0, window.COL_FPS).setValue(5.0)
    window.table.cellWidget(0, window.COL_TARGET).setValue(2.0)
    window._start()
    _wait_until_idle(qapp, window)

    assert window.table.item(0, window.COL_STATUS).text() == '완료'
    assert len(list((tmp_path / 'shot_hold_2s').iterdir())) == 10
    assert window.messages[-1] == '처리 완료\n성공: 1\n스킵: 0\n실패: 0'
    assert window.start_btn.isEnabled()


def test_remove_and_clear(window, tmp_path):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    _make_sequence(tmp_path / 'a')
    _make_sequence(tmp_path / 'b')
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 2
    window.table.selectRow(0)
    window._remove_selected()
    assert window.table.rowCount() == 1
    assert list(window._infos) == [0]
    window._clear_all()
    assert window.table.rowCount() == 0


def test_auto_updater_skips_when_not_installed(qapp, monkeypatch, caplog):
    from mde import update_ui, updater

    monkeypatch.setattr(updater, 'installed_app_path', lambda: None)
    called = []
    monkeypatch.setattr(updater, 'check_for_update', lambda v: called.append(v))
    parent = QWidget()
    au = update_ui.AutoUpdater(parent, 'X')
    au.start()
    qapp.processEvents()
    assert called == []
