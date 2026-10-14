# cc-connect ↔ OpenCode: Telegram-мост с локальным голосом

Поднимает OpenCode в Telegram через мост [cc-connect](https://github.com/chenhg5/cc-connect) так,
чтобы **одна сессия была общей** для Telegram, десктоп-клиента и CLI — плюс распознавание речи
локально (GigaAM на своей видеокарте) и голосовые ответы. Windows.

Полный разбор с обоснованиями и граблями — в **[SKILL.md](SKILL.md)**. Здесь быстрый старт.

---

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

---

## Если что-то сломалось

| Симптом | Что делать |
|---|---|
| `/list` → `exit status 1` | `opencode-shim/install-npm-shim.ps1` |
| после `npm update` перестало работать | там же — `-Verify` только проверяет |
| `/list` пуст, а сессии в базе есть | устаревшая служба, нужен перезапуск машины |
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
/model switch ling         # ling-3.1-flash-free
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
config.example.toml         шаблон конфига моста, 12 бесплатных моделей
opencode.jsonc.example.json модель по умолчанию для всех клиентов сразу
autostart-test.ps1          автотест, секции A–F, включая живой round-trip распознавания
start-cc-connect.ps1        лаунчер моста
gigaam-stt/server.py        OpenAI-совместимый STT поверх Handy (только stdlib)
gigaam-stt/start-gigaam-stt.ps1
opencode-shim/              шим совместимости + тесты + install-npm-shim.ps1
stt-selftest.txt            фраза для автотеста
tools/                      санайтизатор путей и проверка синтаксиса перед публикацией
```

---

## Лицензия и благодарность

MIT. Проект собран вокруг [cc-connect](https://github.com/chenhg5/cc-connect) и
[OpenCode](https://github.com/sst/opencode); обе части — их авторов.
GigaAM — модель Сбера. Распознавание речи и синтез выполняются локально, данные никуда не уходят.