.PHONY: css vendor messages e2e-up e2e-down

TAILWIND_VERSION := v4.3.3
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)

ifeq ($(UNAME_S),Darwin)
  ifeq ($(UNAME_M),arm64)
    TAILWIND_ASSET := tailwindcss-macos-arm64
  else
    TAILWIND_ASSET := tailwindcss-macos-x64
  endif
else
  ifeq ($(UNAME_M),aarch64)
    TAILWIND_ASSET := tailwindcss-linux-arm64
  else
    TAILWIND_ASSET := tailwindcss-linux-x64
  endif
endif

TAILWIND_BIN := .tools/tailwindcss

E2E_PORT ?= 8100
E2E_ENV := DJANGO_SETTINGS_MODULE=config.settings.e2e

css: $(TAILWIND_BIN)
	$(TAILWIND_BIN) -i static/css/src/input.css -o static/css/app.css --minify
	uv run python scripts/css_manifest.py > static/css/.build-manifest.sha256

$(TAILWIND_BIN):
	mkdir -p .tools
	curl -sSL -o $(TAILWIND_BIN) https://github.com/tailwindlabs/tailwindcss/releases/download/$(TAILWIND_VERSION)/$(TAILWIND_ASSET)
	chmod +x $(TAILWIND_BIN)

vendor:
	mkdir -p static/vendor
	curl -sSL -o static/vendor/htmx.min.js https://unpkg.com/htmx.org@2.0.10/dist/htmx.min.js

messages:
	uv run python manage.py makemessages -l uk -a --ignore=static/vendor/*
	uv run python manage.py makemessages -d djangojs -l uk -a --ignore=static/vendor/*
	uv run python manage.py compilemessages -l uk --ignore=.venv

e2e-up:
	$(E2E_ENV) uv run python manage.py migrate --noinput
	$(E2E_ENV) uv run python manage.py seed_e2e
	$(E2E_ENV) uv run python manage.py collectstatic --noinput --ignore=src
	$(E2E_ENV) uv run python manage.py compilemessages -l uk --ignore=.venv
	uv run playwright install chromium
	mkdir -p .e2e
	$(E2E_ENV) nohup uv run python manage.py run_huey > .e2e/huey.log 2>&1 & echo $$! > .e2e/huey.pid
	$(E2E_ENV) nohup uv run python manage.py runserver $(E2E_PORT) --noreload > .e2e/web.log 2>&1 & echo $$! > .e2e/web.pid
	uv run python scripts/wait_for_port.py $(E2E_PORT) 45

e2e-down:
	-kill `cat .e2e/web.pid .e2e/huey.pid 2>/dev/null` 2>/dev/null
	rm -rf .e2e
