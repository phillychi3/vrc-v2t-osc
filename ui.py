from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Input, Header, Footer, Label, RichLog, Switch, Static
from textual.binding import Binding


class VoiceToTextApp(App):
    """語音轉文字應用程式介面"""

    CSS = """
    #main-container {
        width: 100%;
        height: 100%;
    }

    #settings-panel {
        width: 30%;
        height: 100%;
        border-right: solid gray;
        padding: 1;
    }

    #right-panel {
        width: 70%;
        height: 100%;
    }

    Label {
        background: $boost;
        color: $text;
        padding: 1 2;
        border-bottom: solid $primary;
        text-align: center;
        width: 100%;
    }

    RichLog {
        background: $surface;
        color: $text;
        height: 100%;
        border: solid $primary;
        padding: 1 2;
        margin: 0 1;
    }

    Input {
        width: 100%;
        margin: 0 1 1 1;
    }

    #input-container {
        height: auto;
        margin-top: 1;
        padding-bottom: 1;
    }

    .setting-label {
        width: 1fr;
        height: 3;
        content-align: left middle;
        padding: 1;
        margin-bottom: 1;
    }

    .settings-group {
        margin-bottom: 2;
    }


    Switch {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出程式"),
        Binding("ctrl+c", "quit", "退出程式"),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.osc_enabled = True
        self.translation_enabled = False
        self.emotion_enabled = False
        self.on_settings_changed = None
        self.on_input_submitted = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Horizontal(
                Vertical(
                    Horizontal(
                        Static("OSC 傳送", classes="setting-label"),
                        Switch(value=self.osc_enabled, id="osc-switch"),
                        classes="settings-group",
                    ),
                    Horizontal(
                        Static("翻譯 (尚未實現)", classes="setting-label"),
                        Switch(value=self.translation_enabled, id="translation-switch"),
                        classes="settings-group",
                    ),
                    Horizontal(
                        Static("情緒識別 (尚未實現)", classes="setting-label"),
                        Switch(value=self.emotion_enabled, id="emotion-switch"),
                        classes="settings-group",
                    ),
                    Container(
                        Input(
                            placeholder="請輸入文字，按 Enter 發送...",
                            id="text-input",
                        ),
                        id="input-container",
                    ),
                    id="settings-panel",
                ),
                Vertical(
                    Label("語音辨識"),
                    RichLog(id="speech-log", highlight=True, markup=True),
                    id="right-panel",
                ),
                id="main-container",
            )
        )
        yield Footer()

    def on_mount(self) -> None:
        """當應用啟動時執行"""
        self.query_one("#text-input").focus()
        speech_log = self.query_one("#speech-log")
        speech_log.write("[bold green]系統[/bold green]: 語音辨識已啟動")
        speech_log.write("[bold green]系統[/bold green]: 等待語音輸入...")
        speech_log.write(
            "[bold magenta]提示[/bold magenta]: 您可以在左側輸入框中輸入文字"
        )

    @on(Switch.Changed)
    def handle_switch_changed(self, event: Switch.Changed) -> None:
        """處理開關狀態變更"""
        switch_id = event.switch.id
        if switch_id == "osc-switch":
            self.osc_enabled = event.value
            self.notify_settings_changed("osc", event.value)
            self.query_one("#speech-log").write(
                f"[bold green]系統[/bold green]: OSC 傳送已{'啟用' if event.value else '停用'}"
            )
        elif switch_id == "translation-switch":
            self.translation_enabled = event.value
            self.notify_settings_changed("translation", event.value)
            self.query_one("#speech-log").write(
                f"[bold green]系統[/bold green]: 翻譯已{'啟用' if event.value else '停用'} (尚未實現)"
            )
        elif switch_id == "emotion-switch":
            self.emotion_enabled = event.value
            self.notify_settings_changed("emotion", event.value)
            self.query_one("#speech-log").write(
                f"[bold green]系統[/bold green]: 情緒識別已{'啟用' if event.value else '停用'} (尚未實現)"
            )

    def notify_settings_changed(self, setting_name, value):
        """通知設定變更"""
        if self.on_settings_changed:
            self.on_settings_changed(setting_name, value)

    @on(Input.Submitted)
    def handle_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            speech_log = self.query_one("#speech-log")
            speech_log.write(f"[bold blue]您[/bold blue]: {event.value}")
            input_widget = self.query_one(Input)
            input_widget.value = ""

            if self.on_input_submitted:
                self.on_input_submitted(event.value)

    def add_speech_text(self, text: str) -> None:
        speech_log = self.query_one("#speech-log")
        speech_log.write(f"[bold yellow]語音[/bold yellow]: {text}")
