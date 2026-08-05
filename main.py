import os
import time
import requests
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.scrollview import ScrollView

class BotDashboard(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', padding=10, spacing=10, **kwargs)
        
        self.add_widget(Label(text="🤖 Bot Trading Binance", font_size='22sp', size_hint_y=None, height=40))
        
        self.api_key_input = TextInput(hint_text="Binance API Key", password=True, multiline=False, size_hint_y=None, height=40)
        self.secret_input = TextInput(hint_text="Binance Secret", password=True, multiline=False, size_hint_y=None, height=40)
        self.symbols_input = TextInput(text="BTCUSDT, ETHUSDT", hint_text="Pares (ex: BTCUSDT)", multiline=False, size_hint_y=None, height=40)
        self.valor_input = TextInput(text="10", hint_text="Valor por Entrada (USDT)", input_filter='float', multiline=False, size_hint_y=None, height=40)
        
        self.add_widget(self.api_key_input)
        self.add_widget(self.secret_input)
        self.add_widget(self.symbols_input)
        self.add_widget(self.valor_input)
        
        self.btn_toggle = Button(text="🚀 LIGAR ROBÔ", background_color=(0, 1, 0, 1), size_hint_y=None, height=50)
        self.btn_toggle.bind(on_press=self.toggle_bot)
        self.add_widget(self.btn_toggle)
        
        self.log_label = Label(text="Aguardando início...", size_hint_y=None, markup=True)
        self.log_label.bind(texture_size=lambda instance, value: setattr(instance, 'height', value[1]))
        
        scroll = ScrollView(size_hint=(1, 1))
        scroll.add_widget(self.log_label)
        self.add_widget(scroll)
        
        self.bot_ativo = False

    def log(self, mensagem):
        hora = time.strftime('%H:%M:%S')
        self.log_label.text += f"\n[{hora}] {mensagem}"

    def checar_preco_binance(self, symbol="BTCUSDT"):
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            res = requests.get(url, timeout=5).json()
            return res.get('price', 'Erro')
        except Exception as e:
            return str(e)

    def toggle_bot(self, instance):
        self.bot_ativo = not self.bot_ativo
        if self.bot_ativo:
            self.btn_toggle.text = "🛑 DESLIGAR ROBÔ"
            self.btn_toggle.background_color = (1, 0, 0, 1)
            preco = self.checar_preco_binance("BTCUSDT")
            self.log(f"🟢 Robô Iniciado! BTC Atual: ${preco}")
        else:
            self.btn_toggle.text = "🚀 LIGAR ROBÔ"
            self.btn_toggle.background_color = (0, 1, 0, 1)
            self.log("🔴 Robô Pausado!")

class BinanceApp(App):
    def build(self):
        return BotDashboard()

if __name__ == '__main__':
    BinanceApp().run()
        
