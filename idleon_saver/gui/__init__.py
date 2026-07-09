"""idleon-saver 的 GUI 包（Kivy 界面相关模块）。

本包此前作为命名空间包存在；此处显式声明为常规包，保证子模块
（editor.py / main.py 等）的导入行为稳定、可测试。
注意：本 __init__ 不导入任何 kivy 模块，避免在导入期就依赖 kivy 运行环境。
"""
