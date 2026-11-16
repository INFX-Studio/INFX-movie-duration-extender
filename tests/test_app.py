"""UI 스모크 테스트 (offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from mde import app as mde_app
from mde.core import FIT_CROP, ResizeSpec


@pytest.fixture(scope='module')
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = str(tmp_path / 'settings.ini')
    monkeypatch.setattr(mde_app, '_settings', lambda: QSettings(path, QSettings.Format.IniFormat))
    return path


@pytest.fixture
def window(qapp, monkeypatch, settings_path):
    messages = []
    monkeypatch.setattr(QMessageBox, 'information', lambda _parent, _title, text: messages.append(text))
    win = mde_app.MovieDurationExtender()
    win.messages = messages
    yield win
    win.close()


def _make_sequence(folder, count=5):
    folder.mkdir(parents=True, exist_ok=True)
    for frame in range(1001, 1001 + count):
        (folder / f'shot.{frame}.png').write_bytes(f'frame{frame}'.encode())


def _wait_until_idle(qapp, window, timeout_ms=10000):
    from PySide6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(timeout_ms)
    while window._is_running() and not deadline.hasExpired():
        qapp.processEvents()
    assert not window._is_running()


def _status(window, row=0):
    return window.table.item(row, window.COL_STATUS).text()


def test_add_sequence_row_and_default_output(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 1
    assert window.table.item(0, window.COL_SOURCE).text().startswith('PNG SEQ · 0.208초')
    assert window.table.item(0, window.COL_RESULT).text() == 'PNG · 10.000초 · 원본 크기'
    assert window._rows[0].output_path == str(tmp_path / 'shot_hold_10s' / 'shot.%04d.png')
    assert _status(window) == '대기'
    assert window.panel.isHidden()
    assert window.summary_label.text().startswith('1개 중 1개 처리 예정')


def test_duplicate_add_asks_and_collision_blocks(window, tmp_path, monkeypatch):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])

    monkeypatch.setattr(window, '_confirm_add_duplicates', lambda duplicates: False)
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 1

    monkeypatch.setattr(window, '_confirm_add_duplicates', lambda duplicates: True)
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 2
    assert _status(window, 0) == _status(window, 1) == mde_app.OUTPUT_COLLISION_TEXT

    window.table.selectRow(1)
    window.target_spin.setValue(12.0)
    assert _status(window, 0) == _status(window, 1) == '대기'


def test_panel_format_and_target_changes(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)
    assert not window.panel.isHidden()

    window.format_combo.setCurrentIndex(window.format_combo.findData('.mov'))
    assert window._rows[0].output_path == str(tmp_path / 'shot_hold_10s.mov')

    window.target_spin.setValue(0.1)
    assert _status(window).startswith('목표 길이가 원본보다 짧습니다')

    window.table.clearSelection()
    assert window.panel.isHidden()


def test_size_preset_and_lock(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)

    window.preset_combo.setCurrentIndex(2)  # FHD
    assert window._rows[0].resize == ResizeSpec(1920, 1080)
    assert window._rows[0].output_path == str(tmp_path / 'shot_hold_10s_1920x1080' / 'shot.%04d.png')

    window.width_spin.setValue(1280)
    assert window._rows[0].resize == ResizeSpec(1280, 720)

    window.fit_combo.setCurrentIndex(window.fit_combo.findData(FIT_CROP))
    assert window._rows[0].resize.fit == FIT_CROP
    assert window.table.item(0, window.COL_RESULT).text().endswith('1280×720 크롭')

    window.preset_combo.setCurrentIndex(mde_app.PRESET_KEEP)
    assert window._rows[0].resize is None


def test_odd_size_for_mp4_shows_error(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)
    window.format_combo.setCurrentIndex(window.format_combo.findData('.mp4'))
    window.preset_combo.setCurrentIndex(mde_app.PRESET_CUSTOM)
    window.lock_check.setChecked(False)
    window.width_spin.setValue(1281)
    assert _status(window) == 'MOV·MP4 출력은 가로·세로가 짝수여야 합니다.'
    assert not window.size_warning.isHidden()


def test_multi_selection_shows_mixed_and_applies_changed_field(window, tmp_path):
    _make_sequence(tmp_path / 'a')
    _make_sequence(tmp_path / 'b')
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)
    window.target_spin.setValue(12.0)

    window._select_all()
    assert window.target_spin.text() == mde_app.MIXED_TEXT
    window.format_combo.setCurrentIndex(window.format_combo.findData('.mov'))
    assert [state.output_ext for state in window._rows] == ['.mov', '.mov']
    assert [state.target_seconds for state in window._rows] == [12.0, 10.0]


def test_keep_length_sets_source_duration(window, tmp_path):
    _make_sequence(tmp_path)
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)
    window.keep_length_btn.click()
    assert _status(window) == '변경 없음'


def test_start_extends_sequence_in_worker_thread(qapp, window, tmp_path):
    _make_sequence(tmp_path, count=5)
    window._add_paths([str(tmp_path)])
    window.table.selectRow(0)
    window.fps_spin.setValue(5.0)
    window.target_spin.setValue(2.0)
    window._start()
    _wait_until_idle(qapp, window)

    assert _status(window) == '완료'
    assert len(list((tmp_path / 'shot_hold_2s').iterdir())) == 10
    assert window.messages[-1] == '처리 완료\n성공: 1\n스킵: 0\n실패: 0'
    assert window.start_btn.isEnabled()


def test_remove_and_clear(window, tmp_path):
    _make_sequence(tmp_path / 'a')
    _make_sequence(tmp_path / 'b')
    window._add_paths([str(tmp_path)])
    assert window.table.rowCount() == 2
    window.table.selectRow(0)
    window._remove_selected()
    assert window.table.rowCount() == 1
    assert len(window._rows) == 1
    window._clear_all()
    assert window.table.rowCount() == 0
    assert window.panel.isHidden()


def test_window_geometry_is_saved_and_restored(qapp, monkeypatch, settings_path):
    first = mde_app.MovieDurationExtender()
    first.resize(760, 620)  # offscreen 화면(800x800) 안의 크기
    first.close()

    second = mde_app.MovieDurationExtender()
    assert (second.width(), second.height()) == (760, 620)
    second.close()


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
