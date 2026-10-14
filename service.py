"""service.py - serviço Android em primeiro plano (python-for-android).

É declarado no buildozer.spec:  services = botservice:service.py:foreground:sticky
Roda em outro processo, então continua mesmo com a tela fechada ou o app fora da memória.
Se o Android matar o serviço, o modo "sticky" o reinicia e o robô retoma de onde parou
(posições e estado ficam gravados em disco).
Quando o robô fica desligado e não há comandos por ~90 s, o serviço se encerra sozinho
(a notificação some); a tela o religa quando precisar.
"""
import traceback

from core import Motor, pasta_dados

OCIOSO_MAX = 90   # segundos


def _servico():
    try:
        from jnius import autoclass
        return autoclass("org.kivy.android.PythonService").mService
    except Exception:
        return None


def _wake_lock(srv):
    try:
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        PowerManager = autoclass("android.os.PowerManager")
        pm = srv.getSystemService(Context.POWER_SERVICE)
        wl = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BotTrading::WakeLock")
        wl.acquire()
        return wl
    except Exception as e:
        print("WakeLock:", e)
        return None


def main():
    srv = _servico()
    wl = _wake_lock(srv) if srv is not None else None
    try:
        Motor(pasta_dados()).loop(ocioso_max=OCIOSO_MAX)
    except Exception:
        traceback.print_exc()
    finally:
        try:
            if wl is not None and wl.isHeld():
                wl.release()
        except Exception:
            pass
        try:
            if srv is not None:
                srv.stopSelf()
        except Exception:
            pass


main()
