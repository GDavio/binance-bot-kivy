[app]
title = BinanceBot
package.name = binancebot
package.domain = org.bot
source.dir = .
source.include_exts = py,png,jpg,kv,atlas
version = 1.0

# Cython e suporte SSL/ffi para requisições HTTPS e criptografia do ccxt
requirements = python3,kivy==2.3.0,openssl,requests,urllib3,chardet,certifi,idna,cryptography,ccxt

orientation = portrait
fullscreen = 0
android.permissions = INTERNET
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
