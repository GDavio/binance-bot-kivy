import os
import time
import json
import threading
from datetime import datetime

# Configurações do Kivy
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button

# ===== CONFIGURAÇÕES GLOBAIS =====
DADOS_IA = "dados_ia.json"
ARQUIVO_ESTADO = "estado_bot.json"
TAXA_CORRETORA = 0.001
COOLDOWN = 120

historico_ia = []
estado_por_par = {}

def salvar_json_atomico(caminho, dados):
    tmp = f"{caminho}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(dados, f, indent=2)
        os.replace(tmp, caminho)
    except Exception as e:
        print(f"Erro ao salvar {caminho}: {e}")

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

class TradingBotUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [15, 25, 15, 15]
        self.spacing = 10
        self.bot_rodando = False

        carregar_dados_ia()

        self.add_widget(Label(
            text="Bot Trading Binance",
            font_size='20sp',
            bold=True,
            size_hint_y=None,
            height=40
        ))

        scroll = ScrollView(size_hint=(1, 1))
        form = GridLayout(cols=1, spacing=10, size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))

        form.add_widget(Label(text="Binance API Key", size_hint_y=None, height=20))
        self.api_key = TextInput(hint_text="API Key", multiline=False, size_hint_y=None, height=48, padding=[10, 12, 10, 12])
        form.add_widget(self.api_key)

        form.add_widget(Label(text="Binance Secret Key", size_hint_y=None, height=20))
        self.api_secret = TextInput(hint_text="Secret Key", password=True, multiline=False, size_hint_y=None, height=48, padding=[10, 12, 10, 12])
        form.add_widget(self.api_secret)

        form.add_widget(Label(text="Pares (separados por espaco)", size_hint_y=None, height=20))
        self.symbols = TextInput(text="BTC/USDT ETH/USDT", multiline=False, size_hint_y=None, height=48, padding=[10, 12, 10, 12])
        form.add_widget(self.symbols)

        form.add_widget(Label(text="Valor por Ordem (USDT)", size_hint_y=None, height=20))
        self.valor_usdt = TextInput(text="10", multiline=False, size_hint_y=None, height=48, padding=[10, 12, 10, 12])
        form.add_widget(self.valor_usdt)

        self.btn_toggle = Button(
            text="LIGAR ROBO",
            bold=True,
            background_color=(0, 0.6, 0.2, 1),
            size_hint_y=None,
            height=52
        )
        self.btn_toggle.bind(on_press=self.toggle_bot)
        form.add_widget(self.btn_toggle)

        self.status = Label(
            text="Aguardando inicio...",
            font_size='13sp',
            color=(0.7, 0.7, 0.7, 1),
            size_hint_y=None,
            height=60
        )
        form.add_widget(self.status)

        scroll.add_widget(form)
        self.add_widget(scroll)

    def atualizar_status(self, texto):
        Clock.schedule_once(lambda dt: setattr(self.status, 'text', texto))

    def toggle_bot(self, instance):
        if not self.bot_rodando:
            self.bot_rodando = True
            self.btn_toggle.text = "DESLIGAR ROBO"
            self.btn_toggle.background_color = (0.8, 0.1, 0.1, 1)
            threading.Thread(target=self.loop_principal_bot, daemon=True).start()
        else:
            self.bot_rodando = False
            self.btn_toggle.text = "LIGAR ROBO"
            self.btn_toggle.background_color = (0, 0.6, 0.2, 1)
            self.atualizar_status("Robo desligado.")

    def loop_principal_bot(self):
        # Importacao tardia para evitar crash na inicializacao do aplicativo
        try:
            import ccxt
        except Exception as e:
            self.atualizar_status(f"Erro ao carregar ccxt: {e}")
            self.bot_rodando = False
            return

        key = self.api_key.text.strip()
        secret = self.api_secret.text.strip()
        lista_pares = [s.strip().upper() for s in self.symbols.text.split()]
        
        try:
            valor_ordem = float(self.valor_usdt.text.strip())
        except ValueError:
            valor_ordem = 10.0

        try:
            exchange = ccxt.binance({
                'apiKey': key,
                'secret': secret,
                'enableRateLimit': True,
                'timeout': 30000,
            })
            exchange.load_markets()
        except Exception as e:
            self.atualizar_status(f"Erro conexao Binance: {e}")
            self.bot_rodando = False
            return

        for s in lista_pares:
            if s not in estado_por_par:
                estado_por_par[s] = {
                    "preco_entrada": 0, "topo_preco": 0, "ultimo_trade_time": 0,
                    "tipo_operacao": "", "tipo_entrada": "", "entrada_rsi": 0,
                    "entrada_ma9": 0, "entrada_ma21": 0, "tempo_entrada": 0,
                    "wins": 0, "losses": 0,
                    "estrategias": {
                        "pullback": {"wins": 0, "losses": 0},
                        "continuidade": {"wins": 0, "losses": 0},
                        "rompimento": {"wins": 0, "losses": 0}
                    }
                }

        while self.bot_rodando:
            self.atualizar_status("Buscando dados da Binance...")
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

                self.atualizar_status(f"{SYMBOL} | R$: {preco:.2f} | RSI: {rsi:.1f}\nUSDT: {usdt:.2f} | {ativo}: {qtd:.4f}")

                # LÓGICA DE COMPRA
                if not em_operacao and usdt >= valor_ordem:
                    tempo_ok = (time.time() - estado["ultimo_trade_time"]) > COOLDOWN
                    distancia_ma = (ma9 - ma21) / ma21 * 100
                    tendencia = ma9 > ma21

                    pullback = (tendencia and preco <= ma9 * 1.003 and preco > ma9 and rsi < 55)
                    continuidade = (tendencia and preco > ma9 and 52 < rsi < 65)

                    tipo_entrada = "pullback" if pullback else "continuidade" if continuidade else "nenhum"
                    prob = prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada)

                    if (pullback or continuidade) and volatilidade > 0.15 and tempo_ok and prob > 0.50:
                        try:
                            qtd_raw = valor_ordem / preco
                            quantidade = float(exchange.amount_to_precision(SYMBOL, qtd_raw))
                            order = exchange.create_market_buy_order(SYMBOL, quantidade)
                            preco_executado = order.get('average') or order.get('price') or preco

                            estado["preco_entrada"] = preco_executado
                            estado["topo_preco"] = preco_executado
                            estado["tempo_entrada"] = time.time()
                            estado["ultimo_trade_time"] = time.time()
                            estado["tipo_entrada"] = tipo_entrada
                            estado["entrada_rsi"] = rsi
                            estado["entrada_ma9"] = ma9
                            estado["entrada_ma21"] = ma21
                            estado["entrada_distancia_ma"] = distancia_ma
                            estado["entrada_volatilidade"] = volatilidade
                        except Exception as e:
                            print(f"Erro ao comprar {SYMBOL}: {e}")

                # LÓGICA DE VENDA / TRAILING STOP
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
                            qtd_venda = float(exchange.amount_to_precision(SYMBOL, qtd))
                            order = exchange.create_market_sell_order(SYMBOL, qtd_venda)
                            preco_saida = order.get('average') or order.get('price') or preco
                            lucro_real_liquido = (((preco_saida - estado["preco_entrada"]) / estado["preco_entrada"]) * 100) - (TAXA_CORRETORA * 200)

                            historico_ia.append({
                                "tipo_entrada": estado.get("tipo_entrada", "desconhecido"),
                                "rsi": estado["entrada_rsi"],
                                "volatilidade": estado["entrada_volatilidade"],
                                "distancia_ma": estado["entrada_distancia_ma"],
                                "resultado": lucro_real_liquido
                            })
                            estado["preco_entrada"] = 0
                        except Exception as e:
                            print(f"Erro ao vender {SYMBOL}: {e}")

                time.sleep(2)

        self.atualizar_status("Robo finalizado.")

class BinanceBotApp(App):
    def build(self):
        return TradingBotUI()

if __name__ == '__main__':
    BinanceBotApp().run()
                    
