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
from kivy.utils import platform

# ===== ARQUIVOS DE PERSISTÊNCIA =====
ARQUIVO_CONFIG = "config_bot.json"
ARQUIVO_ESTADO = "estado_bot.json"
ARQUIVO_RELATORIO = "relatorio_bot.txt"
DADOS_IA = "dados_ia.json"

TAXA_CORRETORA = 0.001
TEMPO_MAXIMO_TRADE = 7200
COOLDOWN = 120
AMOSTRAGEM_MINIMA_IA = 30  # Quantidade de trades para a IA começar a filtrar

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

def carregar_config():
    if os.path.exists(ARQUIVO_CONFIG):
        try:
            with open(ARQUIVO_CONFIG, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def salvar_config(api_key, api_secret, symbols, valor_usdt):
    dados = {
        "api_key": api_key,
        "api_secret": api_secret,
        "symbols": symbols,
        "valor_usdt": valor_usdt
    }
    salvar_json_atomico(ARQUIVO_CONFIG, dados)

# ===== RECURSOS NATIVOS ANDROID (WAKELOCK E SERVIÇO) =====
def adquirir_wake_lock():
    """Impede que o Android suspenda a CPU quando a tela apaga."""
    if platform == 'android':
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            Context = autoclass('android.content.Context')
            PowerManager = autoclass('android.os.PowerManager')
            
            activity = PythonActivity.mActivity
            power_manager = activity.getSystemService(Context.POWER_SERVICE)
            
            wake_lock = power_manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BotTrading::WakeLock")
            wake_lock.acquire()
            print("WakeLock adquirido com sucesso.")
            return wake_lock
        except Exception as e:
            print(f"Erro ao adquirir WakeLock: {e}")
    return None

def liberar_wake_lock(wake_lock):
    if wake_lock and platform == 'android':
        try:
            if wake_lock.isHeld():
                wake_lock.release()
                print("WakeLock liberado.")
        except Exception as e:
            print(f"Erro ao liberar WakeLock: {e}")

# ===== CLIENTE REST BINANCE NATIVO =====
class BinanceNativeAPI:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api.binance.com"
        
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

# ===== UTILITÁRIOS E ESTRATÉGIAS =====
def carregar_dados_ia():
    global historico_ia
    if os.path.exists(DADOS_IA):
        try:
            with open(DADOS_IA, "r") as f:
                historico_ia = json.load(f)
        except Exception:
            historico_ia = []

def salvar_dados_ia():
    salvar_json_atomico(DADOS_IA, historico_ia)

def carregar_estado():
    global estado_por_par
    if os.path.exists(ARQUIVO_ESTADO):
        try:
            with open(ARQUIVO_ESTADO, "r") as f:
                estado_por_par = json.load(f)
        except Exception:
            estado_por_par = {}

def salvar_estado():
    salvar_json_atomico(ARQUIVO_ESTADO, estado_por_par)

def prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada):
    # FASE DE AQUECIMENTO: Se a amostra for menor que 30 trades, não bloqueia entradas
    if len(historico_ia) < AMOSTRAGEM_MINIMA_IA:
        return 1.0

    peso_total = 0
    score = 0
    for d in historico_ia:
        if "distancia_ma" not in d:
            continue
        peso = 1.0
        peso *= max(0, 1 - abs(d["rsi"] - rsi) / 15)
        peso *= max(0, 1 - abs(d["volatilidade"] - volatilidade) / 0.5)
        peso *= max(0, 1 - abs(d["distancia_ma"] - ((ma9 - ma21) / ma21 * 100)) / 0.3)
        if d.get("posicao_preco_ma9") != (preco > ma9):
            peso *= 0.5
        if d.get("posicao_preco_ma21") != (preco > ma21):
            peso *= 0.5
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

def estrategia_valida(estado, nome):
    dados = estado.get("estrategias", {}).get(nome, {"wins": 0, "losses": 0})
    total = dados["wins"] + dados["losses"]
    if total < 5:
        return True
    return (dados["wins"] / total) >= 0.40

def sync_posicao(symbol, preco_atual, quantidade, rsi, ma9, ma21):
    estado = estado_por_par[symbol]
    if quantidade > 0.0001 and estado.get("preco_entrada", 0) == 0:
        estado["preco_entrada"] = preco_atual
        estado["topo_preco"] = preco_atual
        estado["tipo_operacao"] = "COMPRA_EXTERNAL_SYNC"
        estado["tipo_entrada"] = "desconhecido"
        estado["ultimo_trade_time"] = time.time()
        estado["tempo_entrada"] = time.time()
        estado["entrada_rsi"] = rsi
        estado["entrada_ma9"] = ma9
        estado["entrada_ma21"] = ma21
        estado["entrada_distancia_ma"] = (ma9 - ma21) / ma21 * 100
        estado["entrada_volatilidade"] = 0.2
        estado["entrada_posicao_ma9"] = preco_atual > ma9
        estado["entrada_posicao_ma21"] = preco_atual > ma21
        salvar_estado()

def salvar_trade_relatorio(symbol, estado, lucro, preco_saida, rsi_saida, ma9_saida, ma21_saida, lucro_max):
    tempo_operacao = int(time.time() - estado.get("tempo_entrada", time.time()))
    try:
        with open(ARQUIVO_RELATORIO, "a") as f:
            f.write("==============================\n")
            f.write(f"Par: {symbol}\n")
            f.write(f"Data: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Tipo: {estado.get('tipo_operacao', 'N/A')} ({estado.get('tipo_entrada', 'N/A')})\n")
            f.write(f"Entrada: {estado.get('preco_entrada', 0):.2f} | Saída: {preco_saida:.2f}\n")
            f.write(f"Lucro Líquido: {lucro:.2f}% | Lucro Máx: {lucro_max:.2f}%\n")
            f.write(f"Tempo Operação: {tempo_operacao}s\n\n")
    except Exception as e:
        print(f"Erro relatório: {e}")

# ===== INTERFACE GRÁFICA =====
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
        self.height = dp(44)
        self.font_size = sp(14)
        self.padding = [dp(10), dp(10), dp(10), dp(10)]
        self.background_color = (0.15, 0.16, 0.18, 1)
        self.foreground_color = (1, 1, 1, 1)
        self.cursor_color = (0, 0.7, 1, 1)

class TradingBotUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = [dp(16), dp(16), dp(16), dp(16)]
        self.spacing = dp(10)
        self.bot_rodando = False
        self.wake_lock = None

        carregar_estado()
        carregar_dados_ia()
        config = carregar_config()

        self.add_widget(Label(
            text="Bot Trading Binance",
            font_size=sp(20),
            bold=True,
            color=(1, 1, 1, 1),
            size_hint_y=None,
            height=dp(30)
        ))

        scroll_form = ScrollView(size_hint=(1, None), height=dp(260))
        form = GridLayout(cols=1, spacing=dp(4), size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))

        form.add_widget(CustomLabel(text="Binance API Key"))
        self.api_key = CustomInput(text=config.get("api_key", ""), hint_text="Insira sua API Key", multiline=False)
        form.add_widget(self.api_key)

        form.add_widget(CustomLabel(text="Binance Secret Key"))
        self.api_secret = CustomInput(text=config.get("api_secret", ""), hint_text="Insira sua Secret Key", password=True, multiline=False)
        form.add_widget(self.api_secret)

        form.add_widget(CustomLabel(text="Pares de Moedas"))
        self.symbols = CustomInput(text=config.get("symbols", "ETH/USDT BTC/USDT"), multiline=False)
        form.add_widget(self.symbols)

        form.add_widget(CustomLabel(text="Valor por Ordem (USDT)"))
        self.valor_usdt = CustomInput(text=config.get("valor_usdt", "10"), multiline=False)
        form.add_widget(self.valor_usdt)

        scroll_form.add_widget(form)
        self.add_widget(scroll_form)

        self.btn_toggle = Button(
            text="LIGAR ROBÔ",
            font_size=sp(16),
            bold=True,
            background_color=(0, 0.7, 0.3, 1),
            size_hint_y=None,
            height=dp(48)
        )
        self.btn_toggle.bind(on_press=self.toggle_bot)
        self.add_widget(self.btn_toggle)

        status_card = BoxLayout(orientation='vertical', padding=dp(10))
        with status_card.canvas.before:
            Color(0.1, 0.11, 0.13, 1)
            self.rect = RoundedRectangle(pos=status_card.pos, size=status_card.size, radius=[dp(8)])
        status_card.bind(pos=self._update_rect, size=self._update_rect)

        scroll_status = ScrollView(size_hint=(1, 1))
        self.status = Label(
            text="Aguardando início...",
            font_size=sp(12),
            color=(0.85, 0.85, 0.85, 1),
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
            salvar_config(
                self.api_key.text.strip(),
                self.api_secret.text.strip(),
                self.symbols.text.strip(),
                self.valor_usdt.text.strip()
            )
            self.wake_lock = adquirir_wake_lock()
            self.bot_rodando = True
            self.btn_toggle.text = "DESLIGAR ROBÔ"
            self.btn_toggle.background_color = (0.85, 0.2, 0.2, 1)
            threading.Thread(target=self.loop_principal_bot, daemon=True).start()
        else:
            self.bot_rodando = False
            liberar_wake_lock(self.wake_lock)
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
            try:
                saldo_geral = exchange.fetch_balance()
                usdt_livre = saldo_geral['free'].get('USDT', 0.0)
            except Exception as e:
                self.atualizar_status(f"⚠️ Erro ao carregar saldo: {e}")
                time.sleep(5)
                continue

            amostras_atuais = len(historico_ia)
            status_ia = f"Coletando Amostras ({amostras_atuais}/{AMOSTRAGEM_MINIMA_IA})" if amostras_atuais < AMOSTRAGEM_MINIMA_IA else "IA Ativa (Filtrando)"

            logs_painel = []
            logs_painel.append(f"💰 Saldo USDT Livre: ${usdt_livre:.2f} | IA: {status_ia}\n" + "-"*35)

            for SYMBOL in lista_pares:
                if not self.bot_rodando:
                    break
                
                estado = estado_por_par[SYMBOL]
                try:
                    candles = exchange.fetch_ohlcv(SYMBOL, "1m", limit=50)
                    closes = [c[4] for c in candles]
                    preco = closes[-1]
                    rsi = calcular_rsi(closes)
                    rsi_anterior = calcular_rsi(closes[:-1])
                    ma9 = media(closes, 9)
                    ma21 = media(closes, 21)
                    
                    ativo = SYMBOL.split("/")[0]
                    qtd = saldo_geral['free'].get(ativo, 0.0)
                except Exception as e:
                    print(f"Erro leitura ({SYMBOL}): {e}")
                    continue

                sync_posicao(SYMBOL, preco, qtd, rsi, ma9, ma21)
                em_operacao = qtd > 0.0001
                volatilidade = (max(closes[-10:]) - min(closes[-10:])) / preco * 100

                # LÓGICA DE ENTRADA (MANTÉM CONFIRMAÇÃO DO RSI)
                if not em_operacao and usdt_livre >= valor_ordem:
                    tempo_ok = (time.time() - estado["ultimo_trade_time"]) > COOLDOWN
                    distancia_ma = (ma9 - ma21) / ma21 * 100
                    tendencia = ma9 > ma21
                    tendencia_forte = distancia_ma > 0.02

                    pullback = (tendencia and preco >= ma9 * 0.998 and preco >= ma21 and rsi < 58 and rsi > rsi_anterior and estrategia_valida(estado, "pullback"))
                    continuidade = (tendencia and preco > ma9 and 52 < rsi < 65 and rsi > rsi_anterior and closes[-1] > closes[-2] and (distancia_ma > 0.05 or (distancia_ma > 0.003 and closes[-1] > closes[-2])) and estrategia_valida(estado, "continuidade"))
                    rompimento = (tendencia_forte and preco >= max(closes[-3:]) and closes[-1] > closes[-2] and 55 < rsi < 72 and rsi > rsi_anterior and preco > ma9 and (preco - closes[-3]) / closes[-3] * 100 > 0.03 and estrategia_valida(estado, "rompimento"))

                    tipo_entrada = "pullback" if pullback else "continuidade" if continuidade else "rompimento" if rompimento else "nenhum"
                    prob = prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada)

                    # Regra de permissão da IA: passa direto se estiver na fase de amostragem
                    ia_permissao = True if len(historico_ia) < AMOSTRAGEM_MINIMA_IA else prob >= 0.52

                    if (pullback or continuidade or rompimento) and volatilidade > 0.15 and tempo_ok and ia_permissao:
                        try:
                            order = exchange.create_market_buy_order(SYMBOL, valor_ordem)
                            preco_executado = order.get('price') or preco

                            estado["preco_entrada"] = preco_executado
                            estado["topo_preco"] = preco_executado
                            estado["tempo_entrada"] = time.time()
                            estado["ultimo_trade_time"] = time.time()
                            estado["tipo_operacao"] = "COMPRA"
                            estado["tipo_entrada"] = tipo_entrada
                            estado["entrada_rsi"] = rsi
                            estado["entrada_ma9"] = ma9
                            estado["entrada_ma21"] = ma21
                            estado["entrada_distancia_ma"] = distancia_ma
                            estado["entrada_volatilidade"] = volatilidade
                            estado["entrada_posicao_ma9"] = preco > ma9
                            estado["entrada_posicao_ma21"] = preco > ma21
                            salvar_estado()
                        except Exception as e:
                            print(f"Erro ao comprar {SYMBOL}: {e}")

                # LÓGICA DE SAÍDA / TRAILING STOP
                lucro_liquido = 0.0
                if em_operacao and estado["preco_entrada"] > 0:
                    lucro_bruto = ((preco - estado["preco_entrada"]) / estado["preco_entrada"]) * 100
                    lucro_liquido = lucro_bruto - (TAXA_CORRETORA * 2 * 100)

                    if preco > estado["topo_preco"]:
                        estado["topo_preco"] = preco
                        salvar_estado()

                    lucro_max_bruto = ((estado["topo_preco"] - estado["preco_entrada"]) / estado["preco_entrada"]) * 100
                    lucro_max_liquido = lucro_max_bruto - (TAXA_CORRETORA * 2 * 100)

                    vender = False
                    stop_emergencia = estado["preco_entrada"] * 0.995
                    tempo_em_trade = time.time() - estado.get("tempo_entrada", time.time())

                    if preco <= stop_emergencia:
                        vender = True
                    elif rsi < 38 and rsi < rsi_anterior and preco < ma9 and preco < closes[-2] and lucro_liquido < -0.25:
                        vender = True
                    elif all(c < ma21 for c in closes[-3:]) and rsi < 43 and rsi < rsi_anterior and lucro_liquido < -0.35:
                        vender = True
                    elif tempo_em_trade > TEMPO_MAXIMO_TRADE and lucro_liquido < 0.10:
                        vender = True
                    elif lucro_max_liquido >= 0.35:
                        lucro_travado = 0.10 if lucro_max_liquido < 0.70 else 0.35 if lucro_max_liquido < 1.05 else 0.70 if lucro_max_liquido < 1.40 else 1.05 if lucro_max_liquido < 1.75 else (lucro_max_liquido - 0.35)
                        stop_dinamico = estado["preco_entrada"] * (1 + (lucro_travado + (TAXA_CORRETORA * 200)) / 100)
                        if preco <= stop_dinamico:
                            vender = True

                    if vender:
                        try:
                            order = exchange.create_market_sell_order(SYMBOL, qtd)
                            preco_saida = order.get('price') or preco

                            resultado_pct = ((preco_saida - estado["preco_entrada"]) / estado["preco_entrada"]) * 100 - (TAXA_CORRETORA * 2 * 100)
                            salvar_trade_relatorio(SYMBOL, estado, resultado_pct, preco_saida, rsi, ma9, ma21, lucro_max_liquido)

                            tipo_strat = estado.get("tipo_entrada", "")
                            if tipo_strat in estado.get("estrategias", {}):
                                if resultado_pct > 0:
                                    estado["estrategias"][tipo_strat]["wins"] += 1
                                    estado["wins"] += 1
                                else:
                                    estado["estrategias"][tipo_strat]["losses"] += 1
                                    estado["losses"] += 1

                            historico_ia.append({
                                "rsi": estado.get("entrada_rsi", 50),
                                "volatilidade": estado.get("entrada_volatilidade", 0.2),
                                "distancia_ma": estado.get("entrada_distancia_ma", 0),
                                "posicao_preco_ma9": estado.get("entrada_posicao_ma9", True),
                                "posicao_preco_ma21": estado.get("entrada_posicao_ma21", True),
                                "tipo_entrada": estado.get("tipo_entrada", "desconhecido"),
                                "resultado": resultado_pct
                            })
                            salvar_dados_ia()

                            estado["preco_entrada"] = 0
                            estado["topo_preco"] = 0
                            estado["tipo_operacao"] = ""
                            estado["tipo_entrada"] = ""
                            estado["ultimo_trade_time"] = time.time()
                            salvar_estado()
                        except Exception as e:
                            print(f"Erro ao vender {SYMBOL}: {e}")

                status_pos = f"EM TRADE ({lucro_liquido:+.2f}%)" if em_operacao else "AGUARDANDO ENTRADA"
                tendencia_txt = "ALTA 🟢" if ma9 > ma21 else "BAIXA 🔴"

                logs_painel.append(
                    f"[{SYMBOL}]\n"
                    f"• Preço: ${preco:.2f} | RSI: {rsi:.1f}\n"
                    f"• MA9: ${ma9:.2f} | MA21: ${ma21:.2f}\n"
                    f"• Tendência: {tendencia_txt}\n"
                    f"• Saldo do Ativo: {qtd:.6f}\n"
                    f"• Status: {status_pos}\n"
                    f"-----------------------------------"
                )

            log_texto = f"🕒 Atualizado: {datetime.now().strftime('%H:%M:%S')}\n" + "\n".join(logs_painel)
            self.atualizar_status(log_texto)
            time.sleep(3)

class TradingApp(App):
    def build(self):
        return TradingBotUI()

if __name__ == "__main__":
    TradingApp().run()
