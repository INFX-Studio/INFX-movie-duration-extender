"""INFX Movie Duration Extender 메인 윈도우 (PySide6)."""

from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .core import (
    DEFAULT_SEQUENCE_FPS,
    OUTPUT_EXTENSIONS,
    MediaDurationError,
    MediaInfo,
    default_output_path,
    discover_inputs,
    extend_media,
    normalize_output_extension,
    validate_target,
    warmup_ffprobe,
)
from .update_ui import AutoUpdater

APP_NAME = 'INFX Movie Duration Extender'
APP_DESCRIPTION = 'mov, mp4, jpg/png 시퀀스를 마지막 프레임 hold로 연장하고 mov/mp4/png/jpg로 출력합니다.'
LOG_DIR = Path.home() / 'Library' / 'Logs' / 'INFX Movie Duration Extender'

log = logging.getLogger('mde')

STATUS_WAIT = '대기'
STATUS_NO_CHANGE = '변경 없음'
STATUS_RUNNING = '처리 중'
STATUS_DONE = '완료'
STATUS_NO_OUTPUT = '출력 경로 없음'
STATUS_FAIL_PREFIX = '실패'


@dataclass(frozen=True)
class Job:
    row: int
    info: MediaInfo
    target_seconds: float
    output_path: str
    output_extension: str


class ExtendWorker(QObject):
    """UI 가 멈추지 않도록 별도 스레드에서 항목을 순서대로 처리합니다."""

    row_status = Signal(int, str)
    finished = Signal(int, int)

    def __init__(self, jobs: list[Job]):
        super().__init__()
        self._jobs = jobs

    def run(self) -> None:
        success = 0
        failed = 0
        for job in self._jobs:
            self.row_status.emit(job.row, STATUS_RUNNING)
            try:
                extend_media(job.info, job.target_seconds, job.output_path, job.output_extension)
            except Exception as exc:
                log.exception('처리 실패: %s', job.info.path)
                self.row_status.emit(job.row, f'{STATUS_FAIL_PREFIX}: {exc}')
                failed += 1
            else:
                self.row_status.emit(job.row, STATUS_DONE)
                success += 1
        self.finished.emit(success, failed)


class OutputPathWidget(QWidget):
    """테이블 셀 안에서 출력 경로와 브라우저 버튼을 함께 보여주는 위젯."""

    def __init__(self, path: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.line_edit = QLineEdit(path)
        self.line_edit.setToolTip(path)
        self.line_edit.textEdited.connect(self._mark_user_edited)
        layout.addWidget(self.line_edit)
        self._user_edited = False

        self.button = QPushButton('...')
        self.button.setFixedWidth(36)
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.button)

    def text(self) -> str:
        return self.line_edit.text().strip()

    def is_user_edited(self) -> bool:
        return self._user_edited

    def set_text(self, path: str, user_edited: bool = False) -> None:
        self.line_edit.setText(path)
        self.line_edit.setToolTip(path)
        self._user_edited = user_edited

    def _mark_user_edited(self, *_args) -> None:
        self._user_edited = True

    def _browse(self) -> None:
        current = self.text()
        start_dir = str(Path(current).parent) if current else str(Path.home())
        if '%' in current:
            folder = QFileDialog.getExistingDirectory(self, '출력 폴더 선택', start_dir)
            if folder:
                self.set_text(str(Path(folder) / Path(current).name), user_edited=True)
            return

        selected, _ = QFileDialog.getSaveFileName(self, '출력 파일 선택', current)
        if selected:
            self.set_text(selected, user_edited=True)


class DropTableWidget(QTableWidget):
    """파일 드롭 안내를 표시하는 테이블."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.hint_text = '여기로 mov, mp4, jpg/png 시퀀스 또는 폴더를 드래그하세요'

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.rowCount() > 0:
            return
        painter = QPainter(self.viewport())
        painter.setPen(Qt.GlobalColor.gray)
        painter.drawText(self.viewport().rect(), Qt.AlignmentFlag.AlignCenter, self.hint_text)


class MovieDurationExtender(QWidget):
    """마지막 프레임 hold 방식으로 미디어 길이를 늘리는 메인 윈도우."""

    COL_PATH = 0
    COL_TYPE = 1
    COL_CURRENT = 2
    COL_FPS = 3
    COL_TARGET = 4
    COL_FORMAT = 5
    COL_OUTPUT = 6
    COL_STATUS = 7

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f'{APP_NAME} {__version__}')
        self.resize(1280, 640)
        self.setAcceptDrops(True)
        self._infos: dict[int, MediaInfo] = {}
        self._thread: QThread | None = None
        self._worker: ExtendWorker | None = None
        self._skipped = 0
        self._pre_failed = 0
        self._last_dir = str(Path.home())
        threading.Thread(target=warmup_ffprobe, daemon=True).start()
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        title = QLabel(APP_NAME)
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)
        description = QLabel(APP_DESCRIPTION)
        description.setStyleSheet('color: #9a9a9a;')
        root.addWidget(description)

        top_row = QHBoxLayout()
        root.addLayout(top_row)

        self.add_file_btn = QPushButton('파일 추가')
        self.add_file_btn.clicked.connect(self._select_files)
        top_row.addWidget(self.add_file_btn)

        self.add_folder_btn = QPushButton('폴더 추가')
        self.add_folder_btn.clicked.connect(self._select_folder)
        top_row.addWidget(self.add_folder_btn)

        self.remove_btn = QPushButton('선택 삭제')
        self.remove_btn.clicked.connect(self._remove_selected)
        top_row.addWidget(self.remove_btn)

        self.clear_btn = QPushButton('전체 삭제')
        self.clear_btn.clicked.connect(self._clear_all)
        top_row.addWidget(self.clear_btn)
        top_row.addStretch()

        top_row.addWidget(QLabel('전체 목표 길이(초):'))
        self.global_target = QDoubleSpinBox()
        self.global_target.setDecimals(3)
        self.global_target.setRange(0.001, 86400.0)
        self.global_target.setSingleStep(1.0)
        self.global_target.setValue(10.0)
        top_row.addWidget(self.global_target)

        self.apply_target_btn = QPushButton('선택 항목에 적용')
        self.apply_target_btn.clicked.connect(self._apply_target_to_selected)
        top_row.addWidget(self.apply_target_btn)

        self.table = DropTableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            ['입력', '타입', '현재 길이', 'FPS', '목표 길이(초)', '출력 포맷', '출력', '상태']
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (
            self.COL_TYPE,
            self.COL_CURRENT,
            self.COL_FPS,
            self.COL_TARGET,
            self.COL_FORMAT,
            self.COL_STATUS,
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_OUTPUT, QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.table)

        bottom_row = QHBoxLayout()
        root.addLayout(bottom_row)
        bottom_row.addStretch()

        self.start_btn = QPushButton('시작')
        self.start_btn.setMinimumSize(140, 36)
        self.start_btn.setDefault(True)
        self.start_btn.clicked.connect(self._start)
        bottom_row.addWidget(self.start_btn)

        self.close_btn = QPushButton('닫기')
        self.close_btn.setMinimumSize(140, 36)
        self.close_btn.clicked.connect(self.close)
        bottom_row.addWidget(self.close_btn)

    # ------------------------------------------------------------------
    # 입력 추가
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths and not self._is_running():
            self._add_paths(paths)
            event.acceptProposedAction()
        else:
            event.ignore()

    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            '입력 파일 선택',
            self._last_dir,
            'Media (*.mov *.mp4 *.jpg *.jpeg *.png)',
        )
        if paths:
            self._last_dir = str(Path(paths[0]).parent)
            self._add_paths(paths)

    def _select_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, '입력 폴더 선택', self._last_dir)
        if folder:
            self._last_dir = folder
            self._add_paths([folder])

    def _add_paths(self, paths: list[str]) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            infos = discover_inputs(paths, sequence_fps=DEFAULT_SEQUENCE_FPS)
        except Exception as exc:
            log.exception('입력 분석 실패')
            QApplication.restoreOverrideCursor()
            self._info(f'입력 분석 중 오류가 발생했습니다.\n{exc}')
            return
        QApplication.restoreOverrideCursor()

        if not infos:
            self._info('추가할 수 있는 mov, mp4, jpg/png 시퀀스를 찾지 못했습니다.')
            return

        existing = {info.sequence_pattern or info.path for info in self._infos.values()}
        for info in infos:
            key = info.sequence_pattern or info.path
            if key in existing:
                continue
            self._add_row(info)
            existing.add(key)

    def _add_row(self, info: MediaInfo) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._infos[row] = info

        self._set_item(row, self.COL_PATH, info.path, tooltip=info.path)
        self._set_item(row, self.COL_TYPE, info.display_type)
        self._set_item(row, self.COL_CURRENT, f'{info.duration_seconds:.3f}초')

        fps_spin = QDoubleSpinBox()
        fps_spin.setDecimals(3)
        fps_spin.setRange(1.0, 240.0)
        fps_spin.setValue(info.fps)
        fps_spin.setEnabled(info.media_type == 'sequence')
        fps_spin.valueChanged.connect(lambda _value, w=fps_spin: self._refresh_row_duration(self._row_for_widget(w)))
        self.table.setCellWidget(row, self.COL_FPS, fps_spin)

        target_spin = QDoubleSpinBox()
        target_spin.setDecimals(3)
        target_spin.setRange(0.001, 86400.0)
        target_spin.setSingleStep(1.0)
        target_spin.setValue(max(self.global_target.value(), info.duration_seconds))
        target_spin.valueChanged.connect(lambda _value, w=target_spin: self._row_changed(self._row_for_widget(w)))
        self.table.setCellWidget(row, self.COL_TARGET, target_spin)

        format_combo = QComboBox()
        format_combo.addItems([ext.lstrip('.').upper() for ext in sorted(OUTPUT_EXTENSIONS)])
        format_combo.setCurrentText(normalize_output_extension(info.extension).lstrip('.').upper())
        format_combo.currentTextChanged.connect(
            lambda _value, w=format_combo: self._row_changed(self._row_for_widget(w))
        )
        self.table.setCellWidget(row, self.COL_FORMAT, format_combo)

        out_widget = OutputPathWidget(
            default_output_path(info, target_spin.value(), self._output_extension(row)),
            self,
        )
        self.table.setCellWidget(row, self.COL_OUTPUT, out_widget)

        self._set_status(row, STATUS_WAIT)
        self._validate_row(row)

    # ------------------------------------------------------------------
    # 행 표시·검증
    # ------------------------------------------------------------------

    def _set_item(self, row: int, column: int, text: str, tooltip: str | None = None) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, column, item)

    def _set_status(self, row: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(self._status_color(text))
        item.setToolTip(text)
        self.table.setItem(row, self.COL_STATUS, item)

    @staticmethod
    def _status_color(text: str) -> QColor:
        if text == STATUS_DONE:
            return QColor('#4FC86A')
        if text.startswith(STATUS_FAIL_PREFIX) or text == STATUS_NO_OUTPUT:
            return QColor('#FF5555')
        if text == STATUS_RUNNING:
            return QColor('#FFA040')
        if text == STATUS_NO_CHANGE:
            return QColor('#888888')
        if text == STATUS_WAIT:
            return QColor('#CCCCCC')
        return QColor('#FF8C00')

    def _refresh_row_duration(self, row: int) -> None:
        info = self._row_info(row)
        if not info:
            return
        self._set_item(row, self.COL_CURRENT, f'{info.duration_seconds:.3f}초')
        self._row_changed(row)

    def _row_changed(self, row: int) -> None:
        self._refresh_output_path(row)
        self._validate_row(row)

    def _validate_row(self, row: int) -> bool:
        if row < 0:
            return False
        info = self._row_info(row)
        target = self._target_seconds(row)
        if not info:
            return False
        try:
            target_frames = validate_target(info, target)
            output = self._output_path(row)
            output_ext = self._output_extension(row)
            actual_ext = normalize_output_extension(Path(output).suffix)
            if actual_ext != output_ext:
                raise MediaDurationError('출력 경로 확장자가 선택한 출력 포맷과 다릅니다.')
            if output and output_ext in ('.png', '.jpg') and '%' not in output:
                raise MediaDurationError('이미지 시퀀스 출력 경로에는 %04d 같은 프레임 패턴이 필요합니다.')
            if output and output_ext in ('.mov', '.mp4') and '%' in output:
                raise MediaDurationError('영상 출력 경로에는 프레임 패턴을 사용할 수 없습니다.')
        except MediaDurationError as exc:
            self._set_status(row, str(exc))
            return False

        if target_frames == info.frame_count and not self._has_format_change(info, output_ext):
            self._set_status(row, STATUS_NO_CHANGE)
        else:
            self._set_status(row, STATUS_WAIT)
        return True

    def _row_info(self, row: int) -> MediaInfo | None:
        if row < 0:
            return None
        info = self._infos.get(row)
        if not info:
            return None
        fps_widget = self.table.cellWidget(row, self.COL_FPS)
        if isinstance(fps_widget, QDoubleSpinBox) and info.media_type == 'sequence':
            fps = fps_widget.value()
            return replace(info, fps=fps, duration_seconds=info.frame_count / fps)
        return info

    def _target_seconds(self, row: int) -> float:
        widget = self.table.cellWidget(row, self.COL_TARGET)
        if row >= 0 and isinstance(widget, QDoubleSpinBox):
            return widget.value()
        return 0.0

    def _output_path(self, row: int) -> str:
        widget = self.table.cellWidget(row, self.COL_OUTPUT)
        if row >= 0 and isinstance(widget, OutputPathWidget):
            return widget.text()
        return ''

    def _output_extension(self, row: int) -> str:
        widget = self.table.cellWidget(row, self.COL_FORMAT)
        if isinstance(widget, QComboBox):
            return normalize_output_extension(widget.currentText())
        info = self._row_info(row)
        return normalize_output_extension(info.extension) if info else '.mp4'

    @staticmethod
    def _has_format_change(info: MediaInfo, output_ext: str) -> bool:
        if info.media_type == 'video' and output_ext not in ('.mov', '.mp4'):
            return True
        return normalize_output_extension(info.extension) != output_ext

    def _refresh_output_path(self, row: int) -> None:
        info = self._row_info(row)
        if not info:
            return
        widget = self.table.cellWidget(row, self.COL_OUTPUT)
        if not isinstance(widget, OutputPathWidget) or widget.is_user_edited():
            return
        widget.set_text(default_output_path(info, self._target_seconds(row), self._output_extension(row)))

    def _apply_target_to_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            rows = list(range(self.table.rowCount()))
        for row in rows:
            widget = self.table.cellWidget(row, self.COL_TARGET)
            if isinstance(widget, QDoubleSpinBox):
                widget.setValue(self.global_target.value())
                self._validate_row(row)

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _row_for_widget(self, widget: QWidget) -> int:
        for row in range(self.table.rowCount()):
            for column in (self.COL_FPS, self.COL_TARGET, self.COL_FORMAT, self.COL_OUTPUT):
                if self.table.cellWidget(row, column) is widget:
                    return row
        return -1

    def _remove_selected(self) -> None:
        selected = set(self._selected_rows())
        keep_infos = [
            self._infos[row] for row in range(self.table.rowCount()) if row not in selected and row in self._infos
        ]
        for row in sorted(selected, reverse=True):
            self.table.removeRow(row)
        self._infos = dict(enumerate(keep_infos))

    def _clear_all(self) -> None:
        self.table.setRowCount(0)
        self._infos.clear()

    # ------------------------------------------------------------------
    # 처리 실행
    # ------------------------------------------------------------------

    def _is_running(self) -> bool:
        return self._thread is not None

    def _collect_jobs(self) -> list[Job]:
        """검증을 통과한 행만 작업으로 모읍니다. 스킵·사전 실패 건수는 멤버에 기록합니다."""
        jobs: list[Job] = []
        self._skipped = 0
        self._pre_failed = 0
        for row in range(self.table.rowCount()):
            info = self._row_info(row)
            if not info:
                continue
            target = self._target_seconds(row)
            output = self._output_path(row)
            output_ext = self._output_extension(row)
            if not output:
                self._set_status(row, STATUS_NO_OUTPUT)
                self._pre_failed += 1
                continue
            if not self._validate_row(row):
                self._pre_failed += 1
                continue
            if validate_target(info, target) == info.frame_count and not self._has_format_change(info, output_ext):
                self._skipped += 1
                continue
            jobs.append(Job(row, info, target, output, output_ext))
        return jobs

    def _start(self) -> None:
        if self._is_running():
            return
        if self.table.rowCount() == 0:
            self._info('처리할 항목이 없습니다.')
            return

        jobs = self._collect_jobs()
        if not jobs:
            self._show_summary(0, 0)
            return

        self._set_controls_enabled(False)
        self._thread = QThread(self)
        self._worker = ExtendWorker(jobs)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.row_status.connect(self._set_status)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

    def _on_finished(self, success: int, failed: int) -> None:
        self._thread.quit()
        self._thread.wait()
        self._thread = None
        self._worker = None
        self._set_controls_enabled(True)
        self._show_summary(success, failed)

    def _show_summary(self, success: int, failed: int) -> None:
        self._info(f'처리 완료\n성공: {success}\n스킵: {self._skipped}\n실패: {failed + self._pre_failed}')

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (
            self.add_file_btn,
            self.add_folder_btn,
            self.remove_btn,
            self.clear_btn,
            self.apply_target_btn,
            self.global_target,
            self.start_btn,
            self.table,
        ):
            widget.setEnabled(enabled)
        self.start_btn.setText('시작' if enabled else '처리 중...')

    def closeEvent(self, event):
        if self._is_running():
            self._info('처리 중에는 닫을 수 없습니다. 완료 후 다시 시도해 주세요.')
            event.ignore()
            return
        event.accept()

    def _info(self, text: str) -> None:
        QMessageBox.information(self, APP_NAME, text)


def _apply_dark_theme(app: QApplication) -> None:
    app.setStyle('Fusion')
    palette = QPalette()
    base = QColor('#2b2b2b')
    text = QColor('#dddddd')
    palette.setColor(QPalette.ColorRole.Window, base)
    palette.setColor(QPalette.ColorRole.WindowText, text)
    palette.setColor(QPalette.ColorRole.Base, QColor('#1f1f1f'))
    palette.setColor(QPalette.ColorRole.AlternateBase, base)
    palette.setColor(QPalette.ColorRole.Text, text)
    palette.setColor(QPalette.ColorRole.Button, QColor('#3a3a3a'))
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#3a3a3a'))
    palette.setColor(QPalette.ColorRole.ToolTipText, text)
    palette.setColor(QPalette.ColorRole.Highlight, QColor('#3d6fb4'))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor('#808080'))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor('#777777'))
    app.setPalette(palette)


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_DIR / 'app.log'),
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        encoding='utf-8',
    )


def main() -> int:
    _setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    _apply_dark_theme(app)
    window = MovieDurationExtender()
    window.show()
    auto_updater = AutoUpdater(window, APP_NAME)
    auto_updater.start()
    return app.exec()
