import os
import time
import json
import ssl
import hmac
import hashlib
import urllib.parse
import urllib.request
import threading
from datetime import datetime

from kivy.app import App
from kivy.clock import Clock
from kivy.metrics import dp, sp
from kivy.graphics import Color, RoundedRectangle
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button

# ===== CLIENTE REST BINANCE NATIVO COM BYPASS SSL PARA ANDROID =====
class BinanceNativeAPI:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        
        # Contexto SSL sem verificação estrita para contornar a falta de certificados no Android
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def _assinar_query(self, params):
        query_string = urllib.parse.urlencode(params)
        assinatura = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{query_string}&signature={assinatura}"

    def _requisicao(self, metodo, endpoint, params=None, assinado=False):
        if params is None:
            params = {}

        headers = {"X-MBX-APIKEY": self.api_key} if self.api_key else {}

        if assinado:
            params["timestamp"] = int(time.time() * 1000)
            query = self._assinar_query(params)
            url = f"{self.base_url}{endpoint}?{query}"
        else:
            query = urllib.parse.urlencode(params)
            url = f"{self.base_url}{endpoint}?{query}" if query else f"{self.base_url}{endpoint}"

        req = urllib.request.Request(url, headers=headers, method=metodo)
        with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as response:
            return json.loads(response.read().decode('utf-8'))

    def fetch_ohlcv(self, symbol, interval="1m", limit=50):
        symbol_fmt = symbol.replace("/", "").upper()
        res = self._requisicao("GET", "/api/v3/klines", {"symbol": symbol_fmt, "interval": interval, "limit": limit})
        return [[c[0], float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in res]

    def fetch_balance(self):
        res = self._requisicao("GET", "/api/v3/account", assinado=True)
        saldos = {"free": {}}
        for b in res.get("balances", []):
            saldos["free"][b["asset"]] = float(b["free"])
        return saldos

    def create_market_buy_order(self, symbol, quote_quantity):
        symbol_fmt = symbol.replace("/", "").upper()
        params = {
            "symbol": symbol_fmt,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": f"{quote_quantity:.2f}"
        }
        res = self._requisicao("POST", "/api/v3/order", params=params, assinado=True)
        preco = float(res.get("cummulativeQuoteQty", 0)) / float(res.get("executedQty", 1)) if float(res.get("executedQty", 0)) > 0 else 0
        return {"price": preco, "average": preco}

    def create_market_sell_order(self, symbol, quantity):
        symbol_fmt = symbol.replace("/", "").upper()
        params = {
            "symbol": symbol_fmt,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.6f}"
        }
        res = self._requisicao("POST", "/api/v3/order", params=params, assinado=True)
        preco = float(res.get("cummulativeQuoteQty", 0)) / float(res.get("executedQty", 1)) if float(res.get("executedQty", 0)) > 0 else 0
        return {"price": preco, "average": preco}

# ===== CONFIGURAÇÕES GLOBAIS =====
DADOS_IA = "dados_ia.json"
ARQUIVO_ESTADO = "estado_bot.json"
TAXA_CORRETORA = 0.001
COOLDOWN = 120

historico_ia = []
estado_por_par = {}

def carregar_dados_ia():
    global historico_ia
    if os.path.exists(DADOS_IA):
        try:
            with open(DADOS_IA, "r") as f:
                historico_ia = json.load(f)
        except Exception:
            historico_ia = []

def prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada):
    if len(historico_ia) < 20:
        return 0.50
    peso_total = 0
    score = 0
    for d in historico_ia:
        if "distancia_ma" not in d:
            continue
        peso = 1.0
        peso *= max(0, 1 - abs(d["rsi"] - rsi) / 15)
        peso *= max(0, 1 - abs(d["volatilidade"] - volatilidade) / 0.5)
        peso *= max(0, 1 - abs(d["distancia_ma"] - ((ma9 - ma21) / ma21 * 100)) / 0.3)
        if d.get("tipo_entrada") != tipo_entrada:
            peso *= 0.3
        if peso < 0.1:
            continue
        peso_total += peso
        score += peso * (1.0 if d["resultado"] > 0 else 0.0)
    return max(0, min(1, score / peso_total)) if peso_total >= 1 else 0.5

def calcular_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    delta = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in delta]
    losses = [-d if d < 0 else 0 for d in delta]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def media(lista, periodo):
    return sum(lista[-periodo:]) / periodo

# ===== COMPONENTES DE INTERFACE RESPONSIVOS =====
class CustomLabel(Label):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.font_size = sp(13)
        self.color = (0.85, 0.85, 0.85, 1)
        self.size_hint_y = None
        self.height = dp(24)
        self.halign = 'left'
        self.valign = 'middle'
        self.bind(size=self._update_text_size)

    def _update_text_size(self, instance, value):
        self.text_size = (value[0], value[1])

class CustomInput(TextInput):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.size_hint_y = None
        self.height = dp(46)
        self.font_size = sp(14)
        self.padding = [dp(12), dp(12), dp(12), dp(12)]
        self.background_color = (0.15, 0.16, 0.18, 1)
        self.foreground_color = (1, 1, 1, 1)
        self.cursor_color = (0, 0.7, 1, 1)

class TradingBotUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [dp(16), dp(20), dp(16), dp(16)]
        self.spacing = dp(12)
        self.bot_rodando = False

        carregar_dados_ia()

        # Cabeçalho
        self.add_widget(Label(
            text="Bot Trading Binance",
            font_size=sp(20),
            bold=True,
            color=(1, 1, 1, 1),
            size_hint_y=None,
            height=dp(36)
        ))

        # Formulário em ScrollView para responsividade mobile
        scroll_form = ScrollView(size_hint=(1, None), height=dp(280))
        form = GridLayout(cols=1, spacing=dp(6), size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))

        form.add_widget(CustomLabel(text="Binance API Key"))
        self.api_key = CustomInput(hint_text="Insira sua API Key", multiline=False)
        form.add_widget(self.api_key)

        form.add_widget(CustomLabel(text="Binance Secret Key"))
        self.api_secret = CustomInput(hint_text="Insira sua Secret Key", password=True, multiline=False)
        form.add_widget(self.api_secret)

        form.add_widget(CustomLabel(text="Pares de Moedas"))
        self.symbols = CustomInput(text="BTC/USDT ETH/USDT", multiline=False)
        form.add_widget(self.symbols)

        form.add_widget(CustomLabel(text="Valor por Ordem (USDT)"))
        self.valor_usdt = CustomInput(text="10", multiline=False)
        form.add_widget(self.valor_usdt)

        scroll_form.add_widget(form)
        self.add_widget(scroll_form)

        # Botão Ligar/Desligar
        self.btn_toggle = Button(
            text="LIGAR ROBÔ",
            font_size=sp(16),
            bold=True,
            background_color=(0, 0.7, 0.3, 1),
            size_hint_y=None,
            height=dp(50)
        )
        self.btn_toggle.bind(on_press=self.toggle_bot)
        self.add_widget(self.btn_toggle)

        # Card de Status
        status_card = BoxLayout(orientation='vertical', padding=dp(10))
        with status_card.canvas.before:
            Color(0.1, 0.11, 0.13, 1)
            self.rect = RoundedRectangle(pos=status_card.pos, size=status_card.size, radius=[dp(8)])
        status_card.bind(pos=self._update_rect, size=self._update_rect)

        scroll_status = ScrollView(size_hint=(1, 1))
        self.status = Label(
            text="Aguardando início...",
            font_size=sp(13),
            color=(0.8, 0.8, 0.8, 1),
            size_hint_y=None,
            halign='left',
            valign='top'
        )
        self.status.bind(width=lambda img, val: setattr(self.status, 'text_size', (val, None)))
        self.status.bind(texture_size=lambda img, val: setattr(self.status, 'height', val[1]))

        scroll_status.add_widget(self.status)
        status_card.add_widget(scroll_status)
        self.add_widget(status_card)

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

    def atualizar_status(self, texto):
        Clock.schedule_once(lambda dt: setattr(self.status, 'text', texto))

    def toggle_bot(self, instance):
        if not self.bot_rodando:
            self.bot_rodando = True
            self.btn_toggle.text = "DESLIGAR ROBÔ"
            self.btn_toggle.background_color = (0.85, 0.2, 0.2, 1)
            threading.Thread(target=self.loop_principal_bot, daemon=True).start()
        else:
            self.bot_rodando = False
            self.btn_toggle.text = "LIGAR ROBÔ"
            self.btn_toggle.background_color = (0, 0.7, 0.3, 1)
            self.atualizar_status("Robô desligado.")

    def loop_principal_bot(self):
        key = self.api_key.text.strip()
        secret = self.api_secret.text.strip()
        lista_pares = [s.strip().upper() for s in self.symbols.text.split()]
        
        try:
            valor_ordem = float(self.valor_usdt.text.strip())
        except ValueError:
            valor_ordem = 10.0

        exchange = BinanceNativeAPI(key, secret)

        for s in lista_pares:
            if s not in estado_por_par:
                estado_por_par[s] = {
                    "preco_entrada": 0, "topo_preco": 0, "ultimo_trade_time": 0,
                    "tipo_entrada": ""
                }

        while self.bot_rodando:
            self.atualizar_status("Buscando dados na Binance...")
            try:
                saldo_geral = exchange.fetch_balance()
            except Exception as e:
                self.atualizar_status(f"Erro ao carregar saldo: {e}")
                time.sleep(5)
                continue

            for SYMBOL in lista_pares:
                if not self.bot_rodando:
                    break
                
                estado = estado_por_par[SYMBOL]
                try:
                    candles = exchange.fetch_ohlcv(SYMBOL, "1m", limit=50)
                    closes = [c[4] for c in candles]
                    preco = closes[-1]
                    rsi = calcular_rsi(closes)
                    ma9 = media(closes, 9)
                    ma21 = media(closes, 21)
                    
                    ativo = SYMBOL.split("/")[0]
                    qtd = saldo_geral['free'].get(ativo, 0.0)
                    usdt = saldo_geral['free'].get('USDT', 0.0)
                except Exception as e:
                    print(f"Erro leitura ({SYMBOL}): {e}")
                    continue

                em_operacao = qtd > 0.0001
                volatilidade = (max(closes[-10:]) - min(closes[-10:])) / preco * 100

                self.atualizar_status(
                    f"=== PAINEL DE MONITORAMENTO ===\n\n"
                    f"Par: {SYMBOL}\n"
                    f"Preço Atual: USDT {preco:.2f}\n"
                    f"RSI (14): {rsi:.1f}\n\n"
                    f"--- Carteira ---\n"
                    f"Saldo USDT: {usdt:.2f}\n"
                    f"Saldo {ativo}: {qtd:.4f}\n\n"
                    f"Status: {'EM OPERAÇÃO' if em_operacao else 'AGUARDANDO SINAL'}"
                )

                # LÓGICA DE COMPRA
                if not em_operacao and usdt >= valor_ordem:
                    tempo_ok = (time.time() - estado["ultimo_trade_time"]) > COOLDOWN
                    tendencia = ma9 > ma21

                    pullback = (tendencia and preco <= ma9 * 1.003 and preco > ma9 and rsi < 55)
                    continuidade = (tendencia and preco > ma9 and 52 < rsi < 65)

                    tipo_entrada = "pullback" if pullback else "continuidade" if continuidade else "nenhum"
                    prob = prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada)

                    if (pullback or continuidade) and volatilidade > 0.15 and tempo_ok and prob > 0.50:
                        try:
                            order = exchange.create_market_buy_order(SYMBOL, valor_ordem)
                            preco_executado = order.get('price') or preco

                            estado["preco_entrada"] = preco_executado
                            estado["topo_preco"] = preco_executado
                            estado["ultimo_trade_time"] = time.time()
                            estado["tipo_entrada"] = tipo_entrada
                        except Exception as e:
                            print(f"Erro ao comprar {SYMBOL}: {e}")

                # LÓGICA DE VENDA / STOP
                if em_operacao and estado["preco_entrada"] > 0:
                    lucro_bruto = ((preco - estado["preco_entrada"]) / estado["preco_entrada"]) * 100
                    lucro_liquido = lucro_bruto - (TAXA_CORRETORA * 2 * 100)

                    if preco > estado["topo_preco"]:
                        estado["topo_preco"] = preco

                    vender = False
                    stop_emergencia = estado["preco_entrada"] * 0.995

                    if preco <= stop_emergencia or (rsi < 38 and lucro_liquido < -0.25):
                        vender = True

                    if vender:
                        try:
                            order = exchange.create_market_sell_order(SYMBOL, qtd)
                            estado["preco_entrada"] = 0
                        except Exception as e:
                            print(f"Erro ao vender {SYMBOL}: {e}")

                time.sleep(2)

        self.atualizar_status("Robô desligado.")

class BinanceBotApp(App):
    def build(self):
        return TradingBotUI()

if __name__ == '__main__':
    BinanceBotApp().run()
    
