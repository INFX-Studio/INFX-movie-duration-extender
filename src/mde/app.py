"""INFX Movie Duration Extender 메인 윈도우 (PySide6)."""

from __future__ import annotations

import logging
import os
import sys
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QObject, QRect, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .core import (
    DEFAULT_SEQUENCE_FPS,
    FIT_CROP,
    FIT_PAD,
    FIT_STRETCH,
    OUTPUT_EXTENSIONS,
    MediaDurationError,
    MediaInfo,
    ResizeSpec,
    default_output_path,
    discover_inputs,
    extend_media,
    extract_preview_frame,
    normalize_output_extension,
    target_frame_count,
    validate_resize,
    validate_target,
    warmup_ffprobe,
)
from .update_ui import AutoUpdater

APP_NAME = 'INFX Movie Duration Extender'
APP_DESCRIPTION = (
    'mov, mp4, jpg/png 시퀀스를 마지막 프레임 hold로 연장하거나 사이즈를 바꿔 mov/mp4/png/jpg로 출력합니다.'
)
if sys.platform == 'win32':
    LOG_DIR = Path(os.environ['LOCALAPPDATA']) / APP_NAME / 'Logs'
else:
    LOG_DIR = Path.home() / 'Library' / 'Logs' / APP_NAME

log = logging.getLogger('mde')

STATUS_WAIT = '대기'
STATUS_NO_CHANGE = '변경 없음'
STATUS_RUNNING = '처리 중'
STATUS_DONE = '완료'
STATUS_NO_OUTPUT = '출력 경로 없음'
STATUS_FAIL_PREFIX = '실패'
OUTPUT_COLLISION_TEXT = '다른 항목과 출력 경로가 같습니다.'

DEFAULT_TARGET_SECONDS = 10.0
PREVIEW_MAX_WIDTH = 960
MIXED = '__mixed__'
MIXED_TEXT = '여러 값'
# (표시 이름, 종류, 값1, 값2). fixed 는 가로·세로, ratio 는 가로:세로 비율(원본 가로 기준으로 세로 계산).
SIZE_PRESETS = [
    ('원본 유지', 'keep', 0, 0),
    ('HD 720 · 1280×720', 'fixed', 1280, 720),
    ('FHD · 1920×1080', 'fixed', 1920, 1080),
    ('2K DCI · 2048×1080', 'fixed', 2048, 1080),
    ('UHD · 3840×2160', 'fixed', 3840, 2160),
    ('4K DCI · 4096×2160', 'fixed', 4096, 2160),
    ('16:9 (원본 가로 기준)', 'ratio', 16, 9),
    ('4:3 (원본 가로 기준)', 'ratio', 4, 3),
    ('1:1 (원본 가로 기준)', 'ratio', 1, 1),
    ('1.85:1 (원본 가로 기준)', 'ratio', 1.85, 1),
    ('2.39:1 (원본 가로 기준)', 'ratio', 2.39, 1),
    ('직접 입력', 'custom', 0, 0),
]
PRESET_KEEP = 0
PRESET_CUSTOM = len(SIZE_PRESETS) - 1
FIT_LABELS = {FIT_PAD: '여백', FIT_CROP: '크롭', FIT_STRETCH: '늘리기'}
PRIMARY_BUTTON_STYLE = """
QPushButton {
    color: #09222d;
    background-color: #5ab7dc;
    border: 1px solid #5ab7dc;
    border-radius: 5px;
    padding: 4px 8px;
}
QPushButton:hover { background-color: #74c7e5; }
QPushButton:disabled { color: #637783; background-color: #3b454f; border-color: #3b454f; }
"""


def _settings() -> QSettings:
    """창 위치·크기 등 사용자 설정 저장소."""
    return QSettings('INFX', APP_NAME)


def _even(value: float) -> int:
    """가장 가까운 짝수로 반올림합니다(최소 2)."""
    return max(2, round(value / 2.0) * 2)


def _common(values: Iterable):
    """모든 값이 같으면 그 값을, 다르거나 비어 있으면 None 을 돌려줍니다."""
    items = list(values)
    if not items:
        return None
    first = items[0]
    return first if all(item == first for item in items[1:]) else None


@dataclass
class RowState:
    """목록 한 행의 입력 정보와 출력 설정.

    Attributes:
        info: 입력 정보. 시퀀스 FPS 를 바꾸면 그 값이 반영됩니다.
        target_seconds: 목표 길이(초).
        output_ext: 출력 확장자.
        output_path: 출력 경로.
        output_user_edited: 사용자가 출력 경로를 직접 바꿨는지 여부.
        resize: 리사이즈 설정. None 이면 원본 크기 유지.
        aspect: '비율 유지'로 지킬 가로/세로 비율. None 이면 원본 비율.
    """

    info: MediaInfo
    target_seconds: float
    output_ext: str
    output_path: str = ''
    output_user_edited: bool = False
    resize: ResizeSpec | None = None
    aspect: float | None = None


@dataclass(frozen=True)
class Job:
    row: int
    info: MediaInfo
    target_seconds: float
    output_path: str
    output_extension: str
    resize: ResizeSpec | None


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
                extend_media(
                    job.info, job.target_seconds, job.output_path, job.output_extension, resize=job.resize
                )
            except Exception as exc:
                log.exception('처리 실패: %s', job.info.path)
                self.row_status.emit(job.row, f'{STATUS_FAIL_PREFIX}: {exc}')
                failed += 1
            else:
                self.row_status.emit(job.row, STATUS_DONE)
                success += 1
        self.finished.emit(success, failed)


class _PreviewSignals(QObject):
    """백그라운드 프레임 추출 결과(key, 이미지 경로, 오류)를 UI 스레드로 넘깁니다."""

    loaded = Signal(str, str, str)


class OutputPathWidget(QWidget):
    """출력 경로와 찾아보기 버튼을 함께 보여주는 위젯."""

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


class PreviewWidget(QWidget):
    """원본 프레임과 리사이즈 결과를 나란히 그리는 미리보기.

    결과 쪽은 ffmpeg 를 다시 돌리지 않고 맞춤 방식(여백·크롭·늘리기)대로 Qt 에서 바로 그립니다.
    """

    GAP = 28

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self._image: QImage | None = None
        self._source_size = (0, 0)
        self._resize: ResizeSpec | None = None
        self._message = ''

    def set_state(
        self,
        image: QImage | None,
        source_size: tuple[int, int],
        resize: ResizeSpec | None,
        message: str = '',
    ) -> None:
        """그릴 이미지와 리사이즈 설정을 바꿉니다.

        Args:
            image: 원본 프레임. 없으면 message 를 표시합니다.
            source_size: 원본 가로·세로. 모르면 (0, 0).
            resize: 리사이즈 설정. None 이면 원본 크기.
            message: 이미지가 없을 때 보여줄 문구.
        """
        self._image = image
        self._source_size = source_size
        self._resize = resize
        self._message = message
        self.update()

    @staticmethod
    def _fit_rect(area: QRect, width: int, height: int) -> QRect:
        if width <= 0 or height <= 0:
            width, height = 16, 9
        scale = min(area.width() / width, area.height() / height)
        w = max(1, int(width * scale))
        h = max(1, int(height * scale))
        return QRect(area.x() + (area.width() - w) // 2, area.y() + (area.height() - h) // 2, w, h)

    def _result_rect(self, box: QRect, source_w: int, source_h: int) -> QRect:
        """맞춤 방식에 따라 결과 상자 안에서 원본을 그릴 영역을 계산합니다."""
        if self._resize is None or self._resize.fit == FIT_STRETCH or source_w <= 0 or source_h <= 0:
            return box
        scale_x = box.width() / source_w
        scale_y = box.height() / source_h
        scale = max(scale_x, scale_y) if self._resize.fit == FIT_CROP else min(scale_x, scale_y)
        w = max(1, round(source_w * scale))
        h = max(1, round(source_h * scale))
        return QRect(box.x() + (box.width() - w) // 2, box.y() + (box.height() - h) // 2, w, h)

    @staticmethod
    def _draw_caption(painter: QPainter, area: QRect, lines: list[str]) -> None:
        """상자 아래에 제목·크기·맞춤 방식을 한 줄씩 그립니다. 칸보다 길면 말줄임표로 줄입니다."""
        metrics = painter.fontMetrics()
        line_h = metrics.height()
        for index, text in enumerate(lines):
            if not text:
                continue
            painter.setPen(QColor('#888888') if index == 0 else QColor('#CCCCCC'))
            line_rect = QRect(area.x(), area.y() + index * line_h, area.width(), line_h)
            elided = metrics.elidedText(text, Qt.TextElideMode.ElideRight, area.width())
            painter.drawText(line_rect, Qt.AlignmentFlag.AlignCenter, elided)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        caption_h = painter.fontMetrics().height() * 3 + 6
        half_w = max(1, (self.width() - self.GAP) // 2)
        box_h = max(1, self.height() - caption_h)

        source_w, source_h = self._source_size
        if (source_w <= 0 or source_h <= 0) and self._image is not None:
            source_w, source_h = self._image.width(), self._image.height()
        if self._resize is not None:
            target_w, target_h = self._resize.width, self._resize.height
        else:
            target_w, target_h = source_w, source_h

        left = self._fit_rect(QRect(0, 0, half_w, box_h), source_w, source_h)
        right = self._fit_rect(QRect(half_w + self.GAP, 0, half_w, box_h), target_w, target_h)
        painter.fillRect(left, QColor('#202020'))
        painter.fillRect(right, QColor('#000000'))

        if self._image is not None:
            painter.drawImage(left, self._image)
            painter.save()
            painter.setClipRect(right)
            painter.drawImage(self._result_rect(right, source_w, source_h), self._image)
            painter.restore()

        painter.setPen(QColor('#888888'))
        if self._image is None and self._message:
            flags = Qt.AlignmentFlag.AlignCenter.value | Qt.TextFlag.TextWordWrap.value
            painter.drawText(left, flags, self._message)
        painter.drawText(QRect(half_w, 0, self.GAP, box_h), Qt.AlignmentFlag.AlignCenter, '→')

        # 한 줄에 몰아 쓰면 좁은 패널에서 잘리므로 제목 / 크기 / 맞춤 방식을 줄을 나눠 씁니다.
        source_lines = ['원본', f'{source_w}×{source_h}' if source_w > 0 else '크기 미확인', '']
        if self._resize is not None:
            result_lines = ['결과', f'{target_w}×{target_h}', f'맞춤: {FIT_LABELS.get(self._resize.fit, "")}']
        else:
            result_lines = ['결과', '원본 크기', '']
        self._draw_caption(painter, QRect(0, box_h + 4, half_w, caption_h), source_lines)
        self._draw_caption(painter, QRect(half_w + self.GAP, box_h + 4, half_w, caption_h), result_lines)
        painter.end()


class MovieDurationExtender(QWidget):
    """마지막 프레임 hold 연장과 사이즈 조절을 함께 하는 메인 윈도우.

    목록은 요약만 보여주고, 행을 선택하면 오른쪽에 설정 패널이 나타납니다.
    여러 행을 선택하면 값이 다른 칸은 '여러 값'으로 보이고, 바꾼 칸만 선택 항목 전체에 적용됩니다.
    """

    COL_PATH = 0
    COL_SOURCE = 1
    COL_RESULT = 2
    COL_OUTPUT = 3
    COL_STATUS = 4

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f'{APP_NAME} {__version__}')
        self.resize(1440, 760)
        self.setAcceptDrops(True)
        self._rows: list[RowState] = []
        self._populating = False
        self._thread: QThread | None = None
        self._worker: ExtendWorker | None = None
        self._skipped = 0
        self._pre_failed = 0
        self._last_dir = str(Path.home())
        self._preview_dir = tempfile.TemporaryDirectory(prefix='media_duration_preview_')
        self._preview_paths: dict[str, str] = {}
        self._preview_images: dict[str, QImage] = {}
        self._preview_pending: set[str] = set()
        self._preview_signals = _PreviewSignals()
        self._preview_signals.loaded.connect(self._on_preview_loaded)
        threading.Thread(target=warmup_ffprobe, daemon=True).start()
        self._build_ui()
        self._restore_geometry()

    # ------------------------------------------------------------------
    # UI 구성
    # ------------------------------------------------------------------

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

        self.select_all_btn = QPushButton('전체 선택')
        self.select_all_btn.clicked.connect(self._select_all)
        top_row.addWidget(self.select_all_btn)

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
        self.global_target.setValue(DEFAULT_TARGET_SECONDS)
        top_row.addWidget(self.global_target)

        self.apply_target_btn = QPushButton('선택 항목에 적용')
        self.apply_target_btn.setToolTip('선택한 항목에 적용합니다. 선택이 없으면 전체 항목에 적용합니다.')
        self.apply_target_btn.clicked.connect(self._apply_target_to_selected)
        top_row.addWidget(self.apply_target_btn)

        middle_row = QHBoxLayout()
        root.addLayout(middle_row, 1)

        self.table = DropTableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(['입력', '원본', '결과', '출력', '상태'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # 경로가 '\' 에서 줄바꿈되며 "C:..." 로만 보이지 않도록 한 줄로 두고 가운데를 줄입니다.
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        for column in (self.COL_SOURCE, self.COL_RESULT, self.COL_STATUS):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_PATH, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(self.COL_OUTPUT, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        middle_row.addWidget(self.table, 1)

        self.panel = self._build_panel()
        self.panel.hide()
        middle_row.addWidget(self.panel)

        bottom_row = QHBoxLayout()
        root.addLayout(bottom_row)

        self.summary_label = QLabel('')
        bottom_row.addWidget(self.summary_label)
        bottom_row.addStretch()

        self.start_btn = QPushButton('시작')
        self.start_btn.setMinimumSize(140, 36)
        self.start_btn.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.start_btn.clicked.connect(self._start)
        bottom_row.addWidget(self.start_btn)

    def _build_panel(self) -> QFrame:
        """선택한 항목의 설정 패널을 만듭니다. 선택이 없으면 숨겨집니다."""
        panel = QFrame(self)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setMinimumWidth(360)
        panel.setMaximumWidth(440)
        layout = QVBoxLayout(panel)

        self.panel_title = QLabel('')
        self.panel_title.setStyleSheet('font-weight: bold;')
        layout.addWidget(self.panel_title)

        # 길이
        layout.addWidget(self._section_label('길이'))
        length_grid = QGridLayout()
        layout.addLayout(length_grid)

        self.target_spin = QDoubleSpinBox()
        self.target_spin.setDecimals(3)
        self.target_spin.setRange(0.0, 86400.0)
        self.target_spin.setSingleStep(1.0)
        self.target_spin.setSpecialValueText(MIXED_TEXT)
        self.target_spin.setKeyboardTracking(False)
        self.target_spin.valueChanged.connect(self._on_target_changed)
        self.keep_length_btn = QPushButton('원본 길이로')
        self.keep_length_btn.setToolTip('선택 항목의 목표 길이를 각자의 원본 길이로 맞춥니다(사이즈만 바꿀 때).')
        self.keep_length_btn.clicked.connect(self._on_keep_length)
        length_grid.addWidget(QLabel('목표 길이(초)'), 0, 0)
        length_grid.addWidget(self.target_spin, 0, 1)
        length_grid.addWidget(self.keep_length_btn, 0, 2)

        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setDecimals(3)
        self.fps_spin.setRange(0.0, 240.0)
        self.fps_spin.setSpecialValueText(MIXED_TEXT)
        self.fps_spin.setKeyboardTracking(False)
        self.fps_spin.setToolTip('이미지 시퀀스만 바꿀 수 있습니다. 영상 FPS는 자동으로 읽습니다.')
        self.fps_spin.valueChanged.connect(self._on_fps_changed)
        length_grid.addWidget(QLabel('FPS'), 1, 0)
        length_grid.addWidget(self.fps_spin, 1, 1)

        self.frames_label = self._hint_label()
        layout.addWidget(self.frames_label)

        # 사이즈
        layout.addWidget(self._section_label('사이즈'))
        self.preset_combo = QComboBox()
        for index, (label, _kind, _first, _second) in enumerate(SIZE_PRESETS):
            self.preset_combo.addItem(label, index)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)
        layout.addWidget(self.preset_combo)

        size_row = QHBoxLayout()
        layout.addLayout(size_row)
        self.width_spin = self._size_spin()
        self.width_spin.valueChanged.connect(lambda value: self._on_size_changed('width', value))
        self.height_spin = self._size_spin()
        self.height_spin.valueChanged.connect(lambda value: self._on_size_changed('height', value))
        self.lock_check = QCheckBox('비율 유지')
        self.lock_check.setChecked(True)
        self.lock_check.setToolTip(
            '한쪽을 바꾸면 지금 비율(고른 프리셋·비율, 없으면 원본 비율)에 맞춰 다른 쪽을 짝수로 계산합니다.'
        )
        size_row.addWidget(self.width_spin, 1)
        size_row.addWidget(QLabel('×'))
        size_row.addWidget(self.height_spin, 1)
        size_row.addWidget(self.lock_check)

        fit_row = QHBoxLayout()
        layout.addLayout(fit_row)
        fit_row.addWidget(QLabel('비율이 다를 때'))
        self.fit_combo = QComboBox()
        for fit, label in FIT_LABELS.items():
            self.fit_combo.addItem(label, fit)
        self.fit_combo.setToolTip('여백: 검은 여백으로 채움 / 크롭: 꽉 채우고 넘치는 곳을 자름 / 늘리기: 비율 무시')
        self.fit_combo.currentIndexChanged.connect(self._on_fit_changed)
        fit_row.addWidget(self.fit_combo, 1)

        self.size_warning = QLabel('')
        self.size_warning.setStyleSheet('color: #FF5555;')
        self.size_warning.setWordWrap(True)
        self.size_warning.hide()
        layout.addWidget(self.size_warning)

        # 출력
        layout.addWidget(self._section_label('출력'))
        self.format_combo = QComboBox()
        for ext in sorted(OUTPUT_EXTENSIONS):
            self.format_combo.addItem(ext.lstrip('.').upper(), ext)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        layout.addWidget(self.format_combo)
        self.output_widget = OutputPathWidget('', panel)
        self.output_widget.line_edit.textChanged.connect(self._on_output_path_changed)
        layout.addWidget(self.output_widget)

        # 미리보기
        layout.addWidget(self._section_label('미리보기'))
        self.preview_name = self._hint_label()
        layout.addWidget(self.preview_name)
        self.preview = PreviewWidget(panel)
        layout.addWidget(self.preview)

        frame_row = QHBoxLayout()
        layout.addLayout(frame_row)
        self.first_frame_radio = QRadioButton('첫 프레임')
        self.last_frame_radio = QRadioButton('마지막 프레임')
        self.first_frame_radio.setChecked(True)
        self._frame_group = QButtonGroup(panel)
        self._frame_group.addButton(self.first_frame_radio)
        self._frame_group.addButton(self.last_frame_radio)
        self.first_frame_radio.toggled.connect(self._on_frame_choice_changed)
        frame_row.addWidget(self.first_frame_radio)
        frame_row.addWidget(self.last_frame_radio)
        frame_row.addStretch()

        layout.addStretch()
        return panel

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet('color: #9A9A9A; font-weight: bold; margin-top: 6px;')
        return label

    @staticmethod
    def _hint_label() -> QLabel:
        label = QLabel('')
        label.setStyleSheet('color: #9A9A9A;')
        label.setWordWrap(True)
        return label

    @staticmethod
    def _size_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(0, 16384)
        spin.setSingleStep(2)
        spin.setKeyboardTracking(False)
        return spin

    # ------------------------------------------------------------------
    # 창 위치·크기
    # ------------------------------------------------------------------

    def _restore_geometry(self) -> None:
        geometry = _settings().value('window/geometry')
        if geometry:
            self.restoreGeometry(geometry)

    def _save_geometry(self) -> None:
        _settings().setValue('window/geometry', self.saveGeometry())

    # ------------------------------------------------------------------
    # 입력 추가·삭제
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

        existing = {state.info.sequence_pattern or state.info.path for state in self._rows}
        duplicates = [info for info in infos if (info.sequence_pattern or info.path) in existing]
        if duplicates and not self._confirm_add_duplicates(duplicates):
            infos = [info for info in infos if (info.sequence_pattern or info.path) not in existing]

        for info in infos:
            self._add_row(info)
        self._refresh_statuses()

    def _confirm_add_duplicates(self, duplicates: list[MediaInfo]) -> bool:
        """이미 목록에 있는 항목도 추가할지 묻습니다. 중복 항목도 추가하면 True 를 돌려줍니다."""
        shown = 5
        names = '\n'.join(f'· {Path(info.path).name}' for info in duplicates[:shown])
        if len(duplicates) > shown:
            names += f'\n· 외 {len(duplicates) - shown}개'
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(
            f'이미 목록에 있는 항목이 {len(duplicates)}개 있습니다.\n{names}\n\n'
            '그래도 추가할까요? 설정이 같으면 출력 경로가 겹치므로, '
            '추가한 뒤 길이·사이즈·출력 경로 중 하나를 바꿔야 처리됩니다.'
        )
        add_button = box.addButton('중복도 추가', QMessageBox.ButtonRole.AcceptRole)
        skip_button = box.addButton('중복 제외', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(skip_button)
        box.setEscapeButton(skip_button)
        box.exec()
        return box.clickedButton() is add_button

    def _add_row(self, info: MediaInfo) -> None:
        state = RowState(
            info=info,
            target_seconds=max(self.global_target.value(), info.duration_seconds),
            output_ext=normalize_output_extension(info.extension),
        )
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows.append(state)
        self._set_item(row, self.COL_PATH, info.path, tooltip=info.path)
        self._refresh_row(row)

    def _select_all(self) -> None:
        self.table.selectAll()

    def _apply_target_to_selected(self) -> None:
        """상단 전체 목표 길이를 선택 항목에, 선택이 없으면 전체 항목에 적용합니다."""
        selected = self._selected_rows()
        value = self.global_target.value()
        for row in selected or range(len(self._rows)):
            self._rows[row].target_seconds = value
            self._refresh_row(row)
        self._refresh_statuses()
        if selected:
            self._sync_panel()

    def _remove_selected(self) -> None:
        selected = self._selected_rows()
        self.table.blockSignals(True)
        try:
            for row in reversed(selected):
                self.table.removeRow(row)
                del self._rows[row]
        finally:
            self.table.blockSignals(False)
        self._refresh_statuses()
        self._on_selection_changed()

    def _clear_all(self) -> None:
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            self._rows.clear()
        finally:
            self.table.blockSignals(False)
        self._refresh_statuses()
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # 목록 표시
    # ------------------------------------------------------------------

    def _set_item(self, row: int, column: int, text: str, tooltip: str | None = None) -> QTableWidgetItem:
        # 선택이 풀리지 않도록 기존 아이템이 있으면 글자만 바꿉니다.
        item = self.table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, column, item)
        else:
            item.setText(text)
        if tooltip is not None:
            item.setToolTip(tooltip)
        return item

    def _set_status(self, row: int, text: str) -> None:
        item = self._set_item(row, self.COL_STATUS, text, tooltip=text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setForeground(self._status_color(text))

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

    def _refresh_row(self, row: int) -> None:
        """행의 출력 경로(자동일 때)와 요약 칸을 다시 계산합니다. 상태는 _refresh_statuses 가 맡습니다."""
        state = self._rows[row]
        info = state.info
        if not state.output_user_edited:
            state.output_path = default_output_path(info, state.target_seconds, state.output_ext, state.resize)

        source_size = f'{info.width}×{info.height}' if info.width > 0 and info.height > 0 else '크기 미확인'
        self._set_item(row, self.COL_SOURCE, f'{info.display_type} · {info.duration_seconds:.3f}초 · {source_size}')
        if state.resize is not None:
            size_text = f'{state.resize.width}×{state.resize.height} {FIT_LABELS.get(state.resize.fit, "")}'
        else:
            size_text = '원본 크기'
        self._set_item(
            row,
            self.COL_RESULT,
            f'{state.output_ext.lstrip(".").upper()} · {state.target_seconds:.3f}초 · {size_text}',
        )
        self._set_item(row, self.COL_OUTPUT, state.output_path, tooltip=state.output_path)

    @staticmethod
    def _output_key(path: str) -> str:
        return os.path.normcase(os.path.normpath(path))

    def _colliding_outputs(self) -> set[str]:
        """두 개 이상의 행이 같은 출력 경로를 쓰고 있으면 그 경로들을 돌려줍니다."""
        seen: set[str] = set()
        colliding: set[str] = set()
        for state in self._rows:
            if not state.output_path:
                continue
            key = self._output_key(state.output_path)
            if key in seen:
                colliding.add(key)
            seen.add(key)
        return colliding

    def _row_status(self, state: RowState, colliding: set[str]) -> str:
        """처리 전 상태 문구를 돌려줍니다. 문제가 있으면 오류 문구입니다.

        Args:
            state: 행 상태.
            colliding: 여러 행이 함께 쓰는 출력 경로 목록(_colliding_outputs 결과).
        """
        info = state.info
        output = state.output_path
        if not output:
            return STATUS_NO_OUTPUT
        if self._output_key(output) in colliding:
            return OUTPUT_COLLISION_TEXT
        try:
            target_frames = validate_target(info, state.target_seconds)
            actual_ext = normalize_output_extension(Path(output).suffix)
            if actual_ext != state.output_ext:
                raise MediaDurationError('출력 경로 확장자가 선택한 출력 포맷과 다릅니다.')
            if state.output_ext in ('.png', '.jpg') and '%' not in output:
                raise MediaDurationError('이미지 시퀀스 출력 경로에는 %04d 같은 프레임 패턴이 필요합니다.')
            if state.output_ext in ('.mov', '.mp4') and '%' in output:
                raise MediaDurationError('영상 출력 경로에는 프레임 패턴을 사용할 수 없습니다.')
            validate_resize(state.resize, state.output_ext)
        except MediaDurationError as exc:
            return str(exc)

        if (
            target_frames == info.frame_count
            and state.resize is None
            and not self._has_format_change(info, state.output_ext)
        ):
            return STATUS_NO_CHANGE
        return STATUS_WAIT

    def _refresh_statuses(self) -> None:
        """모든 행의 상태와 하단 요약을 다시 계산합니다(출력 경로 겹침은 행끼리 비교해야 해서 전체를 봅니다)."""
        colliding = self._colliding_outputs()
        pending = 0
        for row, state in enumerate(self._rows):
            status = self._row_status(state, colliding)
            self._set_status(row, status)
            if status == STATUS_WAIT:
                pending += 1
        total = len(self._rows)
        if not total:
            self.summary_label.setText('')
            return
        self.summary_label.setText(f'{total}개 중 {pending}개 처리 예정 · 행을 선택하면 설정 패널이 열립니다')

    @staticmethod
    def _has_format_change(info: MediaInfo, output_ext: str) -> bool:
        if info.media_type == 'video' and output_ext not in ('.mov', '.mp4'):
            return True
        return normalize_output_extension(info.extension) != output_ext

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _selected_states(self) -> list[tuple[int, RowState]]:
        return [(row, self._rows[row]) for row in self._selected_rows() if row < len(self._rows)]

    # ------------------------------------------------------------------
    # 설정 패널
    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        if not self._selected_states():
            self.panel.hide()
            return
        self.panel.show()
        self._sync_panel()

    def _ratio_size(self, info: MediaInfo, ratio_w: float, ratio_h: float) -> tuple[int, int]:
        """원본 가로를 유지하고 세로를 비율에 맞춘 크기(짝수)를 돌려줍니다."""
        width = self._source_even_size(info)[0]
        return width, _even(width * ratio_h / ratio_w)

    def _preset_index(self, state: RowState) -> int:
        """행의 현재 사이즈에 해당하는 프리셋 번호. 고정 크기를 먼저 찾고, 없으면 비율 프리셋을 찾습니다."""
        resize = state.resize
        if resize is None:
            return PRESET_KEEP
        size = (resize.width, resize.height)
        for index, (_label, kind, first, second) in enumerate(SIZE_PRESETS):
            if kind == 'fixed' and (first, second) == size:
                return index
        for index, (_label, kind, first, second) in enumerate(SIZE_PRESETS):
            if kind == 'ratio' and self._ratio_size(state.info, first, second) == size:
                return index
        return PRESET_CUSTOM

    @staticmethod
    def _sync_combo(combo: QComboBox, value) -> None:
        """콤보를 value 로 맞춥니다. None 이면 '여러 값' 항목을 넣어 선택합니다."""
        mixed_index = combo.findData(MIXED)
        if value is None:
            if mixed_index < 0:
                combo.insertItem(0, MIXED_TEXT, MIXED)
                mixed_index = 0
            combo.setCurrentIndex(mixed_index)
            return
        if mixed_index >= 0:
            combo.removeItem(mixed_index)
        combo.setCurrentIndex(combo.findData(value))

    @staticmethod
    def _source_even_size(info: MediaInfo) -> tuple[int, int]:
        if info.width > 0 and info.height > 0:
            return _even(info.width), _even(info.height)
        return 1920, 1080

    def _sync_panel(self) -> None:
        """선택 항목의 현재 값으로 패널을 채웁니다. 값이 다르면 '여러 값'으로 보입니다."""
        states = [state for _row, state in self._selected_states()]
        if not states:
            self.panel.hide()
            return

        self._populating = True
        try:
            self.panel_title.setText(f'선택 {len(states)}개 설정')

            target = _common(state.target_seconds for state in states)
            self.target_spin.setValue(target if target is not None else 0.0)
            all_sequence = all(state.info.media_type == 'sequence' for state in states)
            fps = _common(state.info.fps for state in states)
            self.fps_spin.setEnabled(all_sequence)
            self.fps_spin.setValue(fps if fps is not None else 0.0)
            if len(states) == 1:
                info = states[0].info
                frames = target_frame_count(states[0].target_seconds, info.fps)
                note = '' if all_sequence else ' · FPS는 영상에서 자동으로 읽음'
                self.frames_label.setText(f'= {frames}프레임 (원본 {info.frame_count}프레임){note}')
            else:
                self.frames_label.setText("값이 다른 칸은 '여러 값'으로 보이고, 바꾼 칸만 선택 항목 전체에 적용됩니다.")

            self._sync_combo(self.preset_combo, _common(self._preset_index(state) for state in states))
            resized = [state.resize for state in states if state.resize is not None]
            all_resized = len(resized) == len(states)
            for spin, attr in ((self.width_spin, 'width'), (self.height_spin, 'height')):
                spin.setEnabled(bool(resized))
                if not resized:
                    spin.setSpecialValueText('원본')
                    spin.setValue(0)
                    continue
                value = _common(getattr(resize, attr) for resize in resized) if all_resized else None
                spin.setSpecialValueText(MIXED_TEXT)
                spin.setValue(value or 0)
            self.lock_check.setEnabled(bool(resized))
            self.fit_combo.setEnabled(bool(resized))
            self._sync_combo(self.fit_combo, _common(resize.fit for resize in resized) if resized else FIT_PAD)

            errors = set()
            for state in states:
                try:
                    validate_resize(state.resize, state.output_ext)
                except MediaDurationError as exc:
                    errors.add(str(exc))
            self.size_warning.setText('\n'.join(sorted(errors)))
            self.size_warning.setVisible(bool(errors))

            self._sync_combo(self.format_combo, _common(state.output_ext for state in states))
            if len(states) == 1:
                self.output_widget.setEnabled(True)
                self.output_widget.line_edit.setPlaceholderText('')
                self.output_widget.set_text(states[0].output_path, states[0].output_user_edited)
            else:
                self.output_widget.setEnabled(False)
                self.output_widget.set_text('')
                self.output_widget.line_edit.setPlaceholderText('항목별 자동 경로')
        finally:
            self._populating = False
        self._update_preview()

    def _apply_to_selected(self, update: Callable[[RowState], None]) -> None:
        """선택한 모든 행에 update 를 적용하고 목록과 패널을 갱신합니다."""
        if self._populating:
            return
        for row, state in self._selected_states():
            update(state)
            self._refresh_row(row)
        self._refresh_statuses()
        self._sync_panel()

    def _on_target_changed(self, value: float) -> None:
        if value <= 0:
            return

        def update(state: RowState) -> None:
            state.target_seconds = value

        self._apply_to_selected(update)

    def _on_keep_length(self) -> None:
        def update(state: RowState) -> None:
            # 원본 프레임 수와 정확히 같아지도록 frame_count / fps 로 맞춥니다.
            state.target_seconds = state.info.frame_count / state.info.fps

        self._apply_to_selected(update)

    def _on_fps_changed(self, value: float) -> None:
        if value <= 0:
            return

        def update(state: RowState) -> None:
            if state.info.media_type != 'sequence':
                return
            state.info = replace(state.info, fps=value, duration_seconds=state.info.frame_count / value)

        self._apply_to_selected(update)

    def _on_preset_changed(self, index: int) -> None:
        data = self.preset_combo.itemData(index)
        if self._populating or data == MIXED or data is None:
            return
        _label, kind, first, second = SIZE_PRESETS[data]

        def update(state: RowState) -> None:
            fit = state.resize.fit if state.resize is not None else FIT_PAD
            if kind == 'keep':
                state.resize = None
                state.aspect = None
            elif kind == 'custom':
                if state.resize is None:
                    state.resize = ResizeSpec(*self._source_even_size(state.info), fit)
                    state.aspect = None
            elif kind == 'ratio':
                state.resize = ResizeSpec(*self._ratio_size(state.info, first, second), fit)
                state.aspect = first / second
            else:
                state.resize = ResizeSpec(first, second, fit)
                state.aspect = first / second

        self._apply_to_selected(update)

    def _on_size_changed(self, axis: str, value: int) -> None:
        if self._populating or value <= 0:
            return
        locked = self.lock_check.isChecked()

        def update(state: RowState) -> None:
            current = state.resize or ResizeSpec(*self._source_even_size(state.info))
            width, height = current.width, current.height
            info = state.info
            # 고른 프리셋·비율이 있으면 그 비율, 없으면 원본 비율을 지킵니다(반올림 오차가 쌓이지 않게 저장한 값을 씁니다).
            ratio = state.aspect
            if ratio is None:
                ratio = info.width / info.height if info.width > 0 and info.height > 0 else width / height
            if axis == 'width':
                width = value
                if locked:
                    height = _even(value / ratio)
            else:
                height = value
                if locked:
                    width = _even(value * ratio)
            if not locked:
                state.aspect = width / height
            state.resize = ResizeSpec(width, height, current.fit)

        self._apply_to_selected(update)

    def _on_fit_changed(self, index: int) -> None:
        fit = self.fit_combo.itemData(index)
        if self._populating or fit == MIXED or fit is None:
            return

        def update(state: RowState) -> None:
            if state.resize is not None:
                state.resize = replace(state.resize, fit=fit)

        self._apply_to_selected(update)

    def _on_format_changed(self, index: int) -> None:
        ext = self.format_combo.itemData(index)
        if self._populating or ext == MIXED or ext is None:
            return

        def update(state: RowState) -> None:
            state.output_ext = ext

        self._apply_to_selected(update)

    def _on_output_path_changed(self, _text: str) -> None:
        # 직접 입력·찾아보기 모두 사용자 지정 경로로 봅니다. 패널 전체를 다시 채우면 입력 중 커서가 튀므로 행만 갱신합니다.
        if self._populating:
            return
        selected = self._selected_states()
        if len(selected) != 1:
            return
        row, state = selected[0]
        state.output_path = self.output_widget.text()
        state.output_user_edited = True
        self._refresh_row(row)
        self._refresh_statuses()

    def _on_frame_choice_changed(self, _checked: bool) -> None:
        self._update_preview()

    # ------------------------------------------------------------------
    # 미리보기
    # ------------------------------------------------------------------

    def _update_preview(self) -> None:
        """선택 중 첫 항목의 프레임과 설정으로 미리보기를 다시 그립니다."""
        states = [state for _row, state in self._selected_states()]
        if not states:
            return
        state = states[0]
        which = 'last' if self.last_frame_radio.isChecked() else 'first'
        key = f'{state.info.path}|{which}'
        name = Path(state.info.path).name
        self.preview_name.setText(name if len(states) == 1 else f'{name} (선택 중 첫 항목)')
        source_size = (state.info.width, state.info.height)

        if key in self._preview_paths:
            image = self._preview_image(key)
            message = '' if image is not None else '미리보기를 불러오지 못했습니다.'
            self.preview.set_state(image, source_size, state.resize, message)
            return

        self.preview.set_state(None, source_size, state.resize, '미리보기 불러오는 중…')
        if key not in self._preview_pending:
            self._preview_pending.add(key)
            threading.Thread(
                target=self._load_preview_frame,
                args=(key, state.info, which),
                daemon=True,
            ).start()

    def _load_preview_frame(self, key: str, info: MediaInfo, which: str) -> None:
        """백그라운드 스레드에서 미리보기 프레임을 준비합니다."""
        try:
            path = extract_preview_frame(info, which, self._preview_dir.name)
            error = ''
        except (MediaDurationError, OSError) as exc:
            path, error = '', str(exc)
        self._preview_signals.loaded.emit(key, path, error)

    def _on_preview_loaded(self, key: str, path: str, error: str) -> None:
        self._preview_pending.discard(key)
        self._preview_paths[key] = path
        if error:
            log.warning('미리보기 프레임 추출 실패: %s', error)
        self._update_preview()

    def _preview_image(self, key: str) -> QImage | None:
        if key in self._preview_images:
            return self._preview_images[key]
        path = self._preview_paths.get(key)
        image = QImage(path) if path else QImage()
        if image.isNull():
            return None
        if image.width() > PREVIEW_MAX_WIDTH:
            image = image.scaledToWidth(PREVIEW_MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)
        self._preview_images[key] = image
        return image

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
        colliding = self._colliding_outputs()
        for row, state in enumerate(self._rows):
            status = self._row_status(state, colliding)
            self._set_status(row, status)
            if status == STATUS_NO_CHANGE:
                self._skipped += 1
                continue
            if status != STATUS_WAIT:
                self._pre_failed += 1
                continue
            jobs.append(Job(row, state.info, state.target_seconds, state.output_path, state.output_ext, state.resize))
        return jobs

    def _start(self) -> None:
        if self._is_running():
            return
        if not self._rows:
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
            self.select_all_btn,
            self.remove_btn,
            self.clear_btn,
            self.apply_target_btn,
            self.global_target,
            self.start_btn,
            self.table,
            self.panel,
        ):
            widget.setEnabled(enabled)
        self.start_btn.setText('시작' if enabled else '처리 중...')

    def closeEvent(self, event):
        if self._is_running():
            self._info('처리 중에는 닫을 수 없습니다. 완료 후 다시 시도해 주세요.')
            event.ignore()
            return
        self._save_geometry()
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
