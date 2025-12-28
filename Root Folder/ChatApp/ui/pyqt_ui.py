# pyqt_ui.py
from __future__ import annotations
import sys
import os
import sqlite3
import threading
import time
import logging
from datetime import datetime, date
from typing import Dict, Optional, Callable, Any, List
from ChatApp.client.conversations import ConversationManager
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QHBoxLayout, QTextEdit,
    QPushButton, QListWidget, QListWidgetItem, QMainWindow, QLineEdit,
    QScrollArea, QFrame, QSplitter, QMessageBox, QInputDialog, QFormLayout,
    QToolButton, QSizePolicy, QFrame
)
from PyQt5 import QtWidgets, QtGui, QtCore


# ----------------------------------------------------------
# App Signals (thread-safe)
# ----------------------------------------------------------
class AppSignals(QObject):
    message_received = pyqtSignal(dict)    # incoming message dict
    system_message = pyqtSignal(dict)
    auth_success = pyqtSignal(str)         # username
    auth_fail = pyqtSignal(str)


# ----------------------------------------------------------
# Simple SQLite helper for registered users (UI-side)
# ----------------------------------------------------------
class LocalDB:
    DB_PATH = os.path.join(os.getcwd(), "ui_state.db")

    def __init__(self):
        self._ensure_db()

    def _ensure_db(self):
        conn = sqlite3.connect(self.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    last_seen INTEGER
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def add_user(self, username: str):
        ts = int(time.time())
        conn = sqlite3.connect(self.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("INSERT OR REPLACE INTO users(username, last_seen) VALUES (?, ?)", (username, ts))
            conn.commit()
        finally:
            conn.close()

    def list_users(self) -> List[str]:
        conn = sqlite3.connect(self.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT username FROM users ORDER BY username COLLATE NOCASE")
            rows = cur.fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def touch_user(self, username: str):
        self.add_user(username)


# ----------------------------------------------------------
# Helpers for human-friendly timestamps
# ----------------------------------------------------------
def human_date_group(ts: Optional[int]) -> str:
    if ts is None:
        return "Unknown"
    dt = datetime.fromtimestamp(ts)
    today = date.today()
    if dt.date() == today:
        return "Today"
    if dt.date() == (today.fromordinal(today.toordinal() - 1)):
        return "Yesterday"
    return dt.strftime("%Y-%m-%d")


def human_time(ts: Optional[int]) -> str:
    if ts is None:
        return ""
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%H:%M")


# ----------------------------------------------------------
# UI Widgets
# ----------------------------------------------------------
class MessageBubble(QFrame):
    def __init__(self, text: str, timestamp: Optional[int], sent_by_me: bool = False, status: str = None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.NoFrame)

        if sent_by_me:
            bg = "#DFF7E0"
            text_color = "#0B2E13"
            align_right = True
            border_color = "#CDEECB"
        else:
            bg = "#F3F6FA"
            text_color = "#0C1A2B"
            align_right = False
            border_color = "#E3E9F1"

        self.setStyleSheet(f"""
            QFrame {{ border-radius: 10px; padding: 8px; margin: 6px; background: {bg}; border: 1px solid {border_color}; }}
            QLabel.msg-text {{ color: {text_color}; font-size: 13px; }}
            QLabel.msg-time {{ color: #6C7583; font-size: 10px; }}
            QLabel.msg-status {{ color: #7D8590; font-size: 9px; }}
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # message text
        text_lbl = QLabel(text)
        text_lbl.setWordWrap(True)
        text_lbl.setObjectName("msg-text")

        # time
        time_lbl = QLabel(human_time(timestamp))
        time_lbl.setObjectName("msg-time")

        # status label
        status_lbl = QLabel("")
        status_lbl.setObjectName("msg-status")

        # alignment container
        align = QHBoxLayout()
        inner = QVBoxLayout()
        inner.addWidget(text_lbl)
        inner.addWidget(time_lbl)
        inner.addWidget(status_lbl)

        if align_right:
            align.addStretch()
            align.addLayout(inner)
        else:
            align.addLayout(inner)
            align.addStretch()

        layout.addLayout(align)
        self.setLayout(layout)

class DateSeparator(QLabel):
    def __init__(self, label: str):
        super().__init__(label)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("""
            QLabel { color: #8A93A0; padding: 6px 12px; font-size: 11px; background: transparent; }
        """)


class ChatWidget(QWidget):
    """Single conversation page (chat with one user or global)."""
    def __init__(self, conversation_key: Optional[str], my_username: Optional[str] = None):
        super().__init__()
        self.key = conversation_key  # None => broadcast/global
        self.my_username = my_username

        self.vlayout = QVBoxLayout(self)
        self.vlayout.setContentsMargins(8, 8, 8, 8)
        self.vlayout.setSpacing(6)

        # Scroll area with messages
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.messages_widget = QWidget()
        self.messages_layout = QVBoxLayout(self.messages_widget)
        self.messages_layout.addStretch()
        self.messages_widget.setLayout(self.messages_layout)
        self.scroll_area.setWidget(self.messages_widget)
        self.vlayout.addWidget(self.scroll_area, 1)

        # Input area
        input_h = QHBoxLayout()
        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Type a message...")
        self.send_btn = QPushButton("Send")
        self.send_btn.setDefault(True)
        input_h.addWidget(self.input_box, 1)
        input_h.addWidget(self.send_btn)
        self.vlayout.addLayout(input_h)

        # hook
        self.send_btn.clicked.connect(self._on_send_clicked)
        self.input_box.returnPressed.connect(self._on_send_clicked)

        self._on_send: Optional[Callable[[str, Optional[str]], None]] = None

    def set_sender_callback(self, cb: Callable[[str, Optional[str]], None]):
        """cb(text, recipient) — recipient is conversation key (None for global)."""
        self._on_send = cb

    def _clear_messages(self):
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def append_message(self, text: str, sent_by_me: bool, timestamp: Optional[int] = None, status: str = None):
        bubble = MessageBubble(text, timestamp, sent_by_me, status=None)
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        QTimer.singleShot(40, lambda: self.scroll_area.verticalScrollBar().setValue(
            self.scroll_area.verticalScrollBar().maximum()
        ))


    def load_history(self, messages: List[dict], my_username: Optional[str]):
        # messages: list of dicts with timestamp
        self._clear_messages()
        if not messages:
            return

        # Group messages by day
        grouped: Dict[str, List[dict]] = {}
        for msg in messages:
            ts = msg.get("timestamp")
            group = human_date_group(ts)
            grouped.setdefault(group, []).append(msg)

        # preserve chronological order by earliest timestamp in group
        groups_sorted = sorted(grouped.items(), key=lambda g: min((m.get("timestamp") or 0) for m in g[1]))

        for group_label, msgs in groups_sorted:
            sep = DateSeparator(group_label)
            self.messages_layout.insertWidget(self.messages_layout.count() - 1, sep)
            for msg in msgs:
                payload = msg.get("payload", {})
                text = ""
                if isinstance(payload, dict):
                    text = payload.get("text") or payload.get("ciphertext") or str(payload)
                else:
                    text = str(payload)
                sent_by_me = (msg.get("from") == my_username)
                ts = msg.get("timestamp")
                self.append_message(text, sent_by_me, timestamp=ts)

    def _on_send_clicked(self):
        txt = self.input_box.text().strip()
        if not txt:
            return
        self.input_box.clear()
        if self._on_send:
            try:
                self._on_send(txt, self.key)
            except Exception as e:
                QMessageBox.warning(self, "Send failed", str(e))


# ---------------------------
# Collapsible Sidebar with Active / All users
# ---------------------------
class CollapsibleSection(QWidget):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)

        self._open = True
        self.title = title

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(2)

        # --- Header (button + label) ---
        header = QHBoxLayout()
        self.toggle_btn = QToolButton()
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.setChecked(True)
        self.toggle_btn.setText("▾")  # down arrow
        self.toggle_btn.setFixedWidth(20)
        self.toggle_btn.clicked.connect(self._toggle_section)

        label = QLabel(title)
        label.setStyleSheet("font-weight:600;")

        header.addWidget(self.toggle_btn)
        header.addWidget(label)
        header.addStretch()
        main.addLayout(header)

        # --- Content container ---
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 0, 0, 0)
        self.content_layout.setSpacing(4)

        main.addWidget(self.content)

    def _toggle_section(self):
        """Expand/collapse content section."""
        self._open = not self._open
        self.content.setVisible(self._open)
        self.toggle_btn.setText("▾" if self._open else "▸")  # rotate arrow

    def add_widget(self, widget: QWidget):
        self.content_layout.addWidget(widget)



class Sidebar(QWidget):
    """
    - Conversations list
    - Collapsible Active Users
    - Collapsible All Users
    """

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db

        # Stored lists
        self._conv_items: List[Optional[str]] = []
        self._user_all: List[str] = []
        self._user_active: List[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # --------------------------
        # Search Bar
        # --------------------------
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search users or chats…")
        self.search.textChanged.connect(self._on_search)
        layout.addWidget(self.search)

        # --------------------------
        # Conversations List
        # --------------------------
        self.conv_list = QListWidget()
        layout.addWidget(self.conv_list, 2)
        self.conv_list.itemClicked.connect(self._on_conv_clicked)

        # --------------------------
        # Active Users (collapsible)
        # --------------------------
        self.active_section = CollapsibleSection("Active Users")
        self.active_list = QListWidget()
        self.active_list.itemClicked.connect(self._on_user_clicked)
        self.active_section.add_widget(self.active_list)
        layout.addWidget(self.active_section)

        # --------------------------
        # All Users (collapsible)
        # --------------------------
        self.all_section = CollapsibleSection("All Users")
        self.all_list = QListWidget()
        self.all_list.itemClicked.connect(self._on_user_clicked)
        self.all_section.add_widget(self.all_list)
        layout.addWidget(self.all_section, 1)

        self.refresh_btn = QPushButton("Refresh Users")
        layout.addWidget(self.refresh_btn)


        # --------------------------
        # New Chat
        # --------------------------
        self.new_btn = QPushButton("New Chat")
        layout.addWidget(self.new_btn)

        # callback hooks
        self._on_conv_cb = None
        self._on_user_cb = None

    # --------------------------------------------------
    # Public setters used by MainWindow
    # --------------------------------------------------
    def set_items(self, conv_items: List[Optional[str]]):
        """Conversation keys coming from ConversationManager."""
        self._conv_items = conv_items
        self._refresh_conversations()

    def set_user_lists(self, all_users: List[str], active_users: List[str]):
        """Set active/all users (from USERLIST system message)."""
        self._user_all = sorted(all_users, key=str.lower)
        self._user_active = sorted(active_users, key=str.lower)
        self._refresh_users()
    def on_refresh(self, cb):
        self.refresh_btn.clicked.connect(cb)

    # --------------------------------------------------
    # Refresh UI
    # --------------------------------------------------
    def _refresh_conversations(self):
        self.conv_list.clear()
        for key in self._conv_items:
            label = "Broadcast" if key is None else key
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            self.conv_list.addItem(item)

    def _refresh_users(self):
        # Active Users
        self.active_list.clear()
        for u in self._user_active:
            self.active_list.addItem(u)

        # All Users
        self.all_list.clear()
        for u in self._user_all:
            label = f"{u} • online" if u in self._user_active else u
            self.all_list.addItem(label)

    def refresh(self):
        """Called by MainWindow after any update."""
        self._refresh_conversations()
        self._refresh_users()

    # --------------------------------------------------
    # Search
    # --------------------------------------------------
    def _on_search(self, text: str):
        text = text.lower().strip()

        # filter conversations
        for i in range(self.conv_list.count()):
            it = self.conv_list.item(i)
            it.setHidden(text not in it.text().lower())

        # filter active users
        for i in range(self.active_list.count()):
            it = self.active_list.item(i)
            it.setHidden(text not in it.text().lower())

        # filter all users
        for i in range(self.all_list.count()):
            it = self.all_list.item(i)
            it.setHidden(text not in it.text().lower())

    # --------------------------------------------------
    # Callbacks
    # --------------------------------------------------
    def on_item_activated(self, cb):
        self._on_conv_cb = cb

    def on_new(self, cb):
        self.new_btn.clicked.connect(cb)

    def on_user_clicked(self, cb):
        self._on_user_cb = cb

    # --------------------------------------------------
    # Internal click handlers
    # --------------------------------------------------
    def _on_conv_clicked(self, item):
        if self._on_conv_cb:
            self._on_conv_cb(item.data(Qt.UserRole))

    def _on_user_clicked(self, item):
        if not self._on_user_cb:
            return
        username = item.text().replace(" • online", "").strip()
        self._on_user_cb(username)


# ---------------------------
# LoginWindow
# ---------------------------
class LoginWindow(QWidget):
    login_clicked = pyqtSignal(str, str)
    register_clicked = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChatApp Login")
        self.setMinimumWidth(360)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(12)

        title = QLabel("ChatApp")
        title.setFont(QFont("Segoe UI", 20, QFont.Bold))
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Secure & Minimal")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #6C757D;")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(8)

        self.username_input = QLineEdit()
        self.username_input.setPlaceholderText("username")
        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("password")
        self.password_input.setEchoMode(QLineEdit.Password)

        form.addRow("Username:", self.username_input)
        form.addRow("Password:", self.password_input)

        layout.addLayout(form)

        btn_h = QHBoxLayout()
        self.login_btn = QPushButton("Login")
        self.register_btn = QPushButton("Register")
        btn_h.addWidget(self.login_btn)
        btn_h.addWidget(self.register_btn)

        self.login_btn.clicked.connect(self._on_login)
        self.register_btn.clicked.connect(self._on_register)

        layout.addLayout(btn_h)
        self.setLayout(layout)

    def _on_login(self):
        u = self.username_input.text().strip()
        p = self.password_input.text().strip()
        if not u or not p:
            QMessageBox.warning(self, "Validation", "Username and password required")
            return
        self.login_clicked.emit(u, p)

    def _on_register(self):
        u = self.username_input.text().strip()
        p = self.password_input.text().strip()
        if not u or not p:
            QMessageBox.warning(self, "Validation", "Username and password required")
            return
        self.register_clicked.emit(u, p)

    def show_error(self, message: str):
        QMessageBox.critical(self, "Error", message)


# ---------------------------
# Main Window (Corrected)
# ---------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ChatApp — Minimal")
        self.resize(1000, 640)

        # app signals
        self.signals = AppSignals()
        self.db = LocalDB()
        self.conversations = ConversationManager()

        self.client = None
        self.my_username: Optional[str] = None

        # main layout
        splitter = QSplitter()
        self.sidebar = Sidebar(self.db)
        splitter.addWidget(self.sidebar)

        # chat pages container
        self.pages: Dict[Optional[str], ChatWidget] = {}
        self.pages_container = QWidget()
        self.pages_layout = QVBoxLayout(self.pages_container)
        self.pages_layout.setContentsMargins(0, 0, 0, 0)
        self.pages_layout.setSpacing(0)
        splitter.addWidget(self.pages_container)
        splitter.setStretchFactor(1, 3)
        self.setCentralWidget(splitter)

        # status bar
        self.statusBar().showMessage("Not connected")

        # sidebar actions
        self.sidebar.on_item_activated(self.open_conversation)
        self.sidebar.on_new(self.action_new_chat)
        self.sidebar.on_user_clicked(self.open_user_chat)
        self.sidebar.on_refresh(self.refresh_user_list)


        # received message signals
        self.signals.message_received.connect(self._on_message_received)
        self.signals.system_message.connect(self._on_system_message)

        # ensure broadcast conversation exists
        self._ensure_page(None)
        # populate sidebar conversations
        self.sidebar.set_items(self.conversations.list_conversations())
        self.sidebar.refresh()

        # minimal modern styling
        self.setStyleSheet("""
            QMainWindow { background: #F6F8FA; }
            QListWidget { background: #FFFFFF; border: 1px solid #E6E9EE; }
            QPushButton { background: #0B6E4F; color: white; border-radius: 6px; padding: 6px 8px; }
            QPushButton#cancel { background: #A0AAB5; }
            QLineEdit { border: 1px solid #E3E6EB; padding: 6px; border-radius: 6px; }
        """)

    def closeEvent(self, event):
        """
        Ensure clean disconnect so presence updates immediately.
        Do not block UI for long; attempt a graceful disconnect and continue.
        """
        if self.client:
            try:
                # call disconnect; it is safe/idempotent and returns quickly
                self.client.disconnect()
            except Exception:
                logger = logging.getLogger("chatapp.ui")
                logger.debug("closeEvent: client.disconnect raised", exc_info=True)

        event.accept()   # allow the window to close

    def refresh_user_list(self):
        if not self.client:
            return
        try:
            self.client.send_protocol({"type": "USERLIST_REQUEST"})
        except Exception as e:
            QMessageBox.warning(self, "Refresh failed", str(e))

    # -------------------------
    # Integration with client
    # -------------------------
    def set_client(self, client):
        self.client = client

        # message callback
        try:
            client.set_on_message(lambda msg: self.signals.message_received.emit(msg))
        except Exception:
            try:
                client.on_message(lambda msg: self.signals.message_received.emit(msg))
            except Exception:
                raise RuntimeError("Client does not expose set_on_message/on_message")

        # system message callback
        try:
            client.set_on_system_message(lambda msg: self.signals.system_message.emit(msg))
        except Exception:
            try:
                client.on_system_message(lambda msg: self.signals.system_message.emit(msg))
            except Exception:
                pass

        self.statusBar().showMessage("Connected (not authenticated)")

    def set_username(self, username: str):
        self.my_username = username
        self.statusBar().showMessage(f"Connected as {username}")
        # propagate to existing pages
        for key, page in self.pages.items():
            page.my_username = username
            # reload history so outgoing messages appear as 'me'
            page.load_history(self.conversations.get_messages(key), username)

    # -------------------------
    # Page Management
    # -------------------------
    def _ensure_page(self, key: Optional[str]) -> ChatWidget:
        """
        Ensure a ChatWidget exists for 'key'. Do NOT remove widgets from the layout;
        just create & add when missing, and keep them all in the layout. We control
        visibility with setVisible() so internal widget state (input, scroll, etc.)
        stays intact.
        """
        # If already exists, return
        if key in self.pages:
            return self.pages[key]

        # create new page and keep it in the pages dict
        w = ChatWidget(key, my_username=self.my_username)
        w.set_sender_callback(self._send_from_ui)
        self.pages[key] = w

        # Add widget to layout (do NOT take others out)
        self.pages_layout.addWidget(w)

        # Initially hide it (we'll show the requested one explicitly)
        w.setVisible(False)
        return w

    def _hide_all_pages(self):
        """Hide every page widget (keeps them in the layout)."""
        for i in range(self.pages_layout.count()):
            item = self.pages_layout.itemAt(i)
            if not item:
                continue
            widget = item.widget()
            if widget:
                widget.setVisible(False)

    def refresh_sidebar(self):
        self.sidebar.set_items(self.conversations.list_conversations())
        self.sidebar.refresh()

    def open_conversation(self, key: Optional[str]):
        """
        Show the requested conversation page. Ensure it exists, load history,
        and then hide other pages (without removing them).
        """
        page = self._ensure_page(key)
        # load history into page (keeps input & scroll state)
        page.load_history(self.conversations.get_messages(key), self.my_username)

        # hide all pages and show the requested one
        self._hide_all_pages()
        page.setVisible(True)

        # refresh sidebar to reflect selection / ordering if needed
        self.sidebar.refresh()

    def open_user_chat(self, username: str):
        # ensure a DM exists (conversation keyed by username)
        self.conversations._conversations.setdefault(username, [])
        self.refresh_sidebar()
        self.open_conversation(username)

    def action_new_chat(self):
        text, ok = QInputDialog.getText(self, "New chat", "Enter username:")
        if not ok:
            return
        key = text.strip()
        if not key:
            return

        self.conversations._conversations.setdefault(key, [])
        self.sidebar.set_items(self.conversations.list_conversations())
        self.sidebar.refresh()
        self.open_conversation(key)

    # -------------------------
    # Sending / Receiving
    # -------------------------
    def _send_from_ui(self, text: str, recipient: Optional[str]):
        import time

        # local record
        self.conversations.record_outgoing(recipient, text, self.my_username)

        page = self._ensure_page(recipient)
        page.append_message(text, sent_by_me=True, timestamp=int(time.time()), status=None)

        # send to server
        if self.client:
            try:
                self.client.send_message(text, recipient=recipient)
            except Exception as e:
                QMessageBox.warning(self, "Send failed", str(e))

        self.refresh_sidebar()

    def _on_message_received(self, msg: dict):
        import time

        key = self.conversations.record_incoming(msg)
        page = self._ensure_page(key)

        payload = msg.get("payload", {}) or {}
        text = payload.get("text") if isinstance(payload, dict) else str(payload)
        sent_by_me = (msg.get("from") == self.my_username)

        status = msg.get("status") or "delivered"
        page.append_message(
        text,
        sent_by_me,
        timestamp=msg.get("timestamp") or int(time.time()),
        status=None
        )



        self.refresh_sidebar()

    def _on_system_message(self, msg: dict):
        mtype = msg.get("type", "").upper()

        if mtype == "USERLIST":
            payload = msg.get("payload", {}) or {}

            all_users = payload.get("users", [])
            online = payload.get("online", [])

            self.sidebar.set_user_lists(all_users, online)
            self.refresh_sidebar()
            return

        # Ignore presence events — snapshot will arrive
        if mtype in ("USER_JOINED", "USER_LEFT"):
            return

        # Ignore delivery updates (GUI removed)
        if mtype == "DELIVERY_UPDATE":
            return

        QMessageBox.information(self, "System message", str(msg))




    # convenience for controller to call when auth success occurs externally
    def handle_auth_success(self, username: str):
        # called by run_client_gui when login/register succeeds
        # set username and show main window (login window is external)
        self.set_username(username)
        self.sidebar.set_items(self.conversations.list_conversations())
        self.sidebar.refresh()
        self.show()

    def handle_auth_fail(self, reason: str):
        self.signals.auth_fail.emit(reason)


# ---------------------------
# Quick UI-only test harness
# ---------------------------
def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()

    # fake incoming messages on a timer to demo grouped timestamps
    def fake_incoming():
        import time, random
        cnt = 0
        while True:
            time.sleep(3.0)
            cnt += 1
            if cnt % 4 == 0:
                msg = {"type": "BROADCAST", "from": f"user{cnt%4+1}", "payload": {"text": f"hello everyone {cnt}"}, "meta": {}, "timestamp": int(time.time())}
            else:
                # chat msg to me (simulate)
                msg = {"type": "CHAT", "from": "user1", "to": None, "payload": {"text": f"hi {cnt}"}, "meta": {}, "timestamp": int(time.time())}
            win.signals.message_received.emit(msg)

    t = threading.Thread(target=fake_incoming, daemon=True)
    t.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
