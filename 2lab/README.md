# Лабораторная работа №2
## Клиент-серверная программа для передачи файлов по UDP

### Задание
Модифицировать программу из Л.р. №1 для работы по протоколу UDP с надёжной доставкой.

### Реализованные механизмы

#### 1. Подтверждение передачи пакетов (ACK)
- Каждый полученный пакет подтверждается ACK пакетом
- Формат пакета: type(1) + seq_num(4) + total_size(4) + checksum(4) + данные

#### 2. Повторная передача при потере
- Таймаут ожидания ACK: 2 секунды
- Максимальное количество попыток: 5
- При NACK - мгновенная ретрансмиссия

#### 3. Скользящее окно
- Размер окна: 10 пакетов
- Позволяет отправлять пакеты без ожидания ACK на каждый
- Увеличивает пропускную способность

### Оптимальный размер буфера

**1472 байта** - оптимальный размер для UDP:
- MTU Ethernet: 1500 байт
- Заголовок IP: 20 байт
- Заголовок UDP: 8 байт
- Полезные данные: 1500 - 20 - 8 = **1472 байта**

### Сравнение производительности

| Протокол | Производительность |
|----------|-------------------|
| TCP (Л.р. №1) | ~100 Kbps |
| UDP (Л.р. №2) | ~150-200 Kbps |

**UDP быстрее в 1.5-2 раза** благодаря:
- Отсутствию установки соединения (3-way handshake)
- Меньшим накладным расходам на заголовки
- Отсутствию встроенного flow control

### Файлы
- `udp_transfer.py` - UDP передача файлов

### Запуск

#### 1. Запуск сервера (приём файла):

Откройте первый терминал:
```bash
cd C:\VS_Code\webJESUSv1
python 2lab/udp_transfer.py server 8080
```

Ожидайте подключения клиента:
```
[UDP SERVER] Listening on 0.0.0.0:8080
[UDP RECV] Waiting for file...
[UDP] Window size: 10 packets
[UDP RECV] Waiting for START packet...
```

#### 2. Запуск клиента (отправка файла):

Откройте второй терминал:
```bash
cd C:\VS_Code\webJESUSv1
python 2lab/udp_transfer.py client 127.0.0.1 8080
```

Введите путь к файлу когда попросит:
```
Enter file path: 1lab/uploads/test.txt
```

#### 3. Наблюдайте за процессом:

**Клиент:**
```
[UDP SEND] test.txt (23.00 B)
[UDP] Window size: 10 packets
[UDP] Buffer size: 1472 bytes
[UDP] START acknowledged

[STATISTICS]
  Packets sent: 3
  Bytes sent: 23.00 B
  Time: 0.01 seconds
  Bitrate: 12632.00 bps (12.34 Kbps)
```

**Сервер:**
```
[UDP RECV] test.txt (23.00 B)
[UDP] Sent ACK for START

[STATISTICS]
  Bytes received: 23.00 B
  Time: 4.75 seconds
[UDP RECV COMPLETE] Saved to: C:\VS_Code\webJESUSv1\downloads\test.txt
```

#### 4. Проверка результата:

```bash
# Windows
type downloads\test.txt
type 1lab\uploads\test.txt

# Linux/macOS
cat downloads/test.txt
cat 1lab/uploads/test.txt
```

Файлы должны быть идентичны.

### Тестирование потери пакетов

#### 1. Создание тестового файла:

```bash
# Маленький файл (23 байта)
echo Hello UDP Test File > 1lab/uploads/test.txt

# Большой файл (1 MB) для демонстрации скользящего окна
fsutil file createnew 1lab\uploads\large.bin 1048576
```

#### 2. Передача большого файла:

**Терминал 1 (сервер):**
```bash
python 2lab/udp_transfer.py server 8080
```

**Терминал 2 (клиент):**
```bash
python 2lab/udp_transfer.py client 127.0.0.1 8080
Enter file path: 1lab\uploads\large.bin
```

**Ожидайте:**
- Около 714 пакетов (1048576 / 1472)
- Прогресс в процентах
- Статистику с битрейтом

#### 3. Тестирование с потерей пакетов (Windows Firewall):

**DROP пакеты (без уведомления):**
```powershell
# Создать правило для отбрасывания UDP пакетов
netsh advfirewall firewall add rule name="Drop UDP 8080" dir=in action=block protocol=UDP localport=8080

# Запустить передачу - будут ретрансмиссии

# Удалить правило после теста
netsh advfirewall firewall delete rule name="Drop UDP 8080"
```

**REJECT пакеты (с уведомлением):**
```powershell
# Создать правило для отклонения с ICMP ошибкой
netsh advfirewall firewall add rule name="Reject UDP 8080" dir=in action=block protocol=UDP localport=8080 remoteip=any
```

#### 4. Наблюдение за ретрансмиссиями:

При потере пакетов вы увидите:
```
[RETRANSMIT] Packet 5 (retry 1)
[RETRANSMIT] Packet 12 (retry 2)
```

В статистике:
```
  Retransmissions: 5
  Packet loss: 2.5%
```

### Вопрос 1: Выбор оптимального размера буфера

**Оптимальный размер: 1472 байта**

Обоснование:
1. **MTU Ethernet** - максимальный размер кадра: 1500 байт
2. **Заголовки**:
   - IP header: 20 байт (без опций)
   - UDP header: 8 байт
   - Итого: 28 байт
3. **Фрагментация**:
   - При размере > 1472 байт происходит IP фрагментация
   - Фрагментация увеличивает вероятность потери пакетов
   - Потеря одного фрагмента = потеря всего пакета

**Ограничения Ethernet**:
- Максимальный размер кадра (MTU): 1500 байт
- Минимальный размер: 64 байта
- При превышении MTU - IP фрагментация

### Структура пакета

```
+--------+--------+--------+--------+
|  Type  | Seq Num |Total Size|Checksum|
+--------+--------+--------+--------+
|            Data (max 1472)          |
+-------------------------------------+
```

### Типы пакетов
- `PKT_START (0x01)` - начало передачи
- `PKT_DATA (0x02)` - данные файла
- `PKT_ACK (0x03)` - подтверждение
- `PKT_NACK (0x04)` - запрос ретрансмиссии
- `PKT_END (0x05)` - конец передачи
