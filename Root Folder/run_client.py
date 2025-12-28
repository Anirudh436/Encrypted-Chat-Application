# run_client_gui.py

import sys
import threading
import logging
from PyQt5.QtWidgets import QApplication, QMessageBox

from ChatApp.client.client import ChatClient
from ChatApp.ui.pyqt_ui import LoginWindow, MainWindow


HOST = "127.0.0.1"
PORT = 5000


class GUIController:
    """
    Controls login flow and main chat UI.
    """

    def __init__(self):
        self.client = ChatClient()

        # two windows
        self.login_win = LoginWindow()
        self.main_win = MainWindow()

        # connect login window actions
        self.login_win.login_clicked.connect(self.attempt_login)
        self.login_win.register_clicked.connect(self.attempt_register)

        # connect client signals → main window signals
        self.client.set_on_message(
            lambda msg: self.main_win.signals.message_received.emit(msg)
        )
        self.client.set_on_system_message(
            lambda msg: self.main_win.signals.system_message.emit(msg)
        )

        # custom auth signals used ONLY by this controller
        self.main_win.signals.auth_success.connect(self._auth_success)
        self.main_win.signals.auth_fail.connect(self._auth_fail)

    # ---------------------------------------------------
    # Application Start
    # ---------------------------------------------------
    def start(self):
        self.login_win.show()

    # ---------------------------------------------------
    # Connecting to server
    # ---------------------------------------------------
    def _connect_if_needed(self):
        if not self.client.connected:
            try:
                self.client.connect(HOST, PORT)
            except Exception as e:
                QMessageBox.critical(self.login_win, "Connection Error", str(e))
                raise

    # ---------------------------------------------------
    # User Login / Register
    # ---------------------------------------------------
    def attempt_login(self, username, password):
        self._connect_if_needed()

        def task():
            try:
                ok = self.client.authenticate(username, password)
                if ok:
                    self.main_win.signals.auth_success.emit(username)
                else:
                    self.main_win.signals.auth_fail.emit("Invalid credentials.")
            except Exception as e:
                self.main_win.signals.auth_fail.emit(str(e))

        threading.Thread(target=task, daemon=True).start()

    def attempt_register(self, username, password):
        self._connect_if_needed()

        def task():
            try:
                ok = self.client.register(username, password)
                if ok:
                    self.main_win.signals.auth_success.emit(username)
                else:
                    self.main_win.signals.auth_fail.emit("Registration failed.")
            except Exception as e:
                self.main_win.signals.auth_fail.emit(str(e))

        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------
    # Post-authentication actions
    # ---------------------------------------------------
    def _auth_success(self, username: str):
        """
        Called when server says AUTH_SUCCESS or REGISTER_SUCCESS.
        """
        self.login_win.hide()              # hide login window
        self.main_win.set_client(self.client)
        self.main_win.set_username(username)
        self.main_win.show()               # show main chat UI

    def _auth_fail(self, reason: str):
        QMessageBox.critical(self.login_win, "Authentication Failed", reason)


# ---------------------------------------------------
# App Entry Point
# ---------------------------------------------------
def main():
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)

    gui = GUIController()
    gui.start()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
