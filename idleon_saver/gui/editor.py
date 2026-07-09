"""EditorScreen：存档编辑器 UI（仅做编排，逻辑见 idleon_saver.editor）。

作为独立侧屏接入 MainWindow，不扰动 ``start→find_exe→end`` 线性链。
所有 LevelDB / 编解码交互都委托给纯逻辑层 editor.py，本文件只负责
状态展示、按钮接线、弹窗与目录选择。

为避免与 gui.main 形成顶层循环依赖，本模块只在运行期（方法内）延迟导入
main 中的 ErrorDialog / FileChooserDialog，EditorScreen 直接继承 kivy 的 Screen
并自带弹窗辅助方法。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from kivy.logger import Logger
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.screenmanager import Screen
from kivy.uix.scrollview import ScrollView

from idleon_saver.backup import (
    backup_leveldb,
    list_backups,
    open_in_explorer,
    restore_backup,
)
# 字体工具：用 kivy 自带等宽字体替换 kv 中已移除的系统字体（冻结态可解析）。
from idleon_saver.gui.fonts import apply_mono_font_to
from idleon_saver.editor import (
    encode_to_stencyl,
    is_game_running,
    load_wrapped_json,
    validate_wrapped_json,
    write_leveldb,
)
from idleon_saver.utility import locate_idleon_install, locate_leveldb, user_dir

logger = logging.getLogger(__name__)


class ConfirmDialog(BoxLayout):
    """简单的二次确认弹窗内容（确认 / 取消）。"""

    text = StringProperty("")
    on_confirm = ObjectProperty(None)
    on_cancel = ObjectProperty(None)


class BackupDialog(BoxLayout):
    """列出历史备份并支持一键还原 / 打开备份目录。"""

    ldb_path = ObjectProperty(None)
    backups_root = ObjectProperty(None)
    on_restore = ObjectProperty(None)
    on_open = ObjectProperty(None)
    on_done = ObjectProperty(None)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = "vertical"
        self.spacing = 5
        self.padding = [10]
        self._build()

    def _build(self):
        title = Label(
            text="历史备份（点击还原）",
            size_hint_y=None,
            height=30,
            color=(1, 1, 1, 1),
        )
        self.add_widget(title)

        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=3)
        box.bind(minimum_height=box.setter("height"))

        backups = list_backups(Path(self.backups_root))
        if not backups:
            box.add_widget(
                Label(text="（暂无备份）", size_hint_y=None, height=30, color=(1, 1, 1, 1))
            )
        for backup in backups:
            btn = Button(
                text=backup.name,
                size_hint_y=None,
                height=36,
                on_release=lambda inst, p=backup: self._restore(p),
            )
            box.add_widget(btn)

        sv = ScrollView(size_hint=(1, 1))
        sv.add_widget(box)
        self.add_widget(sv)

        btn_row = BoxLayout(size_hint_y=None, height=30, spacing=5)
        btn_row.add_widget(
            Button(text="打开目录", on_release=lambda *a: self._open())
        )
        btn_row.add_widget(
            Button(
                text="关闭",
                on_release=lambda *a: self.on_done() if self.on_done else None,
            )
        )
        self.add_widget(btn_row)

    def _restore(self, path: Path):
        if self.on_restore:
            self.on_restore(path)

    def _open(self):
        if self.on_open:
            self.on_open()


class EditorScreen(Screen):
    """存档编辑器侧屏。"""

    # 当前存档目录与（可选）安装目录
    ldb_path = ObjectProperty(None)
    idleon_path = ObjectProperty(None)
    # 绑定到 TextInput 的 wrapped JSON 文本（加载时由本屏写入）
    raw_text = StringProperty("")
    # 状态文本：空闲 / 加载中 / 保存中 / 已保存 / 加载失败 ...
    status = StringProperty("空闲")
    # 游戏是否运行中（控制警告横幅显隐）
    game_running = BooleanProperty(False)
    # 进入编辑器前的来源屏（关闭后返回）
    return_screen = ObjectProperty("end")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 备份根目录默认放在工具自身目录，与游戏存档隔离（设计 §7）
        self._backups_root = user_dir() / "backups"
        # 用 kivy 自带等宽字体（IdleonMono）替换 kv 里已移除的 "Courier New"；
        # 冻结态一定可解析，失败则回退 kivy 默认字体，绝不因字体崩溃。
        # 在构造期就应用，确保首次布局前 font_name 已合法（避免启动崩溃）。
        apply_mono_font_to(self.ids.get("json_input"))

    # ------------------------------------------------------------------ #
    # 弹窗辅助（自带，避免与 gui.main 顶层循环依赖）
    # ------------------------------------------------------------------ #
    def dismiss_popup(self):
        try:
            self._popup.dismiss()
        except AttributeError as exc:
            Logger.exception("Popup dismissed before being created", exc_info=exc)

    def popup_error(self, text):
        from idleon_saver.gui.main import ErrorDialog  # 运行期延迟导入

        content = ErrorDialog(text=text, done=self.dismiss_popup)
        # skipcq: PYL-W0201
        self._popup = Popup(
            title="Error :(", content=content, size_hint=(0.95, 0.95)
        )
        self._popup.open()

    # ------------------------------------------------------------------ #
    # 生命周期 / 定位 / 加载
    # ------------------------------------------------------------------ #
    def on_enter(self):
        """进入屏时：记录来源屏、自动定位、加载、检测进程。"""
        # manager.previous() 返回按顺序排列的上一个屏名（本布局下即 end）
        self.return_screen = self.manager.previous() or "end"
        self.status = "加载中"
        self.check_game_running()
        # 双保险：构造期若因 ids 尚未就绪而未生效，进入屏时再补一次等宽字体应用。
        apply_mono_font_to(self.ids.get("json_input"))
        self.load_save()

    def _ensure_located(self) -> bool:
        """尝试自动定位存档目录；失败则提示用户手动选择。"""
        if self.ldb_path is not None and Path(self.ldb_path).exists():
            return True
        located = locate_leveldb()
        if located is not None:
            self.ldb_path = located
            self.idleon_path = locate_idleon_install()
            return True
        self.popup_error(
            text=(
                "未能自动定位存档目录。\n"
                "请点击「手动选择」按钮指定 leveldb 存档目录。"
            )
        )
        self.status = "未定位存档目录"
        return False

    def load_save(self):
        """加载 wrapped JSON 到编辑区。"""
        if not self._ensure_located():
            return
        try:
            wrapped = load_wrapped_json(
                Path(self.ldb_path),
                Path(self.idleon_path) if self.idleon_path else None,
            )
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"加载存档失败：{exc}")
            self.status = "加载失败"
            return
        # 写入 TextInput（kv 中 text: root.raw_text 会同步显示）
        self.raw_text = json.dumps(wrapped, ensure_ascii=False, indent=2)
        self.status = "已加载"

    def on_manual_locate(self):
        """弹出目录选择对话框，手动指定存档目录。"""
        from idleon_saver.gui.main import FileChooserDialog  # 运行期延迟导入

        content = FileChooserDialog(
            done=self._set_ldb_dir,
            cancel=self.dismiss_popup,
            filters=[],
        )
        # skipcq: PYL-W0201
        self._popup = Popup(
            title="选择 leveldb 存档目录",
            content=content,
            size_hint=(1, 1),
        )
        self._popup.open()

    def _set_ldb_dir(self, directory, _filename):
        """FileChooser 选中回调：把选中的目录作为 ldb_path。"""
        try:
            self.ldb_path = Path(directory)
            self.idleon_path = locate_idleon_install()
            self.dismiss_popup()
            self.load_save()
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"设置目录失败：{exc}")

    # ------------------------------------------------------------------ #
    # 保存（校验 → 二次确认 → 写前备份 → 编码回写）
    # ------------------------------------------------------------------ #
    def on_save(self):
        """保存：先校验 JSON 合法且结构完整，再二次确认。"""
        try:
            data = json.loads(self.ids.json_input.text)
        except json.JSONDecodeError as exc:
            self.popup_error(text=f"JSON 语法错误：{exc}")
            return
        ok, msg = validate_wrapped_json(data)
        if not ok:
            self.popup_error(text=f"校验未通过：{msg}")
            return
        self._confirm_save(data)

    def _confirm_save(self, data):
        """二次确认弹窗；确认后写前备份 + 编码回写。"""
        content = ConfirmDialog(
            text=(
                "即将覆盖原存档。\n"
                "系统会先自动创建一份带时间戳的备份。\n"
                "确认继续吗？"
            ),
            on_confirm=lambda: self._do_save(data),
            on_cancel=self.dismiss_popup,
        )
        # skipcq: PYL-W0201
        self._popup = Popup(
            title="确认保存", content=content, size_hint=(0.9, 0.6)
        )
        self._popup.open()

    def _do_save(self, data):
        """写前强制备份，然后编码回写。"""
        self.dismiss_popup()
        self.status = "保存中"
        try:
            backup_leveldb(Path(self.ldb_path), self._backups_root)
            stencyl = encode_to_stencyl(data)
            write_leveldb(
                Path(self.ldb_path),
                stencyl,
                Path(self.idleon_path) if self.idleon_path else None,
            )
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"保存失败：{exc}")
            self.status = "保存失败"
            return
        self.status = "已保存"

    # ------------------------------------------------------------------ #
    # 备份管理
    # ------------------------------------------------------------------ #
    def backup_now(self):
        """立即创建一份备份（不修改存档）。"""
        if not self._ensure_located():
            return
        try:
            dest = backup_leveldb(Path(self.ldb_path), self._backups_root)
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"备份失败：{exc}")
            return
        self.status = f"已备份：{dest.name}"

    def open_backups(self):
        """在资源管理器中打开备份目录。"""
        try:
            open_in_explorer(self._backups_root)
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"无法打开备份目录：{exc}")

    def show_backups(self):
        """打开备份管理对话框（列出 / 还原 / 打开目录）。"""
        if not self._ensure_located():
            return
        content = BackupDialog(
            ldb_path=self.ldb_path,
            backups_root=self._backups_root,
            on_restore=self.restore_backup,
            on_open=self.open_backups,
            on_done=self.dismiss_popup,
        )
        # skipcq: PYL-W0201
        self._popup = Popup(
            title="备份管理", content=content, size_hint=(0.9, 0.85)
        )
        self._popup.open()

    def restore_backup(self, backup_path):
        """从选中备份还原（还原前会再备份当前态）。"""
        if self.ldb_path is None and not self._ensure_located():
            return
        try:
            restore_backup(
                Path(backup_path), Path(self.ldb_path), self._backups_root
            )
        except Exception as exc:
            logger.exception(exc)
            self.popup_error(text=f"还原失败：{exc}")
            return
        self.status = "已还原"
        self.load_save()

    # ------------------------------------------------------------------ #
    # 进程警告 / 关闭
    # ------------------------------------------------------------------ #
    def check_game_running(self):
        """重新检测游戏进程并更新警告横幅状态。"""
        self.game_running = is_game_running()

    def on_cancel(self):
        """关闭编辑器，返回来源屏。"""
        self.manager.transition.direction = "right"
        self.manager.current = self.return_screen
