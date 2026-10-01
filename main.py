"""
Floating Ticker - PyQt6 版 (多数据源 + 代理 + 守护线程 + 浅/深色)

技术栈
  - PyQt6             无边框窗口 + QSS
  - QTimer            价格瞬时变绿/红,400ms 后淡回
  - threading.Thread  守护线程跑 WebSocket,UI 不卡,退出时不会卡死进程
  - websocket-client  Binance / OKX / Bybit / Gate.io / Bitget
  - HTTP/SOCKS5 代理  支持(对话框配置 或 HTTPS_PROXY 环境变量)
  - 浅色 / 深色       跟随系统 / 手动切换
"""
import json
import os
import sys
import threading
import time
from urllib.parse import urlparse

import websocket


_QT_DLL_HANDLES = []


def _prepare_qt_dll_path():
    """让 PyInstaller 程序在干净 Windows 环境中找到 Qt6Core 的依赖。"""
    if sys.platform != "win32":
        return

    roots = []
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        roots.append(os.path.join(bundle_root, "PyQt6", "Qt6", "bin"))
    else:
        roots.append(
            os.path.join(
                os.path.dirname(sys.executable),
                "Lib",
                "site-packages",
                "PyQt6",
                "Qt6",
                "bin",
            )
        )

    for root in roots:
        if not os.path.isdir(root):
            continue
        if hasattr(os, "add_dll_directory"):
            _QT_DLL_HANDLES.append(os.add_dll_directory(root))
        os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")


_prepare_qt_dll_path()

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer, QSettings, QVariantAnimation
from PyQt6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QFontDatabase,
    QMouseEvent,
    QIcon,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


# ====================================================================
#  主题配色
# ====================================================================
THEMES = {
    "dark": {
        "name": "深色",
        "bg":           "#14171f",
        "border":       "#262b36",
        "text_primary": "#ffffff",
        "text_secondary": "#a2a8b5",
        "text_muted":   "#6c7584",
        "text_dim":     "#5d6675",
        "up":           "#0ecb81",
        "down":         "#f6465d",
        "up_bg":        "rgba(14, 203, 129, 0.12)",
        "down_bg":      "rgba(246, 70, 93, 0.12)",
    },
    "light": {
        "name": "浅色",
        "bg":           "#ffffff",
        "border":       "#e5e7eb",
        "text_primary": "#111827",
        "text_secondary": "#4b5563",
        "text_muted":   "#6b7280",
        "text_dim":     "#9ca3af",
        "up":           "#16a34a",
        "down":         "#dc2626",
        "up_bg":        "rgba(22, 163, 74, 0.10)",
        "down_bg":      "rgba(220, 38, 38, 0.10)",
    },
}


# ====================================================================
#  代理工具
# ====================================================================
def _parse_proxy_url(url):
    try:
        u = urlparse(url)
        if not u.hostname or not u.port:
            return None
        ptype = "socks5" if u.scheme.lower().startswith("socks") else "http"
        return {
            "http_proxy_host": u.hostname,
            "http_proxy_port": u.port,
            "proxy_type": ptype,
            "http_proxy_auth": (u.username, u.password) if u.username else None,
        }
    except Exception:
        return None


def resolve_proxy(config):
    if (
        config
        and config.get("enabled")
        and config.get("host")
        and int(config.get("port") or 0) > 0
    ):
        out = {
            "http_proxy_host": config["host"],
            "http_proxy_port": int(config["port"]),
            "proxy_type": config.get("type", "http"),
        }
        if config.get("username"):
            out["http_proxy_auth"] = (config["username"], config["password"])
        return out
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                 "https_proxy", "http_proxy", "all_proxy"):
        url = os.environ.get(name)
        if not url:
            continue
        p = _parse_proxy_url(url)
        if p:
            return p
    return None


def socks_available():
    try:
        import socks  # noqa
        return True
    except ImportError:
        return False


# ====================================================================
#  6 个数据源
# ====================================================================
def parse_binance(raw):
    d = json.loads(raw)
    return {
        "price":  float(d["c"]),
        "change": float(d["P"]),
        "high":   float(d["h"]),
        "low":    float(d["l"]),
    }


def parse_okx(raw):
    d = json.loads(raw)
    items = d.get("data", [])
    if not items:
        return None
    x = items[0]
    return {
        "price":  float(x["last"]),
        "change": float(x["chg24h"]) * 100,
        "high":   float(x["high24h"]),
        "low":    float(x["low24h"]),
    }


def parse_bybit(raw):
    d = json.loads(raw)
    x = d.get("data", {})
    if not x:
        return None
    return {
        "price":  float(x["lastPrice"]),
        "change": float(x["price24hPcnt"]) * 100,
        "high":   float(x["highPrice24h"]),
        "low":    float(x["lowPrice24h"]),
    }


def parse_gateio(raw):
    d = json.loads(raw)
    x = d.get("result", {})
    if not x:
        return None
    return {
        "price":  float(x["last"]),
        "change": float(x["change_percentage"]),
        "high":   float(x["high_24h"]),
        "low":    float(x["low_24h"]),
    }


def parse_bitget(raw):
    d = json.loads(raw)
    items = d.get("data", [])
    if not items:
        return None
    x = items[0]
    return {
        "price":  float(x["lastPr"]),
        "change": float(x["chg24h"]) * 100,
        "high":   float(x["high24h"]),
        "low":    float(x["low24h"]),
    }


def connect_binance(symbol):
    return (f"wss://stream.binance.com:9443/ws/{symbol}@ticker", None)


def connect_okx(symbol):
    sym = (symbol[:-4] + "-USDT").upper() if symbol.endswith("usdt") else symbol.upper()
    msg = json.dumps({"op": "subscribe", "args": [{"channel": "tickers", "instId": sym}]})
    return ("wss://ws.okx.com:8443/ws/v5/public", msg)


def connect_bybit(symbol):
    msg = json.dumps({"op": "subscribe", "args": [f"tickers.{symbol.upper()}"]})
    return ("wss://stream.bybit.com/v5/public/spot", msg)


def connect_gateio(symbol):
    sym = (symbol[:-4] + "_USDT").upper() if symbol.endswith("usdt") else symbol.upper()
    msg = json.dumps({
        "time": int(time.time()),
        "channel": "spot.tickers",
        "event": "subscribe",
        "payload": [sym],
    })
    return ("wss://api.gateio.ws/ws/v4/", msg)


def connect_bitget(symbol):
    msg = json.dumps({
        "op": "subscribe",
        "args": [{"instType": "SPOT", "channel": "ticker", "instId": symbol.upper()}],
    })
    return ("wss://ws.bitget.com/v2/ws/public", msg)


SOURCES = [
    {"key": "binance", "name": "Binance", "logo": "binance32x32.ico", "connect": connect_binance, "parse": parse_binance},
    {"key": "okx",     "name": "OKX",     "logo": "okx192.192.png", "connect": connect_okx,     "parse": parse_okx},
    {"key": "bybit",   "name": "Bybit",   "logo": "bybit32x32.ico", "connect": connect_bybit,   "parse": parse_bybit},
    {"key": "gateio",  "name": "Gate.io", "logo": "gateio32x32.ico", "connect": connect_gateio,  "parse": parse_gateio},
    {"key": "bitget",  "name": "Bitget",  "logo": "bitget144x144.png", "connect": connect_bitget,  "parse": parse_bitget},
]


def get_source(key):
    for s in SOURCES:
        if s["key"] == key:
            return s
    return SOURCES[0]


COIN_LOGOS = {
    "eth": "eth.svg",
    "btc": "btc.svg",
    "sol": "sol.svg",
    "bnb": "bnb.svg",
    "xrp": "xrp.svg",
    "doge": "doge.svg",
    "ton": "ton.svg",
    "ada": "ada.svg",
    "avax": "avax.svg",
    "link": "link.svg",
}


# ====================================================================
#  代理设置对话框
# ====================================================================
class ProxyDialog(QDialog):
    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.setWindowTitle("\u8d4b\u4ee3\u7406\u8bbe\u7f6e")
        self.setMinimumWidth(380)

        layout = QFormLayout(self)

        self.enabled_chk = QCheckBox("\u542f\u7528\u4ee3\u7406")
        self.enabled_chk.setChecked(bool(current.get("enabled")))
        layout.addRow(self.enabled_chk)

        self.type_combo = QComboBox()
        self.type_combo.addItems(["HTTP", "SOCKS5"])
        self.type_combo.setCurrentText(current.get("type", "http").upper())
        layout.addRow("\u4ee3\u7406\u7c7b\u578b:", self.type_combo)

        self.host_edit = QLineEdit(current.get("host", "127.0.0.1"))
        self.host_edit.setPlaceholderText("\u4f8b\u5982 127.0.0.1")
        layout.addRow("\u4e3b\u673a:", self.host_edit)

        self.port_edit = QLineEdit(str(current.get("port", "")))
        self.port_edit.setPlaceholderText("7890 (Clash) / 1080 (socks)")
        layout.addRow("\u7aef\u53e3:", self.port_edit)

        self.user_edit = QLineEdit(current.get("username", ""))
        self.user_edit.setPlaceholderText("\u7559\u7a7a\u5219\u65e0\u9700\u8ba4\u8bc1")
        layout.addRow("\u7528\u6237\u540d:", self.user_edit)

        self.pass_edit = QLineEdit(current.get("password", ""))
        self.pass_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow("\u5bc6\u7801:", self.pass_edit)

        if not socks_available():
            self.type_combo.setItemData(
                1,
                "\u9700\u8981\u5b89\u88c5 pysocks: pip install pysocks",
                Qt.ItemDataRole.ToolTipRole,
            )

        hint = QLabel(
            "\u4e5f\u652f\u6301\u4f7f\u7528\u73af\u5883\u53d8\u91cf HTTPS_PROXY / HTTP_PROXY / ALL_PROXY \u81ea\u52a8\u8bc6\u522b\n"
            "\u4f8b\u5982: set HTTPS_PROXY=http://127.0.0.1:7890"
        )
        hint.setStyleSheet("color: #888; font-size: 11px;")
        hint.setWordWrap(True)
        layout.addRow(hint)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addRow(btns)

    def get_config(self):
        try:
            port = int(self.port_edit.text())
        except ValueError:
            port = 0
        return {
            "enabled":  self.enabled_chk.isChecked(),
            "type":     self.type_combo.currentText().lower(),
            "host":     self.host_edit.text().strip(),
            "port":     port,
            "username": self.user_edit.text().strip(),
            "password": self.pass_edit.text(),
        }


# ====================================================================
#  Logo 大小设置对话框
# ====================================================================
class LogoSizeDialog(QDialog):
    MIN_SIZE = 8
    MAX_SIZE = 32

    def __init__(self, sizes, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Logo 大小")
        self.setMinimumWidth(300)
        self._inputs = {}

        layout = QFormLayout(self)
        hint = QLabel("单位：px。保存后立即应用，分别记住币种 Logo、币种文字和各平台尺寸。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addRow(hint)

        self._add_size_input(layout, "币种 Logo", "coin", sizes.get("coin", 16))
        self._add_size_input(
            layout,
            "币种文字 (BTC/ETH)",
            "coin_text",
            sizes.get("coin_text", 13),
        )
        for source in SOURCES:
            self._add_size_input(
                layout,
                source["name"] + " Logo",
                source["key"],
                sizes.get(source["key"], 14),
            )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _add_size_input(self, layout, label, key, value):
        spin = QSpinBox()
        spin.setRange(self.MIN_SIZE, self.MAX_SIZE)
        spin.setSuffix(" px")
        spin.setValue(int(value))
        self._inputs[key] = spin
        layout.addRow(label + ":", spin)

    def get_sizes(self):
        return {key: spin.value() for key, spin in self._inputs.items()}


# ====================================================================
#  WebSocket Worker(守护线程)
# ====================================================================
class TickerWorker(QObject):
    ticker = pyqtSignal(dict)
    status = pyqtSignal(bool, str)

    def __init__(self, source, symbol, proxy_config):
        super().__init__()
        self.source = source
        self.symbol = symbol.lower()
        self.proxy_config = proxy_config or {}
        self._ws = None
        self._stop = False
        self._thread = None

    def start(self):
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run(self):
        while not self._stop:
            url, subscribe_msg = self.source["connect"](self.symbol)
            self.status.emit(False, "\u8fde\u63a5 " + self.source["name"] + " ...")

            proxy = resolve_proxy(self.proxy_config)
            ws_kwargs = {}
            if proxy:
                ws_kwargs["http_proxy_host"] = proxy["http_proxy_host"]
                ws_kwargs["http_proxy_port"] = proxy["http_proxy_port"]
                ws_kwargs["proxy_type"] = proxy["proxy_type"]
                if proxy.get("http_proxy_auth"):
                    ws_kwargs["http_proxy_auth"] = proxy["http_proxy_auth"]

            self._ws = websocket.WebSocketApp(
                url,
                on_open=lambda ws: self._on_open(ws, subscribe_msg),
                on_message=lambda ws, msg: self._on_message(msg),
                on_error=lambda ws, err: self.status.emit(False, "\u9519\u8bef: " + str(err)),
                on_close=lambda ws, code, msg: self.status.emit(False, "\u5df2\u65ad\u5f00"),
            )

            try:
                self._ws.run_forever(**ws_kwargs)
            except Exception as e:
                self.status.emit(False, "\u9519\u8bef: " + str(e))

            if self._stop:
                break
            self.status.emit(False, "3 \u79d2\u540e\u91cd\u8fde ...")
            for _ in range(30):
                if self._stop:
                    break
                time.sleep(0.1)

    def _on_open(self, ws, subscribe_msg):
        if subscribe_msg:
            try:
                ws.send(subscribe_msg)
            except Exception as e:
                self.status.emit(False, "\u8ba2\u9605\u5931\u8d25: " + str(e))
                return
        self.status.emit(True, "\u5df2\u8fde\u63a5")

    def _on_message(self, msg):
        try:
            data = self.source["parse"](msg)
            if data:
                self.ticker.emit(data)
        except Exception:
            pass


# ====================================================================
#  主窗口
# ====================================================================
class TickerWindow(QWidget):
    SUPPORTED_PAIRS = [
        ("ETH/USDT",  "ethusdt"),
        ("BTC/USDT",  "btcusdt"),
        ("SOL/USDT",  "solusdt"),
        ("BNB/USDT",  "bnbusdt"),
        ("XRP/USDT",  "xrpusdt"),
        ("DOGE/USDT", "dogeusdt"),
        ("TON/USDT",  "tonusdt"),
        ("ADA/USDT",  "adausdt"),
        ("AVAX/USDT", "avaxusdt"),
        ("LINK/USDT", "linkusdt"),
    ]

    def __init__(self):
        super().__init__()

        self._settings = QSettings("FloatingTicker", "Ticker")
        self._current_source_key = self._settings.value("source", "binance")
        if not any(s["key"] == self._current_source_key for s in SOURCES):
            self._current_source_key = "binance"
        self._current_symbol     = self._settings.value("symbol", "ethusdt")
        self._current_label      = self._settings.value("label",  "ETH/USDT")
        self._current_source     = get_source(self._current_source_key)

        # 主题模式: auto / dark / light
        self._theme_mode = self._settings.value("theme_mode", "auto")
        if self._theme_mode not in ("auto", "dark", "light"):
            self._theme_mode = "auto"
        self._system_theme = self._detect_system_theme()

        try:
            self._proxy_config = json.loads(self._settings.value("proxy", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            self._proxy_config = {}
        if not isinstance(self._proxy_config, dict):
            self._proxy_config = {}

        try:
            self._logo_sizes = json.loads(self._settings.value("logo_sizes", "{}") or "{}")
        except (json.JSONDecodeError, TypeError):
            self._logo_sizes = {}
        if not isinstance(self._logo_sizes, dict):
            self._logo_sizes = {}
        self._normalise_logo_sizes()

        # 用于主题切换后重应用动态颜色
        self._connected     = False
        self._price_flash    = None   # 'up' / 'down' / None
        self._price_fade_from = None
        self._change_trend   = None   # 'up' / 'down' / None

        # ---- 状态 ----
        self._drag_pos = None
        self._last_price = None
        self._always_on_top = True

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(280, 120)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

        self._build_ui()
        self._apply_styles()
        self._apply_pair_text_size()

        # 监听系统主题变化
        try:
            from PyQt6.QtGui import QGuiApplication
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme_changed)
        except Exception:
            pass

        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.setInterval(400)
        self._flash_timer.timeout.connect(self._clear_flash)

        self._price_fade = QVariantAnimation(self)
        self._price_fade.setDuration(300)
        self._price_fade.setStartValue(0.0)
        self._price_fade.setEndValue(1.0)
        self._price_fade.valueChanged.connect(self._apply_price_fade)
        self._price_fade.finished.connect(lambda: self._set_price_color(None))

        self._worker = None
        self._start_worker()

    # ---------------- 主题相关 ----------------
    def _detect_system_theme(self):
        try:
            from PyQt6.QtGui import QGuiApplication
            scheme = QGuiApplication.styleHints().colorScheme()
            return "light" if scheme == Qt.ColorScheme.Light else "dark"
        except Exception:
            return "dark"

    def _current_theme_dict(self):
        if self._theme_mode == "auto":
            return THEMES[self._system_theme]
        return THEMES[self._theme_mode]

    def _on_system_scheme_changed(self):
        self._system_theme = self._detect_system_theme()
        if self._theme_mode == "auto":
            self._apply_theme()

    def _set_theme_mode(self, mode):
        if mode not in ("auto", "dark", "light"):
            return
        if mode == self._theme_mode:
            return
        self._theme_mode = mode
        self._settings.setValue("theme_mode", mode)
        self._apply_theme()

    def _apply_theme(self):
        # 1. 重建全局 QSS
        self._apply_styles()
        # 2. 重新应用动态颜色
        self._set_status_color(self._connected)
        self._set_change_color(self._change_trend)
        if self._price_fade.state() == QVariantAnimation.State.Running:
            self._apply_price_fade(self._price_fade.currentValue())
        else:
            self._set_price_color(self._price_flash)

    # ---------------- UI ----------------
    def _build_ui(self):
        self.card = QWidget(self)
        self.card.setObjectName("card")
        self.card.setFixedSize(280, 120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.card)

        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(7)

        # header
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        base, quote = self._current_label.split("/")
        self.pair_icon = QLabel()
        self.pair_icon.setObjectName("pair-icon")
        self.pair_icon.setFixedSize(self._logo_sizes["coin"], self._logo_sizes["coin"])
        self.pair_base = QLabel(base)
        self.pair_base.setObjectName("pair-base")
        self.pair_quote = QLabel(" / " + quote)
        self.pair_quote.setObjectName("pair-quote")

        self.status_dot = QLabel()
        self.status_dot.setObjectName("status-dot")
        self.status_dot.setFixedSize(6, 6)

        self.source_icon = QLabel()
        self.source_icon.setObjectName("source-icon")
        source_size = self._logo_sizes[self._current_source_key]
        self.source_icon.setFixedSize(source_size, source_size)
        self.source_label = QLabel(self._current_source["name"])
        self.source_label.setObjectName("source-label")

        source_box = QHBoxLayout()
        source_box.setContentsMargins(0, 0, 0, 0)
        source_box.setSpacing(4)
        source_box.addWidget(self.status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        source_box.addWidget(self.source_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        source_box.addWidget(self.source_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.source_w = QWidget()
        self.source_w.setLayout(source_box)

        header.addWidget(self.pair_icon, 0, Qt.AlignmentFlag.AlignBottom)
        header.addSpacing(4)
        # BTC/ETH 等基础币种均为大写；底边对齐即稳定的视觉基线。
        # Qt 的 QLabel 在不同 px 字号下不可靠地实现 AlignBaseline。
        header.addWidget(self.pair_base, 0, Qt.AlignmentFlag.AlignBottom)
        header.addWidget(self.pair_quote, 0, Qt.AlignmentFlag.AlignBottom)
        header.addStretch()
        # 右侧数据源整组与 BTC/USDT 文字行的中线对齐。
        header.addWidget(self.source_w, 0, Qt.AlignmentFlag.AlignBottom)
        inner.addLayout(header)

        # price
        price_row = QHBoxLayout()
        price_row.setContentsMargins(0, 0, 0, 0)
        price_row.setSpacing(6)
        self.price_label = QLabel("--")
        self.price_label.setObjectName("price")
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.change_label = QLabel("--")
        self.change_label.setObjectName("change")
        self.change_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        price_row.addWidget(self.price_label)
        price_row.addStretch()
        price_row.addWidget(self.change_label)
        inner.addLayout(price_row)

        # stats
        stats_row = QHBoxLayout()
        stats_row.setContentsMargins(0, 0, 0, 0)
        stats_row.setSpacing(8)

        def stat_block(title_text):
            box = QHBoxLayout()
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(4)
            t = QLabel(title_text)
            t.setObjectName("stat-label")
            v = QLabel("--")
            v.setObjectName("stat-val")
            v.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            box.addWidget(t)
            box.addWidget(v)
            w = QWidget()
            w.setLayout(box)
            return w, v

        high_w, self.high_val = stat_block("24h \u9ad8")
        low_w,  self.low_val  = stat_block("24h \u4f4e")
        stats_row.addWidget(high_w)
        stats_row.addStretch()
        stats_row.addWidget(low_w)
        inner.addLayout(stats_row)

        # 等宽字体 + 固定宽度
        mono_family = self._pick_mono_font()

        f = QFont(mono_family)
        f.setPixelSize(22)
        f.setWeight(QFont.Weight.Bold)
        self.price_label.setFont(f)
        self.price_label.setFixedWidth(150)

        f = QFont(mono_family)
        f.setPixelSize(11)
        f.setWeight(QFont.Weight.Bold)
        self.change_label.setFont(f)
        self.change_label.setFixedWidth(78)

        f = QFont(mono_family)
        f.setPixelSize(11)
        self.high_val.setFont(f)
        self.low_val.setFont(f)
        self.high_val.setFixedWidth(72)
        self.low_val.setFixedWidth(72)

        self._update_pair_logo()
        self._update_source_logo()

    @staticmethod
    def _asset_path(filename):
        root = os.path.dirname(os.path.abspath(__file__))
        ico_path = os.path.join(root, "ico", filename)
        if os.path.exists(ico_path):
            return ico_path
        return os.path.join(root, "assets", filename)

    @classmethod
    def _logo_pixmap(cls, filename, size):
        icon = QIcon(cls._asset_path(filename))
        return icon.pixmap(size, size) if not icon.isNull() else None

    def _update_pair_logo(self):
        base = self._current_symbol[:-4].lower() if self._current_symbol.lower().endswith("usdt") else self._current_symbol.lower()
        filename = COIN_LOGOS.get(base, "coin-generic.svg")
        size = self._logo_sizes["coin"]
        self.pair_icon.setFixedSize(size, size)
        pixmap = self._logo_pixmap(filename, size)
        self.pair_icon.setPixmap(pixmap)

    def _update_source_logo(self):
        size = self._logo_sizes[self._current_source_key]
        self.source_icon.setFixedSize(size, size)
        pixmap = self._logo_pixmap(self._current_source["logo"], size)
        self.source_icon.setPixmap(pixmap)

    def _apply_pair_text_size(self):
        font = self.pair_base.font()
        font.setPixelSize(self._logo_sizes["coin_text"])
        font.setWeight(QFont.Weight.DemiBold)
        self.pair_base.setFont(font)
        # 让右侧数据源组与基础币种文字使用同一行高，中心线严格一致。
        self.source_w.setFixedHeight(self.pair_base.sizeHint().height())

    def _normalise_logo_sizes(self):
        defaults = {"coin": 16, "coin_text": 13}
        defaults.update({source["key"]: 14 for source in SOURCES})
        for key, default in defaults.items():
            try:
                size = int(self._logo_sizes.get(key, default))
            except (TypeError, ValueError):
                size = default
            self._logo_sizes[key] = max(LogoSizeDialog.MIN_SIZE, min(LogoSizeDialog.MAX_SIZE, size))

    def _pick_mono_font(self):
        fams = set(QFontDatabase.families())
        for f in ["Consolas", "Cascadia Mono", "Cascadia Code",
                  "JetBrains Mono", "Menlo", "Monaco", "Courier New", "Courier"]:
            if f in fams:
                return f
        return QFont().defaultFamily()

    def _apply_styles(self):
        theme = self._current_theme_dict()
        qss = f"""
        QWidget {{
            font-family: "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif;
        }}
        QWidget#card {{
            background: {theme['bg']};
            border: 1px solid {theme['border']};
            border-radius: 8px;
        }}
        QLabel#pair-base {{
            color: {theme['text_primary']};
        }}
        QLabel#pair-quote {{
            color: {theme['text_muted']};
            font-size: 11px;
        }}
        QLabel#source-label {{
            color: {theme['text_dim']};
            font-size: 11px;
        }}
        QLabel#stat-label {{
            color: {theme['text_dim']};
            font-size: 11px;
            font-weight: 500;
        }}
        QLabel#stat-val {{
            color: {theme['text_secondary']};
            font-size: 11px;
        }}
        QLabel#price {{
            color: {theme['text_primary']};
            letter-spacing: -0.5px;
        }}
        QLabel#change {{
            color: {theme['text_muted']};
            padding: 2px 6px;
            border-radius: 4px;
            background: transparent;
        }}
        """
        self.setStyleSheet(qss)

    # ----------------- 动态颜色(per-widget setStyleSheet) -----------------
    def _set_price_color(self, color_key):
        self._price_fade.stop()
        self._price_flash = color_key
        if color_key is None:
            self._price_fade_from = None
            self.price_label.setStyleSheet("")
        else:
            color = self._current_theme_dict()[color_key]
            self.price_label.setStyleSheet("color: " + color + ";")

    def _apply_price_fade(self, progress):
        if self._price_fade_from is None:
            return
        start = self._price_fade_from
        end = QColor(self._current_theme_dict()["text_primary"])
        red = round(start.red() + (end.red() - start.red()) * progress)
        green = round(start.green() + (end.green() - start.green()) * progress)
        blue = round(start.blue() + (end.blue() - start.blue()) * progress)
        color = QColor(red, green, blue)
        self.price_label.setStyleSheet("color: " + color.name() + ";")

    def _set_change_color(self, trend):
        self._change_trend = trend
        t = self._current_theme_dict()
        if trend == "up":
            self.change_label.setStyleSheet(
                "color: " + t["up"] + ";"
                "background: " + t["up_bg"] + ";"
            )
        elif trend == "down":
            self.change_label.setStyleSheet(
                "color: " + t["down"] + ";"
                "background: " + t["down_bg"] + ";"
            )
        else:
            self.change_label.setStyleSheet("")

    def _set_status_color(self, connected):
        self._connected = connected
        t = self._current_theme_dict()
        color = t["up"] if connected else t["down"]
        self.status_dot.setStyleSheet(
            "background: " + color + ";"
            "border-radius: 3px;"
            "min-width: 6px; max-width: 6px;"
            "min-height: 6px; max-height: 6px;"
        )

    # ---------------- worker ----------------
    def _start_worker(self):
        if self._worker is not None:
            self._stop_worker()
        source = get_source(self._current_source_key)
        self._current_source = source
        self._worker = TickerWorker(source, self._current_symbol, self._proxy_config)
        self._worker.ticker.connect(self._on_ticker)
        self._worker.status.connect(self._on_status)
        self._worker.start()

    def _stop_worker(self):
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    # ---------------- slots ----------------
    def _on_status(self, ok, msg):
        self._set_status_color(ok)
        if ok:
            self.source_label.setText(self._current_source["name"])
        else:
            self.source_label.setText(msg[:14])

    def _on_ticker(self, data):
        try:
            price  = float(data["price"])
            change = float(data["change"])
            high   = float(data["high"])
            low    = float(data["low"])
        except (KeyError, TypeError, ValueError):
            return

        if self._last_price is not None and price != self._last_price:
            direction = "up" if price > self._last_price else "down"
            self._flash_timer.stop()
            self._set_price_color(direction)
            self._flash_timer.start()
        self._last_price = price
        self.price_label.setText(format(price, ",.2f"))

        trend = "up" if change >= 0 else "down"
        sign  = "+" if change >= 0 else ""
        self.change_label.setText("{}{:.2f}%".format(sign, change))
        self._set_change_color(trend)

        self.high_val.setText(format(high, ",.2f"))
        self.low_val.setText(format(low, ",.2f"))

    def _clear_flash(self):
        if self._price_flash is None:
            return
        self._price_fade_from = QColor(self._current_theme_dict()[self._price_flash])
        self._price_fade.start()

    # ---------------- drag ----------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    # ---------------- menu ----------------
    def contextMenuEvent(self, event):
        menu = QMenu(self)

        # 数据源
        src_menu = menu.addMenu("\u6570\u636e\u6e90")
        for s in SOURCES:
            act = QAction(s["name"], self, checkable=True)
            act.setChecked(s["key"] == self._current_source_key)
            act.triggered.connect(
                lambda _c=False, k=s["key"]: self._switch_source(k)
            )
            src_menu.addAction(act)

        menu.addSeparator()

        # 外观 (浅/深/自动)
        theme_menu = menu.addMenu("\u5916\u89c2")
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for label, mode in [("\u8ddf\u968f\u7cfb\u7edf", "auto"),
                            ("\u6df1\u8272", "dark"),
                            ("\u6d45\u8272", "light")]:
            act = QAction(label, self, checkable=True)
            act.setChecked(self._theme_mode == mode)
            act.triggered.connect(lambda _c=False, mm=mode: self._set_theme_mode(mm))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)

        menu.addSeparator()

        # 切换交易对
        pair_menu = menu.addMenu("\u5207\u6362\u4ea4\u6613\u5bf9")
        for label, sym in self.SUPPORTED_PAIRS:
            act = QAction(label, self, checkable=True)
            act.setChecked(sym == self._current_symbol)
            act.triggered.connect(
                lambda _c=False, s=sym, l=label: self._switch_pair(s, l)
            )
            pair_menu.addAction(act)

        custom = QAction("\u81ea\u5b9a\u4e49\u4ea4\u6613\u5bf9...", self)
        custom.triggered.connect(self._custom_pair)
        menu.addAction(custom)

        menu.addSeparator()

        proxy_act = QAction("\u4ee3\u7406\u8bbe\u7f6e...", self)
        proxy_act.triggered.connect(self._open_proxy_dialog)
        menu.addAction(proxy_act)

        logo_size_act = QAction("Logo \u5927\u5c0f...", self)
        logo_size_act.triggered.connect(self._open_logo_size_dialog)
        menu.addAction(logo_size_act)

        menu.addSeparator()

        top_act = QAction("\u7a97\u53e3\u7f6e\u9876", self, checkable=True)
        top_act.setChecked(self._always_on_top)
        top_act.toggled.connect(self._toggle_top)
        menu.addAction(top_act)

        menu.addSeparator()

        quit_act = QAction("\u9000\u51fa", self)
        quit_act.triggered.connect(self.close)
        menu.addAction(quit_act)

        menu.exec(event.globalPos())

    def _toggle_top(self, checked):
        self._always_on_top = checked
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()

    def _open_proxy_dialog(self):
        dlg = ProxyDialog(self._proxy_config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._proxy_config = dlg.get_config()
            self._settings.setValue("proxy", json.dumps(self._proxy_config))
            self._stop_worker()
            self._start_worker()

    def _open_logo_size_dialog(self):
        dialog = LogoSizeDialog(self._logo_sizes, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._logo_sizes = dialog.get_sizes()
            self._normalise_logo_sizes()
            self._settings.setValue("logo_sizes", json.dumps(self._logo_sizes))
            self._update_pair_logo()
            self._update_source_logo()
            self._apply_pair_text_size()

    def _switch_source(self, key):
        if key == self._current_source_key:
            return
        self._current_source_key = key
        self._current_source = get_source(key)
        self._settings.setValue("source", key)
        self.source_label.setText(self._current_source["name"])
        self._update_source_logo()
        self._reset_state()
        self._stop_worker()
        self._start_worker()

    def _switch_pair(self, symbol, label):
        if symbol == self._current_symbol:
            return
        self._current_symbol = symbol
        self._current_label  = label
        base, quote = label.split("/")
        self.pair_base.setText(base)
        self.pair_quote.setText(" / " + quote)
        self._settings.setValue("symbol", symbol)
        self._settings.setValue("label",  label)
        self._update_pair_logo()
        self._reset_state()
        self._stop_worker()
        self._start_worker()

    def _custom_pair(self):
        text, ok = QInputDialog.getText(
            self,
            "\u81ea\u5b9a\u4e49\u4ea4\u6613\u5bf9",
            "\u8f93\u5165 Binance \u4ea4\u6613\u5bf9(\u5c0f\u5199,\u5982 ethusdt):",
            text=self._current_symbol,
        )
        if not ok:
            return
        raw = text.strip()
        if not raw:
            return
        sym = raw.lower().replace("/", "").replace("-", "")
        up  = raw.upper()
        if "/" not in up and up.endswith("USDT") and len(up) > 4:
            up = up[:-4] + "/USDT"
        self._switch_pair(sym, up)

    def _reset_state(self):
        self._last_price = None
        self.price_label.setText("--")
        self._set_price_color(None)
        self.change_label.setText("--")
        self._set_change_color(None)
        self.high_val.setText("--")
        self.low_val.setText("--")

    def closeEvent(self, event):
        self._settings.setValue("pos", self.pos())
        self._stop_worker()
        super().closeEvent(event)
        QTimer.singleShot(0, QApplication.instance().quit)


# ====================================================================
#  入口
# ====================================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Floating Ticker")

    w = TickerWindow()

    pos = w._settings.value("pos")
    if pos is not None:
        w.move(pos)
    else:
        screen = app.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            w.move(g.right() - w.width() - 20, g.top() + 60)
        else:
            w.move(200, 200)

    w.show()
    rc = app.exec()
    sys.stdout.flush()
    sys.stderr.flush()
    sys.exit(rc)


if __name__ == "__main__":
    main()
