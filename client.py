#!/usr/bin/env python3

import errno
import select
import signal
import socket
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog

import argparse


class Client(tk.Tk):
    RECONNECT_DELAY_MS = 1000
    POLL_INTERVAL_MS = 50

    def __init__(self, send_newline=False):
        super().__init__()

        self.host = "localhost"
        self.port = 5555

        self.socket = None
        self.socket_buffer = ""
        self._send_buffer = b""
        self._send_newline = tk.BooleanVar(value=send_newline)
        self._send_delimiter = b"\n" if send_newline else b"\0"
        self._connected = False
        self._connecting = False
        self._reconnect_after_id = None
        self._poll_after_id = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(0, self.focus_window)

        self._poll_after_id = self.after(self.POLL_INTERVAL_MS, self._poll_socket)

    def _build_ui(self):
        self.title("Client")

        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Connect to...", command=self.prompt_connect)
        file_menu.add_command(label="Quit", command=self.close)
        menu.add_cascade(label="File", menu=file_menu)
        options_menu = tk.Menu(menu, tearoff=False)
        options_menu.add_checkbutton(
            label="Send newline terminator",
            variable=self._send_newline,
            command=self._update_send_delimiter,
        )
        menu.add_cascade(label="Options", menu=options_menu)
        self.config(menu=menu)

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True)

        self.log_text = scrolledtext.ScrolledText(
            frame,
            wrap="word",
            state="disabled",
            takefocus=0,
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.log_text.tag_configure("outgoing", foreground="blue")
        self.log_text.bind("<Button-1>", self._focus_entry)

        self.command_entry = tk.Entry(frame)
        self.command_entry.pack(fill="x", padx=4, pady=4)
        self.command_entry.focus_set()

        self.status_var = tk.StringVar()
        self._status_base = ""
        status = tk.Label(self, textvariable=self.status_var, anchor="w", relief="sunken")
        status.pack(fill="x", side="bottom")

        self.command_history = []
        self.history_pos = 0
        self.command_entry.bind("<Return>", self.commandLineReturn)
        self.command_entry.bind("<Up>", self._history_up)
        self.command_entry.bind("<Down>", self._history_down)
        self._update_send_mode_status()

    def _focus_entry(self, event=None):
        self.command_entry.focus_set()
        return "break"

    def _update_send_delimiter(self):
        self._send_delimiter = b"\n" if self._send_newline.get() else b"\0"
        self._update_send_mode_status()

    def _update_send_mode_status(self):
        self.status_var.set(self._format_status(self._status_base))

    def focus_window(self):
        self.deiconify()
        self.lift()
        try:
            self.attributes("-topmost", True)
            self.after(0, lambda: self.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.focus_force()
        self.after(0, self.command_entry.focus_set)

    def prompt_connect(self):
        try:
            address = simpledialog.askstring(
                "Connect to",
                "Address (host:port):",
                initialvalue=f"{self.host}:{self.port}",
                parent=self,
            )
            if address is None or not address.strip():
                return
            address = address.strip()
            if ":" not in address:
                messagebox.showerror(
                    "Invalid address",
                    "Expected address in host:port format.",
                    parent=self,
                )
                return
            host, port_text = address.rsplit(":", 1)
            host = host.strip()
            port_text = port_text.strip()
            if not host or not port_text:
                messagebox.showerror(
                    "Invalid address",
                    "Expected address in host:port format.",
                    parent=self,
                )
                return
            try:
                port = int(port_text)
            except ValueError:
                messagebox.showerror(
                    "Invalid port",
                    "Port must be a number.",
                    parent=self,
                )
                return
            if not (1 <= port <= 65535):
                messagebox.showerror(
                    "Invalid port",
                    "Port must be between 1 and 65535.",
                    parent=self,
                )
                return
            self.setHost(host, port)
        finally:
            self._focus_entry()

    def message(self, string=""):
        self._status_base = string
        self.status_var.set(self._format_status(string))

    def _format_status(self, string):
        suffix = " | Send: newline" if self._send_newline.get() else " | Send: NUL"
        return f"{string}{suffix}" if string else suffix[3:]

    def close(self):
        self._cancel_reconnect()
        self._cancel_poll()
        self._close_socket()
        self.destroy()

    def _cancel_poll(self):
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None

    def _cancel_reconnect(self):
        if self._reconnect_after_id is not None:
            self.after_cancel(self._reconnect_after_id)
            self._reconnect_after_id = None

    def isConnected(self):
        return self._connected

    def commandLineReturn(self, event=None):
        text = self.command_entry.get()
        if not self.command_history or (text and text != self.command_history[-1]):
            self.command_history.append(text)
        self.history_pos = 0
        self.sendCommand(text)
        self._append_log("> " + text, outgoing=True)
        self.command_entry.delete(0, tk.END)
        return "break"

    def _history_up(self, event=None):
        if self.history_pos < len(self.command_history):
            self.history_pos += 1
            self._set_entry_text(self.command_history[-self.history_pos])
        return "break"

    def _history_down(self, event=None):
        if self.history_pos > 0:
            self.history_pos -= 1
            if self.history_pos == 0:
                self._set_entry_text("")
            else:
                self._set_entry_text(self.command_history[-self.history_pos])
        return "break"

    def _set_entry_text(self, text):
        self.command_entry.delete(0, tk.END)
        self.command_entry.insert(0, text)

    def sendCommand(self, command):
        if not self.isConnected():
            return
        payload = str(command).encode("utf-8") + self._send_delimiter
        self._send_buffer += payload
        self._flush_send_buffer()

    def setHost(self, host="localhost", port=5555):
        self.host = host
        self.port = port
        self.title("Client @ " + host + ":" + str(port))
        self.slotDisconnected()

    def slotConnect(self):
        self._cancel_reconnect()
        self._connect()

    def _connect(self):
        self._close_socket()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setblocking(False)
        self._connecting = True
        self._connected = False
        self.message("Connecting...")
        try:
            err = self.socket.connect_ex((self.host, self.port))
        except OSError as exc:
            self.slotSocketError(exc)
            return
        if err == 0:
            self.slotConnected()
        elif err in (errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK, errno.EINTR):
            return
        else:
            self.slotSocketError(err)

    def slotConnected(self):
        self._connecting = False
        self._connected = True
        self.message("Connected")

    def slotReconnect(self):
        if self.isConnected() or self._connecting:
            self._close_socket()
        self.slotDisconnected()

    def slotDisconnected(self):
        self.message("Disconnected")
        self.socket_buffer = ""
        self._send_buffer = b""
        self._close_socket()
        self.slotConnect()

    def slotSocketError(self, error):
        if isinstance(error, OSError):
            error = error.errno
        self._close_socket()
        if error in (errno.ECONNREFUSED, errno.ECONNRESET, errno.EHOSTUNREACH, errno.ENETUNREACH):
            self.message("Disconnected")
            self._schedule_reconnect()
        else:
            self.message("Socket error")
            self._schedule_reconnect()

    def _schedule_reconnect(self, delay_ms=None):
        if delay_ms is None:
            delay_ms = self.RECONNECT_DELAY_MS
        self._cancel_reconnect()
        self._reconnect_after_id = self.after(delay_ms, self.slotConnect)

    def _close_socket(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
        self.socket = None
        self._connecting = False
        self._connected = False

    def _poll_socket(self):
        if self.socket is not None:
            if self._connecting:
                self._check_connect_ready()
            elif self._connected:
                self._flush_send_buffer()
                self._read_socket()
        self._poll_after_id = self.after(self.POLL_INTERVAL_MS, self._poll_socket)

    def _check_connect_ready(self):
        try:
            _, writable, errored = select.select([], [self.socket], [self.socket], 0)
        except OSError as exc:
            self.slotSocketError(exc)
            return
        if errored:
            err = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            self.slotSocketError(err)
            return
        if not writable:
            return
        err = self.socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err == 0:
            self.slotConnected()
        elif err in (errno.EINPROGRESS, errno.EALREADY, errno.EWOULDBLOCK):
            return
        else:
            self.slotSocketError(err)

    def _flush_send_buffer(self):
        if not self._send_buffer or not self._connected:
            return
        try:
            sent = self.socket.send(self._send_buffer)
        except BlockingIOError:
            return
        except OSError as exc:
            self.slotSocketError(exc)
            return
        self._send_buffer = self._send_buffer[sent:]

    def _read_socket(self):
        try:
            readable, _, errored = select.select([self.socket], [], [self.socket], 0)
        except OSError as exc:
            self.slotSocketError(exc)
            return
        if errored:
            self.slotSocketError(errno.ECONNRESET)
            return
        if not readable:
            return
        while True:
            try:
                data = self.socket.recv(4096)
            except BlockingIOError:
                break
            except OSError as exc:
                self.slotSocketError(exc)
                return
            if not data:
                self.slotDisconnected()
                return
            self._handle_incoming(data)

    def _handle_incoming(self, data):
        text = data.decode("utf-8", errors="replace")
        for ch in text:
            if ch not in ("\0", "\n"):
                self.socket_buffer += ch
            else:
                if self.socket_buffer:
                    self.processCommand(self.socket_buffer)
                    self.socket_buffer = ""

    def processCommand(self, string):
        self._append_log(string)

    def _append_log(self, string, outgoing=False):
        self.log_text.configure(state="normal")
        if outgoing:
            self.log_text.insert("end", string + "\n", "outgoing")
        else:
            self.log_text.insert("end", string + "\n")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    parser = argparse.ArgumentParser(description="Legacy client GUI")
    parser.add_argument("-H", "--host", default="localhost")
    parser.add_argument("-p", "--port", type=int, default=5555)
    parser.add_argument(
        "--send-newline",
        action="store_true",
        help="Send commands terminated by newline instead of NUL",
    )
    parser.add_argument("address", nargs="?", help="host:port")
    parser.add_argument("overrides", nargs="*")
    args = parser.parse_args(sys.argv[1:])

    host = args.host
    port = args.port

    if args.address:
        address = args.address.strip()
        if ":" not in address:
            print("Expected address in host:port format.", file=sys.stderr)
            sys.exit(2)
        host, port_text = address.rsplit(":", 1)
        host = host.strip()
        port_text = port_text.strip()
        if not host or not port_text:
            print("Expected address in host:port format.", file=sys.stderr)
            sys.exit(2)
        try:
            port = int(port_text)
        except ValueError:
            print("Port must be a number.", file=sys.stderr)
            sys.exit(2)
        if not (1 <= port <= 65535):
            print("Port must be between 1 and 65535.", file=sys.stderr)
            sys.exit(2)

    print("Connecting to " + host + ":" + str(port))

    client = Client(send_newline=args.send_newline)
    client.setHost(host, port)

    if sys.platform == "darwin":
        client.lift()

    client.mainloop()
