PIO = python3 -m platformio

# PlatformIOのインストールとバージョン確認
ready:
	pip install platformio
	$(PIO) --version

# ビルド
build:
	$(PIO) run

# ビルド + M5Stackへのアップロード
upload:
	$(PIO) run --target upload

# シリアルモニタ起動
monitor:
	$(PIO) device monitor

# webサーバ起動
serve:
	npx serve web
