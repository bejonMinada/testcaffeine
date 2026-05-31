import queue
import math
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
import zipfile
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .config import (
    DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
    MANDATORY_LOCK_IDLE_TIMEOUT_SECONDS,
    MAX_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
    MAX_LOW_BATTERY_THRESHOLD_PERCENT,
    MAX_MAX_AWAKE_SESSION_HOURS,
    MIN_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
    MIN_LOW_BATTERY_THRESHOLD_PERCENT,
    MIN_MAX_AWAKE_SESSION_HOURS,
    get_app_data_dir,
    get_settings_path,
    load_settings,
    save_settings,
)
from .logging_setup import build_logger
from .monitor import IdleMonitor, MonitorCallbacks, MonitorState
from .pin_security import PIN_MAX_LENGTH, PIN_MIN_LENGTH, PinManager
from .security_events import SecurityEventSink
from .startup import is_auto_start_enabled, set_auto_start
from .tray_icon import WinTrayIcon


class _HoverTooltip:
    def __init__(self, widget: tk.Widget, text_provider: callable) -> None:
        self._widget = widget
        self._text_provider = text_provider
        self._tip_window: tk.Toplevel | None = None
        self._label: tk.Label | None = None

        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _on_enter(self, event: tk.Event) -> None:
        self._show(event)

    def _on_motion(self, event: tk.Event) -> None:
        if self._tip_window is None:
            self._show(event)
            return
        self._tip_window.geometry(f"+{event.x_root + 14}+{event.y_root + 14}")

    def _show(self, event: tk.Event) -> None:
        text = str(self._text_provider() or "").strip()
        if not text:
            self._hide(None)
            return
        if self._tip_window is None:
            tip = tk.Toplevel(self._widget)
            tip.wm_overrideredirect(True)
            tip.attributes("-topmost", True)
            label = tk.Label(
                tip,
                text=text,
                justify="left",
                bg="#111111",
                fg="#f4f4f4",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=5,
                font=("Calibri", 10),
            )
            label.pack()
            self._tip_window = tip
            self._label = label
        elif self._label is not None:
            self._label.configure(text=text)
        self._tip_window.geometry(f"+{event.x_root + 14}+{event.y_root + 14}")

    def _hide(self, _event: tk.Event | None) -> None:
        if self._tip_window is None:
            return
        try:
            self._tip_window.destroy()
        except tk.TclError:
            pass
        self._tip_window = None
        self._label = None


class TestCaffeineApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("TestCaffeine")
        self._set_window_icon()
        self.root.geometry("640x500")
        self.root.minsize(560, 420)
        self.root.configure(bg="#f5efe8")

        self.settings = load_settings()
        self.settings.auto_start_with_windows = is_auto_start_enabled()
        self.logger = build_logger()
        self.security_events = SecurityEventSink(enabled=self.settings.security_events_enabled)
        self.pin_manager = PinManager()
        self._events: queue.Queue[tuple[str, str]] = queue.Queue()
        self._is_hidden = False
        self._is_exiting = False
        self._countdown_started_at: float | None = None
        self._countdown_end_at: float | None = None
        self._focus_overlay: tk.Toplevel | None = None
        self._focus_overlay_hint: tk.Frame | None = None
        self._focus_overlay_blackout_job: str | None = None
        self._focus_overlay_recreate_job: str | None = None
        self._focus_overlay_blackout_active = False
        self._focus_overlay_active = False
        self._focus_overlay_auto_engaged = False
        self._focus_unlock_feedback_var = tk.StringVar(value="")
        self._focus_unlock_entry: ttk.Entry | None = None
        self.tray_icon: WinTrayIcon | None = None
        self.widget_ids: dict[str, str] = {
            "main_menubar": "main_menubar",
            "file_menu": "file_menu",
            "settings_menu": "settings_menu",
            "help_menu": "help_menu",
            "main_container": "main_container",
            "status_card": "status_card",
            "logo_canvas": "logo_canvas",
            "title_label": "title_label",
            "subtitle_label": "subtitle_label",
            "status_caption_label": "status_caption_label",
            "status_value_label": "status_value_label",
            "countdown_caption_label": "countdown_caption_label",
            "countdown_value_label": "countdown_value_label",
            "toggle_monitoring_button": "toggle_monitoring_button",
            "toggle_focus_screen_button": "toggle_focus_screen_button",
            "activity_log_card": "activity_log_card",
            "activity_log_label": "activity_log_label",
            "activity_log_text": "activity_log_text",
            "tray_context_menu": "tray_context_menu",
            "preferences_dialog": "preferences_dialog",
            "preferences_frame": "preferences_frame",
            "idle_timeout_label": "idle_timeout_label",
            "idle_timeout_spinbox": "idle_timeout_spinbox",
            "max_awake_label": "max_awake_label",
            "max_awake_spinbox": "max_awake_spinbox",
            "allow_indefinite_checkbutton": "allow_indefinite_checkbutton",
            "disable_on_battery_checkbutton": "disable_on_battery_checkbutton",
            "pause_on_low_battery_checkbutton": "pause_on_low_battery_checkbutton",
            "low_battery_threshold_label": "low_battery_threshold_label",
            "low_battery_threshold_spinbox": "low_battery_threshold_spinbox",
            "auto_start_checkbutton": "auto_start_checkbutton",
            "minimize_to_tray_checkbutton": "minimize_to_tray_checkbutton",
            "preferences_controls_frame": "preferences_controls_frame",
            "preferences_cancel_button": "preferences_cancel_button",
            "preferences_save_button": "preferences_save_button",
        }

        callbacks = MonitorCallbacks(
            on_state_change=self._on_state_change,
            on_log=self._on_log,
        )
        self.monitor = IdleMonitor(
            self.settings,
            self.logger,
            security_events=self.security_events,
            callbacks=callbacks,
        )

        self._build_style()
        self._build_menu()
        self._build_layout()
        self._apply_state(MonitorState.STOPPED)
        self._init_tray_icon()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(250, self._drain_events)
        self.root.after(250, self._refresh_countdown)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        title_font = self._pick_title_font()
        style.configure("Root.TFrame", background="#f5efe8")
        style.configure("Card.TFrame", background="#fff8f2")
        style.configure(
            "Title.TLabel",
            background="#fff8f2",
            foreground="#4a2f21",
            font=title_font,
        )
        style.configure(
            "Body.TLabel",
            background="#fff8f2",
            foreground="#6f5140",
            font=("Calibri", 10),
        )
        style.configure(
            "Value.TLabel",
            background="#fff8f2",
            foreground="#593728",
            font=("Calibri Bold", 12),
        )
        style.configure(
            "Primary.TButton",
            font=("Calibri Bold", 11),
            foreground="#ffffff",
            background="#7a4b2e",
            borderwidth=0,
            focusthickness=0,
            padding=(16, 10),
        )
        style.map(
            "Primary.TButton",
            background=[("active", "#5f3822"), ("disabled", "#c9b7ac")],
        )
        style.configure(
            "FocusIcon.TButton",
            font=("Segoe UI Symbol", 11),
            foreground="#4a2f21",
            background="#ecd9c8",
            borderwidth=0,
            focusthickness=0,
            padding=(16, 10),
            anchor="center",
        )
        style.map(
            "FocusIcon.TButton",
            background=[("active", "#dfc5ae"), ("disabled", "#f7efe8")],
        )
        style.configure(
            "Secondary.TButton",
            font=("Calibri", 10),
            foreground="#4a2f21",
            background="#ecd9c8",
            borderwidth=0,
            focusthickness=0,
            padding=(12, 8),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#dfc5ae"), ("disabled", "#f7efe8")],
        )
        style.configure(
            "Flat.TSpinbox",
            fieldbackground="#fffdfb",
            background="#fffdfb",
            borderwidth=1,
            relief="solid",
            padding=4,
            arrowsize=12,
        )

    def _pick_title_font(self) -> tuple[str, int, str]:
        pixel_candidates = [
            "Press Start 2P",
            "Pixelify Sans",
            "VT323",
            "Perfect DOS VGA 437",
            "Terminal",
        ]
        available = {name.lower(): name for name in tkfont.families(self.root)}
        for candidate in pixel_candidates:
            if candidate.lower() in available:
                return (available[candidate.lower()], 15, "bold")
        return ("Consolas", 16, "bold")

    def _build_menu(self) -> None:
        menubar = tk.Menu(self.root, name="main_menubar")
        self.menu_ids = {
            "file_hide_to_tray": "File>Hide to Tray",
            "file_start_monitoring": "File>Start Monitoring",
            "file_stop_monitoring": "File>Stop Monitoring",
            "file_export_diagnostics": "File>Export Diagnostics",
            "file_exit": "File>Exit",
            "settings_preferences": "Settings>Preferences",
            "settings_change_pin": "Settings>Change Focus PIN",
            "settings_recover_pin": "Settings>Recover Focus PIN",
            "help_about": "Help>About",
        }

        self._file_menu = tk.Menu(menubar, tearoff=0, name="file_menu")
        self._file_menu.add_command(label="Hide to Tray", command=self._minimize_to_tray)
        self._file_menu.add_separator()
        self._file_menu.add_command(label="Start Monitoring", command=self._start_monitoring)
        self._file_menu.add_command(label="Stop Monitoring", command=self._stop_monitoring)
        self._file_menu.add_separator()
        self._file_menu.add_command(label="Export Diagnostics", command=self._export_diagnostics)
        self._file_menu.add_separator()
        self._file_menu.add_command(label="Exit", command=self._exit_from_tray)
        menubar.add_cascade(label="File", menu=self._file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0, name="settings_menu")
        settings_menu.add_command(label="Preferences", command=self._open_settings_dialog)
        settings_menu.add_separator()
        settings_menu.add_command(label="Change Focus PIN", command=self._open_change_pin_dialog)
        settings_menu.add_command(
            label="Recover Focus PIN (after reboot)",
            command=self._open_recover_pin_dialog,
        )
        menubar.add_cascade(label="Settings", menu=settings_menu)

        help_menu = tk.Menu(menubar, tearoff=0, name="help_menu")
        help_menu.add_command(label="About", command=self._show_about_dialog)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _build_layout(self) -> None:
        container = ttk.Frame(self.root, style="Root.TFrame", padding=18, name="main_container")
        container.pack(fill=tk.BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        card = ttk.Frame(container, style="Card.TFrame", padding=18, name="status_card")
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(1, weight=1)

        logo = tk.Canvas(
            card,
            name="logo_canvas",
            width=56,
            height=56,
            bg="#fff8f2",
            highlightthickness=0,
            bd=0,
        )
        logo.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 10))
        self._logo_canvas = logo
        self._draw_coffee_logo(logo)

        ttk.Label(card, text="TestCaffeine", style="Title.TLabel", name="title_label").grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(
            card,
            text="Focus privacy screen helps hide content while idle; it is not a full device lock.",
            style="Body.TLabel",
            name="subtitle_label",
        ).grid(row=1, column=1, pady=(2, 16), sticky="w")

        ttk.Label(card, text="Status", style="Body.TLabel", name="status_caption_label").grid(
            row=2, column=0, sticky="w", pady=(6, 0)
        )
        self.status_var = tk.StringVar(value="Stopped")
        self.status_label = ttk.Label(
            card,
            textvariable=self.status_var,
            style="Value.TLabel",
            name="status_value_label",
        )
        self.status_label.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(6, 0))

        ttk.Label(card, text="Time left", style="Body.TLabel", name="countdown_caption_label").grid(
            row=3, column=0, sticky="w", pady=(8, 0)
        )
        self.countdown_var = tk.StringVar(value="--:--")
        self.countdown_label = ttk.Label(
            card,
            textvariable=self.countdown_var,
            style="Value.TLabel",
            name="countdown_value_label",
        )
        self.countdown_label.grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(8, 0))

        actions_row = ttk.Frame(card, style="Card.TFrame", name="main_actions_row")
        actions_row.grid(row=4, column=0, columnspan=2, sticky="w", pady=(18, 0))
        button_width = 180
        button_height = 44

        action_slot = ttk.Frame(
            actions_row,
            style="Card.TFrame",
            width=button_width,
            height=button_height,
            name="toggle_monitoring_button_slot",
        )
        action_slot.grid(row=0, column=0, sticky="w")
        action_slot.grid_propagate(False)

        focus_slot = ttk.Frame(
            actions_row,
            style="Card.TFrame",
            width=button_width,
            height=button_height,
            name="toggle_focus_screen_button_slot",
        )
        focus_slot.grid(row=0, column=1, sticky="w", padx=(10, 0))
        focus_slot.grid_propagate(False)

        self.action_btn = ttk.Button(
            action_slot,
            name="toggle_monitoring_button",
            text="Start monitoring",
            style="Primary.TButton",
            command=self._toggle_monitoring,
        )
        self.action_btn.place(relx=0.0, rely=0.0, relwidth=1.0, relheight=1.0)

        self.focus_btn = ttk.Button(
            focus_slot,
            name="toggle_focus_screen_button",
            text="👁",
            style="FocusIcon.TButton",
            command=self._toggle_focus_overlay,
        )
        self.focus_btn.place(relx=0.0, rely=0.0, relwidth=1.0, relheight=1.0)
        self._action_btn_tooltip = _HoverTooltip(self.action_btn, self._get_action_button_tooltip_text)
        self._focus_btn_tooltip = _HoverTooltip(self.focus_btn, self._get_focus_button_tooltip_text)

        log_card = ttk.Frame(container, style="Card.TFrame", padding=14, name="activity_log_card")
        log_card.grid(row=1, column=0, sticky="nsew", pady=(14, 0))
        log_card.columnconfigure(0, weight=1)
        log_card.rowconfigure(1, weight=1)

        ttk.Label(log_card, text="Activity log", style="Body.TLabel", name="activity_log_label").grid(
            row=0, column=0, sticky="w"
        )

        self.log_text = tk.Text(
            log_card,
            name="activity_log_text",
            height=10,
            bg="#fffaf6",
            fg="#4a2f21",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            state=tk.DISABLED,
        )
        self.log_text.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

    def _draw_coffee_logo(self, canvas: tk.Canvas) -> None:
        canvas.delete("all")
        # Saucer
        canvas.create_rectangle(10, 43, 46, 46, fill="#6f4430", outline="#5b3625", width=1)
        # Mug body
        canvas.create_rectangle(14, 22, 40, 42, fill="#fff5eb", outline="#5f3822", width=2)
        # Coffee surface
        canvas.create_rectangle(16, 24, 38, 28, fill="#8d5a3b", outline="#8d5a3b", width=1)
        # Mug handle (outer then inner cutout)
        canvas.create_oval(38, 26, 50, 39, fill="#fff5eb", outline="#5f3822", width=2)
        canvas.create_oval(41, 29, 47, 36, fill="#fff8f2", outline="#fff8f2", width=1)
        # Steam curls
        canvas.create_arc(16, 8, 24, 22, start=70, extent=210, style=tk.ARC, outline="#d3b7a1", width=2)
        canvas.create_arc(24, 6, 32, 20, start=70, extent=210, style=tk.ARC, outline="#d3b7a1", width=2)
        canvas.create_arc(32, 8, 40, 22, start=70, extent=210, style=tk.ARC, outline="#d3b7a1", width=2)

    def _set_window_icon(self) -> None:
        icon_path = self._resolve_asset_path("assets/testcaffeine.ico")
        if not icon_path.exists():
            return
        try:
            self.root.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass

    def _resolve_asset_path(self, relative_path: str) -> Path:
        if hasattr(sys, "_MEIPASS"):
            base = Path(getattr(sys, "_MEIPASS"))
        else:
            base = Path(__file__).resolve().parent.parent
        return base / relative_path

    def _init_tray_icon(self) -> None:
        icon_path = self._resolve_asset_path("assets/testcaffeine.ico")
        self.tray_icon = WinTrayIcon(
            tooltip="TestCaffeine",
            icon_path=icon_path,
            on_show_hide=lambda: self.root.after(0, self._toggle_window_visibility),
            on_context_menu=lambda x, y: self.root.after(0, lambda: self._show_tray_context_menu(x, y)),
        )
        self.tray_icon.start()
        self.tray_icon.set_window_visible(True)
        self.tray_icon.set_running(False)

    def _toggle_window_visibility(self) -> None:
        if self._is_hidden:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._is_hidden = False
        else:
            self._minimize_to_tray()

        if self.tray_icon:
            self.tray_icon.set_window_visible(not self._is_hidden)

    def _show_window(self) -> None:
        if self._is_hidden:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self._is_hidden = False
        if self.tray_icon:
            self.tray_icon.set_window_visible(True)

    def _show_tray_context_menu(self, x: int, y: int) -> None:
        menu = tk.Menu(self.root, tearoff=0, name="tray_context_menu", bg="#fff8f2", fg="#4a2f21",
                       activebackground="#ecd9c8", activeforeground="#4a2f21",
                       relief="flat", borderwidth=1)
        if self._is_hidden:
            menu.add_command(label="Show Window", command=self._show_window)
        else:
            menu.add_command(label="Hide to Tray", command=self._minimize_to_tray)
        menu.add_separator()
        if self.monitor.state == MonitorState.STOPPED:
            menu.add_command(label="Start Monitoring", command=self._start_monitoring)
        else:
            menu.add_command(label="Stop Monitoring", command=self._stop_monitoring)
        menu.add_separator()
        menu.add_command(label="Preferences", command=self._open_settings_dialog)
        menu.add_separator()
        menu.add_command(label="Exit", command=self._exit_from_tray)
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _minimize_to_tray(self) -> None:
        self.root.withdraw()
        self._is_hidden = True
        if self.tray_icon:
            self.tray_icon.set_window_visible(False)

    def _exit_from_tray(self) -> None:
        self._is_exiting = True
        self._on_close()

    def _show_about_dialog(self) -> None:
        messagebox.showinfo(
            "About TestCaffeine",
            "TestCaffeine\n\n"
            "Windows wake-control utility for long automation runs.\n"
            "Uses Windows power APIs without input simulation.\n"
            "Focus privacy mode is an app-layer privacy aid, not a replacement for Windows lock policies.",
        )

    def _toggle_focus_overlay(self) -> None:
        if self.monitor.state == MonitorState.STOPPED:
            self._append_log("Start monitoring to enable focus mode")
            return
        if self._focus_overlay_active:
            self._append_log("Focus privacy screen cannot be disabled while monitoring is active")
            return
        self._enable_focus_overlay(auto=False)

    def _get_action_button_tooltip_text(self) -> str:
        if self.monitor.state == MonitorState.STOPPED:
            return "Start monitoring idle activity and keep the system awake while idle."
        return "Stop monitoring and release the wake assertion immediately."

    def _get_focus_button_tooltip_text(self) -> str:
        if self.monitor.state == MonitorState.STOPPED:
            return "Focus mode is available only while monitoring is running."
        if self._focus_overlay_active:
            return "Focus privacy screen is active and cannot be disabled during monitoring."
        return "Enable the full-screen focus privacy screen."

    def _ensure_pin_setup(self) -> bool:
        if self.pin_manager.is_configured():
            return True
        self.security_events.emit("focus_pin_setup_required", severity="warning")
        messagebox.showinfo(
            "Set Focus PIN",
            f"Focus privacy mode requires a {PIN_MIN_LENGTH}-{PIN_MAX_LENGTH} digit PIN before monitoring starts.",
        )
        return self._open_pin_setup_dialog()

    def _enable_focus_overlay(self, auto: bool) -> None:
        # Forced-close handling may briefly leave the active flag set while recreating the overlay.
        if self._focus_overlay_active and self._focus_overlay is not None:
            self._focus_overlay_auto_engaged = auto
            return

        overlay = tk.Toplevel(self.root, name="focus_overlay")
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.protocol("WM_DELETE_WINDOW", lambda: self._on_focus_overlay_forced_close("window_close"))
        try:
            overlay.attributes("-fullscreen", True)
        except tk.TclError:
            overlay.geometry(
                f"{overlay.winfo_screenwidth()}x{overlay.winfo_screenheight()}+0+0"
            )
        try:
            overlay.attributes("-alpha", 0.72)
        except tk.TclError:
            pass
        overlay.configure(bg="#000000")

        hint = tk.Frame(overlay, bg="#111111", bd=1, relief="solid", name="focus_overlay_hint")
        prompt = tk.Label(
            hint,
            text="Focus privacy screen active\nThis helps hide app content while idle and is not a full OS lock.\nEnter your PIN to unlock.",
            bg="#111111",
            fg="#e6e6e6",
            font=("Calibri", 11),
            justify="center",
            name="focus_overlay_prompt",
        )
        prompt.pack(padx=14, pady=(10, 8))

        entry = ttk.Entry(hint, width=20, show="*", justify="center")
        entry.pack(padx=14, pady=(0, 6))
        entry.focus_force()
        self._focus_unlock_entry = entry
        self._focus_unlock_feedback_var.set("")
        feedback = tk.Label(
            hint,
            textvariable=self._focus_unlock_feedback_var,
            bg="#111111",
            fg="#ffcc8b",
            font=("Calibri", 10),
            justify="center",
            name="focus_overlay_feedback_label",
        )
        feedback.pack(padx=14, pady=(2, 8))
        unlock_btn = ttk.Button(
            hint,
            text="Unlock with PIN",
            style="Secondary.TButton",
            command=self._attempt_focus_unlock,
        )
        unlock_btn.pack(padx=14, pady=(0, 10))

        def show_hint(_event: tk.Event) -> None:
            if self._focus_overlay_blackout_active:
                self._set_focus_overlay_transparent(schedule_blackout=False)
            hint.place(relx=0.5, rely=0.5, anchor="center")

        def on_overlay_destroyed(_event: tk.Event) -> None:
            if self._focus_overlay_active:
                self._on_focus_overlay_forced_close("destroyed")

        def on_overlay_focus_out(_event: tk.Event) -> None:
            self.security_events.emit("focus_lock_focus_lost", severity="warning")
            self.root.after(80, self._refocus_overlay)

        overlay.bind("<Motion>", show_hint)
        overlay.bind("<Key>", show_hint)
        overlay.bind("<Return>", lambda _e: self._attempt_focus_unlock())
        overlay.bind("<Alt-F4>", lambda _e: "break")
        overlay.bind("<Button-1>", show_hint)
        overlay.bind("<FocusOut>", on_overlay_focus_out)
        overlay.bind("<Destroy>", on_overlay_destroyed)
        overlay.focus_force()
        # Show hint immediately so the user sees instructions without needing to move the mouse.
        hint.place(relx=0.5, rely=0.5, anchor="center")

        self._focus_overlay = overlay
        self._focus_overlay_hint = hint
        self._focus_overlay_active = True
        self._focus_overlay_auto_engaged = auto
        self._focus_overlay_blackout_active = False
        self._set_focus_overlay_transparent(schedule_blackout=False)
        self.focus_btn.configure(text="👁")
        self._append_log("Focus privacy screen enabled")
        self.security_events.emit("focus_lock_engaged", auto=auto)

    def _disable_focus_overlay(self) -> None:
        if not self._focus_overlay_active and self._focus_overlay is None:
            return
        # Mark inactive before destroy so <Destroy> handlers do not re-enter disable.
        self._focus_overlay_active = False
        self._focus_overlay_auto_engaged = False
        self._cancel_focus_blackout()
        if self._focus_overlay is not None:
            try:
                self._focus_overlay.destroy()
            except tk.TclError:
                pass
        self._focus_overlay = None
        self._focus_overlay_hint = None
        self._focus_unlock_entry = None
        self._focus_unlock_feedback_var.set("")
        if self._focus_overlay_recreate_job is not None:
            try:
                self.root.after_cancel(self._focus_overlay_recreate_job)
            except tk.TclError:
                pass
            self._focus_overlay_recreate_job = None
        self._focus_overlay_blackout_active = False
        if hasattr(self, "focus_btn"):
            self.focus_btn.configure(text="👁")
        self._append_log("Focus privacy screen disabled")
        self.security_events.emit("focus_lock_disengaged")

    def _attempt_focus_unlock(self) -> None:
        if self._focus_unlock_entry is None:
            return
        entered_pin = self._focus_unlock_entry.get()
        self.security_events.emit("focus_unlock_attempt")
        try:
            result = self.pin_manager.verify_pin(entered_pin)
        except ValueError as exc:
            self._focus_unlock_feedback_var.set(str(exc))
            self.security_events.emit("focus_unlock_error", severity="warning", error=str(exc))
            return

        if result.success:
            self.security_events.emit("focus_unlock_success")
            self._disable_focus_overlay()
            return

        self._focus_unlock_entry.delete(0, tk.END)
        self._focus_unlock_feedback_var.set(
            f"Invalid PIN. Retry in {result.retry_after_seconds}s (attempts: {result.failed_attempts})."
        )
        severity = "warning" if result.failed_attempts < 4 else "error"
        self.security_events.emit(
            "focus_unlock_failed",
            severity=severity,
            failed_attempts=result.failed_attempts,
            retry_after_seconds=result.retry_after_seconds,
        )
        self.root.after(result.retry_after_seconds * 1000, self._refresh_unlock_feedback)

    def _refresh_unlock_feedback(self) -> None:
        if self._focus_overlay_active and self._focus_overlay is not None:
            self._focus_unlock_feedback_var.set("Enter PIN to unlock.")

    def _refocus_overlay(self) -> None:
        if not self._focus_overlay_active or self._focus_overlay is None:
            return
        try:
            self._focus_overlay.attributes("-topmost", True)
            self._focus_overlay.lift()
            self._focus_overlay.focus_force()
        except tk.TclError:
            pass

    def _on_focus_overlay_forced_close(self, reason: str) -> None:
        if not self._focus_overlay_active:
            return
        self.security_events.emit("focus_lock_forced_close_attempt", severity="error", reason=reason)
        self._append_log("Focus privacy screen close attempt blocked")
        self._focus_overlay = None
        self._focus_overlay_hint = None
        self._focus_unlock_entry = None
        if self._focus_overlay_recreate_job is not None:
            try:
                self.root.after_cancel(self._focus_overlay_recreate_job)
            except tk.TclError:
                pass
        self._focus_overlay_recreate_job = self.root.after(
            120,
            lambda: self._recreate_focus_overlay(),
        )

    def _recreate_focus_overlay(self) -> None:
        self._focus_overlay_recreate_job = None
        if self._focus_overlay_active and self.monitor.state != MonitorState.STOPPED:
            self._enable_focus_overlay(auto=self._focus_overlay_auto_engaged)

    def _schedule_focus_blackout(self) -> None:
        if self._focus_overlay is None:
            return
        self._cancel_focus_blackout()
        delay_seconds = max(
            MIN_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
            min(
                int(self.settings.focus_overlay_blackout_delay_seconds),
                MAX_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS,
            ),
        )
        self._focus_overlay_blackout_job = self.root.after(
            int(delay_seconds * 1000),
            self._activate_focus_blackout,
        )

    def _set_focus_overlay_transparent(self, schedule_blackout: bool) -> None:
        if self._focus_overlay is None:
            return
        self._cancel_focus_blackout()
        try:
            self._focus_overlay.attributes("-alpha", 0.72)
        except tk.TclError:
            pass
        try:
            self._focus_overlay.configure(cursor="")
        except tk.TclError:
            pass
        self._focus_overlay_blackout_active = False
        if schedule_blackout:
            self._schedule_focus_blackout()

    def _cancel_focus_blackout(self) -> None:
        if self._focus_overlay_blackout_job is None:
            return
        try:
            self.root.after_cancel(self._focus_overlay_blackout_job)
        except tk.TclError:
            pass
        self._focus_overlay_blackout_job = None

    def _activate_focus_blackout(self) -> None:
        self._focus_overlay_blackout_job = None
        if self._focus_overlay is None or not self._focus_overlay_active:
            return
        try:
            self._focus_overlay.attributes("-alpha", 1.0)
        except tk.TclError:
            pass
        if self._focus_overlay_hint is not None:
            self._focus_overlay_hint.place_forget()
        try:
            self._focus_overlay.configure(cursor="none")
        except tk.TclError:
            pass
        self._focus_overlay_blackout_active = True
        self._append_log("Focus privacy screen switched to blackout mode")

    def _open_pin_setup_dialog(self) -> bool:
        return self._open_pin_dialog(
            title="Set Focus PIN",
            apply_pin=lambda _current, new, _username: self.pin_manager.set_new_pin(new),
            require_current=False,
        )

    def _open_change_pin_dialog(self) -> bool:
        return self._open_pin_dialog(
            title="Change Focus PIN",
            apply_pin=lambda current, new, _username: self._change_existing_pin(current, new),
            require_current=True,
        )

    def _change_existing_pin(self, current_pin: str, new_pin: str) -> None:
        result = self.pin_manager.change_pin(current_pin, new_pin)
        if not result.success:
            raise ValueError(
                f"Current PIN verification failed. Retry in {result.retry_after_seconds}s."
            )

    def _open_recover_pin_dialog(self) -> bool:
        if not self.pin_manager.can_recover_after_reboot():
            messagebox.showerror(
                "Recovery not available",
                "PIN recovery is disabled in the current session. Restart Windows, sign in, and try again.",
            )
            return False
        return self._open_pin_dialog(
            title="Recover Focus PIN",
            apply_pin=lambda _current, new, username: self.pin_manager.recover_pin_after_reboot(
                windows_username=username,
                new_pin=new,
            ),
            require_current=False,
            include_identity_confirmation=True,
        )

    def _open_pin_dialog(
        self,
        title: str,
        apply_pin: Callable[[str, str, str], None],
        require_current: bool,
        include_identity_confirmation: bool = False,
    ) -> bool:
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg="#fff8f2")
        dialog.grab_set()
        dialog.focus_force()

        frame = ttk.Frame(dialog, style="Card.TFrame", padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        row = 0
        current_var = tk.StringVar(value="")
        if require_current:
            ttk.Label(frame, text="Current PIN", style="Body.TLabel").grid(row=row, column=0, sticky="w")
            ttk.Entry(frame, textvariable=current_var, show="*", width=24).grid(
                row=row,
                column=1,
                sticky="w",
                padx=(10, 0),
            )
            row += 1

        username_var = tk.StringVar(value="")
        if include_identity_confirmation:
            ttk.Label(frame, text="Windows username", style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=(8, 0))
            ttk.Entry(frame, textvariable=username_var, width=24).grid(
                row=row,
                column=1,
                sticky="w",
                padx=(10, 0),
                pady=(8, 0),
            )
            row += 1

        new_var = tk.StringVar(value="")
        confirm_var = tk.StringVar(value="")
        ttk.Label(frame, text=f"New PIN ({PIN_MIN_LENGTH}-{PIN_MAX_LENGTH} digits)", style="Body.TLabel").grid(
            row=row, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Entry(frame, textvariable=new_var, show="*", width=24).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=(8, 0)
        )
        row += 1
        ttk.Label(frame, text="Confirm PIN", style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=confirm_var, show="*", width=24).grid(
            row=row, column=1, sticky="w", padx=(10, 0), pady=(8, 0)
        )
        row += 1

        feedback_var = tk.StringVar(value="")
        ttk.Label(frame, textvariable=feedback_var, style="Body.TLabel").grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        row += 1

        action_frame = ttk.Frame(frame, style="Card.TFrame")
        action_frame.grid(row=row, column=0, columnspan=2, sticky="e", pady=(14, 0))

        success = {"value": False}

        def on_save() -> None:
            current_pin = current_var.get()
            new_pin = new_var.get()
            confirm_pin = confirm_var.get()
            if new_pin != confirm_pin:
                feedback_var.set("PIN values do not match.")
                return
            try:
                apply_pin(current_pin, new_pin, username_var.get())
            except Exception as exc:
                feedback_var.set(str(exc))
                return
            success["value"] = True
            self.security_events.emit("focus_pin_updated", mode=title.lower().replace(" ", "_"))
            dialog.destroy()

        ttk.Button(action_frame, text="Cancel", style="Secondary.TButton", command=dialog.destroy).grid(
            row=0, column=0, padx=(0, 8)
        )
        ttk.Button(action_frame, text="Save PIN", style="Primary.TButton", command=on_save).grid(row=0, column=1)

        self.root.wait_window(dialog)
        return bool(success["value"])

    def _open_settings_dialog(self) -> None:
        # Ensure preferences are visible when opened from tray while the main window is hidden.
        self._show_window()
        dialog = tk.Toplevel(self.root, name="preferences_dialog")
        dialog.title("Preferences")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.configure(bg="#fff8f2")

        frame = ttk.Frame(dialog, style="Card.TFrame", padding=16, name="preferences_frame")
        frame.pack(fill=tk.BOTH, expand=True)

        max_awake_hours_var = tk.IntVar(value=self.settings.max_awake_session_hours)
        allow_indefinite_awake_var = tk.BooleanVar(value=self.settings.allow_indefinite_awake)
        disable_on_battery_var = tk.BooleanVar(value=self.settings.disable_on_battery)
        pause_on_low_battery_var = tk.BooleanVar(value=self.settings.pause_on_low_battery)
        low_battery_threshold_var = tk.IntVar(value=self.settings.low_battery_threshold_percent)
        auto_start_var = tk.BooleanVar(value=self.settings.auto_start_with_windows)
        minimize_to_tray_var = tk.BooleanVar(value=self.settings.minimize_to_tray)

        ttk.Label(frame, text="Idle timeout (seconds)", style="Body.TLabel", name="idle_timeout_label").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            name="idle_timeout_spinbox",
            text=str(MANDATORY_LOCK_IDLE_TIMEOUT_SECONDS),
            style="Value.TLabel",
        ).grid(row=0, column=1, sticky="w", padx=(10, 0))

        ttk.Label(frame, text="Max awake session (hours)", style="Body.TLabel", name="max_awake_label").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        max_awake_spin = ttk.Spinbox(
            frame,
            name="max_awake_spinbox",
            from_=MIN_MAX_AWAKE_SESSION_HOURS,
            to=MAX_MAX_AWAKE_SESSION_HOURS,
            textvariable=max_awake_hours_var,
            width=8,
            style="Flat.TSpinbox",
            justify="center",
        )
        max_awake_spin.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(10, 0))

        ttk.Checkbutton(
            frame,
            name="allow_indefinite_checkbutton",
            text="Allow indefinite awake while idle",
            variable=allow_indefinite_awake_var,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            frame,
            name="disable_on_battery_checkbutton",
            text="Do not keep awake on battery",
            variable=disable_on_battery_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Checkbutton(
            frame,
            name="pause_on_low_battery_checkbutton",
            text="Pause wake assertions on low battery",
            variable=pause_on_low_battery_var,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(frame, text="Low battery threshold (%)", style="Body.TLabel", name="low_battery_threshold_label").grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )
        low_battery_spin = ttk.Spinbox(
            frame,
            name="low_battery_threshold_spinbox",
            from_=MIN_LOW_BATTERY_THRESHOLD_PERCENT,
            to=MAX_LOW_BATTERY_THRESHOLD_PERCENT,
            textvariable=low_battery_threshold_var,
            width=8,
            style="Flat.TSpinbox",
            justify="center",
        )
        low_battery_spin.grid(row=5, column=1, sticky="w", padx=(10, 0), pady=(8, 0))

        ttk.Checkbutton(
            frame,
            name="auto_start_checkbutton",
            text="Start automatically with Windows",
            variable=auto_start_var,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

        ttk.Checkbutton(
            frame,
            name="minimize_to_tray_checkbutton",
            text="Minimize to system tray when closing",
            variable=minimize_to_tray_var,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(
            frame,
            text=f"Focus privacy lock idle timeout: {MANDATORY_LOCK_IDLE_TIMEOUT_SECONDS} seconds (always on)",
            style="Body.TLabel",
            name="focus_blackout_delay_label",
        ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))

        ttk.Label(
            frame,
            text=f"Focus blackout delay: {DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS} seconds (managed by policy)",
            style="Body.TLabel",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 0))

        def refresh_dependent_controls() -> None:
            if allow_indefinite_awake_var.get():
                max_awake_spin.state(["disabled"])
            else:
                max_awake_spin.state(["!disabled"])

            if pause_on_low_battery_var.get():
                low_battery_spin.state(["!disabled"])
            else:
                low_battery_spin.state(["disabled"])

        allow_indefinite_awake_var.trace_add("write", lambda *_: refresh_dependent_controls())
        pause_on_low_battery_var.trace_add("write", lambda *_: refresh_dependent_controls())
        refresh_dependent_controls()

        controls = ttk.Frame(frame, style="Card.TFrame", name="preferences_controls_frame")
        controls.grid(row=10, column=0, columnspan=2, sticky="e", pady=(14, 0))

        def save_and_close() -> None:
            self._save_settings_values(
                max_awake_session_hours=max_awake_hours_var.get(),
                allow_indefinite_awake=allow_indefinite_awake_var.get(),
                disable_on_battery=disable_on_battery_var.get(),
                pause_on_low_battery=pause_on_low_battery_var.get(),
                low_battery_threshold_percent=low_battery_threshold_var.get(),
                auto_start_with_windows=auto_start_var.get(),
                minimize_to_tray=minimize_to_tray_var.get(),
                silent=False,
            )
            dialog.destroy()

        ttk.Button(
            controls,
            name="preferences_cancel_button",
            text="Cancel",
            style="Secondary.TButton",
            command=dialog.destroy,
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            controls,
            name="preferences_save_button",
            text="Save",
            style="Primary.TButton",
            command=save_and_close,
        ).grid(row=0, column=1)

        dialog.grab_set()
        dialog.focus_force()

    def _export_diagnostics(self) -> None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suggested_name = f"testcaffeine_diagnostics_{timestamp}.zip"
        destination = filedialog.asksaveasfilename(
            title="Export diagnostics",
            defaultextension=".zip",
            initialfile=suggested_name,
            filetypes=[("Zip archive", "*.zip")],
        )
        if not destination:
            return

        app_data_dir = get_app_data_dir()
        settings_path = get_settings_path()
        log_path = app_data_dir / "test_caffeine.log"
        security_events_path = app_data_dir / "security_events.jsonl"

        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if settings_path.exists():
                archive.write(settings_path, arcname="settings.json")
            if log_path.exists():
                archive.write(log_path, arcname="test_caffeine.log")
            if security_events_path.exists():
                archive.write(security_events_path, arcname="security_events.jsonl")

            metadata = (
                f"Export time: {datetime.now().isoformat()}\n"
                f"Monitor state: {self.monitor.state.value}\n"
                f"Auto start enabled: {self.settings.auto_start_with_windows}\n"
            )
            archive.writestr("diagnostics_meta.txt", metadata)

        self._append_log(f"Diagnostics exported: {destination}")
        messagebox.showinfo("Diagnostics exported", f"Created:\n{destination}")

    def _on_state_change(self, state: MonitorState) -> None:
        self._events.put(("state", state.value))

    def _on_log(self, message: str) -> None:
        self._events.put(("log", message))

    def _drain_events(self) -> None:
        delay_ms = 300
        try:
            while True:
                kind, value = self._events.get_nowait()
                delay_ms = 120
                if kind == "state":
                    self._apply_state(MonitorState(value))
                elif kind == "log":
                    self._append_log(value)
        except queue.Empty:
            pass
        finally:
            self.root.after(delay_ms, self._drain_events)

    def _apply_state(self, state: MonitorState) -> None:
        self.status_var.set(state.value)

        if state == MonitorState.KEEPING_AWAKE:
            self.status_label.configure(foreground="#9a4d14")
            self.action_btn.configure(text="Stop monitoring")
        elif state == MonitorState.ACTIVE:
            self.status_label.configure(foreground="#2f7a38")
            self.action_btn.configure(text="Stop monitoring")
        elif state == MonitorState.POLICY_BLOCKED:
            self.status_label.configure(foreground="#9f2f1f")
            self.action_btn.configure(text="Stop monitoring")
        else:
            self.status_label.configure(foreground="#6f5140")
            self.action_btn.configure(text="Start monitoring")

        is_stopped = state == MonitorState.STOPPED
        if hasattr(self, "focus_btn"):
            self.focus_btn.configure(state="disabled" if is_stopped else "normal")
        if hasattr(self, "_file_menu"):
            self._file_menu.entryconfig("Start Monitoring", state="normal" if is_stopped else "disabled")
            self._file_menu.entryconfig("Stop Monitoring", state="disabled" if is_stopped else "normal")

        if is_stopped and self._focus_overlay_active:
            self._disable_focus_overlay()

        if self.tray_icon:
            self.tray_icon.set_running(state != MonitorState.STOPPED)

        if not is_stopped and state in (MonitorState.KEEPING_AWAKE, MonitorState.POLICY_BLOCKED):
            if not self._focus_overlay_active:
                self._enable_focus_overlay(auto=True)
        elif state == MonitorState.STOPPED and self._focus_overlay_active and self._focus_overlay_auto_engaged:
            self._disable_focus_overlay()

        if self._focus_overlay_active:
            if state in (MonitorState.KEEPING_AWAKE, MonitorState.POLICY_BLOCKED):
                if not self._focus_overlay_blackout_active and self._focus_overlay_blackout_job is None:
                    self._schedule_focus_blackout()
            elif self._focus_overlay_blackout_active or self._focus_overlay_blackout_job is not None:
                self._set_focus_overlay_transparent(schedule_blackout=False)

        self._update_countdown_display()

    def _format_duration(self, total_seconds: int) -> str:
        total_minutes = max(0, total_seconds) // 60
        hours = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours:02d}:{minutes:02d}"

    def _update_countdown_display(self) -> None:
        if not hasattr(self, "countdown_var"):
            return

        if self.monitor.state == MonitorState.STOPPED:
            self.countdown_var.set("--:--")
            return

        if self.settings.allow_indefinite_awake:
            self.countdown_var.set("Indefinite")
            return

        max_seconds = max(0.0, float(self.settings.max_awake_session_hours * 3600))
        now = time.monotonic()
        started_at = self._countdown_started_at
        end_at = self._countdown_end_at
        if started_at is None or end_at is None:
            started_at = now
            end_at = now + max_seconds
            self._countdown_started_at = started_at
            self._countdown_end_at = end_at

        remaining_seconds = int(max(0, math.ceil(end_at - now)))
        # Display whole minutes remaining (exclusive of the current running minute)
        # so a newly started 8-hour session immediately shows 07:59.
        display_seconds = max(0, remaining_seconds - 1)

        self.countdown_var.set(self._format_duration(display_seconds))

    def _refresh_countdown(self) -> None:
        self._update_countdown_display()
        if self.monitor.state == MonitorState.STOPPED or self.settings.allow_indefinite_awake:
            next_tick_ms = 2000
        else:
            now = time.monotonic()
            end_at = self._countdown_end_at
            if end_at is None:
                next_tick_ms = 2000
            else:
                remaining = max(0.0, end_at - now)
                if remaining <= 0:
                    next_tick_ms = 2000
                else:
                    # HH:MM display changes only when remaining time crosses a minute boundary.
                    current_minutes = int(math.ceil(remaining / 60.0))
                    next_boundary_seconds = max(0.0, (current_minutes - 1) * 60.0)
                    seconds_until_change = max(0.05, remaining - next_boundary_seconds)
                    next_tick_ms = max(100, min(60000, int(seconds_until_change * 1000)))
        self.root.after(next_tick_ms, self._refresh_countdown)

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{timestamp}] - {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _toggle_monitoring(self) -> None:
        if self.monitor.state == MonitorState.STOPPED:
            self._start_monitoring()
        else:
            self._stop_monitoring()

    def _start_monitoring(self) -> None:
        if not self._ensure_pin_setup():
            self._append_log("Monitoring start canceled: focus PIN setup is required")
            return
        self.security_events.emit(
            "focus_lock_platform_limitations",
            severity="warning",
            message=(
                "System-level shortcuts like Ctrl+Alt+Del and Alt+Tab cannot be fully blocked "
                "without kiosk mode or managed device policy."
            ),
        )
        if self.monitor.state == MonitorState.STOPPED:
            self._countdown_started_at = time.monotonic()
            if self.settings.allow_indefinite_awake:
                self._countdown_end_at = None
                self.countdown_var.set("Indefinite")
            else:
                total_seconds = int(self.settings.max_awake_session_hours * 3600)
                self._countdown_end_at = self._countdown_started_at + float(total_seconds)
                # Ensure the first visible value reflects an actively running minute.
                self.countdown_var.set(self._format_duration(max(0, total_seconds - 1)))
        self.monitor.start()
        self._update_countdown_display()

    def _stop_monitoring(self) -> None:
        self.monitor.stop()
        self._countdown_started_at = None
        self._countdown_end_at = None

    def _save_settings_values(
        self,
        max_awake_session_hours: int,
        allow_indefinite_awake: bool,
        disable_on_battery: bool,
        pause_on_low_battery: bool,
        low_battery_threshold_percent: int,
        auto_start_with_windows: bool,
        minimize_to_tray: bool,
        silent: bool,
    ) -> None:
        timeout = MANDATORY_LOCK_IDLE_TIMEOUT_SECONDS
        max_awake_hours = max(
            MIN_MAX_AWAKE_SESSION_HOURS,
            min(int(max_awake_session_hours), MAX_MAX_AWAKE_SESSION_HOURS),
        )
        low_battery_threshold = max(
            MIN_LOW_BATTERY_THRESHOLD_PERCENT,
            min(int(low_battery_threshold_percent), MAX_LOW_BATTERY_THRESHOLD_PERCENT),
        )
        blackout_delay = DEFAULT_FOCUS_OVERLAY_BLACKOUT_DELAY_SECONDS

        self.settings.idle_timeout_seconds = timeout
        self.settings.max_awake_session_hours = max_awake_hours
        self.settings.allow_indefinite_awake = bool(allow_indefinite_awake)
        self.settings.disable_on_battery = bool(disable_on_battery)
        self.settings.pause_on_low_battery = bool(pause_on_low_battery)
        self.settings.low_battery_threshold_percent = low_battery_threshold
        self.settings.auto_start_with_windows = bool(auto_start_with_windows)
        self.settings.minimize_to_tray = bool(minimize_to_tray)
        self.settings.auto_focus_overlay_on_idle = True
        self.settings.focus_overlay_blackout_delay_seconds = blackout_delay
        if (
            self._focus_overlay_active
            and not self._focus_overlay_blackout_active
            and self.monitor.state in (MonitorState.KEEPING_AWAKE, MonitorState.POLICY_BLOCKED)
        ):
            self._schedule_focus_blackout()
        if self.monitor.state != MonitorState.STOPPED and self._countdown_started_at is not None:
            if self.settings.allow_indefinite_awake:
                self._countdown_end_at = None
            else:
                self._countdown_end_at = (
                    self._countdown_started_at + float(self.settings.max_awake_session_hours * 3600)
                )
        self._update_countdown_display()

        try:
            set_auto_start(self.settings.auto_start_with_windows)
        except OSError as exc:
            self._append_log(f"Auto-start update failed: {exc}")

        save_settings(self.settings)

        if not silent:
            awake_policy = "indefinite" if self.settings.allow_indefinite_awake else f"{max_awake_hours}h"
            self._append_log(
                "Settings saved: timeout={}s, max awake={}, low battery={}%, auto-start={}, tray={}, focus-blackout-delay={}s".format(
                    timeout,
                    awake_policy,
                    low_battery_threshold,
                    self.settings.auto_start_with_windows,
                    self.settings.minimize_to_tray,
                    self.settings.focus_overlay_blackout_delay_seconds,
                )
            )
            self.logger.info(
                "Settings updated: timeout=%s max_awake_hours=%s allow_indefinite_awake=%s disable_on_battery=%s pause_on_low_battery=%s low_battery_threshold=%s auto_start=%s minimize_to_tray=%s focus_overlay_blackout_delay_seconds=%s",
                timeout,
                max_awake_hours,
                self.settings.allow_indefinite_awake,
                self.settings.disable_on_battery,
                self.settings.pause_on_low_battery,
                self.settings.low_battery_threshold_percent,
                self.settings.auto_start_with_windows,
                self.settings.minimize_to_tray,
                self.settings.focus_overlay_blackout_delay_seconds,
            )
            self.security_events.emit(
                "settings_updated",
                idle_timeout_seconds=timeout,
                max_awake_session_hours=max_awake_hours,
                allow_indefinite_awake=self.settings.allow_indefinite_awake,
                disable_on_battery=self.settings.disable_on_battery,
                pause_on_low_battery=self.settings.pause_on_low_battery,
                low_battery_threshold_percent=self.settings.low_battery_threshold_percent,
                auto_start_with_windows=self.settings.auto_start_with_windows,
                minimize_to_tray=self.settings.minimize_to_tray,
                focus_overlay_blackout_delay_seconds=self.settings.focus_overlay_blackout_delay_seconds,
            )

    def _on_close(self) -> None:
        if not self._is_exiting:
            user_choice = messagebox.askyesnocancel(
                "Close TestCaffeine",
                "Do you want to close the app?\n\n"
                "Yes = Close\n"
                "No = Minimize to tray\n"
                "Cancel = Stay open",
            )
            if user_choice is None:
                return
            if user_choice is False:
                self._minimize_to_tray()
                return

        try:
            self.monitor.stop()
        finally:
            self._disable_focus_overlay()
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.destroy()

    def get_ui_test_ids(self) -> dict[str, str]:
        ids: dict[str, str] = {}
        ids.update(self.widget_ids)
        ids.update(self.menu_ids)
        return ids


def run_app() -> None:
    root = tk.Tk()
    app = TestCaffeineApp(root)
    app._append_log("Application ready")
    root.mainloop()
