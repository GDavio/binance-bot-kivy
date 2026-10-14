import hashlib
import hmac
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal, ROUND_DOWN

from kivy.app import App
from kivy.clock import Clock
from kivy.core.text import Label as CoreLabel
from kivy.core.window import Window
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.switch import Switch
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget
from kivy.utils import platform

# ----------------------------------------------------------------------------
# Android: wake lock + serviço em primeiro plano (igual ao seu código original)
# ----------------------------------------------------------------------------
if platform == "android":
    from jnius import autoclass
    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Context = autoclass("android.content.Context")
    Intent = autoclass("android.content.Intent")
    NotificationBuilder = autoclass("android.app.Notification$Builder")
    NotificationManager = autoclass("android.app.NotificationManager")
    NotificationChannel = autoclass("android.app.NotificationChannel")
    PendingIntent = autoclass("android.app.PendingIntent")
    PowerManager = autoclass("android.os.PowerManager")

if platform == "android":
    ARQUIVO_RELATORIO = "/storage/emulated/0/Download/relatorio_bot.txt"
else:
    ARQUIVO_RELATORIO = "relatorio_bot.txt"


def adquirir_wake_lock():
    if platform != "android":
        return None
    try:
        activity = PythonActivity.mActivity
        pm = activity.getSystemService(Context.POWER_SERVICE)
        wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BotTrading::WakeLock")
        wl.acquire()
        return wl
    except Exception as e:
        print("Erro WakeLock:", e)
        return None


def liberar_wake_lock(wl):
    if wl and wl.isHeld():
        try:
            wl.release()
        except Exception as e:
            print("Erro ao liberar WakeLock:", e)


def iniciar_foreground_service():
    if platform != "android":
        return
    try:
        activity = PythonActivity.mActivity
        canal = "bot_trading_channel_id"
        nm = activity.getSystemService(Context.NOTIFICATION_SERVICE)
        nm.createNotificationChannel(
            NotificationChannel(canal, "Bot Trading Service", NotificationManager.IMPORTANCE_LOW))
        intent = Intent(activity, PythonActivity)
        pi = PendingIntent.getActivity(activity, 0, intent, PendingIntent.FLAG_IMMUTABLE)
        b = NotificationBuilder(activity, canal)
        b.setContentTitle("Bot Trading Binance")
        b.setContentText("Executando estratégias em segundo plano...")
        b.setSmallIcon(activity.getApplicationInfo().icon)
        b.setContentIntent(pi)
        b.setOngoing(True)
        activity.startForeground(1001, b.build())
    except Exception as e:
        print("Erro Foreground Service:", e)


def parar_foreground_service():
    if platform != "android":
        return
    try:
        PythonActivity.mActivity.stopForeground(True)
    except Exception as e:
        print("Erro ao parar Foreground Service:", e)


# ----------------------------------------------------------------------------
# Paleta
# ----------------------------------------------------------------------------
BG = (0.071, 0.075, 0.090, 1)
NAV = (0.067, 0.071, 0.086, 1)
CARD = (0.105, 0.115, 0.150, 1)
CARD2 = (0.140, 0.150, 0.190, 1)
YELLOW = (0.941, 0.725, 0.050, 1)
GREEN = (0.070, 0.800, 0.520, 1)
RED = (0.970, 0.270, 0.350, 1)
TEXT = (1, 1, 1, 1)
MUTED = (0.55, 0.58, 0.65, 1)
DARK = (0.10, 0.10, 0.10, 1)
BLUE = (0.25, 0.40, 1.00, 1)
CLEAR = (0, 0, 0, 0)

PARES = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT", "DOGEUSDT"]
TF_LISTA = ["1m", "5m", "15m", "1h", "4h", "1D"]
TF_API = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1D": "1d"}
QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "BTC", "ETH", "BNB", "EUR", "BRL", "TRY")

# ----------------------------------------------------------------------------
# Parâmetros da estratégia (os mesmos do seu bot)
# ----------------------------------------------------------------------------
COOLDOWN = 120        # segundos entre operações do mesmo par
VOL_MIN = 0.10        # volatilidade mínima (%) para entrar
PROB_MIN = 0.55       # filtro da IA (só vale depois de IA_MIN_HIST trades)
IA_MIN_HIST = 50
STOP_PCT = 0.5        # stop loss fixo (antes de o trailing ativar)
TRAIL_ATIVA = 0.35    # lucro máximo (%) que ativa o trailing escada
ESTRATEGIAS = ["pullback", "continuidade", "rompimento"]
ROTULO = {"pullback": "Pullback", "continuidade": "Continuidade", "rompimento": "Rompimento"}
INTERVALO_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}


# ----------------------------------------------------------------------------
# Rede
# ----------------------------------------------------------------------------
BASE_PUBLICA = "https://api.binance.com"
BASE_TESTNET = "https://testnet.binance.vision"
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()


def http_json(path, params=None, timeout=10, base=BASE_PUBLICA, headers=None, metodo="GET"):
    url = base + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    h = {"User-Agent": "Mozilla/5.0"}
    if headers:
        h.update(headers)
    for tentativa in range(3):
        try:
            req = urllib.request.Request(url, headers=h, method=metodo)
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            corpo = e.read().decode("utf-8", "ignore") if e.fp else ""
            raise Exception("HTTP %s: %s" % (e.code, corpo or e.reason))
        except (urllib.error.URLError, OSError):
            if tentativa == 2:
                raise
            time.sleep(1.5)


def buscar_klines(symbol, intervalo, limit=100, inicio=None):
    """Lista de (open, high, low, close, open_time_ms)."""
    p = {"symbol": symbol, "interval": intervalo, "limit": limit}
    if inicio:
        p["startTime"] = int(inicio)
    res = http_json("/api/v3/klines", p)
    return [(float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(k[0])) for k in res]


def buscar_tickers(symbols):
    arr = json.dumps(symbols, separators=(",", ":"))
    return http_json("/api/v3/ticker/24hr", {"symbols": arr})


def validar_par(sym):
    return float(http_json("/api/v3/ticker/price", {"symbol": sym})["price"])


def ajustar_qtd(qtd, step):
    d = Decimal(str(qtd))
    if step and step > 0:
        d = (d / step).to_integral_value(rounding=ROUND_DOWN) * step
    return d


class Binance:
    """Cliente assinado. Funciona com qualquer par (lê stepSize/minQty no exchangeInfo)."""

    def __init__(self, key, secret, testnet=True):
        self.key = key.strip()
        self.secret = secret.strip()
        self.base = BASE_TESTNET if testnet else BASE_PUBLICA
        self._info = {}

    def _assinado(self, metodo, endpoint, params=None):
        p = dict(params or {})
        p["timestamp"] = int(time.time() * 1000)
        p["recvWindow"] = 10000
        q = urllib.parse.urlencode(p)
        p["signature"] = hmac.new(self.secret.encode("utf-8"), q.encode("utf-8"),
                                  hashlib.sha256).hexdigest()
        return http_json(endpoint, p, base=self.base, metodo=metodo,
                         headers={"X-MBX-APIKEY": self.key})

    def saldo_livre(self):
        res = self._assinado("GET", "/api/v3/account")
        return {b["asset"]: float(b["free"]) for b in res.get("balances", [])}

    def info(self, sym):
        if sym not in self._info:
            r = http_json("/api/v3/exchangeInfo", {"symbol": sym}, base=self.base)["symbols"][0]
            step, minq, minn = Decimal("0"), 0.0, 0.0
            for f in r["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step, minq = Decimal(f["stepSize"]), float(f["minQty"])
                elif f["filterType"] in ("NOTIONAL", "MIN_NOTIONAL"):
                    minn = float(f.get("minNotional", 0))
            self._info[sym] = {"base": r["baseAsset"], "quote": r["quoteAsset"],
                               "step": step, "minq": minq, "minn": minn}
        return self._info[sym]

    def _resumo(self, res, sym):
        fills = res.get("fills", [])
        tq = sum(float(f["qty"]) for f in fills)
        preco = sum(float(f["price"]) * float(f["qty"]) for f in fills) / tq if tq > 0 else 0.0
        if preco == 0:
            ex, cq = float(res.get("executedQty", 0)), float(res.get("cummulativeQuoteQty", 0))
            preco = cq / ex if ex > 0 else 0.0
        base = self.info(sym)["base"]
        taxa = sum(float(f.get("commission", 0)) for f in fills if f.get("commissionAsset") == base)
        return {"preco": preco, "qtd": float(res.get("executedQty", 0)) - taxa}

    def comprar(self, sym, valor_quote):
        v = ("%.8f" % valor_quote).rstrip("0").rstrip(".")
        res = self._assinado("POST", "/api/v3/order", {
            "symbol": sym, "side": "BUY", "type": "MARKET", "quoteOrderQty": v})
        return self._resumo(res, sym)

    def vender(self, sym, qtd):
        info = self.info(sym)
        q = ajustar_qtd(qtd, info["step"])
        if q <= 0 or float(q) < info["minq"]:
            raise Exception("Invalid quantity: %s abaixo do mínimo" % qtd)
        res = self._assinado("POST", "/api/v3/order", {
            "symbol": sym, "side": "SELL", "type": "MARKET",
            "quantity": format(q.normalize(), "f")})
        return self._resumo(res, sym)


# ----------------------------------------------------------------------------
# Indicadores (iguais ao seu código)
# ----------------------------------------------------------------------------
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


def serie_ma(closes, p):
    return [None if i + 1 < p else sum(closes[i + 1 - p:i + 1]) / p for i in range(len(closes))]


def serie_rsi(closes, p=14):
    out = [None] * len(closes)
    if len(closes) <= p:
        return out
    ganhos = perdas = 0.0
    for i in range(1, p + 1):
        d = closes[i] - closes[i - 1]
        ganhos += max(d, 0)
        perdas += max(-d, 0)
    ag, al = ganhos / p, perdas / p
    out[p] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(p + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (p - 1) + max(d, 0)) / p
        al = (al * (p - 1) + max(-d, 0)) / p
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


# ----------------------------------------------------------------------------
# ESTRATÉGIA: entradas
# ----------------------------------------------------------------------------
def analisar(closes):
    """Indicadores + os três gatilhos de entrada. Use as últimas ~50 closes."""
    if len(closes) < 30:
        return None
    preco, ant = closes[-1], closes[-2]
    rsi, rsi_ant = calcular_rsi(closes), calcular_rsi(closes[:-1])
    ma9, ma21 = media(closes, 9), media(closes, 21)
    vol = (max(closes[-10:]) - min(closes[-10:])) / preco * 100
    dist = (ma9 - ma21) / ma21 * 100
    tend = ma9 > ma21
    forte = dist > 0.02
    pullback = (tend and preco <= ma9 * 1.003 and preco > ma9
                and rsi < 62 and rsi > rsi_ant)
    continuidade = (tend and preco > ma9 and 50 < rsi < 60      # RSI 50-60 (antes 52-68)
                    and rsi > rsi_ant and preco > ant
                    and (dist > 0.03 or (dist > 0.001 and preco > ant)))
    rompimento = (forte and preco >= max(closes[-3:]) and preco > ant
                  and 55 < rsi < 75 and rsi > rsi_ant and preco > ma9
                  and (preco - closes[-3]) / closes[-3] * 100 > 0.01)
    return {"preco": preco, "ant": ant, "rsi": rsi, "rsi_ant": rsi_ant, "ma9": ma9, "ma21": ma21,
            "vol": vol, "dist": dist, "tend": tend, "pullback": pullback,
            "continuidade": continuidade, "rompimento": rompimento}


def escolher_entrada(a, ativas):
    """Prioridade: pullback > continuidade > rompimento (só as estratégias ligadas)."""
    if a is None or a["vol"] <= VOL_MIN:
        return None
    for nome in ESTRATEGIAS:
        if nome in ativas and a[nome]:
            return nome
    return None


def tipo_prioritario(a):
    for nome in ESTRATEGIAS:
        if a[nome]:
            return nome
    return "nenhum"


def prever_probabilidade(hist, rsi, ma9, ma21, vol, preco, tipo):
    if len(hist) < 20:
        return 0.50
    peso_total = score = 0
    for d in hist:
        if "distancia_ma" not in d:
            continue
        peso = 1.0
        peso *= max(0, 1 - abs(d["rsi"] - rsi) / 15)
        peso *= max(0, 1 - abs(d["volatilidade"] - vol) / 0.5)
        peso *= max(0, 1 - abs(d["distancia_ma"] - ((ma9 - ma21) / ma21 * 100)) / 0.3)
        if d["posicao_preco_ma9"] != (preco > ma9):
            peso *= 0.5
        if d["posicao_preco_ma21"] != (preco > ma21):
            peso *= 0.5
        if d.get("tipo_entrada") != tipo:
            peso *= 0.3
        if peso < 0.1:
            continue
        peso_total += peso
        score += peso * d["resultado"]
    if peso_total < 1:
        return 0.5
    return max(0, min(1, 0.5 + (score / peso_total)))


# ----------------------------------------------------------------------------
# ESTRATÉGIA: saídas
#   1) ANTES do trailing ativar  -> STOP LOSS fixo (-0,5%) é a saída principal
#   2) DEPOIS do trailing ativar -> TRAILING ESCADA passa a ser a principal
#   3) Em seguida (2ª camada)    -> REVERSAO FORTE e PERDEU TENDENCIA
# ----------------------------------------------------------------------------
def trava_trailing(lucro_max):
    if lucro_max < 0.70:
        return 0.10
    if lucro_max < 1.05:
        return 0.35
    if lucro_max < 1.40:
        return 0.70
    if lucro_max < 1.75:
        return 1.05
    return lucro_max - 0.35


def nivel_stop(entrada, topo):
    """(preço do stop, motivo, trailing_ativo)."""
    lucro_max = (topo / entrada - 1) * 100
    if lucro_max >= TRAIL_ATIVA:
        t = trava_trailing(lucro_max)
        return entrada * (1 + t / 100), "TRAILING STOP (%.2f%%)" % t, True
    return entrada * (1 - STOP_PCT / 100), "STOP LOSS -%.1f%%" % STOP_PCT, False


def saida_secundaria(entrada, preco, closes, a):
    lucro = (preco / entrada - 1) * 100
    if a["rsi"] < 38 and a["rsi"] < a["rsi_ant"] and preco < a["ma9"] and preco < a["ant"] and lucro < -0.25:
        return "REVERSAO FORTE"
    ma21 = a["ma21"]
    if (all(c < ma21 for c in closes[-3:]) and a["rsi"] < 43 and a["rsi"] < a["rsi_ant"]
            and lucro < -0.35):
        return "PERDEU TENDENCIA"
    return None


def decidir_saida(entrada, topo, preco, closes):
    px, motivo, _ = nivel_stop(entrada, topo)
    if preco <= px:
        return motivo
    a = analisar(closes)
    if a is None:
        return None
    return saida_secundaria(entrada, preco, closes, a)


# ----------------------------------------------------------------------------
# BACKTEST (mesmas regras do robô, candle a candle)
# ----------------------------------------------------------------------------
def baixar_candles(sym, intervalo, dias):
    fim = int(time.time() * 1000)
    cur = fim - dias * 86400000
    out = []
    while cur < fim:
        lote = buscar_klines(sym, intervalo, 1000, cur)
        if not lote:
            break
        out += lote
        cur = lote[-1][4] + 1
        if len(lote) < 1000:
            break
    return out


def backtest_par(candles, ativas):
    trades, pos = [], None
    for i in range(30, len(candles)):
        o, h, l, c, t = candles[i]
        janela = [x[3] for x in candles[max(0, i - 49):i + 1]]
        if pos:
            # 1) stop atingido dentro do candle (usa o topo dos candles anteriores)
            px, motivo, _ = nivel_stop(pos["entrada"], pos["topo"])
            if l <= px:
                saida = min(px, o)
            else:
                # 2) atualiza topo e avalia no fechamento (stop/trailing atualizado + 2ª camada)
                pos["topo"] = max(pos["topo"], h)
                motivo = decidir_saida(pos["entrada"], pos["topo"], c, janela)
                saida = c
                if not motivo:
                    continue
            trades.append({"lucro": (saida / pos["entrada"] - 1) * 100, "motivo": motivo,
                           "tipo": pos["tipo"]})
            pos = None
        else:
            tipo = escolher_entrada(analisar(janela), ativas)
            if tipo:
                pos = {"entrada": c, "topo": c, "tipo": tipo}
    return trades


def resumo_trades(trades, valor):
    n = len(trades)
    lucros = [t["lucro"] for t in trades]
    ganhos = len([x for x in lucros if x > 0])
    soma = sum(lucros)
    taxa = soma - 0.2 * n
    mot = {}
    for t in trades:
        k = t["motivo"].split(" (")[0]
        mot[k] = mot.get(k, 0) + 1
    ms = " · ".join("%s %d" % (k, v) for k, v in sorted(mot.items(), key=lambda x: -x[1]))
    return n, soma, ("%d op · acerto %.0f%%\nSoma %+.2f%% (≈ %+.2f USDT)\n"
                     "Com taxa 0,1%%/lado: %+.2f%% (≈ %+.2f USDT)\n"
                     "Melhor %+.2f%% · Pior %+.2f%%\nSaídas: %s"
                     % (n, ganhos / n * 100, soma, valor * soma / 100, taxa, valor * taxa / 100,
                        max(lucros), min(lucros), ms))


# ----------------------------------------------------------------------------
# Formatação / pares
# ----------------------------------------------------------------------------
def fmt_preco(p):
    if p >= 10000:
        return "$%s" % format(p, ",.0f")
    if p >= 10:
        return "$%s" % format(p, ",.2f")
    return "$%.4f" % p


def fmt_vol(v):
    return "$%.1fM" % (v / 1e6)


def par_fmt(s):
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[:-len(q)] + "/" + q
    return s


def normalizar_par(s):
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    if not s:
        return ""
    if s in ("BTC", "ETH", "BNB") or not any(s.endswith(q) and len(s) > len(q) for q in QUOTES):
        s += "USDT"
    return s


def lista_pares(texto, maximo=8):
    out = []
    for p in re.split(r"[\s,;]+", texto or ""):
        p = normalizar_par(p)
        if p and p not in out:
            out.append(p)
    return out[:maximo]


# ----------------------------------------------------------------------------
# Widgets base
# ----------------------------------------------------------------------------
def txt(texto, tam=14, cor=TEXT, bold=False, halign="left", altura=None, **kw):
    lb = Label(text=texto, font_size=sp(tam), color=cor, bold=bold,
               halign=halign, valign="middle", **kw)
    lb.bind(size=lambda w, s: setattr(w, "text_size", s))
    if altura:
        lb.size_hint_y = None
        lb.height = dp(altura)
    return lb


def txt_auto(texto="", tam=15, cor=TEXT):
    lb = Label(text=texto, font_size=sp(tam), color=cor, halign="left", valign="top",
               size_hint_y=None)
    lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    lb.bind(texture_size=lambda w, s: setattr(w, "height", s[1]))
    return lb


class Card(BoxLayout):
    def __init__(self, bg=CARD, radius=16, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size


class ClickCard(Card):
    def __init__(self, cb=None, **kw):
        super().__init__(**kw)
        self.cb = cb

    def on_touch_up(self, touch):
        if (self.cb and self.collide_point(*touch.pos)
                and abs(touch.y - touch.oy) < dp(12) and abs(touch.x - touch.ox) < dp(12)):
            self.cb(self)
        return super().on_touch_up(touch)


class RoundBtn(Button):
    def __init__(self, bg=CARD2, fg=TEXT, radius=22, **kw):
        kw.setdefault("background_normal", "")
        kw.setdefault("background_down", "")
        kw.setdefault("bold", True)
        super().__init__(background_color=CLEAR, color=fg, **kw)
        with self.canvas.before:
            self._c = Color(*bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(radius)])
        self.bind(pos=self._upd, size=self._upd)

    def _upd(self, *a):
        self._r.pos = self.pos
        self._r.size = self.size

    def estilo(self, bg, fg=None):
        self._c.rgba = bg
        if fg is not None:
            self.color = fg


def auto_altura(w):
    w.size_hint_y = None
    w.bind(minimum_height=w.setter("height"))
    return w


def rolavel(box):
    auto_altura(box)
    sv = ScrollView(do_scroll_x=False, bar_width=0)
    sv.add_widget(box)
    return sv


def chip_row(opcoes, selecionado, ao_escolher, altura=44, tam=13):
    row = BoxLayout(size_hint_y=None, height=dp(altura), spacing=dp(8))
    botoes = {}

    def pintar(sel):
        for k, b in botoes.items():
            b.estilo(YELLOW if k == sel else CARD2, DARK if k == sel else TEXT)

    def clicou(inst):
        pintar(inst.text)
        ao_escolher(inst.text)

    for o in opcoes:
        b = RoundBtn(text=o, font_size=sp(tam))
        b.bind(on_release=clicou)
        botoes[o] = b
        row.add_widget(b)
    pintar(selecionado)
    return row


def toggle_row(opcoes, ativas, ao_mudar, altura=46, tam=13):
    """Botões liga/desliga (pelo menos um fica sempre ligado). opcoes = [(chave, rótulo)]."""
    ativas = set(ativas)
    row = BoxLayout(size_hint_y=None, height=dp(altura), spacing=dp(8))
    botoes = {}

    def pintar():
        for k, b in botoes.items():
            lig = k in ativas
            b.estilo(YELLOW if lig else CARD2, DARK if lig else TEXT)

    def clicou(inst):
        k = inst._chave
        if k in ativas:
            if len(ativas) == 1:
                return
            ativas.discard(k)
        else:
            ativas.add(k)
        pintar()
        ao_mudar([x for x, _ in opcoes if x in ativas])

    for k, rot in opcoes:
        b = RoundBtn(text=rot, font_size=sp(tam))
        b._chave = k
        b.bind(on_release=clicou)
        botoes[k] = b
        row.add_widget(b)
    pintar()
    return row


def tag(texto, vermelha=False):
    w = dp(26 + 7.4 * len(texto))
    c = Card(bg=RED if vermelha else CARD2, radius=15, size_hint=(None, None), size=(w, dp(30)))
    c.add_widget(txt(texto, 12, TEXT, True, "center"))
    return c


def campo_numero(valor, tam=22):
    return TextInput(text=str(valor), input_filter="float", multiline=False,
                     font_size=sp(tam), size_hint_y=None, height=dp(56),
                     padding=[dp(16), dp(14), dp(16), dp(10)],
                     background_normal="", background_active="", background_color=CARD2,
                     foreground_color=TEXT, cursor_color=YELLOW)


def campo_texto(valor="", hint="", tam=18, senha=False):
    return TextInput(text=valor, hint_text=hint, multiline=False, write_tab=False, password=senha,
                     font_size=sp(tam), size_hint_y=None, height=dp(52),
                     padding=[dp(14), dp(13), dp(14), dp(8)],
                     background_normal="", background_active="", background_color=CARD2,
                     foreground_color=TEXT, hint_text_color=MUTED, cursor_color=YELLOW)


# ----------------------------------------------------------------------------
# Gráfico de candles + MA9 / MA21 + RSI14
# ----------------------------------------------------------------------------
class CandleChart(Widget):
    N = 70

    def __init__(self, **kw):
        super().__init__(**kw)
        self.candles = []
        self.bind(pos=self.redraw, size=self.redraw)

    def set_data(self, candles):
        self.candles = candles
        self.redraw()

    def _t(self, s, x, y, cor=MUTED, tam=11, dir_="l"):
        lb = CoreLabel(text=s, font_size=sp(tam))
        lb.refresh()
        tx = lb.texture
        Color(*cor)
        px = x if dir_ == "l" else x - tx.width
        Rectangle(texture=tx, pos=(px, y - tx.height / 2), size=tx.size)

    def redraw(self, *a):
        self.canvas.clear()
        if not self.candles or self.width < 20:
            return
        full = self.candles
        closes = [c[3] for c in full]
        m9, m21, rs = serie_ma(closes, 9), serie_ma(closes, 21), serie_rsi(closes)
        vis = full[-self.N:]
        off = len(full) - len(vis)
        x0, y0 = self.pos
        w, h = self.size
        xa, xb = x0 + dp(58), x0 + w - dp(6)
        h_rsi = h * 0.2
        r0 = y0 + dp(8)
        p0 = r0 + h_rsi + dp(22)
        p1 = y0 + h - dp(8)
        lo = min(c[2] for c in vis)
        hi = max(c[1] for c in vis)
        if hi == lo:
            hi, lo = hi + 1, lo - 1
        pad = (hi - lo) * 0.05
        lo, hi = lo - pad, hi + pad

        def py(v):
            return p0 + (v - lo) / (hi - lo) * (p1 - p0)

        def ry(v):
            return r0 + v / 100.0 * h_rsi

        cw = (xb - xa) / len(vis)

        def cx(i):
            return xa + (i + 0.5) * cw

        with self.canvas:
            for k in range(5):
                v = lo + (hi - lo) * k / 4.0
                Color(1, 1, 1, 0.06)
                Line(points=[xa, py(v), xb, py(v)], width=1)
                self._t("%.1f" % v if hi > 100 else "%.5g" % v, xa - dp(6), py(v), MUTED, 11, "r")
            for i, c in enumerate(vis):
                o, hh, ll, cl = c[0], c[1], c[2], c[3]
                Color(*(GREEN if cl >= o else RED))
                Line(points=[cx(i), py(ll), cx(i), py(hh)], width=1)
                top, bot = py(max(o, cl)), py(min(o, cl))
                Rectangle(pos=(cx(i) - cw * 0.35, bot), size=(cw * 0.7, max(top - bot, 1)))
            for serie, cor in ((m9, YELLOW), (m21, BLUE)):
                pts = []
                for i in range(len(vis)):
                    v = serie[off + i]
                    if v:
                        pts += [cx(i), py(v)]
                if len(pts) >= 4:
                    Color(*cor)
                    Line(points=pts, width=1.3)
            self._t("MA9", xa + dp(4), p1 - dp(6), YELLOW, 12)
            self._t("MA21", xa + dp(44), p1 - dp(6), BLUE, 12)
            for nivel, cor in ((70, (0.97, 0.27, 0.35, 0.8)), (30, (0.07, 0.8, 0.52, 0.8))):
                Color(*cor)
                Line(points=[xa, ry(nivel), xb, ry(nivel)], width=1, dash_length=dp(3), dash_offset=dp(3))
                self._t(str(nivel), xa - dp(6), ry(nivel), MUTED, 11, "r")
            pts = []
            for i in range(len(vis)):
                v = rs[off + i]
                if v is not None:
                    pts += [cx(i), ry(v)]
            if len(pts) >= 4:
                Color(*YELLOW)
                Line(points=pts, width=1.3)
            self._t("RSI14", xa + dp(4), ry(100) + dp(10), YELLOW, 12)


# ----------------------------------------------------------------------------
# App
# ----------------------------------------------------------------------------
def estado_padrao():
    return {
        "modo": "SIM", "saldo": 10000.0, "posicoes": {},
        "pos_sim": {}, "pos_real": {}, "robo_ativo": False,
        "regras": [{"par": p, "valor": 12, "estrategias": list(ESTRATEGIAS)}
                   for p in ("BTCUSDT", "SOLUSDT", "BNBUSDT")],
        "execucoes": [], "ia": [], "ia_sim": [], "ultimo": {},
        "cfg": {"key": "", "secret": "", "testnet": True},
    }


class BotApp(App):
    title = "Bot Binance"

    # ---------- ciclo de vida ----------
    def build(self):
        Window.clearcolor = BG
        self.arquivo = os.path.join(self.user_data_dir, "estado_app.json")
        self.lock = threading.RLock()
        self.estado = self.carregar()
        self.aba, self.sub = "markets", "robo"
        self.par, self.tf = "BTCUSDT", "15m"
        self.valor_ordem = "100"
        self.preco_atual = None
        self.saldo_real = None
        self.lbl_saldo = None
        self.sw_robo = None
        self.lbl_robo_sub = None
        self.msg_robo = ""
        self._silencio = False
        self._ev = None
        self._thr = None
        self._wake = None
        self._cli = None
        self._ult_exec = []
        self.bt = {"pares": "BTCUSDT", "estr": list(ESTRATEGIAS), "dias": "7d",
                   "int": "15m", "valor": "100"}

        if self.estado["robo_ativo"] and self.estado["modo"] == "REAL":
            self.estado["robo_ativo"] = False   # nunca retoma ordens reais sozinho

        root = BoxLayout(orientation="vertical")
        root.add_widget(self.montar_barra())
        self.conteudo = BoxLayout(orientation="vertical")
        root.add_widget(self.conteudo)
        root.add_widget(self.montar_nav())
        self.abrir_aba("markets")
        if self.estado["robo_ativo"]:
            self.iniciar_robo()
        return root

    def on_stop(self):
        self.salvar()

    def on_pause(self):
        self.salvar()
        return True

    def carregar(self):
        d = estado_padrao()
        try:
            with open(self.arquivo, "r") as f:
                d.update(json.load(f))
        except Exception:
            pass
        d["cfg"] = dict(estado_padrao()["cfg"], **d.get("cfg", {}))
        for r in d["regras"]:
            r.setdefault("estrategias", list(ESTRATEGIAS))
        return d

    def salvar(self):
        try:
            with self.lock:
                with open(self.arquivo, "w") as f:
                    json.dump(self.estado, f)
        except Exception as e:
            print("Erro ao salvar:", e)

    def cliente(self):
        c = self.estado["cfg"]
        if not c["key"] or not c["secret"]:
            return None
        chave = (c["key"], c["secret"], c["testnet"])
        if not self._cli or self._cli[0] != chave:
            self._cli = (chave, Binance(c["key"], c["secret"], c["testnet"]))
        return self._cli[1]

    def hist(self, modo=None):
        modo = modo or self.estado["modo"]
        return self.estado["ia"] if modo == "REAL" else self.estado["ia_sim"]

    # ---------- barra superior ----------
    def montar_barra(self):
        self.barra = Card(bg=CARD, radius=0, size_hint_y=None, height=dp(72),
                          padding=[dp(16), dp(8)], spacing=dp(8))
        dot_box = AnchorLayout(anchor_y="center", size_hint_x=None, width=dp(20))
        self.dot = Card(bg=GREEN, radius=6, size_hint=(None, None), size=(dp(12), dp(12)))
        dot_box.add_widget(self.dot)
        info = BoxLayout(orientation="vertical", padding=[0, dp(10)])
        self.lbl_modo = txt("SIMULAÇÃO", 17, TEXT, True)
        self.lbl_modo_sub = txt("", 14, MUTED)
        info.add_widget(self.lbl_modo)
        info.add_widget(self.lbl_modo_sub)
        btn_box = AnchorLayout(anchor_x="right", anchor_y="center", size_hint_x=None, width=dp(150))
        self.btn_modo = RoundBtn(bg=RED, text="Ir p/ Real", font_size=sp(15),
                                 size_hint=(None, None), size=(dp(140), dp(46)))
        self.btn_modo.bind(on_release=self.clicou_modo)
        btn_box.add_widget(self.btn_modo)
        for w in (dot_box, info, btn_box):
            self.barra.add_widget(w)
        self.atualizar_barra()
        return self.barra

    def atualizar_barra(self):
        est = self.estado
        if est["modo"] == "SIM":
            self.lbl_modo.text = "SIMULAÇÃO"
            self.lbl_modo_sub.text = "$%.2f USDT fictício" % est["saldo"]
            self.dot._c.rgba = GREEN
            self.btn_modo.text = "Ir p/ Real"
            self.btn_modo.estilo(RED, TEXT)
        else:
            rede = "Testnet" if est["cfg"]["testnet"] else "Conta real"
            self.lbl_modo.text = "REAL"
            self.lbl_modo_sub.text = ("$%.2f USDT · %s" % (self.saldo_real, rede)
                                      if self.saldo_real is not None else rede)
            self.dot._c.rgba = RED
            self.btn_modo.text = "Ir p/ Simulação"
            self.btn_modo.estilo(CARD2, TEXT)
        self.atualizar_saldo_label()

    def atualizar_saldo_real(self):
        ex = self.cliente()
        if self.estado["modo"] != "REAL" or not ex:
            return

        def job():
            try:
                v = ex.saldo_livre().get("USDT", 0.0)
            except Exception as e:
                print("saldo:", e)
                return

            def ui(dt):
                self.saldo_real = v
                self.atualizar_barra()

            Clock.schedule_once(ui)

        threading.Thread(target=job, daemon=True).start()

    def clicou_modo(self, *a):
        if self.estado["modo"] == "SIM":
            self.confirmar_real()
        else:
            self.trocar_modo("SIM")

    def trocar_modo(self, modo):
        self.desligar_robo()
        self.estado["modo"] = modo
        self.salvar()
        self.atualizar_barra()
        if modo == "REAL":
            if not self.cliente():
                self.aviso("Configure as chaves da Binance em Ajustes (aba Markets).")
            else:
                self.atualizar_saldo_real()

    def confirmar_real(self):
        box = BoxLayout(orientation="vertical", padding=dp(20), spacing=dp(12))
        box.add_widget(txt("!", 38, RED, True, "center", altura=48))
        box.add_widget(txt("Ativar Modo Real?", 22, TEXT, True, "center", altura=36))
        box.add_widget(txt("Ordens em modo real usarão as chaves da Binance configuradas em "
                           "Ajustes. Por padrão, o app opera na Testnet. Confirme para continuar.",
                           14, MUTED, False, "center"))
        linha = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.92, None),
                    height=dp(360), background="", background_color=CARD, auto_dismiss=False)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", font_size=sp(15), radius=14)
        b2 = RoundBtn(bg=RED, text="Ativar Real", font_size=sp(15), radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def ativar(*_):
            pop.dismiss()
            self.trocar_modo("REAL")

        b2.bind(on_release=ativar)
        linha.add_widget(b1)
        linha.add_widget(b2)
        box.add_widget(linha)
        pop.open()

    def aviso(self, msg):
        pop = Popup(title="", separator_height=0, size_hint=(0.85, None), height=dp(140),
                    content=txt(msg, 15, TEXT, False, "center"), background="", background_color=CARD2)
        pop.open()
        Clock.schedule_once(lambda dt: pop.dismiss(), 2.8)

    def aviso_thread(self, msg):
        Clock.schedule_once(lambda dt: self.aviso(msg))

    def ajustes(self):
        c = self.estado["cfg"]
        box = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        box.add_widget(txt("Ajustes · Binance", 20, TEXT, True, altura=32))
        box.add_widget(txt("API Key", 13, MUTED, altura=18))
        k = campo_texto(c["key"], "API Key", 15, senha=True)
        box.add_widget(k)
        box.add_widget(txt("Secret Key", 13, MUTED, altura=18))
        s = campo_texto(c["secret"], "Secret Key", 15, senha=True)
        box.add_widget(s)
        linha = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(10))
        linha.add_widget(txt("Usar Testnet (recomendado)", 15, TEXT))
        sw = Switch(active=c["testnet"], size_hint=(None, None), size=(dp(90), dp(46)))
        linha.add_widget(sw)
        box.add_widget(linha)
        botoes = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.94, None),
                    height=dp(470), background="", background_color=CARD)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", radius=14)
        b2 = RoundBtn(bg=YELLOW, fg=DARK, text="Salvar", radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def salvar(*_):
            self.estado["cfg"] = {"key": k.text.strip(), "secret": s.text.strip(),
                                  "testnet": bool(sw.active)}
            self.saldo_real = None
            self.salvar()
            pop.dismiss()
            self.atualizar_barra()
            self.atualizar_saldo_real()

        b2.bind(on_release=salvar)
        botoes.add_widget(b1)
        botoes.add_widget(b2)
        box.add_widget(botoes)
        pop.open()

    # ---------- navegação ----------
    def montar_nav(self):
        nav = Card(bg=NAV, radius=0, size_hint_y=None, height=dp(64), padding=dp(4))
        self.nav_btns = {}
        for chave, rotulo in (("markets", "MARKETS"), ("trade", "TRADE"), ("bot", "BOT · IA")):
            b = RoundBtn(bg=CLEAR, fg=MUTED, text=rotulo, font_size=sp(13), radius=0)
            b.bind(on_release=lambda inst, k=chave: self.abrir_aba(k))
            self.nav_btns[chave] = b
            nav.add_widget(b)
        return nav

    def abrir_aba(self, nome):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        self.aba = nome
        self.lbl_saldo = None
        self.sw_robo = None
        self.lbl_robo_sub = None
        for k, b in self.nav_btns.items():
            b.estilo(CLEAR, YELLOW if k == nome else MUTED)
        self.conteudo.clear_widgets()
        if nome == "markets":
            self.tela_markets()
        elif nome == "trade":
            self.tela_trade()
            self.atualizar_saldo_real()
        else:
            self.tela_bot()

    # ---------- MARKETS ----------
    def tela_markets(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(10))
        topo = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        topo.add_widget(txt("Mercado Spot", 30, TEXT, True))
        aj = RoundBtn(bg=CARD2, text="Ajustes", font_size=sp(14), size_hint=(None, None),
                      size=(dp(96), dp(42)))
        aj.bind(on_release=lambda *_: self.ajustes())
        topo.add_widget(aj)
        box.add_widget(topo)
        box.add_widget(txt("Preços em tempo real · Binance", 14, MUTED, altura=22))

        busca = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.inp_busca = campo_texto("", "Digite qualquer par (ex.: PEPEUSDT)", 16)
        self.inp_busca.bind(on_text_validate=lambda *_: self.abrir_busca())
        ok = RoundBtn(bg=YELLOW, fg=DARK, text="Abrir", font_size=sp(15), size_hint_x=None,
                      width=dp(86), radius=14)
        ok.bind(on_release=lambda *_: self.abrir_busca())
        busca.add_widget(self.inp_busca)
        busca.add_widget(ok)
        box.add_widget(busca)

        lista = BoxLayout(orientation="vertical", spacing=dp(12), padding=[0, dp(6)])
        self.linhas = {}
        for p in PARES:
            card = ClickCard(cb=lambda c, s=p: self.ir_trade(s), bg=CARD, radius=16,
                             size_hint_y=None, height=dp(84), padding=dp(16))
            esq = BoxLayout(orientation="vertical")
            esq.add_widget(txt(par_fmt(p), 18, TEXT, True))
            lv = txt("Vol 24h: —", 13, MUTED)
            esq.add_widget(lv)
            dir_ = BoxLayout(orientation="vertical")
            lp = txt("—", 18, TEXT, True, "right")
            dir_.add_widget(lp)
            ancora = AnchorLayout(anchor_x="right", anchor_y="center")
            pill = Card(bg=CARD2, radius=10, size_hint=(None, None), size=(dp(90), dp(28)))
            lc = txt("", 13, TEXT, True, "center")
            pill.add_widget(lc)
            ancora.add_widget(pill)
            dir_.add_widget(ancora)
            card.add_widget(esq)
            card.add_widget(dir_)
            lista.add_widget(card)
            self.linhas[p] = (lp, lv, pill, lc)
        box.add_widget(rolavel(lista))
        self.conteudo.add_widget(box)
        self.atualizar_markets()
        self._ev = Clock.schedule_interval(lambda dt: self.atualizar_markets(), 6)

    def abrir_busca(self):
        sym = normalizar_par(self.inp_busca.text)
        if sym:
            self.ir_trade(sym)

    def atualizar_markets(self):
        def job():
            try:
                dados = buscar_tickers(PARES)
            except Exception as e:
                print("markets:", e)
                return
            Clock.schedule_once(lambda dt: self.pintar_markets(dados))

        threading.Thread(target=job, daemon=True).start()

    def pintar_markets(self, dados):
        if self.aba != "markets":
            return
        for d in dados:
            ref = self.linhas.get(d["symbol"])
            if not ref:
                continue
            lp, lv, pill, lc = ref
            var = float(d["priceChangePercent"])
            lp.text = fmt_preco(float(d["lastPrice"]))
            lv.text = "Vol 24h: " + fmt_vol(float(d["quoteVolume"]))
            lc.text = "%s%.2f%%" % ("+" if var >= 0 else "", var)
            pill._c.rgba = GREEN if var >= 0 else RED

    def ir_trade(self, simbolo):
        self.par = simbolo
        self.preco_atual = None
        self.abrir_aba("trade")

    # ---------- TRADE ----------
    def tela_trade(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(12))
        linha = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.inp_par = campo_texto(par_fmt(self.par), "Digite qualquer par (ex.: SOLUSDT)", 20)
        self.inp_par.bind(on_text_validate=lambda *_: self.trocou_par(self.inp_par.text))
        ir = RoundBtn(bg=YELLOW, fg=DARK, text="Ir", font_size=sp(16), size_hint_x=None,
                      width=dp(72), radius=14)
        ir.bind(on_release=lambda *_: self.trocou_par(self.inp_par.text))
        linha.add_widget(self.inp_par)
        linha.add_widget(ir)
        box.add_widget(linha)

        info = BoxLayout(size_hint_y=None, height=dp(52), spacing=dp(8))
        self.lbl_par = txt(par_fmt(self.par), 22, TEXT, True)
        info.add_widget(self.lbl_par)
        dir_ = BoxLayout(orientation="vertical")
        self.lbl_preco = txt("—", 24, TEXT, True, "right")
        self.lbl_var = txt("", 14, GREEN, True, "right")
        dir_.add_widget(self.lbl_preco)
        dir_.add_widget(self.lbl_var)
        info.add_widget(dir_)
        box.add_widget(info)

        box.add_widget(chip_row(TF_LISTA, self.tf, self.trocou_tf, altura=46, tam=14))

        cg = Card(bg=CARD, radius=20, size_hint_y=None, height=dp(360), padding=dp(6))
        self.chart = CandleChart()
        cg.add_widget(self.chart)
        box.add_widget(cg)

        ordem = Card(bg=CARD, radius=20, orientation="vertical", padding=dp(20), spacing=dp(12))
        ordem.add_widget(txt("Nova Ordem · Market", 22, TEXT, True, altura=32))
        ordem.add_widget(txt("Valor (USDT)", 15, MUTED, altura=22))
        self.inp_valor = campo_numero(self.valor_ordem)
        self.inp_valor.bind(text=lambda w, v: setattr(self, "valor_ordem", v))
        ordem.add_widget(self.inp_valor)
        rapidos = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        for v in (25, 50, 100, 250, 500):
            b = RoundBtn(bg=CARD2, text="$%d" % v, font_size=sp(14))
            b.bind(on_release=lambda inst, x=v: setattr(self.inp_valor, "text", str(x)))
            rapidos.add_widget(b)
        ordem.add_widget(rapidos)
        lin = BoxLayout(size_hint_y=None, height=dp(56), spacing=dp(12))
        bc = RoundBtn(bg=GREEN, fg=DARK, text="COMPRAR", font_size=sp(17), radius=16)
        bv = RoundBtn(bg=CARD2, text="VENDER", font_size=sp(17), radius=16)
        bc.bind(on_release=lambda *_: self.enviar_ordem("BUY"))
        bv.bind(on_release=lambda *_: self.enviar_ordem("SELL"))
        lin.add_widget(bc)
        lin.add_widget(bv)
        ordem.add_widget(lin)
        self.lbl_saldo = txt("", 14, MUTED, False, "center", altura=24)
        ordem.add_widget(self.lbl_saldo)
        auto_altura(ordem)
        box.add_widget(ordem)

        self.conteudo.add_widget(rolavel(box))
        self.atualizar_saldo_label()
        self.carregar_trade()
        self._ev = Clock.schedule_interval(lambda dt: self.carregar_trade(), 6)

    def atualizar_saldo_label(self):
        if self.lbl_saldo is None:
            return
        if self.estado["modo"] == "SIM":
            self.lbl_saldo.text = "Saldo virtual: $%.2f USDT" % self.estado["saldo"]
        else:
            self.lbl_saldo.text = "Modo real · ordens a mercado na Binance"

    def trocou_par(self, texto):
        sym = normalizar_par(texto)
        if not sym:
            return
        self.par = sym
        self.preco_atual = None
        self.inp_par.text = par_fmt(sym)
        self.lbl_par.text = par_fmt(sym)
        self.lbl_preco.text = "—"
        self.lbl_var.text = ""
        self.carregar_trade()

    def trocou_tf(self, tf):
        self.tf = tf
        self.carregar_trade()

    def carregar_trade(self):
        par, tf = self.par, TF_API[self.tf]

        def job():
            try:
                candles = buscar_klines(par, tf, 100)
                tk = http_json("/api/v3/ticker/24hr", {"symbol": par})
            except Exception as e:
                print("trade:", e)

                def erro(dt):
                    if self.aba == "trade" and par == self.par:
                        self.lbl_var.text = "par inválido ou sem conexão"
                        self.lbl_var.color = RED

                Clock.schedule_once(erro)
                return

            def ui(dt):
                if self.aba != "trade" or par != self.par:
                    return
                self.chart.set_data(candles)
                self.preco_atual = candles[-1][3]
                var = float(tk["priceChangePercent"])
                self.lbl_preco.text = fmt_preco(float(tk["lastPrice"]))
                self.lbl_var.text = "%s%.2f%%" % ("+" if var >= 0 else "", var)
                self.lbl_var.color = GREEN if var >= 0 else RED

            Clock.schedule_once(ui)

        threading.Thread(target=job, daemon=True).start()

    def enviar_ordem(self, lado):
        est = self.estado
        try:
            valor = float(self.valor_ordem or 0)
        except ValueError:
            valor = 0
        if valor <= 0 or not self.preco_atual:
            return self.aviso("Informe um valor válido e aguarde o preço carregar.")
        sym, preco = self.par, self.preco_atual
        if est["modo"] == "SIM":
            with self.lock:
                if lado == "BUY":
                    if valor > est["saldo"]:
                        return self.aviso("Saldo virtual insuficiente.")
                    est["saldo"] -= valor
                    est["posicoes"][sym] = est["posicoes"].get(sym, 0) + valor / preco
                else:
                    qtd = min(valor / preco, est["posicoes"].get(sym, 0))
                    if qtd <= 0:
                        return self.aviso("Sem posição para vender neste par.")
                    est["saldo"] += qtd * preco
                    est["posicoes"][sym] -= qtd
                self.registrar(lado, sym, "manual", "$%.2f" % preco, True)
            self.salvar()
            self.atualizar_barra()
            return
        ex = self.cliente()
        if not ex:
            return self.aviso("Configure as chaves da Binance em Ajustes.")

        def job():
            try:
                if lado == "BUY":
                    r = ex.comprar(sym, valor)
                else:
                    livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
                    r = ex.vender(sym, min(valor / preco, livre))
                with self.lock:
                    self.registrar(lado, sym, "manual", "$%.2f" % (r["preco"] or preco), True)
                self.salvar()
                self.aviso_thread("%s executada em %s." % ("Compra" if lado == "BUY" else "Venda", par_fmt(sym)))
                Clock.schedule_once(lambda dt: self.atualizar_saldo_real())
            except Exception as e:
                self.aviso_thread("Erro na ordem: %s" % str(e)[:160])

        threading.Thread(target=job, daemon=True).start()

    def registrar(self, lado, sym, motivo, direita, positivo):
        with self.lock:
            self.estado["execucoes"].insert(0, {
                "t": "%s %s" % (lado, sym), "s": motivo, "d": direita, "ok": bool(positivo)})
            del self.estado["execucoes"][50:]

    # ---------- BOT · IA ----------
    def tela_bot(self):
        box = BoxLayout(orientation="vertical", padding=[dp(14), dp(10)], spacing=dp(12))
        sw = Card(bg=CARD, radius=18, size_hint_y=None, height=dp(58), padding=dp(5), spacing=dp(4))
        self.sub_btns = {}
        for chave, rotulo in (("robo", "Robô"), ("ia", "Assistente IA"), ("backtest", "Backtest")):
            b = RoundBtn(bg=CLEAR, fg=TEXT, text=rotulo, font_size=sp(15), radius=14)
            b.bind(on_release=lambda inst, k=chave: self.mostrar_sub(k))
            self.sub_btns[chave] = b
            sw.add_widget(b)
        box.add_widget(sw)
        self.sub_area = BoxLayout(orientation="vertical")
        box.add_widget(self.sub_area)
        self.conteudo.add_widget(box)
        self.mostrar_sub(self.sub)

    def mostrar_sub(self, chave):
        self.sub = chave
        self.sw_robo = None
        self.lbl_robo_sub = None
        for k, b in self.sub_btns.items():
            b.estilo(YELLOW if k == chave else CLEAR, DARK if k == chave else TEXT)
        self.sub_area.clear_widgets()
        if chave == "robo":
            self.sub_area.add_widget(self.sub_robo())
        elif chave == "ia":
            self.sub_area.add_widget(self.sub_ia())
        else:
            self.sub_area.add_widget(self.sub_backtest())

    # --- Robô ---
    def sub_robo(self):
        est = self.estado
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])

        c1 = Card(bg=CARD, radius=18, size_hint_y=None, height=dp(86), padding=dp(18))
        t = BoxLayout(orientation="vertical")
        t.add_widget(txt("Robô 24/7", 22, TEXT, True))
        self.lbl_robo_sub = txt("", 14, MUTED)
        t.add_widget(self.lbl_robo_sub)
        c1.add_widget(t)
        self.sw_robo = Switch(active=est["robo_ativo"], size_hint=(None, None), size=(dp(90), dp(46)))
        self.sw_robo.pos_hint = {"center_y": 0.5}
        self.sw_robo.bind(active=self.toggle_robo)
        c1.add_widget(self.sw_robo)
        box.add_widget(c1)
        self.atualizar_sub_robo()

        topo = BoxLayout(size_hint_y=None, height=dp(48))
        topo.add_widget(txt("Regras (%d)" % len(est["regras"]), 22, TEXT, True))
        bn = RoundBtn(bg=YELLOW, fg=DARK, text="+  Nova", font_size=sp(16),
                      size_hint=(None, None), size=(dp(120), dp(46)))
        bn.bind(on_release=lambda *_: self.nova_regra())
        topo.add_widget(bn)
        box.add_widget(topo)

        for i, r in enumerate(est["regras"]):
            card = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                        height=dp(168), padding=dp(16), spacing=dp(8))
            linha = BoxLayout(size_hint_y=None, height=dp(52))
            tt = BoxLayout(orientation="vertical")
            tt.add_widget(txt("Trailing Escada", 20, TEXT, True))
            tt.add_widget(txt("%s · $%s USDT/ordem" % (r["par"], r["valor"]), 15, MUTED))
            linha.add_widget(tt)
            rm = RoundBtn(bg=CLEAR, fg=RED, text="X", font_size=sp(20),
                          size_hint=(None, None), size=(dp(44), dp(44)))
            rm.bind(on_release=lambda inst, idx=i: self.remover_regra(idx))
            linha.add_widget(rm)
            card.add_widget(linha)
            l1 = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
            for e in r["estrategias"]:
                l1.add_widget(tag(ROTULO[e]))
            l1.add_widget(Widget())
            card.add_widget(l1)
            l2 = BoxLayout(size_hint_y=None, height=dp(30), spacing=dp(8))
            l2.add_widget(tag("Stop -%s%%" % STOP_PCT, True))
            l2.add_widget(tag("Trailing escada"))
            l2.add_widget(Widget())
            card.add_widget(l2)
            box.add_widget(card)

        box.add_widget(txt("Últimas execuções", 22, TEXT, True, altura=40))
        if not est["execucoes"]:
            box.add_widget(txt("Nenhuma execução ainda.", 15, MUTED, altura=30))
        for e in est["execucoes"][:20]:
            c = Card(bg=CARD, radius=16, size_hint_y=None, height=dp(70), padding=[dp(18), dp(10)])
            tt = BoxLayout(orientation="vertical")
            tt.add_widget(txt(e["t"], 18, TEXT, True))
            tt.add_widget(txt(e["s"], 14, MUTED))
            c.add_widget(tt)
            c.add_widget(txt(e["d"], 19, GREEN if e["ok"] else RED, True, "right"))
            box.add_widget(c)
        self._ult_exec = est["execucoes"][:1]
        return rolavel(box)

    def atualizar_sub_robo(self):
        if self.lbl_robo_sub is None:
            return
        if self.msg_robo:
            self.lbl_robo_sub.text = self.msg_robo
        elif self.estado["robo_ativo"]:
            self.lbl_robo_sub.text = "Operando com trailing-stop escada · %s" % (
                "simulação" if self.estado["modo"] == "SIM" else "REAL")
        else:
            self.lbl_robo_sub.text = "Desligado"

    def nova_regra(self):
        escolha = list(ESTRATEGIAS)
        box = BoxLayout(orientation="vertical", padding=dp(18), spacing=dp(10))
        box.add_widget(txt("Nova regra · Trailing Escada", 19, TEXT, True, altura=32))
        box.add_widget(txt("Par (qualquer um da Binance)", 13, MUTED, altura=18))
        inp = campo_texto("", "ex.: BTCUSDT, PEPEUSDT, ETHBTC", 18)
        box.add_widget(inp)
        box.add_widget(txt("Valor por ordem", 13, MUTED, altura=18))
        val = campo_numero("12", 20)
        box.add_widget(val)
        box.add_widget(txt("Estratégias de entrada", 13, MUTED, altura=18))
        box.add_widget(toggle_row([(k, ROTULO[k]) for k in ESTRATEGIAS], escolha,
                                  lambda lst: escolha.__setitem__(slice(None), lst), altura=44, tam=12))
        msg = txt("", 13, RED, altura=20)
        box.add_widget(msg)
        linha = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(12))
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.94, None),
                    height=dp(520), background="", background_color=CARD)
        b1 = RoundBtn(bg=CARD2, text="Cancelar", radius=14)
        b2 = RoundBtn(bg=YELLOW, fg=DARK, text="Salvar", radius=14)
        b1.bind(on_release=lambda *_: pop.dismiss())

        def salvar(*_):
            sym = normalizar_par(inp.text)
            try:
                v = float(val.text)
            except ValueError:
                v = 0
            if not sym or v <= 0:
                msg.text = "Informe um par e um valor válidos."
                return
            if any(r["par"] == sym for r in self.estado["regras"]):
                msg.text = "Já existe uma regra para %s." % sym
                return
            msg.text = "Validando %s..." % sym
            msg.color = MUTED

            def job():
                try:
                    validar_par(sym)
                except Exception:
                    def erro(dt):
                        msg.color = RED
                        msg.text = "Par inválido na Binance: %s" % sym

                    Clock.schedule_once(erro)
                    return

                def ok(dt):
                    with self.lock:
                        self.estado["regras"].append({
                            "par": sym, "valor": int(v) if v == int(v) else v,
                            "estrategias": list(escolha)})
                    self.salvar()
                    pop.dismiss()
                    self.mostrar_sub("robo")

                Clock.schedule_once(ok)

            threading.Thread(target=job, daemon=True).start()

        b2.bind(on_release=salvar)
        linha.add_widget(b1)
        linha.add_widget(b2)
        box.add_widget(linha)
        pop.open()

    def remover_regra(self, idx):
        with self.lock:
            if 0 <= idx < len(self.estado["regras"]):
                del self.estado["regras"][idx]
        self.salvar()
        self.mostrar_sub("robo")

    # --- motor do robô (mesma lógica do seu bot, 1m, ciclo de 10 s) ---
    def toggle_robo(self, sw, valor):
        if self._silencio:
            return
        self.estado["robo_ativo"] = valor
        self.msg_robo = ""
        self.salvar()
        self.atualizar_sub_robo()
        if valor:
            self.iniciar_robo()
        else:
            self.parar_robo()

    def desligar_robo(self):
        if not self.estado["robo_ativo"]:
            return
        self.estado["robo_ativo"] = False
        self.parar_robo()
        if self.sw_robo is not None:
            self._silencio = True
            self.sw_robo.active = False
            self._silencio = False
        self.atualizar_sub_robo()

    def iniciar_robo(self):
        if self._thr and self._thr.is_alive():
            return
        self._wake = adquirir_wake_lock()
        iniciar_foreground_service()
        self._thr = threading.Thread(target=self.loop_robo, daemon=True)
        self._thr.start()

    def parar_robo(self):
        parar_foreground_service()
        liberar_wake_lock(self._wake)
        self._wake = None

    def loop_robo(self):
        while self.estado["robo_ativo"]:
            modo = self.estado["modo"]
            pos_d = self.estado["pos_sim"] if modo == "SIM" else self.estado["pos_real"]
            with self.lock:
                regras = [dict(r) for r in self.estado["regras"]]
                # posição aberta sem regra (regra apagada) continua sendo gerenciada, só saída
                for s in list(pos_d):
                    if not any(r["par"] == s for r in regras):
                        regras.append({"par": s, "valor": 0, "estrategias": []})
            erro = ""
            for regra in regras:
                if not self.estado["robo_ativo"]:
                    break
                try:
                    self.processar(regra, modo)
                except Exception as e:
                    erro = "[%s] %s" % (regra["par"], str(e)[:120])
            self.msg_robo = erro
            Clock.schedule_once(lambda dt: self.refrescar())
            for _ in range(10):
                if not self.estado["robo_ativo"]:
                    break
                time.sleep(1)

    def refrescar(self):
        self.atualizar_barra()
        self.atualizar_sub_robo()
        if self.aba == "bot" and self.sub == "robo" and self.estado["execucoes"][:1] != self._ult_exec:
            self.mostrar_sub("robo")

    def processar(self, regra, modo):
        est = self.estado
        sym = regra["par"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        closes = [c[3] for c in buscar_klines(sym, "1m", 50)]
        if len(closes) < 30:
            return
        preco = closes[-1]
        ex, livre = None, {}
        if modo == "REAL":
            ex = self.cliente()
            if not ex:
                raise Exception("configure as chaves em Ajustes")
            livre = ex.saldo_livre()
        pos = pos_d.get(sym)

        if pos:
            if modo == "REAL":
                base = ex.info(sym)["base"]
                if livre.get(base, 0.0) < 0.0001:
                    with self.lock:
                        pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posição encerrada na Binance", "—", True)
                    self.salvar()
                    return
            with self.lock:
                pos["topo"] = max(pos["topo"], preco)
            motivo = decidir_saida(pos["entrada"], pos["topo"], preco, closes)
            if motivo:
                self.fechar(sym, pos, preco, motivo, closes, modo, ex, livre)
            return

        # entrada
        a = analisar(closes)
        tipo = escolher_entrada(a, regra.get("estrategias", ESTRATEGIAS))
        if not tipo:
            return
        chave = "%s:%s" % (modo, sym)
        if time.time() - est["ultimo"].get(chave, 0) <= COOLDOWN:
            return
        hist = self.hist(modo)
        prob = prever_probabilidade(hist, a["rsi"], a["ma9"], a["ma21"], a["vol"], preco,
                                    tipo_prioritario(a))
        if not (len(hist) < IA_MIN_HIST or prob > PROB_MIN):
            return
        valor = float(regra["valor"])
        if valor <= 0:
            return
        if modo == "SIM":
            with self.lock:
                if est["saldo"] < valor:
                    return
                est["saldo"] -= valor
                preco_exec, qtd = preco, valor / preco
        else:
            quote = ex.info(sym)["quote"]
            if livre.get(quote, 0.0) < valor:
                return
            r = ex.comprar(sym, valor)
            preco_exec, qtd = r["preco"] or preco, r["qtd"]
        with self.lock:
            pos_d[sym] = {
                "entrada": preco_exec, "topo": preco_exec, "t": time.time(), "qtd": qtd,
                "valor": valor, "tipo": tipo, "rsi": a["rsi"], "ma9": a["ma9"], "ma21": a["ma21"],
                "vol": a["vol"], "dist": a["dist"], "p9": preco > a["ma9"], "p21": preco > a["ma21"]}
            est["ultimo"][chave] = time.time()
            self.registrar("BUY", sym, tipo, "$%.2f" % preco_exec, True)
        self.salvar()

    def fechar(self, sym, pos, preco, motivo, closes, modo, ex, livre):
        est = self.estado
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        preco_saida = preco
        if modo == "REAL":
            try:
                base = ex.info(sym)["base"]
                r = ex.vender(sym, min(pos["qtd"], livre.get(base, 0.0)))
                preco_saida = r["preco"] or preco
            except Exception as e:
                if "-1013" in str(e) or "Invalid quantity" in str(e):
                    with self.lock:
                        pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posição já encerrada na Binance", "—", True)
                    self.salvar()
                    return
                raise
        lucro = (preco_saida / pos["entrada"] - 1) * 100
        lucro_max = (pos["topo"] / pos["entrada"] - 1) * 100
        a = analisar(closes) or {"rsi": 0, "ma9": 0, "ma21": 0}
        with self.lock:
            if modo == "SIM":
                est["saldo"] += pos["qtd"] * preco_saida
            self.hist(modo).append({
                "tipo_entrada": pos["tipo"], "rsi": pos["rsi"], "volatilidade": pos["vol"],
                "distancia_ma": pos["dist"], "posicao_preco_ma9": pos["p9"],
                "posicao_preco_ma21": pos["p21"], "forca_tendencia": abs(pos["dist"]),
                "resultado": lucro})
            del self.hist(modo)[:-500]
            pos_d.pop(sym, None)
            self.registrar("SELL", sym, motivo, "%+.2f%%" % lucro, lucro >= 0)
        self.salvar()
        self.salvar_relatorio(sym, pos, modo, lucro, preco_saida, lucro_max, a)

    def salvar_relatorio(self, sym, pos, modo, lucro, preco_saida, lucro_max, a):
        try:
            with open(ARQUIVO_RELATORIO, "a") as f:
                f.write("==============================\n")
                f.write("Par: %s  (%s)\nData: %s\n" % (sym, "SIMULAÇÃO" if modo == "SIM" else "REAL",
                                                       datetime.now()))
                f.write("Estratégia: %s\n--- ENTRADA ---\n" % pos["tipo"])
                f.write("Preço: %.4f\nRSI: %.2f\nMA9: %.4f\nMA21: %.4f\n" % (
                    pos["entrada"], pos["rsi"], pos["ma9"], pos["ma21"]))
                f.write("--- SAÍDA ---\nPreço: %.4f\nRSI: %.2f\nMA9: %.4f\nMA21: %.4f\n" % (
                    preco_saida, a["rsi"], a["ma9"], a["ma21"]))
                f.write("--- RESULTADO ---\nLucro: %.2f%%\nLucro Máximo: %.2f%%\n"
                        "Tempo operação: %ds\n\n\n" % (lucro, lucro_max, int(time.time() - pos["t"])))
        except Exception as e:
            print("Erro ao salvar relatório:", e)

    # --- Assistente IA ---
    def sub_ia(self):
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])
        c = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                 padding=dp(16), spacing=dp(12))
        c.add_widget(txt("Analisar par", 17, TEXT, True, altura=26))
        self.ia_inp = campo_texto("BTCUSDT", "Digite qualquer par (ex.: SOLUSDT)", 18)
        c.add_widget(self.ia_inp)
        bg = RoundBtn(bg=YELLOW, fg=DARK, text="Gerar sinal com IA", font_size=sp(17),
                      size_hint_y=None, height=dp(54), radius=16)
        bg.bind(on_release=lambda *_: self.gerar_sinal())
        c.add_widget(bg)
        auto_altura(c)
        box.add_widget(c)
        self.ia_out = BoxLayout(orientation="vertical", size_hint_y=None)
        auto_altura(self.ia_out)
        box.add_widget(self.ia_out)
        return rolavel(box)

    def mostrar_card(self, destino, msg):
        destino.clear_widgets()
        card = Card(bg=CARD, radius=18, orientation="vertical", padding=dp(18))
        card.add_widget(txt_auto(msg, 15, TEXT))
        auto_altura(card)
        destino.add_widget(card)

    def gerar_sinal(self):
        sym = normalizar_par(self.ia_inp.text)
        if not sym:
            return
        self.ia_inp.text = sym
        self.mostrar_card(self.ia_out, "Analisando %s..." % sym)
        modo = self.estado["modo"]

        def job():
            try:
                closes = [c[3] for c in buscar_klines(sym, "1m", 50)]
                a = analisar(closes)
                if a is None:
                    raise Exception("poucos candles")
            except Exception as e:
                msg = "Não consegui analisar %s (par inválido ou sem conexão)." % sym
                Clock.schedule_once(lambda dt: self.mostrar_card(self.ia_out, msg))
                return
            hist = self.hist(modo)
            tipo = tipo_prioritario(a)
            prob = prever_probabilidade(hist, a["rsi"], a["ma9"], a["ma21"], a["vol"], a["preco"], tipo)
            liberado = len(hist) < IA_MIN_HIST or prob > PROB_MIN
            ent = escolher_entrada(a, ESTRATEGIAS)
            sinal = ("COMPRA (%s)" % ent) if ent and liberado else (
                "Gatilho %s, mas a IA bloqueou" % ent if ent else "AGUARDAR")
            msg = ("%s · 1m\nPreço: %s\nRSI14: %.1f (anterior %.1f)\nMA9: %.4f · MA21: %.4f\n"
                   "Tendência: %s · volatilidade %.2f%%\n"
                   "Pullback: %s · Continuidade: %s · Rompimento: %s\n"
                   "Prob. IA: %.2f (%d trades no histórico)\n\nSinal: %s\n\n"
                   "Heurística do robô, não é recomendação."
                   % (sym, fmt_preco(a["preco"]), a["rsi"], a["rsi_ant"], a["ma9"], a["ma21"],
                      "alta" if a["tend"] else "baixa", a["vol"],
                      "sim" if a["pullback"] else "não", "sim" if a["continuidade"] else "não",
                      "sim" if a["rompimento"] else "não", prob, len(hist), sinal))
            Clock.schedule_once(lambda dt: self.mostrar_card(self.ia_out, msg))

        threading.Thread(target=job, daemon=True).start()

    # --- Backtest ---
    def sub_backtest(self):
        box = BoxLayout(orientation="vertical", spacing=dp(14), padding=[0, dp(4)])
        c = Card(bg=CARD, radius=18, orientation="vertical", size_hint_y=None,
                 padding=dp(20), spacing=dp(12))
        c.add_widget(txt("Backtest Rápido", 24, TEXT, True, altura=34))
        d = txt("Simula a estratégia escada (stop fixo + trailing em escada) nos últimos N dias "
                "usando candles reais, com as regras e os pares que você escolher.", 15, MUTED)
        d.size_hint_y = None
        d.height = dp(74)
        c.add_widget(d)
        c.add_widget(txt("Par(es) · qualquer par, separados por espaço", 15, MUTED, altura=22))
        self.bt_pares = campo_texto(self.bt["pares"], "ex.: BTCUSDT PEPEUSDT SOLUSDT", 17)
        self.bt_pares.bind(text=lambda w, v: self.bt.__setitem__("pares", v))
        c.add_widget(self.bt_pares)
        c.add_widget(txt("Regras de entrada", 15, MUTED, altura=22))
        c.add_widget(toggle_row([(k, ROTULO[k]) for k in ESTRATEGIAS], self.bt["estr"],
                                lambda lst: self.bt.__setitem__("estr", lst), altura=46, tam=13))
        c.add_widget(txt("Período", 15, MUTED, altura=22))
        c.add_widget(chip_row(["3d", "7d", "14d", "30d"], self.bt["dias"],
                              lambda k: self.bt.__setitem__("dias", k), altura=48, tam=15))
        c.add_widget(txt("Intervalo", 15, MUTED, altura=22))
        c.add_widget(chip_row(["1m", "5m", "15m", "1h", "4h"], self.bt["int"],
                              lambda k: self.bt.__setitem__("int", k), altura=48, tam=15))
        c.add_widget(txt("Valor por ordem (USDT)", 15, MUTED, altura=22))
        bv = campo_numero(self.bt["valor"], 20)
        bv.bind(text=lambda w, v: self.bt.__setitem__("valor", v))
        c.add_widget(bv)
        run = RoundBtn(bg=YELLOW, fg=DARK, text="Rodar backtest", font_size=sp(18),
                       size_hint_y=None, height=dp(58), radius=18)
        run.bind(on_release=lambda *_: self.executar_backtest())
        c.add_widget(run)
        auto_altura(c)
        box.add_widget(c)
        self.bt_out = BoxLayout(orientation="vertical", size_hint_y=None)
        auto_altura(self.bt_out)
        box.add_widget(self.bt_out)
        return rolavel(box)

    def executar_backtest(self):
        pares = lista_pares(self.bt["pares"])
        if not pares:
            return self.aviso("Digite ao menos um par.")
        try:
            valor = float(self.bt["valor"] or 100)
        except ValueError:
            valor = 100.0
        dias, inter = int(self.bt["dias"][:-1]), self.bt["int"]
        ativas = list(self.bt["estr"])
        self.mostrar_card(self.bt_out, "Rodando backtest...")

        def prog(msg):
            Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, msg))

        def job():
            partes, tot_n, tot_soma = [], 0, 0.0
            for i, sym in enumerate(pares):
                prog("Rodando %s (%d/%d)..." % (sym, i + 1, len(pares)))
                try:
                    candles = baixar_candles(sym, inter, dias)
                    if len(candles) < 40:
                        partes.append("%s: poucos candles (par novo ou inválido)." % sym)
                        continue
                    trades = backtest_par(candles, ativas)
                except Exception as e:
                    partes.append("%s: erro (%s)" % (sym, str(e)[:80]))
                    continue
                if not trades:
                    partes.append("%s: nenhuma operação." % sym)
                    continue
                n, soma, texto = resumo_trades(trades, valor)
                tot_n += n
                tot_soma += soma
                partes.append("%s · %s" % (sym, texto))
            cab = "%s · %d dias · %s · %s\n" % (
                " ".join(ROTULO[e] for e in ativas), dias, inter, "" if len(pares) == 1 else
                "%d pares" % len(pares))
            if len(pares) > 1 and tot_n:
                partes.append("TOTAL: %d op · soma %+.2f%% (≈ %+.2f USDT)" % (
                    tot_n, tot_soma, valor * tot_soma / 100))
            rodape = "\n\nExecução no fechamento do candle; stop/trailing checados na mínima. " \
                     "Sem filtro de IA e sem slippage."
            msg = cab + "\n\n".join(partes) + rodape
            Clock.schedule_once(lambda dt: self.mostrar_card(self.bt_out, msg))

        threading.Thread(target=job, daemon=True).start()


if __name__ == "__main__":
    BotApp().run()
