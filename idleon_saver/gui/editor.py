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
import threading
from pathlib import Path

from kivy.clock import Clock
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
from idleon_saver.editor import (
    SaveCorruptedError,
    encode_to_stencyl,
    is_game_running,
    load_wrapped_json,
    overlay_unwrapped,
    validate_wrapped_json,
    wrapped_to_unwrapped,
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
        # 操作互斥锁：任一后台操作（保存/备份/还原）进行中时禁止启动新操作
        self._busy = False
        # 不为编辑框指定等宽字体：Kivy 默认字体已在 gui/main.py 启动期被
        # setup_cjk_font() 覆盖为系统 CJK 字体（微软雅黑/黑体），中文可正常
        # 渲染；额外指定 RobotoMono 等纯拉丁字体会覆盖掉 CJK 默认导致方块。

    # ------------------------------------------------------------------ #
    # 弹窗辅助（自带，避免与 gui.main 顶层循环依赖）
    # ------------------------------------------------------------------ #
    def dismiss_popup(self):
        try:
            self._popup.dismiss()
        except AttributeError as exc:
            # 注意：绝不用 exc_info= 或把异常对象作为日志参数——kivy 的
            # Logger.format 会对 record 做 deepcopy，traceback 不可 pickle 会崩溃。
            Logger.warning("Popup dismissed before being created: %s", exc)

    # ------------------------------------------------------------------ #
    # 后台线程执行阻塞 I/O（存档写回/还原/解析可能耗时数秒到数十秒，
    # 必须离开 Kivy 主线程，否则整窗冻结、所有按钮点不动）
    # ------------------------------------------------------------------ #
    def _run_in_thread(self, worker, on_done):
        """在后台线程跑阻塞 I/O，结束后切回主线程回调。

        ``worker`` 在子线程执行，不得触碰任何 Kivy 控件/属性；
        任何异常都会被捕获并作为 ``err`` 传给 ``on_done``（主线程）。
        ``on_done(err)`` 在主线程执行，可安全更新 UI / 弹窗。
        """
        holder = {}

        def _target():
            err = None
            try:
                worker()
            # 任何异常都透传给主线程上报，避免子线程静默吞掉
            except Exception as exc:  # noqa: BLE001
                err = exc
            holder["err"] = err
            # 切回主线程：Kivy 规定只能从 UI 线程碰控件
            Clock.schedule_once(lambda dt: on_done(holder["err"]), 0)

        threading.Thread(target=_target, daemon=True).start()

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
        # 进入屏即加载存档（不再应用自定义字体，避免中文方块）。
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
        """加载存档到编辑区（重活放后台线程，避免大存档卡死 UI）。

        显示 unwrapped JSON（紧凑、人类可读，~5.7MB）而非 wrapped JSON
        （~31.7MB，含 start/contents/end 类型标签），避免 TextInput 卡死。
        原始 wrapped 结构保留在 ``self._original_wrapped``，保存时把用户编辑
        叠回 wrapped 以保留类型标签供 StencylEncoder 编码。
        """
        if self._busy:
            return
        if not self._ensure_located():
            return
        self.status = "加载中"
        ldb = Path(self.ldb_path)
        idleon = Path(self.idleon_path) if self.idleon_path else None
        result = {}

        def worker():
            # 解析整个 leveldb（大存档可达数十 MB）耗时，必须子线程
            wrapped = load_wrapped_json(ldb, idleon)
            result["wrapped"] = wrapped
            result["text"] = json.dumps(
                wrapped_to_unwrapped(wrapped), ensure_ascii=False, indent=2
            )

        def on_done(err):
            if err is not None:
                if isinstance(err, SaveCorruptedError):
                    self.popup_error(
                        text=(
                            "存档读取失败。\n\n"
                            f"{err}\n\n"
                            "可尝试通过『备份管理』还原最近备份。"
                        )
                    )
                else:
                    logger.error("加载存档失败：%s", err)
                    self.popup_error(text=f"加载存档失败：{err}")
                self.status = "加载失败"
                return
            # 以下均为主线程：安全更新控件/属性
            self._original_wrapped = result["wrapped"]
            self.raw_text = result["text"]
            self.status = "已加载"

        self._run_in_thread(worker, on_done)

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
        """FileChooser 选中回调：把选中的目录作为 ldb_path（规范化分隔符）。"""
        try:
            # 统一路径分隔符：Windows 用户可能拿到 \ 或 / 混合的路径，
            # 规范化为 / 避免显示不一致和后续比较出错。
            norm = str(directory).replace("\\", "/")
            self.ldb_path = Path(norm)
            self.idleon_path = locate_idleon_install()
            self.dismiss_popup()
            self.load_save()
        except Exception as exc:
            logger.error("设置目录失败：%s", exc)
            self.popup_error(text=f"设置目录失败：{exc}")

    # ------------------------------------------------------------------ #
    # 保存（校验 → 二次确认 → 写前备份 → 编码回写）
    # ------------------------------------------------------------------ #
    def on_save(self):
        """保存：解析 JSON → 后台 overlay+validate → 主线程确认弹窗。

        overlay_unwrapped 和 validate_wrapped_json 遍历整个大结构（~5.7MB
        unwrapped + ~31.7MB wrapped），可能耗时 >1s，必须放后台线程，
        否则点「保存」后整个 UI 冻结 1~3 秒才弹确认窗。
        """
        if self._busy:
            return
        try:
            unwrapped = json.loads(self.ids.json_input.text)
        except json.JSONDecodeError as exc:
            self.popup_error(text=f"JSON 语法错误：{exc}")
            return
        original = getattr(self, "_original_wrapped", None)
        if original is None:
            self.popup_error(text="内部错误：原始 wrapped 数据丢失，请重新加载存档。")
            return
        self.status = "校验中"
        holder = {}

        def worker():
            wrapped = overlay_unwrapped(original, unwrapped)
            ok, msg = validate_wrapped_json(wrapped)
            holder["wrapped"] = wrapped
            holder["ok"] = ok
            holder["msg"] = msg

        def on_done(err):
            if err is not None:
                logger.error("处理存档失败：%s", err)
                self.popup_error(text=f"处理存档失败：{err}")
                self.status = "处理失败"
                return
            if not holder["ok"]:
                self.popup_error(text=f"校验未通过：{holder['msg']}")
                self.status = "校验失败"
                return
            # 校验通过 → 弹二次确认窗，此时 UI 已恢复响应
            self.status = "待确认"
            self._confirm_save(holder["wrapped"])

        self._run_in_thread(worker, on_done)

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
        """写前强制备份，然后编码回写（重活在后台线程，避免 UI 冻结）。"""
        if self._busy:
            return
        self._busy = True
        self.dismiss_popup()
        self.status = "保存中"
        ldb = Path(self.ldb_path)
        idleon = Path(self.idleon_path) if self.idleon_path else None
        backups_root = self._backups_root

        def worker():
            # 以下均为阻塞 I/O（copytree 整目录 + 解析/写回整个 leveldb），
            # 必须在子线程执行，否则点击「确认」后整窗卡死、所有按钮点不动。
            backup_leveldb(ldb, backups_root)
            stencyl = encode_to_stencyl(data)
            write_leveldb(ldb, stencyl, idleon)

        def on_done(err):
            self._busy = False
            if err is not None:
                logger.error("保存失败：%s", err)
                self.popup_error(text=f"保存失败：{err}")
                self.status = "保存失败"
            else:
                # 编辑区本就显示用户改后的内容，无需再回读磁盘，省一次解析卡顿
                self.status = "已保存"

        self._run_in_thread(worker, on_done)

    # ------------------------------------------------------------------ #
    # 备份管理
    # ------------------------------------------------------------------ #
    def backup_now(self):
        """立即创建一份备份（不修改存档；重活在后台线程）。"""
        if self._busy:
            return
        if not self._ensure_located():
            return
        self._busy = True
        ldb = Path(self.ldb_path)
        backups_root = self._backups_root
        holder = {}

        def worker():
            # copytree 整目录，大存档会卡顿，放子线程
            holder["name"] = backup_leveldb(ldb, backups_root).name

        def on_done(err):
            self._busy = False
            if err is not None:
                logger.error("备份失败：%s", err)
                self.popup_error(text=f"备份失败：{err}")
                return
            self.status = f"已备份：{holder['name']}"

        self._run_in_thread(worker, on_done)

    def open_backups(self):
        """在资源管理器中打开备份目录。"""
        try:
            open_in_explorer(self._backups_root)
        except Exception as exc:
            logger.error("无法打开备份目录：%s", exc)
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
        """从选中备份还原（还原前会再备份当前态；重活放后台线程）。"""
        if self._busy:
            return
        if self.ldb_path is None and not self._ensure_located():
            return
        self._busy = True
        ldb = Path(self.ldb_path)
        idleon = Path(self.idleon_path) if self.idleon_path else None
        backups_root = self._backups_root
        bp = Path(backup_path)
        result = {}

        def worker():
            # 还原（copytree 整目录）已在子线程执行
            restore_backup(bp, ldb, backups_root)
            # 还原后回读磁盘也要解析整个 leveldb，同样耗时必须放子线程；
            # 仅把解析结果（数据）带回主线程设置属性，绝不在这里碰 Kivy 控件。
            wrapped = load_wrapped_json(ldb, idleon)
            result["wrapped"] = wrapped
            result["text"] = json.dumps(
                wrapped_to_unwrapped(wrapped), ensure_ascii=False, indent=2
            )

        def on_done(err):
            self._busy = False
            if err is not None:
                logger.error("还原失败：%s", err)
                self.popup_error(text=f"还原失败：{err}")
                return
            # 以下均为主线程：安全更新控件/属性
            self._original_wrapped = result["wrapped"]
            self.raw_text = result["text"]
            # 关闭备份对话框，让编辑器立刻显示还原后的存档
            self.dismiss_popup()
            self.status = "已还原"

        self._run_in_thread(worker, on_done)

    # ------------------------------------------------------------------ #
    # 进程警告 / 关闭
    # ------------------------------------------------------------------ #
    def check_game_running(self):
        """重新检测游戏进程并更新警告横幅状态。"""
        self.game_running = is_game_running()

    # ------------------------------------------------------------------ #
    # 导出 / 导入 JSON（绕开剪贴板，避免大文本复制损坏）
    # ------------------------------------------------------------------ #
    def on_export_json(self):
        """把当前编辑区 JSON 写到文件，用户可用外部编辑器打开编辑。

        绕开 Kivy TextInput 的剪贴板复制——1.2MB 文本通过剪贴板复制会
        被截断/损坏，导致 JSON 语法错误。直接写文件保证完整。
        """
        out = user_dir() / "editor_export.json"
        try:
            out.write_text(self.raw_text, encoding="utf-8")
        except OSError as exc:
            self.popup_error(text=f"导出失败：{exc}")
            return
        self.status = f"已导出：{out.name}"
        # 在文件管理器中打开导出目录，方便用户找到文件
        try:
            open_in_explorer(user_dir())
        except Exception:
            pass  # 打开目录失败不阻塞流程

    def on_import_json(self):
        """弹出文件选择器，选一个 JSON 文件导入到编辑区。

        用户可在外部编辑器编辑导出的 JSON，改完后导入回来，再点「保存」。
        """
        from idleon_saver.gui.main import FileChooserDialog  # 运行期延迟导入

        content = FileChooserDialog(
            done=self._load_json_file,
            cancel=self.dismiss_popup,
            filters=["*.json"],
        )
        # skipcq: PYL-W0201
        self._popup = Popup(
            title="选择 JSON 文件",
            content=content,
            size_hint=(1, 1),
        )
        self._popup.open()

    def _load_json_file(self, directory, filename):
        """FileChooser 回调：读取选中的 JSON 文件到编辑区。"""
        try:
            if not filename:
                return
            fp = Path(directory, filename[0])
            text = fp.read_text(encoding="utf-8")
            # 校验 JSON 合法性
            json.loads(text)
            self.raw_text = text
            self.status = f"已导入：{fp.name}"
            self.dismiss_popup()
        except json.JSONDecodeError as exc:
            self.popup_error(text=f"JSON 语法错误：{exc}")
        except Exception as exc:
            self.popup_error(text=f"导入失败：{exc}")

    def on_cancel(self):
        """关闭编辑器，返回来源屏。"""
        self.manager.transition.direction = "right"
        self.manager.current = self.return_screen
