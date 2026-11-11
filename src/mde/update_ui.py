"""시작 시 자동 업데이트 흐름 (PySide6).

패키징된 앱으로 실행 중일 때만 동작합니다. (macOS 는 `/Applications` 에 설치된 번들만 해당합니다.)
새 릴리즈가 있으면 진행 창을 띄우고 배포 파일을 받은 뒤, 헬퍼 스크립트를 실행하고 앱을 종료합니다.
네트워크 오류 등으로 확인·다운로드에 실패하면 로그만 남기고 현재 버전으로 계속 실행합니다.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QWidget

from . import __version__, updater

log = logging.getLogger(__name__)


class _CheckWorker(QObject):
    found = Signal(object)  # ReleaseInfo
    done = Signal()

    def run(self) -> None:
        try:
            info = updater.check_for_update(__version__)
        except Exception as exc:  # noqa: BLE001 - 업데이트 확인 실패는 앱 실행을 막지 않습니다.
            log.info('업데이트 확인 실패: %s', exc)
            info = None
        if info is not None:
            self.found.emit(info)
        self.done.emit()


class _DownloadWorker(QObject):
    progress = Signal(int, int)
    ready = Signal(str)  # 헬퍼 스크립트 경로
    failed = Signal(str, bool)  # 메시지, 사용자에게 표시 여부

    def __init__(self, info: updater.ReleaseInfo, app_path: str) -> None:
        super().__init__()
        self._info = info
        self._app_path = app_path

    def run(self) -> None:
        try:
            script = updater.download_and_prepare(self._info, self._app_path, self.progress.emit)
        except updater.UpdateError as exc:
            self.failed.emit(str(exc), True)
            return
        except Exception as exc:  # noqa: BLE001 - 네트워크 오류 등은 조용히 건너뜁니다.
            self.failed.emit(str(exc), False)
            return
        self.ready.emit(script)


class AutoUpdater(QObject):
    """메인 윈도우가 떠 있는 동안 백그라운드로 확인하고, 새 버전이 있으면 모달 진행 창으로 적용합니다."""

    def __init__(self, parent: QWidget, app_name: str) -> None:
        super().__init__(parent)
        self._parent = parent
        self._app_name = app_name
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._dialog: QProgressDialog | None = None

    def start(self) -> None:
        app_path = updater.installed_app_path()
        if app_path is None:
            log.info('설치된 번들이 아니므로 업데이트 확인을 건너뜁니다.')
            return
        self._app_path = app_path
        worker = _CheckWorker()
        worker.found.connect(self._on_found)
        self._run_in_thread(worker, worker.done)

    def _run_in_thread(self, worker: QObject, finished_signal: Signal) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        finished_signal.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()

    def _on_found(self, info: updater.ReleaseInfo) -> None:
        log.info('새 버전 %s 발견 (현재 %s). 업데이트를 시작합니다.', info.tag, __version__)
        dialog = QProgressDialog(f'새 버전 {info.tag} 을(를) 내려받는 중입니다…', '', 0, 0, self._parent)
        dialog.setWindowTitle(f'{self._app_name} 업데이트')
        dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
        dialog.setCancelButton(None)
        dialog.setMinimumDuration(0)
        dialog.show()
        self._dialog = dialog

        worker = _DownloadWorker(info, self._app_path)
        worker.progress.connect(self._on_progress)
        worker.ready.connect(self._on_ready)
        worker.failed.connect(self._on_failed)
        self._run_in_thread(worker, worker.ready)
        worker.failed.connect(lambda *_: self._thread and self._thread.quit())

    def _on_progress(self, done: int, total: int) -> None:
        if self._dialog is None:
            return
        if total > 0:
            self._dialog.setRange(0, total)
            self._dialog.setValue(done)
            self._dialog.setLabelText(f'다운로드 중… {done // (1024 * 1024)} / {total // (1024 * 1024)} MB')

    def _on_ready(self, script_path: str) -> None:
        if self._dialog is not None:
            self._dialog.setLabelText('설치를 준비하고 있습니다. 앱을 다시 시작합니다…')
        log.info('업데이트 헬퍼 실행: %s', script_path)
        updater.launch_helper(script_path)
        QApplication.quit()

    def _on_failed(self, message: str, show: bool) -> None:
        log.warning('업데이트 실패, 현재 버전으로 계속 실행합니다: %s', message)
        if self._dialog is not None:
            self._dialog.close()
            self._dialog = None
        if show:
            QMessageBox.warning(self._parent, self._app_name, f'업데이트를 적용하지 못했습니다.\n{message}')
