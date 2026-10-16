<div align="center">

> 🇷🇺 **Русская версия доступна ниже** — [перейти к русской документации ↓](#ru)

# 📱 OPENCODE.telegram.BRIDGE

**Drive OpenCode from Telegram — one shared session with the desktop app and the CLI, plus fully local voice.**

![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D4)
![Installer: Python 3.11+](https://img.shields.io/badge/installer-Python-3.11%2B-blue)
![Shim tests: 24/24](https://img.shields.io/badge/shim_tests-24%2F24-brightgreen)
![Docs: EN / RU](https://img.shields.io/badge/docs-EN%20%2F%20RU-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

[**Install**](#-install) · [**How it works**](#-how-it-works) · [**Audit**](#-audit-every-mismatch-at-once) · [**Troubleshooting**](#-if-something-breaks) · [**Why the shim is mandatory**](#-why-install-npm-shimps1-is-mandatory) · [**Русский**](#ru)

</div>

---

## What it gives you

```
Telegram ──┐
Desktop  ──┼─→ cc-connect ──→ opencode-cli serve --service ──→ opencode.db
CLI TUI  ──┘                      (ONE server, ONE session)

voice in  : Telegram OGG → ffmpeg → MP3 → shim → GigaAM GGUF (Vulkan) → text
voice out : edge-tts (no API key), only after you ask with /tts always
```

No API keys. No cloud. No subscription. The model is a free gateway model and voice
recognition runs on your own GPU.

The point is the single shared session. Two servers means two isolated histories that
never agree, so the bridge is wired to the same `serve --service` the desktop app uses.

---

## Requirements

| | How to check |
|---|---|
| Node.js + the `opencode` CLI | `opencode --version` |
| cc-connect | `npm i -g cc-connect` |
| **ffmpeg** | `ffmpeg -version` — both voice paths need it |
| Python 3.11+ | for the installer and the shim |
| Handy with a GigaAM model | `handy.exe --list-models --json` → `"is_downloaded": true` |
| `edge-tts` | `pip install edge-tts` — only for spoken replies |

The installer checks every one of these against the live registries, offers to update
what is outdated, and installs what is missing — after you agree.

---

## Install

```powershell
git clone https://github.com/dobrdigital/OPENCODE.telegram.BRIDGE.git
cd OPENCODE.telegram.BRIDGE
python setup.py
```

Nine steps. It writes the configs, wires the shim, registers the scheduled tasks and
verifies the result. The only thing you need beforehand is a bot token from
[@BotFather](https://t.me/BotFather).

See what it would do without touching anything:

```powershell
python setup.py --dry-run
```

Unattended:

```powershell
python setup.py --yes --project-dir C:\src\my-app --token 123:ABC --name demo --autostart
```

Every step says what it is for and what breaks without it. After writing, the installer
**reads each answer back off disk and compares**. A mismatch stops the run rather than
leaving a silently wrong config in place.

Full manual steps are in [README.ru](#ru) and in [SKILL.md](SKILL.md).

---

## Audit every mismatch at once

```powershell
python tools\audit.py               # human readable
python tools\audit.py --json        # machine readable
python tools\audit.py --only config # one group
```

Nearly everything that breaks here fails silently. OpenCode prints its help text and
exits 1 on a flag it does not recognise, so a wrong argument is indistinguishable from
a missing feature — and nothing announces itself. The audit reads the repository, the
live install and the machine, prints **every** mismatch rather than stopping at the
first, and puts the fixing command next to each line. It only ever reads.

| Group | What it reconciles |
|---|---|
| `repo` | caches and leftovers committed by a tool run from the repo root, oversized files, tokens |
| `dep` | installed version against the live npm / pip / winget registry |
| `config` | multi-element `cmd`, a pinned `model`, `reset_on_idle_mins`, unreplaced placeholders, `speech` with nothing on its port |
| `shim` | both npm launchers — `opencode.cmd` **and** the extensionless `opencode` — routed through the shim |
| `task` | registered, correct working directory, shim delay strictly before the bridge delay |
| `runtime` | port occupancy, how many servers are running, and **whether the server can see every session** |
| `skew` | all three opencode CLI copies against each other |
| `sessions` | a summary of the session table; deletes nothing |
| `drift` | repository against the live install |

---

## Why `install-npm-shim.ps1` is mandatory

cc-connect 1.5.0 calls OpenCode in two ways that 2.0.x rejects:

```
opencode run --dir <workDir>      → Unrecognized flag: --dir
opencode session list --json      → needs --format json, otherwise a silent exit 1
```

Both fail with a wall of help text and a non-zero exit code, which reads like a broken
feature rather than a rejected flag. The script redirects the npm launcher through a
shim that fixes this.

**Any `npm i -g @opencode/cli` overwrites the shim.** After updating:

```powershell
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1 -Verify
```

`-Verify` changes nothing and returns 1 when a repair is needed. `tools/audit.py --only shim`
reports the same thing without needing the script.

---

## If something breaks

| Symptom | What to do |
|---|---|
| `/list` says `exit status 1` | `opencode-shim/install-npm-shim.ps1` |
| stopped working after `npm update` | same script; `-Verify` only checks |
| `/list` empty but the database has sessions | `restart-server.ps1` — restart the OpenCode server, **not** the machine |
| desktop does not show the Telegram session | open the same project folder in the desktop |
| bot silent after a reboot | the scheduled tasks, see [SKILL.md](SKILL.md) |
| nothing explains itself | `python tools\audit.py` first |

---

## Models

A **free** gateway model ships configured — `space-bunny-free`, no key required. It is
written to `opencode.jsonc.example.json`, which the desktop, the CLI and Telegram all
read, so they start on the same model.

```powershell
opencode models          # what is actually available now
```

Twelve models with short aliases are registered for the in-chat `/model` command:

```
/model switch bunny        # space-bunny-free
/model switch glm          # glm-5.3-flash-free
/model switch nemotron-l   # nemotron-3.5-lightning-free
...
```

The default is **deliberately not pinned** in the bridge config: a `model` set there
silently breaks `/model switch`. It lives only in the shared opencode config.

---

## Version skew is the real enemy

This project exists mostly because **cc-connect 1.5.0 was written against an older
OpenCode**, and a newer OpenCode does not complain about an unknown flag — it prints its
help and exits 1.

Three copies of the opencode CLI exist on a typical machine, updated by three different
schedules: the npm one, the one bundled in the desktop app, and the versioned one the
live server actually runs from. `tools/audit.py --only skew` compares all three and
separates a major/minor difference (FAIL — different code lines) from a patch difference
(WARN — probably harmless).

If something breaks "for no reason", start with `python tools\audit.py`.

---

## License

MIT. Built around [cc-connect](https://github.com/chenhg5/cc-connect) and
[OpenCode](https://github.com/sst/opencode); both are the work of their authors.
---

<a id="ru"></a>

## Русская документация

## Что это даёт

```
Telegram ──┐
Desktop  ──┼─→ cc-connect ──→ opencode-cli serve --service ──→ opencode.db
CLI TUI  ──┘                      (ОДИН сервер, ОДНА сессия)

голос на вход : Telegram OGG → ffmpeg → MP3 → shim → GigaAM GGUF (Vulkan) → текст
голос на выход: edge-tts (без API-ключа), только по команде /tts always
```

Без ключей, без облака, без подписки.

---

## Требования

| | Проверка |
|---|---|
| Node.js + `opencode` CLI | `opencode --version` |
| cc-connect | `npm i -g cc-connect` |
| **ffmpeg** | `ffmpeg -version` — нужен **обоим** голосовым путям |
| Python 3.10+ | для shim |
| Handy + модель GigaAM | `handy.exe --list-models --json` → `"is_downloaded": true` |
| `edge-tts` | `pip install edge-tts` — только если нужен голос на выход |

---

## Установка

```powershell
git clone <this-repo> cc-connect-bridge
cd cc-connect-bridge
python setup.py
```

Восемь вопросов — дальше установщик сам пишет конфиги, ставит шим, регистрирует
задачи и проверяет результат. Единственное, что нужно заранее, — токен бота у
[@BotFather](https://t.me/BotFather).

Посмотреть, ничего не записывая:

```powershell
python setup.py --dry-run
```

Для скриптов и машин без диалога:

```powershell
python setup.py --yes --project-dir C:\src\my-app --token 123:ABC --name demo --autostart
```

### Что спрашивает установщик

| Шаг | Вопрос | Откуда берёт |
|---|---|---|
| 1 | ничего | проверяет opencode, cc-connect, ffmpeg, edge-tts, Handy |
| 2 | токен бота | вы, с проверкой формата |
| 3 | папка проекта | вы, либо создаёт |
| 4 | модель по умолчанию | меню из 12 бесплатных, дефолт `space-bunny-free` |
| 5 | имя сессии | вы, либо из имени папки |
| 6 | голос на вход | предлагает, если Handy и модель уже скачаны |
| 7 | голос на выход | по умолчанию **выключен** |
| 8 | — | пишет конфиги и сверяет результат |

После записи установщик **перечитывает каждый ответ из файла на диске** и сверяет.
Не совпало — останавливается, а не оставляет молча неверный конфиг.

### Установка вручную

Если установщик не подходит — те же шаги по отдельности:

```powershell
# 1. Модель по умолчанию (бесплатная, ключ не нужен)
copy opencode.jsonc.example.json "$env:USERPROFILE\.config\opencode\opencode.jsonc"

# 2. Конфиг моста: копия шаблона + свой токен бота
copy config.example.toml "$env:USERPROFILE\.cc-connect\config.toml"
notepad "$env:USERPROFILE\.cc-connect\config.toml"   # токен + work_dir

# 3. Лаунчеры
copy start-cc-connect.ps1 "$env:USERPROFILE\.cc-connect\"
copy gigaam-stt\* "$env:USERPROFILE\.cc-connect\gigaam-stt\" -Force

# 4. Починить вызов opencode (обязательно, см. «Почему это важно»)
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1

# 5. Автозагрузка
powershell -ExecutionPolicy Bypass -File .\install-autostart.ps1

# 6. Проверка
powershell -ExecutionPolicy Bypass -File .\autostart-test.ps1
```

Ожидаемо: `failures: 0`.

---

## Почему `install-npm-shim.ps1` обязателен

cc-connect v1.5.0 вызывает OpenCode двумя способами, которые 2.0.28 отвергает:

```
opencode run --dir <workDir>      → Unrecognized flag: --dir
opencode session list --json      → нужен --format json, иначе тихий exit 1
```

Скрипт перенаправляет npm-шим на шим, который это чинит.

**Любой `npm i -g @opencode/cli` перезапишет шим.** После обновления:

```powershell
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1 -Verify
```

`-Verify` ничего не меняет и возвращает 1, если чинить надо.

---

## Проверка

```powershell
# всё ли живо
powershell -ExecutionPolicy Bypass -File .\autostart-test.ps1

# шим
node .\opencode-shim\test-opencode-shim.js          # 24 passing

# голос
curl http://127.0.0.1:8300/health
curl -X POST http://127.0.0.1:8300/v1/audio/transcriptions -F "file=@sample.wav" -F "model=whisper-1"

# шим не сломан (после любого npm update)
powershell -ExecutionPolicy Bypass -File .\opencode-shim\install-npm-shim.ps1 -Verify
```

### Аудит: все расхождения сразу

```powershell
python tools\audit.py               # человекочитаемо
python tools\audit.py --json        # для CI
python tools\audit.py --only config # одна группа
```

Большинство поломок здесь **молчат**. OpenCode на неизвестный флаг печатает
свою справку и выходит с кодом 1 — неправильный аргумент выглядит ровно так
же, как отсутствующая функция. Ничто само о себе не сообщает, поэтому есть
один инструмент, который проверяет всё и **не останавливается на первой
ошибке**:

| группа | что сверяет |
|---|---|
| `repo` | кэши и мусор в репозитории, крупные файлы, токены |
| `dep` | установленная версия против официального реестра |
| `config` | `cmd` из нескольких элементов, закреплённая модель, `reset_on_idle_mins`, заглушки, `speech` без слушающего порта |
| `shim` | оба лаунчера npm (`opencode.cmd` **и** `opencode`) перенаправлены |
| `task` | задачи зарегистрированы, рабочий каталог, порядок задержек |
| `runtime` | порты, сколько серверов, и **видит ли сервер все сессии** |
| `skew` | все три копии opencode-CLI против друг друга |
| `sessions` | сводка по базе сессий; ничего не удаляет |
| `drift` | репозиторий против живой установки |

### Сколько серверов — и какие у них мнения

Один сервер и один устаревший сервер снаружи выглядят одинаково, а разница —
ровно тот симптом, о котором сообщают: `/list` пуст, а база полна. Поэтому
проверка сравнивает два вида: через работающий сервер и напрямую
(`--standalone`, читает базу в обход сервера).

Здесь это дало конкретный диагноз вместо совета «перезагрузите машину»:

```
[FAIL] the shared server sees 0 of 9 session(s)
       fix: restart the OpenCode server - not the bridge, and not the machine
```

Сервер **запущен и жив**, просто его представление о базе отстало. Раньше это
выглядело как «нужна перезагрузка» — теперь видно, какая половина сравнения
врёт, и что именно надо перезапустить. Перезагрузка машины ради этого
убивала сессию, чтобы починить процесс, который заменяется за две секунды:

```powershell
powershell -ExecutionPolicy Bypass -File .\restart-server.ps1
```

Скрипт останавливает **только** `serve --service`, не трогая ни мост, ни окно
десктопа, и сначала печатает, что собирается сделать.

### Про расхождение версий

Десктоп OpenCode — Electron-приложение, и оно **поставляет собственный
`opencode-cli.exe`**, который обновляется вместе с приложением. npm-CLI
обновляется только когда кто-то вручную выполнит `npm i -g @opencode/cli`.
Они разъезжаются сами, без чьего-либо решения, а симптом у них общий: тот,
кто не понимает чужих аргументов, печатает справку и выходит с кодом 1 —
и это выглядит как отсутствующая функция, а не как расхождение версий.

Поэтому `skew` сверяет `resources/opencode-cli.version` из десктопа с тем,
что стоит в npm. Разница в мажорной или минорной части — **FAIL**: это разные
линии кода, и база, написанная одной, может не читаться другой. Разница
только в патче — **WARN**: скорее всего безобидна, и лечится догонкой, а не
паникой. Смешивать их в одну FAIL нельзя — тогда перестанут читать и настоящие.

Аудит **только читает**. Он ничего не чинит и не останавливает — рядом с
каждым расхождением печатается команда, которая его исправляет. `setup.py`
запускает его последним шагом, а `--dry-run` — тоже.

Проверить, что аудит вообще способен что-то найти:

```powershell
python tools\check-audit.py     # 9 проверок на заведомо сломанной копии
```

Этот тест существует потому, что «всё хорошо» на исправной машине
выглядит **неотличимо** от проверки, которая никогда не срабатывает.

### Почему у каждого шага есть пояснение

Каждый шаг установщика говорит одной фразой, зачем он и что сломается без
него: «OpenCode выходит с кодом 1 на незнакомый флаг, не сообщая почему —
поэтому устаревшая копия выглядит ровно как отсутствующая функция» полезно,
а «проверьте настройку» не полезно никому.

Это легко соблюдать один раз и легко забыть, когда добавляешь шаг, поэтому
проверяется:

```powershell
python tools\check-steps.py
```

Шаг без пояснения валит сборку. И пояснение обязано быть коротким — абзац
отодвинет вопрос за край экрана, а вопрос, которого не видно, отвечают Enter'ом.

---

## Если что-то сломалось

| Симптом | Что делать |
|---|---|
| `/list` → `exit status 1` | `opencode-shim/install-npm-shim.ps1` |
| после `npm update` перестало работать | там же — `-Verify` только проверяет |
| `/list` пуст, а сессии в базе есть | устаревший сервер → `restart-server.ps1`, **не** перезагрузка ОС |
| десктоп не показывает сессию Telegram | открой ту же папку проекта в десктопе |
| бот молчит после перезагрузки | задачи планировщика, см. ниже |

Подробности — в [SKILL.md](SKILL.md): расхождение версий cc-connect и OpenCode, и все
грабли с обоснованием.

---

## Модели

Из коробки настроена **бесплатная** модель шлюза opencode — `space-bunny-free`, ключ не нужен.
Она прописана в `opencode.jsonc.example.json`, который читают десктоп, CLI и Telegram одинаково.

```powershell
opencode models          # актуальный список доступных
```

В мостовом конфиге зарегистрированы 12 моделей с короткими алиасами — реестр для команды `/model`:

```
/model switch bunny        # space-bunny-free
/model switch glm          # glm-5.3-flash-free
/model switch nemotron-l   # nemotron-3.5-lightning-free
...
```

**Модель по умолчанию не закрепляется в мостовом конфиге намеренно** — прописанный там `model`
ломает `/model switch`. Значение живёт только в общем конфиге opencode.

---

## Расхождение версий — главный источник боли

Этот проект существует в основном потому, что **cc-connect 1.5.0 писался под более старый
OpenCode**, а более новый OpenCode на неизвестный флаг не ругается, а молча печатает справку
и выходит с кодом 1.

Если что-то сломалось «непонятно как» — сначала прочитай раздел
**«Расхождение версий»** в [SKILL.md](SKILL.md).

---

## Состав

```
SKILL.md                    разбор: архитектура, все шаги, 15 граблей
install-autostart.ps1       регистрация задач планировщика (+/- -Uninstall)
restart-server.ps1          перезапуск общего сервера OpenCode, без перезагрузки ОС
config.example.toml         шаблон конфига моста, 12 бесплатных моделей
opencode.jsonc.example.json модель по умолчанию для всех клиентов сразу
autostart-test.ps1          автотест, секции A–F, включая живой round-trip распознавания
start-cc-connect.ps1        лаунчер моста
gigaam-stt/server.py        OpenAI-совместимый STT поверх Handy (только stdlib)
gigaam-stt/start-gigaam-stt.ps1
opencode-shim/              шим совместимости + тесты + install-npm-shim.ps1
stt-selftest.txt            фраза для автотеста (UTF-8 с BOM — иначе PS 5.1 ломается)
tools/audit.py              все расхождения сразу: репо ↔ живая установка ↔ машина
tools/check-audit.py        доказывает, что аудит вообще способен сработать
tools/check-steps.py        требует пояснение у каждого шага установщика
tools/deps.py               версии из живых реестров; отличает «нет» от «не смог проверить»
tools/                      санайтизатор путей и проверка синтаксиса перед публикацией
```

---

## Лицензия и благодарность

MIT. Проект собран вокруг [cc-connect](https://github.com/chenhg5/cc-connect) и
[OpenCode](https://github.com/sst/opencode); обе части — их авторов.
GigaAM — модель Сбера. Распознавание речи и синтез выполняются локально, данные никуда не уходят.