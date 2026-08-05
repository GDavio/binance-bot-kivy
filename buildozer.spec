[app]
title = BinanceBot
package.name = binancebot
package.domain = org.bot
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# Dependências da aplicação com Python, Cython, Kivy e CCXT incluídos
requirements = hostpython3==3.11.0,python3==3.11.0,cython==0.29.36,kivy==2.3.0,requests,urllib3,chardet,certifi,idna,ccxt

orientation = portrait
fullscreen = 0
android.permissions = INTERNET

# Configurações do SDK/NDK suportadas pelo container
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
