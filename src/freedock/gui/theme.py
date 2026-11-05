"""Dark theme shared by every FreeDock window."""

ACCENT = "#5b8cff"

STYLE = f"""
* {{ font-family: "Inter", "Segoe UI", sans-serif; font-size: 13px; color: #e6e9f0; }}
QMainWindow, QDialog, QWidget#root {{ background: #0f1117; }}
QWidget#panel, QScrollArea#panel > QWidget > QWidget {{ background: #151821; }}
QScrollArea {{ border: none; background: transparent; }}
QLabel#h1 {{ font-size: 20px; font-weight: 800; letter-spacing: 0.5px; }}
QLabel#h2 {{ font-size: 12px; font-weight: 700; color: #8b93a7; text-transform: uppercase; letter-spacing: 1px; padding-top: 10px; }}
QLabel#muted {{ color: #8b93a7; }}
QLabel#pill {{ background: #1f2430; border-radius: 10px; padding: 4px 10px; color: #8b93a7; }}
QLabel#pillok {{ background: #12331f; border-radius: 10px; padding: 4px 10px; color: #4ade80; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: #0f1117; border: 1px solid #262b38; border-radius: 8px; padding: 6px 8px; selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: #151821; border: 1px solid #262b38; selection-background-color: #26304a; }}
QPushButton {{ background: #1f2430; border: 1px solid #2a3040; border-radius: 8px; padding: 7px 12px; }}
QPushButton:hover {{ background: #262c3a; border-color: #3a4256; }}
QPushButton:pressed {{ background: #1a1e28; }}
QPushButton#primary {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 700; }}
QPushButton#primary:hover {{ background: #6d99ff; }}
QPushButton#danger {{ color: #f87171; }}
QPushButton:checked {{ background: #26304a; border-color: {ACCENT}; }}
QListWidget, QListView {{ background: #151821; border: none; outline: none; }}
QListWidget::item, QListView::item {{ padding: 8px; border-radius: 8px; margin: 2px 6px; }}
QListWidget::item:selected, QListView::item:selected {{ background: #26304a; color: white; }}
QListWidget::item:hover, QListView::item:hover {{ background: #1c2130; }}
QGroupBox {{ border: 1px solid #232838; border-radius: 10px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #8b93a7; font-weight: 700; }}
QSlider::groove:horizontal {{ height: 4px; background: #262b38; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #3a4256; background: #0f1117; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QToolTip {{ background: #1f2430; border: 1px solid #2a3040; padding: 6px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2a3040; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QMenu {{ background: #151821; border: 1px solid #262b38; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #26304a; }}
QStatusBar {{ background: #0b0d12; color: #8b93a7; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; color: #8b93a7; }}
QTabBar::tab:selected {{ color: white; border-bottom: 2px solid {ACCENT}; }}
"""

# one-click looks for keys: (name, bg, bg2, accent)
PALETTES = [
    ("Midnight", "#1d2b53", "#0b1020", "#5b8cff"),
    ("Neon", "#3b1d6e", "#160b2e", "#a855f7"),
    ("Mint", "#0f2a1d", "#07130d", "#22c55e"),
    ("Ember", "#431407", "#1c0802", "#f97316"),
    ("Racing", "#3a0d14", "#1a0609", "#f43f5e"),
    ("Gold", "#3a2a05", "#1a1302", "#facc15"),
    ("Ocean", "#062a33", "#03161b", "#22d3ee"),
    ("Carbon", "#1e2533", "#0d1118", "#94a3b8"),
    ("Mono", "#000000", "#111111", "#ffffff"),
]
