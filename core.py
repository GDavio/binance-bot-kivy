"""core.py - lógica compartilhada (SEM Kivy): estratégia, Binance, backtest e o Motor do robô.

É usado por duas partes do app:
  * main.py     -> a tela (só mostra e envia comandos)
  * service.py  -> serviço Android em primeiro plano que roda o robô em segundo plano
"""
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
# Arquivos compartilhados entre a tela e o serviço
#   config_ui.json  -> escrito SÓ pela tela (modo, chaves, regras, robô ligado/desligado)
#   estado_app.json -> escrito SÓ pelo Motor (saldo, posições, execuções, IA...)
#   cmds/*.json     -> pedidos da tela para o Motor (ordem manual, encerrar posição)
# Cada arquivo tem um único "dono", então não há conflito de escrita.
# ----------------------------------------------------------------------------
ARQ_CONFIG = "config_ui.json"
ARQ_ESTADO = "estado_app.json"
CONFIG_KEYS = ("modo", "robo_ativo", "regras", "cfg")
ESTADO_KEYS = ("saldo", "posicoes", "pos_sim", "pos_real", "pos_manual_real", "execucoes",
               "ia", "ia_sim", "ultimo", "resultados", "msg_robo", "hb")


def config_padrao():
    return {
        "modo": "SIM", "robo_ativo": False,
        "regras": [{"par": p, "valor": 12, "estrategias": list(ESTRATEGIAS)}
                   for p in ("BTCUSDT", "SOLUSDT", "BNBUSDT")],
        "cfg": {"key": "", "secret": "", "testnet": True},
    }


def estado_servico_padrao():
    return {"saldo": 10000.0, "posicoes": {}, "pos_sim": {}, "pos_real": {}, "pos_manual_real": {},
            "execucoes": [], "ia": [], "ia_sim": [], "ultimo": {}, "resultados": {},
            "msg_robo": "", "hb": 0}


def ler_json(path, padrao):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return padrao


def gravar_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)          # troca atômica: ninguém lê arquivo pela metade


def pasta_dados():
    """Mesma pasta para a tela e para o serviço (files dir do app)."""
    try:
        from jnius import autoclass
        srv = autoclass("org.kivy.android.PythonService").mService
        if srv is not None:
            return srv.getFilesDir().getAbsolutePath()
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if act is not None:
            return act.getFilesDir().getAbsolutePath()
    except Exception:
        pass
    return os.environ.get("ANDROID_PRIVATE") or os.path.dirname(os.path.abspath(__file__))


def caminho_relatorio(pasta):
    d = "/storage/emulated/0/Download"
    if os.path.isdir(d) and os.access(d, os.W_OK):
        return os.path.join(d, "relatorio_bot.txt")
    return os.path.join(pasta, "relatorio_bot.txt")


# ----------------------------------------------------------------------------
# Motor do robô + executor de ordens
# ----------------------------------------------------------------------------
class Motor:
    """Roda no serviço Android (ou numa thread local, se o serviço não existir, ex.: Pydroid)."""

    def __init__(self, pasta):
        self.pasta = pasta
        self.arq_estado = os.path.join(pasta, ARQ_ESTADO)
        self.arq_config = os.path.join(pasta, ARQ_CONFIG)
        self.dir_cmds = os.path.join(pasta, "cmds")
        os.makedirs(self.dir_cmds, exist_ok=True)
        self.arq_relatorio = caminho_relatorio(pasta)
        self.lock = threading.RLock()
        self.parar = threading.Event()
        self.est = self._carregar_estado()
        self.config = config_padrao()
        self._cfg_mtime = None
        self._cli = None
        self._ultimo_ciclo = 0.0
        self._ultimo_hb = 0.0
        self._ativo_em = time.time()
        self._fd_trava = None

    # ---------- estado / config ----------
    def _carregar_estado(self):
        d = estado_servico_padrao()
        antigo = ler_json(self.arq_estado, {})
        for k in ESTADO_KEYS:
            if k in antigo:
                d[k] = antigo[k]
        d["resultados"] = {}
        return d

    def ler_config(self):
        try:
            m = os.path.getmtime(self.arq_config)
        except OSError:
            return
        if m != self._cfg_mtime:
            cfg = ler_json(self.arq_config, None)
            if cfg:
                novo = dict(config_padrao(), **cfg)
                novo["cfg"] = dict(config_padrao()["cfg"], **novo.get("cfg", {}))
                for r in novo["regras"]:
                    r.setdefault("estrategias", list(ESTRATEGIAS))
                self.config = novo
                self._cfg_mtime = m
                self._ativo_em = time.time()

    def salvar(self):
        try:
            with self.lock:
                self.est["hb"] = time.time()
                gravar_json(self.arq_estado, self.est)
            self._ultimo_hb = time.time()
        except Exception as e:
            print("Erro ao salvar estado:", e)

    def cliente(self):
        c = self.config["cfg"]
        if not c["key"] or not c["secret"]:
            return None
        chave = (c["key"], c["secret"], c["testnet"])
        if not self._cli or self._cli[0] != chave:
            self._cli = (chave, Binance(c["key"], c["secret"], c["testnet"]))
        return self._cli[1]

    def hist(self, modo):
        return self.est["ia"] if modo == "REAL" else self.est["ia_sim"]

    def manual_d(self, modo):
        return self.est["posicoes"] if modo == "SIM" else self.est["pos_manual_real"]

    def registrar(self, lado, sym, motivo, direita, positivo):
        with self.lock:
            self.est["execucoes"].insert(0, {
                "t": "%s %s" % (lado, sym), "s": motivo, "d": direita, "ok": bool(positivo)})
            del self.est["execucoes"][50:]

    def resultado(self, cid, msg):
        with self.lock:
            r = self.est["resultados"]
            r[cid] = msg
            while len(r) > 30:
                r.pop(next(iter(r)))
        self.salvar()

    # ---------- comandos vindos da tela ----------
    def processar_cmds(self):
        try:
            nomes = sorted(n for n in os.listdir(self.dir_cmds) if n.endswith(".json"))
        except OSError:
            return
        for n in nomes:
            caminho = os.path.join(self.dir_cmds, n)
            cmd = ler_json(caminho, None)
            try:
                os.remove(caminho)
            except OSError:
                pass
            if not cmd:
                continue
            self._ativo_em = time.time()
            threading.Thread(target=self._executar_cmd, args=(cmd,), daemon=True).start()

    def _executar_cmd(self, cmd):
        cid = cmd.get("id", "?")
        try:
            if cmd.get("tipo") == "ordem":
                msg = self.cmd_ordem(cmd)
            elif cmd.get("tipo") == "encerrar":
                msg = self.cmd_encerrar(cmd)
            else:
                msg = "Comando desconhecido."
        except Exception as e:
            msg = "Erro: %s" % str(e)[:160]
        self.resultado(cid, msg)

    def cmd_ordem(self, cmd):
        est, modo = self.est, self.config["modo"]
        lado, sym = cmd["lado"], cmd["sym"]
        valor, preco = float(cmd["valor"]), float(cmd["preco"])
        nome = "Compra" if lado == "BUY" else "Venda"
        if modo == "SIM":
            with self.lock:
                pos = est["posicoes"]
                if lado == "BUY":
                    if valor > est["saldo"]:
                        return "Saldo virtual insuficiente."
                    est["saldo"] -= valor
                    pos[sym] = pos.get(sym, 0) + valor / preco
                else:
                    qtd = min(valor / preco, pos.get(sym, 0))
                    if qtd <= 0:
                        return "Sem posição para vender neste par."
                    est["saldo"] += qtd * preco
                    pos[sym] -= qtd
                    if pos[sym] <= 1e-12:
                        pos.pop(sym, None)
                self.registrar(lado, sym, "manual", "$%.2f" % preco, True)
            self.salvar()
            return "%s simulada em %s." % (nome, par_fmt(sym))
        ex = self.cliente()
        if not ex:
            return "Configure as chaves da Binance em Ajustes."
        if lado == "BUY":
            r = ex.comprar(sym, valor)
        else:
            livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
            r = ex.vender(sym, min(valor / preco, livre))
        with self.lock:
            man = est["pos_manual_real"]
            if lado == "BUY":
                man[sym] = man.get(sym, 0.0) + r["qtd"]
            else:
                man[sym] = max(0.0, man.get(sym, 0.0) - r["qtd"])
                if man[sym] <= 1e-12:
                    man.pop(sym, None)
            self.registrar(lado, sym, "manual", "$%.2f" % (r["preco"] or preco), True)
        self.salvar()
        return "%s executada em %s." % (nome, par_fmt(sym))

    def cmd_encerrar(self, cmd):
        modo = self.config["modo"]
        msgs = []
        for s in cmd.get("simbolos", []):
            try:
                msgs.append(self.encerrar_sync(s, modo))
            except Exception as e:
                msgs.append("%s: erro (%s)" % (par_fmt(s), str(e)[:100]))
        self.salvar()
        return "\n".join(msgs) or "Nenhuma posição aberta."

    def encerrar_sync(self, sym, modo):
        est = self.est
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        man = self.manual_d(modo)
        with self.lock:
            pos = pos_d.get(sym)
            qtd_man = man.get(sym, 0.0)
            if not pos and qtd_man <= 1e-12:
                return "%s: sem posição aberta." % par_fmt(sym)
            if pos:
                pos["encerrando"] = True
        try:
            if modo == "SIM":
                ps = validar_par(sym)
            else:
                ex = self.cliente()
                if not ex:
                    raise Exception("configure as chaves em Ajustes")
                livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
                total = (pos["qtd"] if pos else 0.0) + qtd_man
                ps = ex.vender(sym, min(total, livre))["preco"]
        except Exception as e:
            with self.lock:
                if pos:
                    pos.pop("encerrando", None)
            if modo == "REAL" and ("-1013" in str(e) or "Invalid quantity" in str(e)):
                with self.lock:
                    pos_d.pop(sym, None)
                    man.pop(sym, None)
                    self.registrar("SYNC", sym, "posição já encerrada ou abaixo do mínimo", "—", True)
                return "%s: já estava encerrada na Binance (registro local limpo)." % par_fmt(sym)
            raise
        with self.lock:
            if pos:
                lucro = (ps / pos["entrada"] - 1) * 100
                if modo == "SIM":
                    est["saldo"] += pos["qtd"] * ps
                pos_d.pop(sym, None)
                self.registrar("SELL", sym, "ENCERRADA MANUAL", "%+.2f%%" % lucro, lucro >= 0)
            if qtd_man > 1e-12:
                if modo == "SIM":
                    est["saldo"] += qtd_man * ps
                man.pop(sym, None)
                self.registrar("SELL", sym, "manual (encerrar)", "$%.2f" % ps, True)
        return "%s encerrada @ %s" % (par_fmt(sym), fmt_preco(ps))

    # ---------- robô ----------
    def ciclo(self):
        est, modo = self.est, self.config["modo"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        with self.lock:
            regras = [dict(r) for r in self.config["regras"]]
            # posição aberta sem regra (regra apagada) continua sendo gerenciada, só saída
            for s in list(pos_d):
                if not any(r["par"] == s for r in regras):
                    regras.append({"par": s, "valor": 0, "estrategias": []})
        erro = ""
        for regra in regras:
            if not self.config["robo_ativo"] or self.parar.is_set():
                break
            try:
                self.processar(regra, modo)
            except Exception as e:
                erro = "[%s] %s" % (regra["par"], str(e)[:120])
        with self.lock:
            est["msg_robo"] = erro
        self.salvar()

    def processar(self, regra, modo):
        est = self.est
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
            if pos.get("encerrando"):
                return
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
        est = self.est
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
            with open(self.arq_relatorio, "a") as f:
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

    # ---------- laço principal ----------
    def tentar_travar(self):
        """Garante UM motor só (serviço ou thread local), para nunca operar em dobro."""
        try:
            import fcntl
        except ImportError:
            return True
        try:
            self._fd_trava = open(os.path.join(self.pasta, "motor.lock"), "w")
            fcntl.flock(self._fd_trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
        except Exception:
            return True

    def loop(self, ocioso_max=None):
        """ocioso_max: segundos sem robô ligado e sem comandos até o serviço se encerrar."""
        if not self.tentar_travar():
            print("Motor já está em execução; saindo.")
            return
        try:
            while not self.parar.is_set():
                try:
                    self.ler_config()
                    self.processar_cmds()
                    agora = time.time()
                    if self.config["robo_ativo"]:
                        self._ativo_em = agora
                        if agora - self._ultimo_ciclo >= 10:
                            self._ultimo_ciclo = agora
                            self.ciclo()
                    if agora - self._ultimo_hb >= 3:
                        self.salvar()
                    if ocioso_max and agora - self._ativo_em > ocioso_max:
                        break
                except Exception as e:
                    print("motor:", e)
                self.parar.wait(1.0)
        finally:
            self.salvar()
            if self._fd_trava:
                try:
                    self._fd_trava.close()
                except Exception:
                    pass
