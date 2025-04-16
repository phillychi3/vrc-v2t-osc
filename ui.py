from textual import on
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Input, Header, Footer, Label, RichLog


from textual.binding import Binding


class VoiceToTextApp(App):
    """語音轉文字應用程式介面"""

    CSS = """
    #main-container {
        width: 100%;
        height: 100%;
    }

    #left-panel {
        width: 50%;
        height: 100%;
        border-right: solid gray;
    }

    #right-panel {
        width: 50%;
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

    TextLog {
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
        height: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "退出程式"),
        Binding("ctrl+c", "quit", "退出程式"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Horizontal(
                Vertical(
                    Label("文字輸入區"),
                    Container(
                        Input(placeholder="請輸入文字，按 Enter 發送..."),
                        id="input-container",
                    ),
                    id="left-panel",
                ),
                Vertical(
                    Label("語音辨識區"),
                    RichLog(id="speech-log", highlight=True, markup=True),
                    id="right-panel",
                ),
                id="main-container",
            )
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        speech_log = self.query_one("#speech-log")
        speech_log.write("[bold green]系統[/bold green]: 語音辨識系統已啟動")
        speech_log.write("[bold green]系統[/bold green]: 等待語音輸入...")

    @on(Input.Submitted)
    def handle_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            speech_log = self.query_one("#speech-log")
            speech_log.write(f"[bold blue]您[/bold blue]: {event.value}")
            input_widget = self.query_one(Input)
            input_widget.value = ""

            if hasattr(self, "on_input_submitted") and callable(
                self.on_input_submitted
            ):
                self.on_input_submitted(event.value)

    def add_speech_text(self, text: str) -> None:
        speech_log = self.query_one("#speech-log")
        speech_log.write(f"[bold yellow]語音[/bold yellow]: {text}")


def main():
    app = VoiceToTextApp()
    app.run()


if __name__ == "__main__":
    main()
