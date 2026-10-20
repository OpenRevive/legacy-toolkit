# UIKit и мультитач на iPhone 4 — 15 октября 2026

OpenRevive 0.3.0 получает настоящее дерево UIKit и accessibility-контейнеров, выполняет защищённые действия с формами и отправляет настоящие одновременные касания через системный HID. Проверено на физическом **iPhone 4 GSM / iPhone3,1 / iOS 6.1.3 / 10B329**, armv7, портретный экран 640×960. Телефон подключён USB к BlackLion; контроллер работает по Wi-Fi/SSH через Lion 10.7.5 и Xcode 4.6.3.

Использованы уже существующие jailbreak, AFC2, AppSync и MobileSubstrate. Новый jailbreak или восстановление прошивки не выполнялись. Instruments не участвует: его старые проблемы авторизации не исправлены и не мешают этому пути.

## Что проверено на устройстве

| Проверка | Наблюдаемый результат |
|---|---|
| Дерево тестового приложения | 22 реальных узла; UITextField, UIButton, пользовательский UIView и отдельный UIAccessibilityElement |
| Геометрия | Поле `[40,250,560,72]`, кнопка `[40,370,560,88]`, площадка `[40,710,560,210]` в пикселях; масштаб UIKit 2 |
| Защищённое поле | Значение пустое, внутреннее поддерево исключено; контрольная строка отсутствует во всём JSON |
| Границы обхода | Depth 1 / limit 2 возвращает не больше 2 узлов и `truncated:true` |
| Штатные «Настройки» | 105 узлов на главном экране; распознаны Wi-Fi, Bluetooth, «Основные», заголовок и кнопка возврата |
| Настоящий тап по дереву | «Основные» открывается через HID; возврат в «Настройки» подтверждён новым деревом |
| Прокрутка | UIScrollView offset изменился с `[0,0]` до `[0,100]` UIKit points |
| Форма | Новый nonce с `Привет 日本語` появился на экране и в независимом `probe-result.json` приложения |
| Неверные действия | Подмена label, уже использованный снимок, другой foreground owner и снимок до реального перезапуска процесса отклонены без изменения результата |
| Раздельные касания | Две непересекающиеся по времени дорожки дают пик 1, без pinch |
| Пять пальцев | В одном UIEvent приложение наблюдает 5 разных UITouch; затем все 5 завершаются, active 0 |
| Pinch внутрь | 24 события UIPinchGestureRecognizer; scale ≈ 0,401 |
| Pinch наружу | 29 событий; scale ≈ 2,588; оба контакта завершены |
| Плохой ввод | Координаты вне экрана и неверный foreground bundle отклонены; приложение не получило новых событий |
| Прерывание жеста | Home на двух активных пальцах → `gesture-interrupted`, active 0; следующий pinch дал scale 2,588 и 29 событий |
| Нативная проверка | Move-before-down отклонён самим драйвером до отправки, receipt приложения не изменился |
| Повторная установка | Совпадение хешей и загруженного build ID даёт `alreadyInstalled:true`, `ready:true`, без respring |
| Установленные плагины | Копии Codex и Claude Code 0.3.0 прочитали живые status/tree из временных каталогов вне репозитория; все файлы пакета совпали с исходниками |

Значение `maxContactsSent` проверяет только отправку. Доказательство мультитача — собственный журнал приложения: `UIEvent.allTouches`, различные идентификаторы UITouch, фазы и счётчик активных контактов. Pinch обрабатывает обычный UIPinchGestureRecognizer; тест не вызывает его обработчик напрямую. Масштаб распознавателя не обязан совпадать с отношением расстояний пальцев из-за порога начала распознавания.

## Устройство реализации

- `legacy-uikit.m` загружается MobileSubstrate в UIKit-приложения, обходит живые UIView и UIAccessibility-контейнеры в главном потоке. Снимок ограничен по времени, глубине и числу узлов. У iOS 6 навигационные заголовки рисуются без UILabel: подписи этих узлов берутся из публичных UINavigationItem, с явным `labelSource`.
- `legacy-hid.m` загружается только в backboardd на iOS 6. Перехватывает настоящий callback IOHIDEventSystemOpen и подаёт туда digitizer hand/finger события. Одновременные контакты входят в один hand event со стабильными идентификаторами. Поддельные UITouch и ручной вызов методов приложения не используются.
- `legacy-uikit-client.m` проверяет текущие SpringBoard owner/PID/lock и передаёт запросы через локальные CFMessagePorts. Команды идут через существующий paired USB root service, затем SSH. Дополнительного сетевого слушателя нет.
- Сборка выполняется пользовательским SDK iphoneos6.1, armv7, с `-Werror`. Двоичные файлы подписываются на телефоне и публикуются переименованием нового файла, чтобы не перезаписывать inode уже загруженной библиотеки.

Снимок действует 30 секунд, следующий обход заменяет его, успешное действие поглощает. Проверяются UDID, bundle ID, PID, сессия, nonce снимка, путь/класс/подпись узла, текущие bounds, доступность и hit test. Секретный токен хранится в личных файлах и скомпилированных для этого устройства библиотеках; в репозиторий они не входят.

## Использование

Полная последовательность установки, формат жестов, действия и восстановление находятся в самодостаточном [скилле](../plugins/openrevive/skills/legacy-mac/SKILL.md) и [справочнике UIKit/HID](../plugins/openrevive/skills/legacy-mac/references/uikit.md).

```sh
./bin/openrevive device --udid "$UDID" setup-uikit --respring
# После первого setup: разбудить/разблокировать и запустить приложение.
./bin/openrevive device --udid "$UDID" ui-status --bundle org.openrevive.TouchProbe
./bin/openrevive device --udid "$UDID" tree --bundle org.openrevive.TouchProbe \
  --depth 12 --limit 800 --output /tmp/tree.json
./bin/openrevive device --udid "$UDID" ui-action /tmp/tree.json "$NODE_ID" set-text -- 'Привет 日本語'
# Перед следующим действием нужен новый tree и актуальный NODE_ID.
./bin/openrevive device --udid "$UDID" pinch 320 820 120 360 \
  --bundle org.openrevive.TouchProbe --duration 1
./bin/openrevive device --udid "$UDID" gesture /tmp/gesture.json --bundle org.openrevive.TouchProbe
```

`set-text`, `press` и прочие семантические действия вызывают соответствующее поведение UIKit-контрола; для приложений, зависящих от полной последовательности касаний, есть `tap` и `gesture`. Дорожки жеста поддерживают 1–5 пальцев, независимое время down/up и промежуточные точки, максимум 5 секунд. Положение и pinch span задаются в **пикселях**, scroll offset — в **UIKit points**. Проверка owner/PID/lock повторяется перед каждым кадром; ошибка прерывает жест и освобождает оставшиеся контакты.

## Артефакты и воспроизведение

- [Дерево приложения](uikit-evidence/probe-tree.json), [дерево раздела «Основные»](uikit-evidence/settings-general-tree.json), [проверка геометрии/навигации](uikit-evidence/tree-validation.json).
- [Результат формы и отказов](uikit-evidence/actions-validation.json), [сводка мультитача](uikit-evidence/multitouch-validation.json), [проверка двух установленных копий](uikit-evidence/install-validation.json).
- [Прерывание и восстановление](uikit-evidence/interruption-validation.json), [события при Home](uikit-evidence/interrupted-receipt.json), [события следующего pinch](uikit-evidence/recovered-receipt.json).
- Независимые журналы: [раздельные тапы](uikit-evidence/sequential-receipt.json), [пять пальцев](uikit-evidence/five-receipt.json), [pinch внутрь](uikit-evidence/pinch-close-receipt.json), [pinch наружу](uikit-evidence/pinch-open-receipt.json).

![Unicode в поле и результате приложения](uikit-evidence/unicode-form.png)

![Пять контактов в UIEvent](uikit-evidence/five-contacts.png)

![Результат pinch](uikit-evidence/pinch.png)

![Настройки после прокрутки](uikit-evidence/settings-scrolled.png)

```sh
python3 tests/check_uikit_protocol.py
python3 tests/check_uikit_live.py tree --udid "$UDID"
python3 tests/check_uikit_live.py actions --udid "$UDID"
python3 tests/check_uikit_live.py multitouch --udid "$UDID"
python3 tests/check_uikit_install.py --udid "$UDID"
python3 tests/check_uikit_interruption.py --udid "$UDID"
```

Проверки с телефоном выполняются последовательно и управляют устройством. Они используют свежие UDID/PID/containerPath, новый текстовый nonce и собственные текущие журналы, не старые файлы из отчёта. Все **7/7 критериев** выполнены: шесть исполняемых проверок и ручная сверка изображений; [команды, fingerprints и хеши артефактов](uikit-evidence/verification.json). Исполняемые проверки завершились кодом 0 со своими маркерами успеха; изображения просмотрены отдельно и сопоставлены с деревьями/receipt.

## Границы результата

Дерево принадлежит одному UIKit-приложению. Это не универсальное системное AX-дерево: SpringBoard, чужие системные окна, web DOM, OpenGL/canvas и виртуализованные невидимые строки требуют отдельного подхода. Для последних нужно прокручивать и читать дерево заново. Портрет iPhone3,1 / iOS 6.1.3 подтверждён; другие устройства, версии iOS, повороты и особенности сторонних приложений требуют отдельной проверки. Базовые путь/версия проверяются установщиком; новый iOS ABI автоматически не выбирается.

Live-проверены set-text, UIButton press, тап из дерева, UIScrollView scroll, 1/2/5 контактов и pinch в обе стороны. Focus, switch/slider setters и accessibilityActivate реализованы для соответствующих объектов, но отдельного физического теста каждого такого типа этот отчёт не заявляет. Instruments и UIKit-дерево Simulator по-прежнему не подтверждены.

Исходные материалы и точные API/ABI-ссылки приведены в [справочнике](../plugins/openrevive/skills/legacy-mac/references/uikit.md#implementation-sources): SimulateTouch, старые IOKit headers, Apple IOHIDFamily, WebKit и LLDB. Чужие исполняемые touch-библиотеки не распространяются.
