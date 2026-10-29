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

COOLDOWN = 60
VOL_MIN = 0.10
PROB_MIN = 0.55
IA_MIN_HIST = 50
STOP_PCT = 1.2
TRAIL_ATIVA = 0.80
TRAIL_TRAVA = 0.40
TAXA_IDA_VOLTA = 0.2

PARAMS = {
    "vol_min": VOL_MIN, "trail_ativa": TRAIL_ATIVA, "trail_trava": TRAIL_TRAVA,
    "stop_pct": STOP_PCT, "stop_max": 1.2, "escala": 1.0,
    "vol_estouro": 1.0, "corpo_estouro": 2.0, "atr_compressao": 4.0,
    "n_compressao": 8, "atr_esticado": 4.0,
    "rsi_pull_min": 25, "rsi_pull_max": 55, "rsi_cont_min": 45, "rsi_cont_max": 65,
    "sec_rev": True, "sec_trend": True,
}

PERFIL_5M = {
    "nome": "5m", "tf_gatilho": "5m", "tf_semaforo": "15m",
    "ma_f": 9, "ma_s": 21, "atr_p": 14, "rsi_p": 14,
    "vol_min": 0.08, "vwap": True, "ctx_ma": 21,
    "n_compressao": 8, "atr_compressao": 4.0, "atr_esticado": 4.0,
    "rsi_pull_min": 40, "rsi_pull_max": 65, "rsi_cont_min": 45, "rsi_cont_max": 65
}

PERFIL_1M = {
    "nome": "1m", "tf_gatilho": "1m", "tf_semaforo": "5m",
    "ma_f": 9, "ma_s": 21, "atr_p": 9, "rsi_p": 9,
    "vol_min": 0.02, "vwap": True, "ctx_ma": 21,
    "n_compressao": 5, "atr_compressao": 5.0, "atr_esticado": 6.0,
    "rsi_pull_min": 20, "rsi_pull_max": 70, "rsi_cont_min": 40, "rsi_cont_max": 80
}

PERFIS = {"5m": PERFIL_5M, "1m": PERFIL_1M}
TREND_RSI = 40
TREND_ATR_SEP = 0.1
K_STOP = 2.0
K_ATIVA = 2.5
ATIVA_MAX = 1.5
TRAVA_FRAC = 0.55
ESTRATEGIAS = ["pullback", "continuidade", "rompimento"]
ROTULO = {"pullback": "Pullback", "continuidade": "Continuidade", "rompimento": "Rompimento"}
INTERVALO_S = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400}
BASE_PUBLICA = "https://api.binance.com"
BASE_TESTNET = "https://testnet.binance.vision"

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl.create_default_context()

def http_json(path, params=None, timeout=15, base=BASE_PUBLICA, headers=None, metodo="GET"):
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
    p = {"symbol": symbol, "interval": intervalo, "limit": limit}
    if inicio:
        p["startTime"] = int(inicio)
    res = http_json("/api/v3/klines", p)
    return [(float(k[1]), float(k[2]), float(k[3]), float(k[4]), int(k[0]), float(k[5])) for k in res]

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

def baixar_candles(sym, intervalo, dias, progresso=None):
    fim = int(time.time() * 1000)
    cur = fim - dias * 86400000
    intervalo_ms = INTERVALO_S.get(intervalo, 300) * 1000
    estimado = max(1, int((fim - cur) / intervalo_ms))
    out = []
    tentativas_erro = 0
    while cur < fim:
        try:
            lote = buscar_klines(sym, intervalo, 500, cur)
            if not lote:
                break
            out += lote
            cur = lote[-1][4] + 1
            if progresso:
                try:
                    progresso(len(out), estimado)
                except Exception:
                    pass
            if len(lote) < 500:
                break
        except Exception as e:
            print("Erro no download de %s: %s" % (sym, e))
            tentativas_erro += 1
            if tentativas_erro >= 3:
                raise Exception("Falha de conexao com a Binance ao baixar %s" % sym)
            time.sleep(2)
    return out

class Binance:
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
        p["signature"] = hmac.new(self.secret.encode("utf-8"), q.encode("utf-8"), hashlib.sha256).hexdigest()
        return http_json(endpoint, p, base=self.base, metodo=metodo, headers={"X-MBX-APIKEY": self.key})

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
            self._info[sym] = {"base": r["baseAsset"], "quote": r["quoteAsset"], "step": step, "minq": minq, "minn": minn}
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
        res = self._assinado("POST", "/api/v3/order", {"symbol": sym, "side": "BUY", "type": "MARKET", "quoteOrderQty": v})
        return self._resumo(res, sym)

    def vender(self, sym, qtd):
        info = self.info(sym)
        q = ajustar_qtd(qtd, info["step"])
        if q <= 0 or float(q) < info["minq"]:
            raise Exception("Invalid quantity: %s abaixo do minimo" % qtd)
        res = self._assinado("POST", "/api/v3/order", {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": format(q.normalize(), "f")})
        return self._resumo(res, sym)

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

def _ema(vals, p):
    out = [None] * len(vals)
    if len(vals) < p:
        return out
    k = 2.0 / (p + 1)
    e = sum(vals[:p]) / p
    out[p - 1] = e
    for i in range(p, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out

def _atr(h, l, c, p=14):
    n = len(c)
    out = [None] * n
    if n <= p:
        return out
    tr = [None] * n
    for i in range(1, n):
        tr[i] = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
    a = sum(tr[1:p + 1]) / p
    out[p] = a
    for i in range(p + 1, n):
        a = (a * (p - 1) + tr[i]) / p
        out[i] = a
    return out

def _ctx_semaforo(c_gatilho, tf_gatilho, tf_semaforo, ma=21):
    # CORREÇÃO DO BUG DO SEMÁFORO:
    # Em vez de sincronizar por timestamp (que quebrava no backtest devido a fusos/milissegundos),
    # sincronizamos pela quantidade de candles (índice).
    n = len(c_gatilho)
    ctx = [False] * n
    s_gat = INTERVALO_S[tf_gatilho]
    s_sem = INTERVALO_S[tf_semaforo]
    fator = s_sem // s_gat
    if fator < 1:
        return ctx
    
    cl = []
    e = None
    e_ant = None
    k = 2.0 / (ma + 1)
    
    for i in range(n):
        # A cada 'fator' candles de gatilho, formamos 1 candle de semáforo.
        # Ex: perfil 1m (gatilho 1m, semaforo 5m) -> a cada 5 candles, atualiza a EMA de 5m.
        if (i + 1) % fator == 0:
            cl.append(c_gatilho[i][3])
            m = len(cl)
            if m == ma:
                e = sum(cl) / ma
                e_ant = e
            elif m > ma:
                e_ant = e
                e = cl[-1] * k + e * (1 - k)
                
        if e is not None and e_ant is not None and len(cl) >= ma:
            ctx[i] = cl[-1] > e and e > e_ant
            
    return ctx

def _vwap_dia(c_gatilho):
    out = [None] * len(c_gatilho)
    dia, pv, vv, ini = None, 0.0, 0.0, 0
    for i, cd in enumerate(c_gatilho):
        h, l, c, t, v = cd[1], cd[2], cd[3], cd[4], cd[5]
        d = t // 86400000
        if d != dia:
            dia, pv, vv, ini = d, 0.0, 0.0, i
        pv += (h + l + c) / 3.0 * v
        vv += v
        out[i] = pv / vv if vv > 0 and i - ini >= 3 else None
    return out

def preparar_perfil(candles, perfil):
    n = len(candles)
    o = [x[0] for x in candles]
    h = [x[1] for x in candles]
    l = [x[2] for x in candles]
    c = [x[3] for x in candles]
    t = [x[4] for x in candles]
    v = [x[5] for x in candles]
    return {
        "o": o, "h": h, "l": l, "c": c, "t": t, "v": v,
        "ema9": _ema(c, perfil["ma_f"]), "ema21": _ema(c, perfil["ma_s"]), "ema50": _ema(c, 50),
        "rsi": serie_rsi(c, perfil["rsi_p"]), "atr": _atr(h, l, c, perfil["atr_p"]),
        "vavg": [None] * 20 + [sum(v[i - 20:i]) / 20 for i in range(20, n)],
        "vwap": _vwap_dia(candles) if perfil["vwap"] else [None] * n,
        "ctx": _ctx_semaforo(candles, perfil["tf_gatilho"], perfil["tf_semaforo"], perfil["ctx_ma"]),
    }

def _funil_novo():
    return {"total": 0, "geral": {}, "passou_geral": 0, "estrategia": {"pullback": {}, "continuidade": {}, "rompimento": {}}, "entradas": {"pullback": 0, "continuidade": 0, "rompimento": 0}}

def _funil_somar(destino, origem):
    destino["total"] += origem.get("total", 0)
    destino["passou_geral"] += origem.get("passou_geral", 0)
    for k, v in origem.get("geral", {}).items():
        destino["geral"][k] = destino["geral"].get(k, 0) + v
    for est in ESTRATEGIAS:
        src = origem.get("estrategia", {}).get(est, {})
        dst = destino["estrategia"].setdefault(est, {})
        for k, v in src.items():
            dst[k] = dst.get(k, 0) + v
    for est in ESTRATEGIAS:
        destino["entradas"][est] += origem.get("entradas", {}).get(est, 0)
    return destino

def formatar_funil(funil, perfil_nome=None, dias=None):
    if not funil or not funil.get("total"):
        return "Funil: sem dados (nenhum candle avaliado no periodo)."
    tot = funil["total"]
    linhas = ["FUNIL%s - %d candles avaliados" % ((" (%s, %sd)" % (perfil_nome, dias)) if perfil_nome else "", tot)]
    for motivo, qtd in sorted(funil["geral"].items(), key=lambda x: -x[1]):
        linhas.append("  [geral] %s: %d (%.1f%%)" % (motivo, qtd, qtd / tot * 100))
    pg = funil["passou_geral"]
    linhas.append("  -> passaram do geral: %d (%.1f%%)" % (pg, pg / tot * 100))
    base = pg if pg > 0 else 1
    for est in ESTRATEGIAS:
        motivos = funil["estrategia"].get(est, {})
        entrada_n = funil["entradas"].get(est, 0)
        if not motivos and not entrada_n:
            continue
        linhas.append("  %s: %d entradas" % (ROTULO[est], entrada_n))
        for motivo, qtd in sorted(motivos.items(), key=lambda x: -x[1]):
            linhas.append("    - %s: %d (%.1f%%)" % (motivo, qtd, qtd / base * 100))
    return "\n".join(linhas)

def sinais_perfil(d, i, perfil, p=None, funil=None):
    p = dict(PARAMS, **(p or {}))
    p.update({k: perfil[k] for k in ["n_compressao", "atr_compressao", "atr_esticado", "rsi_pull_min", "rsi_pull_max", "rsi_cont_min", "rsi_cont_max"] if k in perfil})
    res = {"pullback": False, "continuidade": False, "rompimento": False, "falhas": {}, "info": {}}

    def falha_geral(motivo):
        for nm in ESTRATEGIAS:
            res["falhas"][nm] = motivo
        if funil is not None:
            funil["geral"][motivo] = funil["geral"].get(motivo, 0) + 1
        return res

    if i < 60:
        return falha_geral("historico curto (< 60 velas)")
    o, h, l, c, v = d["o"][i], d["h"][i], d["l"][i], d["c"][i], d["v"][i]
    atr = d["atr"][i - 1]
    e9, e21, e50 = d["ema9"][i], d["ema21"][i], d["ema50"][i]
    rsi, rsi_a = d["rsi"][i], d["rsi"][i - 1]
    vw, va = d["vwap"][i], d["vavg"][i]
    if None in (atr, e9, e21, e50, rsi, rsi_a, va) or va <= 0 or atr <= 0:
        return falha_geral("dados insuficientes")
    atr_pct = atr / c * 100
    rvol = v / va
    res["info"] = {"preco": c, "rsi": rsi, "atr_pct": atr_pct, "rvol": rvol, "vwap": vw, "ema9": e9, "ema21": e21, "ema50": e50, "ctx": d["ctx"][i], "perfil": perfil["nome"]}

    if not d["ctx"][i]:
        return falha_geral("sem tendencia de alta no %s" % perfil["tf_semaforo"])
    if perfil["vwap"] and vw is None:
        return falha_geral("VWAP do dia ainda sem dados")
    if perfil["vwap"] and c <= vw:
        return falha_geral("fechou abaixo do VWAP do dia")
    if atr_pct < perfil["vol_min"]:
        return falha_geral("ATR%% baixo (%.3f%% < %.2f%%)" % (atr_pct, perfil["vol_min"]))
    if c > e21 + p["atr_esticado"] * atr:
        return falha_geral("esticado (> %.1f ATR acima da EMA21)" % p["atr_esticado"])

    if funil is not None:
        funil["passou_geral"] = funil.get("passou_geral", 0) + 1

    def checar(nome, regras):
        for motivo, ok in regras:
            if not ok:
                res["falhas"][nome] = motivo
                if funil is not None:
                    dic = funil["estrategia"].setdefault(nome, {})
                    dic[motivo] = dic.get(motivo, 0) + 1
                return False
        res[nome] = True
        return True

    lows, highs, closes, vols = d["l"], d["h"], d["c"], d["v"]
    
    K = 8
    jan = range(i - K, i)
    min_low = min(lows[j] for j in jan)
    idx_min = max(j for j in jan if lows[j] == min_low)
    pico = max(highs[j] for j in range(i - 24, i))
    idx_pico = max(j for j in range(i - 24, i) if highs[j] == pico)
    rsi_min = min(d["rsi"][j] for j in jan)
    vol_rec = (sum(vols[idx_pico + 1:i]) / max(1, i - idx_pico - 1)) if i - idx_pico > 1 else 0.0

    checar("pullback", [
        ("topo anterior nao veio antes do fundo do recuo", pico > min_low and i - idx_pico >= 2),
        ("recuo fora de 0.3-4.5 ATR", 0.3 <= (pico - min_low) / atr <= 4.5),
        ("recuo nao chegou na EMA21", any(lows[j] <= d["ema21"][j] * 1.001 for j in jan)),
        ("recuo perdeu a EMA50", min_low >= d["ema50"][idx_min] * 0.998),
        ("RSI nao desceu ate ~%.0f no recuo" % p["rsi_pull_max"], rsi_min <= p["rsi_pull_max"]),
        ("RSI caiu abaixo de %.0f no recuo" % p["rsi_pull_min"], rsi_min >= p["rsi_pull_min"]),
        ("RSI nao esta virando para cima", rsi > rsi_a and rsi >= 45),
        ("volume do recuo acima da media", vol_rec <= va),
        ("sem vela de confirmacao", c > o and c > highs[i - 1] and c > e21)])

    Kf = 4
    fl = range(i - Kf, i)
    f_high = max(highs[j] for j in fl)
    f_low = min(lows[j] for j in fl)
    pre_low = min(lows[j] for j in range(i - 16, i - Kf))
    checar("continuidade", [
        ("medias fora de ordem ou preco abaixo da EMA9", e9 > e21 > e50 and c >= e9 * 0.998),
        ("sem impulso anterior (>= 1.5 ATR)", closes[i - Kf - 1] - pre_low >= 1.5 * atr),
        ("bandeira larga (> 2.5 ATR)", f_high - f_low <= 2.5 * atr),
        ("bandeira perdeu a EMA21", f_low > e21),
        ("RSI fora de %.0f-%.0f" % (p["rsi_cont_min"], p["rsi_cont_max"]), p["rsi_cont_min"] <= rsi <= p["rsi_cont_max"]),
        ("nao fechou acima da maxima da bandeira com vela de alta", c > f_high and c > o),
        ("volume abaixo da media no rompimento da bandeira", rvol >= 1.0)])

    Nc = int(p["n_compressao"])
    comp = range(i - Nc, i)
    comp_high = max(highs[j] for j in comp)
    comp_low = min(lows[j] for j in comp)
    faixa_comp = comp_high - comp_low
    corpos_comp = [abs(closes[j] - d["o"][j]) for j in comp]
    corpo_medio = sum(corpos_comp) / len(corpos_comp) if corpos_comp else 0.0
    corpo_atual = abs(c - o)
    dist_medias = sum(abs(closes[j] - d["ema21"][j]) for j in comp) / Nc
    perto_ma = dist_medias <= 1.5 * atr
    vol_exigido = p["vol_estouro"]

    checar("rompimento", [
        ("sem compressao (faixa de %d velas > %.1f ATR)" % (Nc, p["atr_compressao"]), faixa_comp <= p["atr_compressao"] * atr),
        ("preco nao estava colado nas medias na compressao", perto_ma),
        ("vela de estouro sem corpo (> %.1fx a media)" % p["corpo_estouro"], corpo_atual >= p["corpo_estouro"] * corpo_medio and corpo_atual > 0),
        ("nao fechou acima da EMA21", c > e21),
        ("nao fechou acima da maxima da compressao", c > comp_high),
        ("fechamento fraco (rejeicao)", c > o and c >= l + 0.6 * (h - l)),
        ("volume abaixo de %.1fx a media" % vol_exigido, rvol >= vol_exigido)])
    return res
    
    
def escolher_tipo_dia(res, ativas):
    for nome in ESTRATEGIAS:
        if nome in ativas and res.get(nome):
            return nome
    return None

def velas_fechadas(candles, intervalo_s, agora_ms=None):
    agora_ms = agora_ms or int(time.time() * 1000)
    if candles and candles[-1][4] + intervalo_s * 1000 > agora_ms:
        return candles[:-1]
    return candles

def params_atr(atr_pct, base=None):
    b = dict(PARAMS, **(base or {}))
    smin = b["stop_pct"]
    smax = max(smin, b.get("stop_max", 1.2))
    stop = min(max(K_STOP * atr_pct, smin), smax)
    a0, t0 = b["trail_ativa"], b["trail_trava"]
    ativa = min(max(K_ATIVA * atr_pct, a0), max(a0, ATIVA_MAX))
    trava = min(max(t0, TRAVA_FRAC * ativa), ativa - 0.05)
    b.update(stop_pct=stop, trail_ativa=ativa, trail_trava=trava, escala=(ativa / a0 if a0 > 0 else 1.0))
    return b

def trava_trailing(lucro_max, trava1=None, esc=1.0):
    t1 = TRAIL_TRAVA if trava1 is None else trava1
    if lucro_max < 0.70 * esc: return t1
    if lucro_max < 1.05 * esc: return max(t1, 0.35 * esc)
    if lucro_max < 1.40 * esc: return max(t1, 0.70 * esc)
    if lucro_max < 1.75 * esc: return max(t1, 1.05 * esc)
    return max(t1, lucro_max - 0.35 * esc)

def nivel_stop(entrada, topo, p=None):
    p = p or PARAMS
    stop = p.get("stop_pct", STOP_PCT)
    lucro_max = (topo / entrada - 1) * 100
    if lucro_max >= p["trail_ativa"]:
        t = trava_trailing(lucro_max, p["trail_trava"], p.get("escala", 1.0))
        return entrada * (1 + t / 100), "TRAILING STOP (%.2f%%)" % t, True
    return entrada * (1 - stop / 100), "STOP LOSS -%.2f%%" % stop, False

def indicadores_saida(velas, q):
    c = [x[3] for x in velas]
    h = [x[1] for x in velas]
    l = [x[2] for x in velas]
    pf, ps = q.get("ma_f", 9), q.get("ma_s", 21)
    if q.get("ma_tipo", "ema") == "ema":
        mf, ms = _ema(c, pf)[-1], _ema(c, ps)[-1]
    else:
        mf, ms = (media(c, pf), media(c, ps)) if len(c) >= ps else (None, None)
    rp = q.get("rsi_p", 14)
    rsi, rsi_ant = calcular_rsi(c, rp), calcular_rsi(c[:-1], rp)
    atr = _atr(h, l, c, q.get("atr_p", 14))[-1]
    return mf, ms, rsi, rsi_ant, atr

def perdeu_tendencia(mf, ms, rsi, rsi_ant, atr, lucro=None, k=1.0):
    if None in (mf, ms, atr) or atr <= 0: return False
    return (mf < ms and (ms - mf) >= 0.15 * atr and rsi <= 35 and rsi < rsi_ant)

def saida_secundaria(entrada, preco, closes, a, p=None, velas=None):
    q = p or PARAMS
    k = q.get("stop_pct", STOP_PCT) / STOP_PCT
    lucro = (preco / entrada - 1) * 100
    if (q.get("sec_rev", True) and a["rsi"] < 30 and a["rsi"] < a["rsi_ant"] and preco < a["ma9"] and preco < a["ma21"] and lucro < -0.25 * k):
        return "REVERSAO FORTE"
    if q.get("sec_trend", True) and velas and len(velas) >= 30:
        mf, ms, rsi, rsi_ant, atr = indicadores_saida(velas, q)
        if perdeu_tendencia(mf, ms, rsi, rsi_ant, atr, lucro, k):
            return "PERDEU TENDENCIA"
    return None

def decidir_saida(entrada, topo, preco, closes, p=None, velas=None):
    px, motivo, _ = nivel_stop(entrada, topo, p)
    if preco <= px:
        return motivo
    a = analisar_legado(closes, (p or PARAMS).get("rsi_p", 14))
    if a is None: return None
    return saida_secundaria(entrada, preco, closes, a, p, velas)

def analisar_legado(closes, rsi_p=14, atr_pct=None):
    if len(closes) < 30: return None
    preco, ant = closes[-1], closes[-2]
    rsi, rsi_ant = calcular_rsi(closes, rsi_p), calcular_rsi(closes[:-1], rsi_p)
    ma9, ma21 = media(closes, 9), media(closes, 21)
    vol = atr_pct if atr_pct is not None else (max(closes[-10:]) - min(closes[-10:])) / preco * 100
    dist = (ma9 - ma21) / ma21 * 100
    return {"preco": preco, "ant": ant, "rsi": rsi, "rsi_ant": rsi_ant, "ma9": ma9, "ma21": ma21, "vol": vol, "dist": dist, "tend": ma9 > ma21}

def _grupo(trades, chave):
    g = {}
    for t in trades:
        g.setdefault(chave(t), []).append(t["lucro"])
    return sorted(g.items(), key=lambda x: -len(x[1]))

def resumo_trades(trades, valor, dias=None):
    n = len(trades)
    lucros = [t["lucro"] for t in trades]
    soma = sum(lucros)
    liq = soma - TAXA_IDA_VOLTA * n
    por_dia = " (%.1f/dia)" % (n / dias) if dias else ""
    linhas = [
        "%d op%s . acerto %.0f%%" % (n, por_dia, len([x for x in lucros if x > 0]) / n * 100),
        "Por op: %+.3f%% bruto . %+.3f%% c/ taxa" % (soma / n, liq / n),
        "Soma %+.2f%% (aprox %+.2f USDT)" % (soma, valor * soma / 100),
        "C/ taxa 0.1%%/lado: %+.2f%% (aprox %+.2f USDT)" % (liq, valor * liq / 100),
        "Melhor %+.2f%% . Pior %+.2f%%" % (max(lucros), min(lucros)),
        "Por estrategia:"]
    for k, v in _grupo(trades, lambda t: t["tipo"]):
        m = sum(v) / len(v)
        linhas.append("  %s: %d op . acerto %.0f%% . media %+.3f%% (c/ taxa %+.3f%%)" % (k, len(v), len([x for x in v if x > 0]) / len(v) * 100, m, m - TAXA_IDA_VOLTA))
    linhas.append("Por saida:")
    for k, v in _grupo(trades, lambda t: re.sub(r" -[\d.]+%$", "", t["motivo"].split(" (")[0])):
        linhas.append("  %s: %d . media %+.2f%% . soma %+.1f%%" % (k, len(v), sum(v) / len(v), sum(v)))
    return n, soma, "\n".join(linhas)

def backtest_perfil(candles, ativas, perfil, p=None, t_ini=0, stats=None, funil=None, progresso_cb=None):
    p = dict(PARAMS, **(p or {}))
    p.update({k: perfil[k] for k in ["n_compressao", "atr_compressao", "atr_esticado", "rsi_pull_min", "rsi_pull_max", "rsi_cont_min", "rsi_cont_max"] if k in perfil})
    prop = p.pop("prop", True)
    d = preparar_perfil(candles, perfil)
    trades, pos = [], None
    atrs, stops, ativas_l = [], [], []
    n_total = len(candles)
    for i in range(60, n_total):
        if progresso_cb and (i % 200 == 0):
            try: progresso_cb(i, n_total)
            except Exception: pass
        o, h, l, c, t = candles[i][:5]
        if t >= t_ini and d["atr"][i] is not None:
            atrs.append(d["atr"][i] / c * 100)
        if pos:
            pp = pos["p"]
            px, motivo, _ = nivel_stop(pos["entrada"], pos["topo"], pp)
            if l <= px:
                saida = min(px, o)
            else:
                pos["topo"] = max(pos["topo"], h)
                motivo = decidir_saida(pos["entrada"], pos["topo"], c, d["c"][max(0, i - 49):i + 1], pp, candles[max(0, i - 69):i + 1])
                saida = c
                if not motivo: continue
            trades.append({"lucro": (saida / pos["entrada"] - 1) * 100, "motivo": motivo, "tipo": pos["tipo"]})
            pos = None
        elif t >= t_ini:
            if funil is not None:
                funil["total"] = funil.get("total", 0) + 1
            sin = sinais_perfil(d, i, perfil, p, funil=funil)
            tipo = escolher_tipo_dia(sin, ativas)
            if tipo:
                if funil is not None:
                    funil["entradas"][tipo] = funil["entradas"].get(tipo, 0) + 1
                pp = params_atr(sin["info"]["atr_pct"], p) if prop else dict(p)
                pp.update(ma_f=perfil["ma_f"], ma_s=perfil["ma_s"], ma_tipo="ema", atr_p=perfil["atr_p"], rsi_p=perfil["rsi_p"])
                stops.append(pp["stop_pct"])
                ativas_l.append(pp["trail_ativa"])
                pos = {"entrada": c, "topo": c, "tipo": tipo, "p": pp}
    if progresso_cb:
        try: progresso_cb(n_total, n_total)
        except Exception: pass
    if stats is not None:
        stats["atr_medio"] = sum(atrs) / len(atrs) if atrs else 0.0
        stats["stop_medio"] = sum(stops) / len(stops) if stops else None
        stats["ativa_media"] = sum(ativas_l) / len(ativas_l) if ativas_l else None
    return trades

def diagnostico_perfil(sym, perfil, p=None):
    fech = velas_fechadas(buscar_klines(sym, perfil["tf_gatilho"], 300), INTERVALO_S[perfil["tf_gatilho"]])
    if len(fech) < 120: raise Exception("poucos candles")
    d = preparar_perfil(fech, perfil)
    i = len(fech) - 1
    return d, i, sinais_perfil(d, i, perfil, p)

def fmt_preco(p):
    if p >= 10000: return "$%s" % format(p, ",.0f")
    if p >= 10: return "$%s" % format(p, ",.2f")
    return "$%.4f" % p

def fmt_vol(v): return "$%.1fM" % (v / 1e6)

def par_fmt(s):
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q): return s[:-len(q)] + "/" + q
    return s

def normalizar_par(s):
    s = re.sub(r"[^A-Za-z0-9]", "", s or "").upper()
    if not s: return ""
    if s in ("BTC", "ETH", "BNB") or not any(s.endswith(q) and len(s) > len(q) for q in QUOTES): s += "USDT"
    return s

def lista_pares(texto, maximo=8):
    out = []
    for p in re.split(r"[\s,;]+", texto or ""):
        p = normalizar_par(p)
        if p and p not in out: out.append(p)
    return out[:maximo]

ARQ_CONFIG = "config_ui.json"
ARQ_ESTADO = "estado_app.json"
CONFIG_KEYS = ("modo", "robo_ativo", "regras", "cfg", "modelo", "custom_params")
ESTADO_KEYS = ("saldo", "posicoes", "pos_sim", "pos_real", "pos_manual_real", "execucoes", "ia", "ia_sim", "ultimo", "resultados", "msg_robo", "hb", "origem", "inicio", "ciclo_ts")

def config_padrao():
    return {"modo": "SIM", "robo_ativo": False, "modelo": "5m", "regras": [{"par": p, "valor": 12, "estrategias": list(ESTRATEGIAS)} for p in ("BTCUSDT", "SOLUSDT", "BNBUSDT")], "cfg": {"key": "", "secret": "", "testnet": True}, "custom_params": {}}

def estado_servico_padrao():
    return {"saldo": 10000.0, "posicoes": {}, "pos_sim": {}, "pos_real": {}, "pos_manual_real": {}, "execucoes": [], "ia": [], "ia_sim": [], "ultimo": {}, "resultados": {}, "msg_robo": "", "hb": 0, "origem": "", "inicio": 0, "ciclo_ts": 0}

def ler_json(path, padrao):
    try:
        with open(path, "r") as f: return json.load(f)
    except Exception: return padrao

def gravar_json(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f: json.dump(obj, f)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("gravar_json falhou em %s: %s" % (path, e))
        try:
            if os.path.exists(tmp): os.remove(tmp)
        except Exception: pass
        return False

def pasta_dados():
    candidatos = []
    try:
        from jnius import autoclass
        srv = autoclass("org.kivy.android.PythonService").mService
        if srv is not None: candidatos.append(srv.getFilesDir().getAbsolutePath())
    except Exception: pass
    try:
        from jnius import autoclass
        act = autoclass("org.kivy.android.PythonActivity").mActivity
        if act is not None: candidatos.append(act.getFilesDir().getAbsolutePath())
    except Exception: pass
    env = os.environ.get("ANDROID_PRIVATE")
    if env: candidatos.append(env)
    candidatos.append(os.path.dirname(os.path.abspath(__file__)))
    for p in candidatos:
        try:
            os.makedirs(p, exist_ok=True)
            teste = os.path.join(p, ".write_test")
            with open(teste, "w") as f: f.write("ok")
            os.remove(teste)
            return p
        except Exception: continue
    return candidatos[-1]

def migrar_estado(pasta):
    novo = os.path.join(pasta, ARQ_ESTADO)
    if os.path.exists(novo): return
    antigos = ["/data/data/org.bot.binancebot/files/" + ARQ_ESTADO, os.path.join(os.environ.get("ANDROID_PRIVATE", ""), ARQ_ESTADO)]
    for a in antigos:
        if a and os.path.exists(a):
            try:
                import shutil
                shutil.copy2(a, novo)
                return
            except Exception: pass

def caminho_relatorio(pasta):
    return os.path.join(pasta, "relatorio_bot.csv")

class Motor:
    def __init__(self, pasta, origem="local"):
        self.origem = origem
        self.pasta = pasta
        migrar_estado(pasta)
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

    def _carregar_estado(self):
        d = estado_servico_padrao()
        antigo = ler_json(self.arq_estado, {})
        for k in ESTADO_KEYS:
            if k in antigo: d[k] = antigo[k]
        d["resultados"] = {}
        return d

    def ler_config(self):
        try: m = os.path.getmtime(self.arq_config)
        except OSError: return
        if m != self._cfg_mtime:
            cfg = ler_json(self.arq_config, None)
            if cfg:
                novo = dict(config_padrao(), **cfg)
                novo["cfg"] = dict(config_padrao()["cfg"], **novo.get("cfg", {}))
                for r in novo["regras"]: r.setdefault("estrategias", list(ESTRATEGIAS))
                if novo.get("modelo") not in PERFIS: novo["modelo"] = "5m"
                
                # ATUALIZA OS PARAMS GLOBAIS COM OS DADOS VINDOS DA INTERFACE
                custom = novo.get("custom_params", {})
                for k, v in custom.items():
                    if k in PARAMS:
                        PARAMS[k] = v
                
                self.config = novo
                self._cfg_mtime = m
                self._ativo_em = time.time()

    def salvar(self):
        try:
            with self.lock:
                self.est["hb"] = time.time()
                ok = gravar_json(self.arq_estado, self.est)
                if not ok: self.est["msg_robo"] = "Falha ao gravar estado (verifique armazenamento)"
            self._ultimo_hb = time.time()
        except Exception as e: print("Erro ao salvar estado:", e)

    def cliente(self):
        c = self.config["cfg"]
        if not c["key"] or not c["secret"]: return None
        chave = (c["key"], c["secret"], c["testnet"])
        if not self._cli or self._cli[0] != chave: self._cli = (chave, Binance(c["key"], c["secret"], c["testnet"]))
        return self._cli[1]

    def hist(self, modo): return self.est["ia"] if modo == "REAL" else self.est["ia_sim"]
    def manual_d(self, modo): return self.est["posicoes"] if modo == "SIM" else self.est["pos_manual_real"]

    def registrar(self, lado, sym, motivo, direita, positivo):
        with self.lock:
            self.est["execucoes"].insert(0, {"t": "%s %s" % (lado, sym), "s": motivo, "d": direita, "ok": bool(positivo), "ts": time.time()})
            del self.est["execucoes"][50:]

    def resultado(self, cid, msg):
        with self.lock:
            r = self.est["resultados"]
            r[cid] = msg
            while len(r) > 30: r.pop(next(iter(r)))
        self.salvar()

    def processar_cmds(self):
        try: nomes = sorted(n for n in os.listdir(self.dir_cmds) if n.endswith(".json"))
        except OSError: return
        for n in nomes:
            caminho = os.path.join(self.dir_cmds, n)
            cmd = ler_json(caminho, None)
            try: os.remove(caminho)
            except OSError: pass
            if not cmd: continue
            self._ativo_em = time.time()
            threading.Thread(target=self._executar_cmd, args=(cmd,), daemon=True).start()

    def _executar_cmd(self, cmd):
        cid = cmd.get("id", "?")
        try:
            if cmd.get("tipo") == "ordem": msg = self.cmd_ordem(cmd)
            elif cmd.get("tipo") == "encerrar": msg = self.cmd_encerrar(cmd)
            else: msg = "Comando desconhecido."
        except Exception as e: msg = "Erro: %s" % str(e)[:160]
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
                    if valor > est["saldo"]: return "Saldo virtual insuficiente."
                    est["saldo"] -= valor
                    pos[sym] = pos.get(sym, 0) + valor / preco
                else:
                    qtd = min(valor / preco, pos.get(sym, 0))
                    if qtd <= 0: return "Sem posicao para vender neste par."
                    est["saldo"] += qtd * preco
                    pos[sym] -= qtd
                    if pos[sym] <= 1e-12: pos.pop(sym, None)
                self.registrar(lado, sym, "manual", "$%.2f" % preco, True)
            self.salvar()
            return "%s simulada em %s." % (nome, par_fmt(sym))
        ex = self.cliente()
        if not ex: return "Configure as chaves da Binance em Ajustes."
        if lado == "BUY": r = ex.comprar(sym, valor)
        else:
            livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
            r = ex.vender(sym, min(valor / preco, livre))
        with self.lock:
            man = est["pos_manual_real"]
            if lado == "BUY": man[sym] = man.get(sym, 0.0) + r["qtd"]
            else:
                man[sym] = max(0.0, man.get(sym, 0.0) - r["qtd"])
                if man[sym] <= 1e-12: man.pop(sym, None)
            self.registrar(lado, sym, "manual", "$%.2f" % (r["preco"] or preco), True)
        self.salvar()
        return "%s executada em %s." % (nome, par_fmt(sym))

    def cmd_encerrar(self, cmd):
        modo = self.config["modo"]
        msgs = []
        for s in cmd.get("simbolos", []):
            try: msgs.append(self.encerrar_sync(s, modo))
            except Exception as e: msgs.append("%s: erro (%s)" % (par_fmt(s), str(e)[:100]))
        self.salvar()
        return "\n".join(msgs) or "Nenhuma posicao aberta."

    def encerrar_sync(self, sym, modo):
        est = self.est
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        man = self.manual_d(modo)
        with self.lock:
            pos = pos_d.get(sym)
            qtd_man = man.get(sym, 0.0)
            if not pos and qtd_man <= 1e-12: return "%s: sem posicao aberta." % par_fmt(sym)
            if pos: pos["encerrando"] = True
        try:
            if modo == "SIM": ps = validar_par(sym)
            else:
                ex = self.cliente()
                if not ex: raise Exception("configure as chaves em Ajustes")
                livre = ex.saldo_livre().get(ex.info(sym)["base"], 0.0)
                total = (pos["qtd"] if pos else 0.0) + qtd_man
                ps = ex.vender(sym, min(total, livre))["preco"]
        except Exception as e:
            with self.lock:
                if pos: pos.pop("encerrando", None)
            if modo == "REAL" and ("-1013" in str(e) or "Invalid quantity" in str(e)):
                with self.lock:
                    pos_d.pop(sym, None)
                    man.pop(sym, None)
                    self.registrar("SYNC", sym, "posicao ja encerrada ou abaixo do minimo", "-", True)
                return "%s: ja estava encerrada na Binance (registro local limpo)." % par_fmt(sym)
            raise
        with self.lock:
            if pos:
                lucro = (ps / pos["entrada"] - 1) * 100
                if modo == "SIM": est["saldo"] += pos["qtd"] * ps
                pos_d.pop(sym, None)
                self.registrar("SELL", sym, "ENCERRADA MANUAL", "%+.2f%%" % lucro, lucro >= 0)
            if qtd_man > 1e-12:
                if modo == "SIM": est["saldo"] += qtd_man * ps
                man.pop(sym, None)
                self.registrar("SELL", sym, "manual (encerrar)", "$%.2f" % ps, True)
        return "%s encerrada @ %s" % (par_fmt(sym), fmt_preco(ps))

    def ciclo(self):
        est, modo = self.est, self.config["modo"]
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        with self.lock:
            regras = [dict(r) for r in self.config["regras"]]
            for s in list(pos_d):
                if not any(r["par"] == s for r in regras): regras.append({"par": s, "valor": 0, "estrategias": []})
        erro = ""
        for regra in regras:
            if not self.config["robo_ativo"] or self.parar.is_set(): break
            try: self.processar_perfil(regra, modo)
            except Exception as e: erro = "[%s] %s" % (regra["par"], str(e)[:120])
        with self.lock:
            est["msg_robo"] = erro
            est["ciclo_ts"] = time.time()
        self.salvar()

    def processar_perfil(self, regra, modo):
        est = self.est
        sym = regra["par"]
        perfil = PERFIS.get(self.config.get("modelo", "5m"), PERFIL_5M)
        pos_d = est["pos_sim"] if modo == "SIM" else est["pos_real"]
        s_gat = INTERVALO_S[perfil["tf_gatilho"]]
        candles = buscar_klines(sym, perfil["tf_gatilho"], 300)
        agora_ms = int(time.time() * 1000)
        fech = velas_fechadas(candles, s_gat, agora_ms)
        if len(fech) < 120: return
        preco = candles[-1][3]
        closes = [x[3] for x in fech][-49:] + [preco]
        ex, livre = None, {}
        if modo == "REAL":
            ex = self.cliente()
            if not ex: raise Exception("configure as chaves em Ajustes")
            livre = ex.saldo_livre()
        pos = pos_d.get(sym)
        if pos:
            if pos.get("encerrando"): return
            if modo == "REAL":
                base = ex.info(sym)["base"]
                if livre.get(base, 0.0) < 0.0001:
                    with self.lock: pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posicao encerrada na Binance", "-", True)
                    self.salvar()
                    return
            with self.lock: pos["topo"] = max(pos["topo"], preco)
            motivo = decidir_saida(pos["entrada"], pos["topo"], preco, closes, pos.get("p"), candles[-70:])
            if motivo: self.fechar(sym, pos, preco, motivo, closes, modo, ex, livre)
            return
        d = preparar_perfil(fech, perfil)
        i = len(fech) - 1
        sinais = sinais_perfil(d, i, perfil)
        tipo = escolher_tipo_dia(sinais, regra.get("estrategias", ESTRATEGIAS))
        if not tipo: return
        if agora_ms - (fech[-1][4] + s_gat * 1000) > s_gat * 300: return
        if preco > fech[-1][3] * 1.002: return
        chave = "%s:%s" % (modo, sym)
        if time.time() - est["ultimo"].get(chave, 0) <= COOLDOWN: return
        valor = float(regra["valor"])
        if valor <= 0: return
        if modo == "SIM":
            with self.lock:
                if est["saldo"] < valor: return
                est["saldo"] -= valor
                preco_exec, qtd = preco, valor / preco
        else:
            inf_s = ex.info(sym)
            quote = inf_s["quote"]
            if valor < inf_s["minn"]: raise Exception("ordem de %.2f abaixo do minimo da Binance para %s (%.2f)" % (valor, sym, inf_s["minn"]))
            if livre.get(quote, 0.0) < valor: return
            r = ex.comprar(sym, valor)
            preco_exec, qtd = r["preco"] or preco, r["qtd"]
        inf = sinais["info"]
        pp = params_atr(inf["atr_pct"])
        pp.update(ma_f=perfil["ma_f"], ma_s=perfil["ma_s"], ma_tipo="ema", atr_p=perfil["atr_p"], rsi_p=perfil["rsi_p"])
        with self.lock:
            pos_d[sym] = {"entrada": preco_exec, "topo": preco_exec, "t": time.time(), "qtd": qtd, "valor": valor, "tipo": tipo, "modelo": perfil["nome"], "rsi": inf["rsi"], "ma9": inf["ema9"], "ma21": inf["ema21"], "vol": inf["atr_pct"], "dist": (inf["ema9"] - inf["ema21"]) / inf["ema21"] * 100, "p9": preco > inf["ema9"], "p21": preco > inf["ema21"], "p": pp}
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
                    with self.lock: pos_d.pop(sym, None)
                    self.registrar("SYNC", sym, "posicao ja encerrada na Binance", "-", True)
                    self.salvar()
                    return
                raise
        lucro = (preco_saida / pos["entrada"] - 1) * 100
        lucro_max = (pos["topo"] / pos["entrada"] - 1) * 100
        a = analisar_legado(closes, (pos.get("p") or {}).get("rsi_p", 14)) or {"rsi": 0, "ma9": 0, "ma21": 0}
        with self.lock:
            if modo == "SIM": est["saldo"] += pos["qtd"] * preco_saida
            self.hist(modo).append({"tipo_entrada": pos["tipo"], "rsi": pos["rsi"], "volatilidade": pos["vol"], "distancia_ma": pos["dist"], "posicao_preco_ma9": pos["p9"], "posicao_preco_ma21": pos["p21"], "forca_tendencia": abs(pos["dist"]), "modelo": pos.get("modelo", "5m"), "resultado": lucro})
            del self.hist(modo)[:-500]
            pos_d.pop(sym, None)
            self.registrar("SELL", sym, motivo, "%+.2f%%" % lucro, lucro >= 0)
        self.salvar()
        self.salvar_relatorio(sym, pos, modo, lucro, preco_saida, lucro_max, a)

    def salvar_relatorio(self, sym, pos, modo, lucro, preco_saida, lucro_max, a):
        try:
            cabecalho = not os.path.exists(self.arq_relatorio) or os.path.getsize(self.arq_relatorio) == 0
            with open(self.arq_relatorio, "a") as f:
                if cabecalho:
                    f.write("Data,Par,Perfil,Estrategia,Preco_Entrada,RSI_Entrada,ATR_Entrada,MA9_Entrada,MA21_Entrada,Preco_Saida,RSI_Saida,MA9_Saida,MA21_Saida,Lucro_Maximo(%),Lucro_Final(%),Motivo_Saida,Tempo(s)\n")
                
                dt_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rsi_in = pos.get("rsi", 0)
                atr_in = pos.get("vol", 0)
                ma9_in = pos.get("ma9", 0)
                ma21_in = pos.get("ma21", 0)
                rsi_out = a.get("rsi", 0)
                ma9_out = a.get("ma9", 0)
                ma21_out = a.get("ma21", 0)
                tempo = int(time.time() - pos["t"])
                
                linha = "%s,%s,%s,%s,%.4f,%.2f,%.3f%%,%.4f,%.4f,%.4f,%.2f,%.4f,%.4f,%.2f,%.2f,%s,%d\n" % (
                    dt_str, sym, pos.get("modelo", "5m"), pos["tipo"], 
                    pos["entrada"], rsi_in, atr_in, ma9_in, ma21_in,
                    preco_saida, rsi_out, ma9_out, ma21_out,
                    lucro_max, lucro, motivo.replace(",", ""), tempo
                )
                f.write(linha)
        except Exception as e: 
            print("Erro ao salvar relatorio CSV:", e)

    def tentar_travar(self):
        try: import fcntl
        except ImportError: return True
        try:
            self._fd_trava = open(os.path.join(self.pasta, "motor.lock"), "w")
            fcntl.flock(self._fd_trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError: return False
        except Exception: return True

    def loop(self, ocioso_max=None):
        if not self.tentar_travar():
            print("Motor ja esta em execucao; saindo.")
            return
        with self.lock:
            self.est["origem"] = self.origem
            self.est["inicio"] = time.time()
        self.salvar()
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
                    if agora - self._ultimo_hb >= 3: self.salvar()
                    if ocioso_max and agora - self._ativo_em > ocioso_max: break
                except Exception as e: print("motor:", e)
                self.parar.wait(1.0)
        finally:
            with self.lock: self.est["origem"] = ""
            self.salvar()
            if self._fd_trava:
                try: self._fd_trava.close()
                except Exception: pass
