# Единое дерево интерфейса iPhone — OpenRevive 0.5.0

Проверено 15 октября 2026 на BlackLion / MacBook4,1 / OS X 10.7.5 / Xcode 4.6.3 и физическом iPhone3,1 с iOS 6.1.3. Телефон подключён по USB к MacBook; агент работает с современного Mac по SSH/Wi-Fi. Использован существующий jailbreak. Все перечисленные действия выполнены на устройстве.

## Что добавлено

`ui-processes` обнаруживает реальные PID, локальные UIKit-мосты и окна. `tree --all` объединяет интерфейс в дерево `desktop → process → window → view/AX element`, сохраняя владельца, PID, session, локальный ID и native snapshot каждого узла. `ui-find` выбирает единственную доступную цель по подписи, роли, классу, identifier или владельцу. `ui-action … auto` выбирает semantic для кнопок/полей обычного приложения, HID для SpringBoard и VNC для AssistiveTouch. При заданном `--output … --summary` полный снимок сохраняется в файле, а агент получает компактные счётчики и покрытие.

В [снимке обнаружения](unified-ui-evidence/discovery-tree.json) было **60 процессов**, **6 отвечающих мостов**, включая одну службу без окон:

| Процесс | UI-узлов | Тип владельца |
| --- | ---: | --- |
| `org.openrevive.TouchProbe` | 37 | application |
| `com.apple.mobilemail` | 15 | application |
| `com.apple.mobilephone` | 17 | application |
| `com.apple.managedconfiguration.profiled` | 0 | ui-service |
| `com.apple.springboard` | 8 | springboard |
| `com.apple.assistivetouchd` | 9 | assistive-touch |

Это конкретный снимок: число процессов и доступность фоновых приложений меняются. SpringBoard отдаёт больше объектов на Home, чем поверх активного приложения. Перечисление PID использует [Apple `sysctl`](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/sys/sysctl.h); внутренние окна — проверенный на устройстве селектор [UIWindow iOS 6.1](https://github.com/MP0w/iOS-Headers/blob/master/iOS6.1/Frameworks/UIKit/UIWindow.h).

## Проверенные действия

- Общий снимок нашёл настоящий `HNDRocker` с подписью «Меню AssistiveTouch». Автоматическая VNC-команда раскрыла меню; новый снимок нашёл `HNDRockerButton` «Домой». Нажатие вернуло из Probe на Home: foreground сменился с `org.openrevive.TouchProbe` на пустой bundle, `surface:home`. Координаты получены из дерева. [Проверка](unified-ui-evidence/assistive-validation.json).
- Через то же дерево открыто «Аппарат», прочитаны подписи системных контролов, найдена и нажата «Назад», затем «Домой». Звук, громкость, блокировка и поворот при этом не менялись. [Дерево подменю](unified-ui-evidence/assistive-device-tree.json), [наблюдение](unified-ui-evidence/observation-validation.json).
- Поле получило `UNIFIED-48b502dafd Привет 日本語` через semantic `set-text`, а `auto` выбрал semantic `press` для Apply. Приложение независимо записало тот же текст и видимый результат. [Receipt и маршруты](unified-ui-evidence/actions-validation.json).
- Настоящий системный CFUserNotification-диалог поверх приложения найден в том же дереве. Цель приложения под ним отклонена; Accept выбран по владельцу/роли и нажат через HID. Независимый receipt содержит нужный nonce, `accepted=1`, `error=0`, `responseFlags=0`. Из Home также найдена по identifier и запущена иконка Settings.
- Просроченные, использованные, подменённые owner/node/route/snapshot, неоднозначные подписи и изменившийся экран/AssistiveTouch отклонены. Secure-поля и их implementation-поддеревья остались скрыты. [Протокол](unified-ui-evidence/protocol-validation.json).
- Прежние app/system проверки прошли; реальное приложение получило **5 одновременных контактов**, pinch внутрь/наружу с масштабами **0.401/2.588**, все контакты отпущены. [Регрессия мультитача](unified-ui-evidence/multitouch-regression.json).

## Перекрытие и границы покрытия

Перед вводом повторно проверяются текущие владельцы экрана, PID/session/build/layout, foreground/lock/alert-контекст, identity/geometry цели и реальные cross-process hit tests. Прозрачное полноэкранное окно само по себе не закрывает весь экран. На iOS 6 idle-слой AssistiveTouch передаёт касания вокруг плавающей кнопки; раскрытое меню перехватывает также внешние касания. Для этого добавлен отдельный адаптер. Целый desktop epoch расходуется одним успешным действием/разрешением tap и живёт максимум 30 секунд.

Фоновые приложения iOS приостанавливает. Их main-thread мост может перестать отвечать; это показано как пробел в дереве, а не как исчезнувший телефон или пустой интерфейс. [Снимок Home с таким пробелом](unified-ui-evidence/home-with-background-gap.json) показывает недоступный Probe; при этом проверенный HID-запуск Settings с Home работает. Такие фоновые приложения и исчезнувшие службы без окон не блокируют ввод в неизменившийся экран. Foreground, SpringBoard, AssistiveTouch и видимые UI-службы остаются обязательными.

`kbd` и `imagent` перечислены как кандидаты без UIKit endpoint. Их наличие не доказывает существования текущего видимого окна. В дереве есть корень каждого kernel process, но UI-поддерево появляется только при реальном ответе моста. Не заявляется извлечение DOM, OpenGL/canvas-содержимого, stock-iOS и универсальных интерфейсов неизвестных служб; у неподтверждённых UI-служб нет автоматического input route. `coverage` различает полное покрытие известных владельцев экрана и полноту всех отвечающих мостов.

Veency иногда отдаёт кадр без AssistiveTouch из нового соединения, хотя дерево и Home-действие подтверждают открытое меню. [Пример такого кадра](unified-ui-evidence/veency-new-connection-menu.png) и [пояснение](unified-ui-evidence/capture-notes.json) сохранены отдельно. Штатный screenshot-сервис и непрерывная VNC-сессия дали корректные изображения ниже. Для проверки накладок скилл рекомендует native `screenshot` после `prepare`; одно неполное VNC-изображение не служит доказательством отсутствия окна.

| AssistiveTouch, native screenshot | «Аппарат», непрерывная VNC-сессия | Системный диалог |
| --- | --- | --- |
| ![AssistiveTouch](unified-ui-evidence/assistive-menu.png) | ![Device](unified-ui-evidence/assistive-device.png) | ![Dialog](unified-ui-evidence/system-dialog.png) |

## Установка и использование

[Скилл](../plugins/openrevive/skills/legacy-mac/SKILL.md), [подробные команды и восстановление](../plugins/openrevive/skills/legacy-mac/references/unified-ui.md) и все скрипты входят в плагин. Обе фактические установленные копии — Codex и Claude Code — прошли обнаружение, tree/summary, поиск и semantic-действие с app receipt из временных каталогов вне checkout. [Результат установки](unified-ui-evidence/install-validation.json).

```sh
./bin/openrevive device --udid UDID ui-processes
./bin/openrevive device --udid UDID tree --all --output /tmp/interface.json --summary
./bin/openrevive device --udid UDID ui-find /tmp/interface.json --contains AssistiveTouch --role button
./bin/openrevive device --udid UDID ui-action /tmp/interface.json NODE_ID auto
# После каждого действия — новый tree и проверка результата.
```

Нативная сборка телефона: `2006d14a2c09855a1ff74d8da572c3a734417df4c41b02bc8868cf5436be05d8`. BlackLion helper: `releases/0.5.0-fa7afd7cf7ce134c`; doctor готов, desktop AX доступен и версия 0.5.0 подтверждена. Нового сетевого демона не добавлено. Временный исследовательский helper удалён с телефона. Авторизация/USB pairing и прежние схемы деплоя сохранены.

Проверки: **6/6 выполнены**, 0 невыполненных, 0 оставленных. G1–G5 — команды из `tests/check_unified_live.py`, `check_unified_protocol.py`, `check_unified_install.py`; G6 — просмотр настоящих экранов и сопоставление с деревьями/receipt. [G1](unified-ui-evidence/gate-G1.txt), [G2](unified-ui-evidence/gate-G2.txt), [G3](unified-ui-evidence/gate-G3.txt), [G4](unified-ui-evidence/gate-G4.txt), [G5](unified-ui-evidence/gate-G5.txt).
