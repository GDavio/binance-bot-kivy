import os
import sys
import time
import json
import hmac
import hashlib
import urllib.parse
import urllib.request
import urllib.error
import ssl
import threading
from datetime import datetime

# --- Kivy Imports ---
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.utils import platform

# --- Android Integration ---
if platform == 'android':
    from jnius import autoclass
    PythonActivity = autoclass('org.kivy.android.PythonActivity')
    Context = autoclass('android.content.Context')
    Intent = autoclass('android.content.Intent')
    NotificationBuilder = autoclass('android.app.Notification$Builder')
    NotificationManager = autoclass('android.app.NotificationManager')
    NotificationChannel = autoclass('android.app.NotificationChannel')
    PendingIntent = autoclass('android.app.PendingIntent')
    PowerManager = autoclass('android.os.PowerManager')

CONFIG_FILE = "config_bot.json"
ARQUIVO_ESTADO = "estado_bot.json"
DADOS_IA = "dados_ia.json"

if platform == 'android':
    ARQUIVO_RELATORIO = "/storage/emulated/0/Download/relatorio_bot.txt"
else:
    ARQUIVO_RELATORIO = "relatorio_bot.txt"

# ==========================================
# GERENCIAMENTO DE ENERGIA E SERVIÇO ANDROID
# ==========================================

def adquirir_wake_lock():
    if platform != 'android':
        return None
    try:
        activity = PythonActivity.mActivity
        power_manager = activity.getSystemService(Context.POWER_SERVICE)
        wake_lock = power_manager.newWakeLock(
            PowerManager.PARTIAL_WAKE_LOCK, "BotTrading::WakeLock"
        )
        wake_lock.acquire()
        return wake_lock
    except Exception as e:
        print(f"Erro ao adquirir WakeLock: {e}")
        return None

def liberar_wake_lock(wake_lock):
    if wake_lock and wake_lock.isHeld():
        try:
            wake_lock.release()
        except Exception as e:
            print(f"Erro ao liberar WakeLock: {e}")

def iniciar_foreground_service():
    if platform != 'android':
        return
    try:
        activity = PythonActivity.mActivity
        CHANNEL_ID = "bot_trading_channel_id"
        
        notification_manager = activity.getSystemService(Context.NOTIFICATION_SERVICE)
        channel = NotificationChannel(
            CHANNEL_ID,
            "Bot Trading Service",
            NotificationManager.IMPORTANCE_LOW
        )
        notification_manager.createNotificationChannel(channel)

        intent = Intent(activity, PythonActivity)
        pending_intent = PendingIntent.getActivity(
            activity, 0, intent, PendingIntent.FLAG_IMMUTABLE
        )

        builder = NotificationBuilder(activity, CHANNEL_ID)
        builder.setContentTitle("Bot Trading Binance")
        builder.setContentText("Executando estratégias em segundo plano...")
        builder.setSmallIcon(activity.getApplicationInfo().icon)
        builder.setContentIntent(pending_intent)
        builder.setOngoing(True)

        notification = builder.build()
        activity.startForeground(1001, notification)
    except Exception as e:
        print(f"Erro ao iniciar Foreground Service: {e}")

def parar_foreground_service():
    if platform != 'android':
        return
    try:
        activity = PythonActivity.mActivity
        activity.stopForeground(True)
    except Exception as e:
        print(f"Erro ao parar Foreground Service: {e}")

# ==========================================
# CLIENTE NATIVO DA API BINANCE (SSL FIX)
# ==========================================

class BinanceNativeAPI:
    def __init__(self, api_key="", api_secret=""):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.base_url = "https://api.binance.com"
        
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

    def _requisicao(self, metodo, endpoint, params=None, assinado=False):
        if params is None:
            params = {}

        headers = {
            "X-MBX-APIKEY": self.api_key,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }

        if assinado:
            params["timestamp"] = int(time.time() * 1000)
            query_string = urllib.parse.urlencode(params)
            assinatura = hmac.new(
                self.api_secret.encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            url = f"{self.base_url}{endpoint}?{query_string}&signature={assinatura}"
        else:
            query = urllib.parse.urlencode(params)
            url = f"{self.base_url}{endpoint}?{query}" if query else f"{self.base_url}{endpoint}"

        for tentativa in range(3):
            try:
                req = urllib.request.Request(url, headers=headers, method=metodo)
                with urllib.request.urlopen(req, timeout=10, context=self.ssl_context) as response:
                    return json.loads(response.read().decode('utf-8'))
            except (urllib.error.URLError, OSError) as e:
                if tentativa == 2:
                    raise e
                time.sleep(2)

    def fetch_ohlcv(self, symbol, timeframe="1m", limit=50):
        symbol_fmt = symbol.replace("/", "").upper()
        res = self._requisicao("GET", "/api/v3/klines", {"symbol": symbol_fmt, "interval": timeframe, "limit": limit})
        return [float(c[4]) for c in res]

    def fetch_balance(self):
        res = self._requisicao("GET", "/api/v3/account", assinado=True)
        free_balances = {item['asset']: float(item['free']) for item in res.get('balances', [])}
        return {'free': free_balances}

    def create_market_buy_order(self, symbol, quantity):
        symbol_fmt = symbol.replace("/", "").upper()
        params = {
            "symbol": symbol_fmt,
            "side": "BUY",
            "type": "MARKET",
            "quantity": f"{quantity:.4f}"
        }
        res = self._requisicao("POST", "/api/v3/order", params=params, assinado=True)
        fills = res.get('fills', [])
        avg_price = 0.0
        if fills:
            total_qty = sum(float(f['qty']) for f in fills)
            if total_qty > 0:
                avg_price = sum(float(f['price']) * float(f['qty']) for f in fills) / total_qty
        return {'average': avg_price or float(res.get('price', 0))}

    def create_market_sell_order(self, symbol, quantity):
        symbol_fmt = symbol.replace("/", "").upper()
        params = {
            "symbol": symbol_fmt,
            "side": "SELL",
            "type": "MARKET",
            "quantity": f"{quantity:.4f}"
        }
        res = self._requisicao("POST", "/api/v3/order", params=params, assinado=True)
        fills = res.get('fills', [])
        avg_price = 0.0
        if fills:
            total_qty = sum(float(f['qty']) for f in fills)
            if total_qty > 0:
                avg_price = sum(float(f['price']) * float(f['qty']) for f in fills) / total_qty
        return {'average': avg_price or float(res.get('price', 0))}

# ==========================================
# IA E PERSISTÊNCIA
# ==========================================

historico_ia = []
estado_por_par = {}

def salvar_dados_ia():
    try:
        with open(DADOS_IA, "w") as f:
            json.dump(historico_ia, f)
    except Exception as e:
        print(f"Erro ao salvar IA: {e}")

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

        if d["posicao_preco_ma9"] != (preco > ma9):
            peso *= 0.5

        if d["posicao_preco_ma21"] != (preco > ma21):
            peso *= 0.5

        if d.get("tipo_entrada") != tipo_entrada:
            peso *= 0.3

        if peso < 0.1:
            continue

        peso_total += peso
        score += peso * d["resultado"]

    if peso_total < 1:
        return 0.5

    return max(0, min(1, 0.5 + (score / peso_total)))

def salvar_estado():
    try:
        with open(ARQUIVO_ESTADO, "w") as f:
            json.dump(estado_por_par, f)
    except Exception as e:
        print(f"Erro ao salvar estado: {e}")

def carregar_estado():
    global estado_por_par
    if os.path.exists(ARQUIVO_ESTADO):
        try:
            with open(ARQUIVO_ESTADO, "r") as f:
                estado_por_par = json.load(f)
        except Exception:
            estado_por_par = {}

def sync_posicao(symbol, preco_atual, quantidade, app_instance=None):
    estado = estado_por_par[symbol]
    if quantidade > 0.0001 and estado["preco_entrada"] == 0:
        estado["preco_entrada"] = preco_atual
        estado["topo_preco"] = preco_atual
        estado["tipo_operacao"] = "COMPRA"
        estado["ultimo_trade_time"] = time.time()
        estado["tempo_entrada"] = time.time()
        salvar_estado()

def salvar_trade_relatorio(symbol, estado, lucro, preco_saida, rsi_saida, ma9_saida, ma21_saida, lucro_max):
    tempo_operacao = int(time.time() - estado.get("tempo_entrada", time.time()))
    try:
        with open(ARQUIVO_RELATORIO, "a") as f:
            f.write("==============================\n")
            f.write(f"Par: {symbol}\n")
            f.write(f"Data: {datetime.now()}\n")
            f.write(f"Tipo: {estado.get('tipo_operacao', 'COMPRA')}\n")
            f.write("--- ENTRADA ---\n")
            f.write(f"Preço: {estado.get('preco_entrada', 0):.2f}\n")
            f.write(f"RSI: {estado.get('entrada_rsi', 0):.2f}\n")
            f.write(f"MA9: {estado.get('entrada_ma9', 0):.2f}\n")
            f.write(f"MA21: {estado.get('entrada_ma21', 0):.2f}\n")
            f.write("--- SAÍDA ---\n")
            f.write(f"Preço: {preco_saida:.2f}\n")
            f.write(f"RSI: {rsi_saida:.2f}\n")
            f.write(f"MA9: {ma9_saida:.2f}\n")
            f.write(f"MA21: {ma21_saida:.2f}\n")
            f.write("--- RESULTADO ---\n")
            f.write(f"Lucro: {lucro:.2f}%\n")
            f.write(f"Lucro Máximo: {lucro_max:.2f}%\n")
            f.write(f"Tempo operação: {tempo_operacao}s\n\n\n")
    except Exception as e:
        print(f"Erro ao salvar relatório: {e}")

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

def salvar_config(api_key, api_secret, symbols, valor_usdt):
    data = {
        "api_key": api_key,
        "api_secret": api_secret,
        "symbols": symbols,
        "valor_usdt": valor_usdt
    }
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f)

def carregar_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"api_key": "", "api_secret": "", "symbols": "ETH/USDT BTC/USDT SOL/USDT LTC/USDT", "valor_usdt": "12"}

# ==========================================
# INTERFACE KIVY FIXA E CLARA
# ==========================================

class BotTradingApp(App):
    def build(self):
        Window.clearcolor = (0.07, 0.07, 0.09, 1)
        self.bot_rodando = False
        self.wake_lock = None

        carregar_estado()
        carregar_dados_ia()

        root = BoxLayout(orientation='vertical', padding=10, spacing=8)

        # Header Fixo
        lbl_titulo = Label(
            text="Bot Trading Binance",
            font_size='18sp',
            bold=True,
            size_hint=(1, 0.05),
            color=(1, 1, 1, 1)
        )
        root.add_widget(lbl_titulo)

        config = carregar_config()

        # Formulário Fixo Visível (35% da tela)
        form_layout = GridLayout(cols=1, spacing=6, size_hint=(1, 0.38))

        form_layout.add_widget(Label(text="Binance API Key:", size_hint=(1, 0.12), font_size='11sp', halign='left', color=(0.7, 0.7, 0.7, 1)))
        self.api_key = TextInput(text=config.get("api_key", ""), multiline=False, password=True, size_hint=(1, 0.18), background_color=(0.15, 0.15, 0.18, 1), foreground_color=(1, 1, 1, 1))
        form_layout.add_widget(self.api_key)

        form_layout.add_widget(Label(text="Binance Secret Key:", size_hint=(1, 0.12), font_size='11sp', halign='left', color=(0.7, 0.7, 0.7, 1)))
        self.api_secret = TextInput(text=config.get("api_secret", ""), multiline=False, password=True, size_hint=(1, 0.18), background_color=(0.15, 0.15, 0.18, 1), foreground_color=(1, 1, 1, 1))
        form_layout.add_widget(self.api_secret)

        form_layout.add_widget(Label(text="Pares (separados por espaço):", size_hint=(1, 0.12), font_size='11sp', halign='left', color=(0.7, 0.7, 0.7, 1)))
        self.symbols = TextInput(text=config.get("symbols", "ETH/USDT BTC/USDT SOL/USDT LTC/USDT"), multiline=False, size_hint=(1, 0.18), background_color=(0.15, 0.15, 0.18, 1), foreground_color=(1, 1, 1, 1))
        form_layout.add_widget(self.symbols)

        form_layout.add_widget(Label(text="Valor por Ordem (USDT):", size_hint=(1, 0.12), font_size='11sp', halign='left', color=(0.7, 0.7, 0.7, 1)))
        self.valor_usdt = TextInput(text=config.get("valor_usdt", "12"), multiline=False, size_hint=(1, 0.18), background_color=(0.15, 0.15, 0.18, 1), foreground_color=(1, 1, 1, 1))
        form_layout.add_widget(self.valor_usdt)

        root.add_widget(form_layout)

        # Botão Ligar/Desligar Fixo
        self.btn_toggle = Button(
            text="LIGAR ROBÔ",
            size_hint=(1, 0.08),
            bold=True,
            background_normal='',
            background_color=(0, 0.7, 0.3, 1)
        )
        self.btn_toggle.bind(on_press=self.toggle_bot)
        root.add_widget(self.btn_toggle)

        # Container de Logs (49% restante da tela)
        scroll_logs = ScrollView(size_hint=(1, 0.49))
        self.lbl_logs = Label(
            text="[SISTEMA] Preencha as chaves acima e clique em LIGAR ROBÔ.\n",
            size_hint_y=None,
            font_size='11sp',
            halign='left',
            valign='top',
            color=(0.9, 0.9, 0.9, 1)
        )
        self.lbl_logs.bind(texture_size=lambda instance, value: setattr(instance, 'height', value[1]))
        self.lbl_logs.bind(width=lambda instance, value: setattr(instance, 'text_size', (value, None)))
        scroll_logs.add_widget(self.lbl_logs)
        root.add_widget(scroll_logs)

        return root

    def atualizar_status(self, texto):
        def _update(dt):
            self.lbl_logs.text += f"{texto}\n"
            if len(self.lbl_logs.text) > 15000:
                self.lbl_logs.text = self.lbl_logs.text[-10000:]
        Clock.schedule_once(_update)

    def toggle_bot(self, instance):
        if not self.bot_rodando:
            salvar_config(
                self.api_key.text.strip(),
                self.api_secret.text.strip(),
                self.symbols.text.strip(),
                self.valor_usdt.text.strip()
            )
            self.wake_lock = adquirir_wake_lock()
            iniciar_foreground_service()

            self.bot_rodando = True
            self.btn_toggle.text = "DESLIGAR ROBÔ"
            self.btn_toggle.background_color = (0.85, 0.2, 0.2, 1)
            self.atualizar_status("🚀 ROBÔ INICIADO COM SUCESSO!")
            threading.Thread(target=self.loop_principal_bot, daemon=True).start()
        else:
            self.bot_rodando = False
            parar_foreground_service()
            liberar_wake_lock(self.wake_lock)

            self.btn_toggle.text = "LIGAR ROBÔ"
            self.btn_toggle.background_color = (0, 0.7, 0.3, 1)
            self.atualizar_status("🛑 Robô desligado.")

    def loop_principal_bot(self):
        symbols_list = [s.strip().upper() for s in self.symbols.text.strip().split() if s.strip()]
        valor_ordem = float(self.valor_usdt.text.strip() or 12)
        cooldown = 120

        exchange = BinanceNativeAPI(self.api_key.text, self.api_secret.text)

        for s in symbols_list:
            if s not in estado_por_par:
                estado_por_par[s] = {
                    "preco_entrada": 0,
                    "topo_preco": 0,
                    "ultimo_trade_time": 0,
                    "tipo_operacao": "",
                    "entrada_rsi": 0,
                    "entrada_ma9": 0,
                    "entrada_ma21": 0,
                    "tempo_entrada": 0,
                    "historico": [],
                    "wins": 0,
                    "losses": 0,
                    "estrategias": {
                        "pullback": {"wins": 0, "losses": 0},
                        "continuidade": {"wins": 0, "losses": 0},
                        "rompimento": {"wins": 0, "losses": 0}
                    }
                }

        while self.bot_rodando:
            for SYMBOL in symbols_list:
                if not self.bot_rodando:
                    break

                estado = estado_por_par[SYMBOL]

                try:
                    closes = exchange.fetch_ohlcv(SYMBOL, "1m", limit=50)
                    preco = closes[-1]
                    preco_anterior = closes[-2]

                    rsi = calcular_rsi(closes)
                    rsi_anterior = calcular_rsi(closes[:-1])

                    ma9 = media(closes, 9)
                    ma21 = media(closes, 21)

                    saldo = exchange.fetch_balance()
                    ativo = SYMBOL.split("/")[0]
                    qtd = saldo['free'].get(ativo, 0.0)
                    usdt = saldo['free'].get('USDT', 0.0)

                except Exception as e:
                    self.atualizar_status(f"⚠️ Erro ao carregar ({SYMBOL}): {e}")
                    continue

                sync_posicao(SYMBOL, preco, qtd, self)

                em_operacao = qtd > 0.0001
                volatilidade = (max(closes[-10:]) - min(closes[-10:])) / preco * 100
                tempo_ok = (time.time() - estado["ultimo_trade_time"]) > cooldown
                distancia_ma = (ma9 - ma21) / ma21 * 100

                tendencia = ma9 > ma21
                tendencia_forte = distancia_ma > 0.02

                pullback = (
                    tendencia and
                    preco <= ma9 * 1.003 and preco > ma9 and
                    rsi < 62 and
                    rsi > rsi_anterior
                )

                continuidade = (
                    tendencia and
                    preco > ma9 and
                    52 < rsi < 68 and
                    rsi > rsi_anterior and
                    closes[-1] > closes[-2] and
                    (distancia_ma > 0.03 or (distancia_ma > 0.001 and closes[-1] > closes[-2]))
                )

                rompimento = (
                    tendencia_forte and
                    preco >= max(closes[-3:]) and
                    closes[-1] > closes[-2] and
                    55 < rsi < 75 and
                    rsi > rsi_anterior and
                    preco > ma9 and
                    (preco - closes[-3]) / closes[-3] * 100 > 0.01
                )

                tipo_entrada = "nenhum"
                if pullback:
                    tipo_entrada = "pullback"
                elif continuidade:
                    tipo_entrada = "continuidade"
                elif rompimento:
                    tipo_entrada = "rompimento"

                prob = prever_probabilidade(rsi, ma9, ma21, volatilidade, preco, tipo_entrada)

                status_posicao = f"🔒 POSIÇÃO ABERTA ({estado['preco_entrada']:.2f})" if em_operacao else "⏳ SEM POSIÇÃO"
                
                bloco_log = (
                    f"============================== {SYMBOL}\n"
                    f"📊 MERCADO\n"
                    f"Preço: {preco:.2f} | RSI: {rsi:.2f} | MA9: {ma9:.2f} | MA21: {ma21:.2f}\n"
                    f"💰 USDT: {usdt:.2f} | {ativo}: {qtd:.6f}\n"
                    f"{status_posicao}\n"
                    f"🧠 CONDIÇÕES:\n"
                    f"Tendência: {tendencia} | Pullback: {pullback} | Continuidade: {continuidade} | Rompimento: {rompimento}\n"
                    f"Volatilidade: {volatilidade:.2f} | Tempo OK: {tempo_ok}\n"
                    f"🧠 Probabilidade IA: {prob:.2f}"
                )
                self.atualizar_status(bloco_log)

                # --- LÓGICA DE ENTRADA ---
                if not em_operacao and usdt >= valor_ordem:
                    entrada_valida = (pullback or continuidade or rompimento) and volatilidade > 0.10

                    if entrada_valida and tempo_ok and (len(historico_ia) < 50 or prob > 0.55):
                        self.atualizar_status(f"🚀 ENTRADA CONFIRMADA ({tipo_entrada.upper()}) | Prob IA: {prob:.2f}")
                        try:
                            quantidade = round(valor_ordem / preco, 4)
                            order = exchange.create_market_buy_order(SYMBOL, quantidade)

                            estado["preco_entrada"] = order.get('average') or preco
                            estado["topo_preco"] = estado["preco_entrada"]
                            estado["tempo_entrada"] = time.time()
                            estado["ultimo_trade_time"] = time.time()
                            estado["tipo_entrada"] = tipo_entrada
                            estado["entrada_rsi"] = rsi
                            estado["entrada_ma9"] = ma9
                            estado["entrada_ma21"] = ma21
                            estado["entrada_distancia_ma"] = distancia_ma
                            estado["entrada_volatilidade"] = volatilidade
                            estado["entrada_posicao_ma9"] = preco > ma9
                            estado["entrada_posicao_ma21"] = preco > ma21

                            salvar_estado()
                            self.atualizar_status(f"✅ COMPRA EXECUTADA em {SYMBOL} @ {estado['preco_entrada']:.2f}")
                        except Exception as e:
                            self.atualizar_status(f"❌ ERRO AO COMPRAR {SYMBOL}: {e}")

                # --- LÓGICA DE SAÍDA ---
                if em_operacao:
                    lucro = ((preco - estado["preco_entrada"]) / estado["preco_entrada"]) * 100

                    if preco > estado["topo_preco"]:
                        estado["topo_preco"] = preco
                        salvar_estado()

                    lucro_max = ((estado["topo_preco"] - estado["preco_entrada"]) / estado["preco_entrada"]) * 100

                    vender = False
                    motivo_venda = ""

                    stop_emergencia = estado["preco_entrada"] * 0.995

                    if preco <= stop_emergencia:
                        vender = True
                        motivo_venda = "🚨 STOP DURO -0.5%"

                    elif (rsi < 38 and rsi < rsi_anterior and preco < ma9 and preco < preco_anterior and lucro < -0.25):
                        vender = True
                        motivo_venda = "📉 REVERSÃO FORTE"

                    elif (all(c < ma21 for c in closes[-3:]) and rsi < 43 and rsi < rsi_anterior and lucro < -0.35):
                        vender = True
                        motivo_venda = "💀 PERDEU TENDÊNCIA"

                    elif lucro_max >= 0.35:
                        if lucro_max < 0.70:
                            lucro_travado = 0.10
                        elif lucro_max < 1.05:
                            lucro_travado = 0.35
                        elif lucro_max < 1.40:
                            lucro_travado = 0.70
                        elif lucro_max < 1.75:
                            lucro_travado = 1.05
                        else:
                            lucro_travado = lucro_max - 0.35

                        stop_dinamico = estado["preco_entrada"] * (1 + lucro_travado / 100)

                        if preco <= stop_dinamico:
                            vender = True
                            motivo_venda = f"🛡️ TRAILING STOP ({lucro_travado:.2f}%)"

                    if vender:
                        self.atualizar_status(f"❌ VENDENDO ({SYMBOL}): {motivo_venda}")
                        try:
                            order = exchange.create_market_sell_order(SYMBOL, qtd)
                            preco_saida = order.get('average') or preco
                            lucro_real = ((preco_saida - estado["preco_entrada"]) / estado["preco_entrada"]) * 100

                            salvar_trade_relatorio(
                                SYMBOL, estado, lucro_real, preco_saida, rsi, ma9, ma21, lucro_max
                            )

                            historico_ia.append({
                                "tipo_entrada": estado.get("tipo_entrada", ""),
                                "rsi": estado.get("entrada_rsi", 50),
                                "volatilidade": estado.get("entrada_volatilidade", 0),
                                "distancia_ma": estado.get("entrada_distancia_ma", 0),
                                "posicao_preco_ma9": estado.get("entrada_posicao_ma9", True),
                                "posicao_preco_ma21": estado.get("entrada_posicao_ma21", True),
                                "forca_tendencia": abs(estado.get("entrada_distancia_ma", 0)),
                                "resultado": lucro_real
                            })
                            salvar_dados_ia()

                            estado["preco_entrada"] = 0
                            estado["topo_preco"] = 0
                            estado["entrada_rsi"] = 0
                            estado["entrada_ma9"] = 0
                            estado["entrada_ma21"] = 0
                            estado["entrada_distancia_ma"] = 0
                            estado["entrada_volatilidade"] = 0
                            estado["entrada_posicao_ma9"] = False
                            estado["entrada_posicao_ma21"] = False
                            salvar_estado()

                            self.atualizar_status(f"💰 RESULTADO FINAL: {lucro_real:.2f}%")
                        except Exception as e:
                            self.atualizar_status(f"⚠️ Erro ao vender {SYMBOL}: {e}")

            time.sleep(10)

if __name__ == '__main__':
    BotTradingApp().run()
