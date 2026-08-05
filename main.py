import os
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.core.window import Window

class TradingBotUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [15, 25, 15, 15]
        self.spacing = 10

        # Título do App
        title = Label(
            text="Bot Trading Binance",
            font_size='20sp',
            bold=True,
            size_hint_y=None,
            height=40
        )
        self.add_widget(title)

        # ScrollView para evitar que os campos fiquem esmagados
        scroll = ScrollView(size_hint=(1, 1))
        
        form_layout = GridLayout(cols=1, spacing=12, size_hint_y=None)
        form_layout.bind(minimum_height=form_layout.setter('height'))

        # API Key
        form_layout.add_widget(Label(text="Binance API Key", size_hint_y=None, height=20, halign='left'))
        self.api_key = TextInput(
            hint_text="Insira sua API Key",
            multiline=False,
            size_hint_y=None,
            height=48,
            padding=[10, 12, 10, 12]
        )
        form_layout.add_widget(self.api_key)

        # API Secret
        form_layout.add_widget(Label(text="Binance Secret Key", size_hint_y=None, height=20, halign='left'))
        self.api_secret = TextInput(
            hint_text="Insira sua Secret Key",
            password=True,
            multiline=False,
            size_hint_y=None,
            height=48,
            padding=[10, 12, 10, 12]
        )
        form_layout.add_widget(self.api_secret)

        # Par de Moedas
        form_layout.add_widget(Label(text="Par de Moedas", size_hint_y=None, height=20, halign='left'))
        self.symbol = TextInput(
            text="BTCUSDT ETHUSDT",
            hint_text="Ex: BTCUSDT ETHUSDT",
            multiline=False,
            size_hint_y=None,
            height=48,
            padding=[10, 12, 10, 12]
        )
        form_layout.add_widget(self.symbol)

        # Valor / US
        form_layout.add_widget(Label(text="Valor / Quantidade", size_hint_y=None, height=20, halign='left'))
        self.amount = TextInput(
            text="10",
            hint_text="Ex: 10",
            multiline=False,
            size_hint_y=None,
            height=48,
            padding=[10, 12, 10, 12]
        )
        form_layout.add_widget(self.amount)

        # Botão de Ação (Verde, sem caracteres Unicode que quebram)
        self.btn_toggle = Button(
            text="LIGAR ROBÔ",
            bold=True,
            background_color=(0, 0.6, 0.2, 1),
            size_hint_y=None,
            height=52
        )
        self.btn_toggle.bind(on_press=self.toggle_bot)
        form_layout.add_widget(self.btn_toggle)

        # Status Log
        self.status = Label(
            text="Aguardando início...",
            font_size='14sp',
            color=(0.7, 0.7, 0.7, 1),
            size_hint_y=None,
            height=30
        )
        form_layout.add_widget(self.status)

        scroll.add_widget(form_layout)
        self.add_widget(scroll)

    def toggle_bot(self, instance):
        if self.btn_toggle.text == "LIGAR ROBÔ":
            self.btn_toggle.text = "DESLIGAR ROBÔ"
            self.btn_toggle.background_color = (0.8, 0.1, 0.1, 1)
            self.status.text = "Robô em execução..."
        else:
            self.btn_toggle.text = "LIGAR ROBÔ"
            self.btn_toggle.background_color = (0, 0.6, 0.2, 1)
            self.status.text = "Robô parado."

class BinanceBotApp(App):
    def build(self):
        return TradingBotUI()

if __name__ == '__main__':
    BinanceBotApp().run()
    
