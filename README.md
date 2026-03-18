# TCP Socket Laboratory Work #1

Python implementation of a TCP socket server and client with file transfer capabilities.

## Project Structure

```
webJESUSv1/
├── server.py           # TCP server implementation
├── client.py           # TCP client implementation
├── uploads/            # Files available for download
├── downloads/          # Downloaded files storage
├── .checkpoints/       # Transfer resume checkpoints
├── .gitignore          # Git ignore rules
└── README.md           # This file
```

## Features

### Server Commands
| Command | Description |
|---------|-------------|
| `ECHO <text>` | Returns text back to client |
| `TIME` | Returns server current time |
| `UPLOAD <filename>` | Receive file from client |
| `DOWNLOAD <filename>` | Send file to client |
| `CLOSE` / `EXIT` / `QUIT` | Close connection |

### Client Features
- Interactive command interface
- Progress bar with speed calculation
- Resume support for interrupted transfers
- Out-of-band progress data display

### Technical Features
- **SO_KEEPALIVE**: Connection health monitoring with configurable intervals
- **Resume Support**: Automatic resume from checkpoint after connection loss
- **Progress Display**: Real-time progress bar with transfer speed
- **Bitrate Calculation**: Shows transfer speed in bps/Kbps
- **Single-threaded**: Sequential client handling
- **Cross-platform**: Works on Windows, Linux, macOS

## Requirements

- Python 3.6+
- No external dependencies (uses only standard library)

## Usage

### Start Server

```bash
python server.py [port]
# Default port: 8080
```

Example:
```bash
python server.py 8080
```

### Start Client

```bash
python client.py <server_address> [port]
# Default port: 8080
```

Example:
```bash
python client.py 127.0.0.1 8080
```

### Using telnet/netcat

```bash
# Using telnet
telnet localhost 8080

# Using netcat
nc localhost 8080

# Example commands via telnet/netcat:
ECHO Hello World
TIME
CLOSE
```

## File Transfer

### Upload File to Server

In client interactive mode:
```
> UPLOAD myfile.txt
```

Or the server will prompt for the file after UPLOAD command.

### Download File from Server

```
> DOWNLOAD myfile.txt
```

Files are saved to:
- Server: `uploads/` directory
- Client: `downloads/` directory

### Resume Interrupted Transfer

If a transfer is interrupted:
1. Checkpoint is automatically saved
2. Reconnect and issue the same command
3. Transfer resumes from the last position

## Connection Recovery

The implementation handles network interruptions:

1. **SO_KEEPALIVE** is configured for connection monitoring
2. **Automatic resume** - transfers continue from checkpoint
3. **Session tracking** - same client can resume their transfer
4. **Checkpoint cleanup** - removed after successful transfer

### Keepalive Settings

| Platform | Keepalive Time | Interval | Count |
|----------|---------------|----------|-------|
| Linux | 10s | 5s | 3 |
| macOS | 10s | default | default |
| Windows | 10s | 5s | 3 |

## Network Testing

### Port Scanning (nmap)

```bash
nmap -p 1-10000 <server_ip>
```

### Check Open Sockets

**Windows:**
```cmd
netstat -an | findstr :8080
```

**Linux/macOS:**
```bash
netstat -an | grep :8080
# or
ss -tlnp | grep 8080
```

## Protocol

### Command Format
```
COMMAND [args]\r\n
```

### Server Responses
```
OK\r\n
ERROR: message\r\n
READY\r\n
RESUME <offset>\r\n
GOODBYE\r\n
```

### File Transfer Protocol

**Upload:**
1. Client sends: `UPLOAD filename\r\n`
2. Server responds: `READY\r\n` or `RESUME <offset>\r\n`
3. Client sends metadata: `<size> <filename>\r\n`
4. Client sends file data in chunks
5. Server sends progress via OOB data
6. Server responds: `OK\r\n` or `ERROR\r\n`

**Download:**
1. Client sends: `DOWNLOAD filename\r\n`
2. Server responds: `READY\r\n`
3. Client sends offset: `<offset>\r\n`
4. Server sends file data in chunks
5. Server sends progress via OOB data
6. Server sends: `EOF\r\n`

## Demonstration

### Server Output
```
[SERVER] Listening on 0.0.0.0:8080
[SERVER] Uploads directory: C:\webJESUSv1\uploads
[SERVER] Downloads directory: C:\webJESUSv1\downloads

[CONNECT] 10.166.120.201:23101
[CMD] 10.166.120.201:23101 - ECHO Hello
[CMD] 10.166.120.201:23101 - TIME
[CMD] 10.166.120.201:23101 - UPLOAD file.txt
[UPLOAD] Receiving: file.txt (1048576 bytes)
[PROGRESS] 524288/1048576 bytes (50.0%)
[UPLOAD COMPLETE] file.txt
  Total: 1048576 bytes
  Time: 2.34 seconds
  Bitrate: 3584521.37 bps (3499.92 Kbps)
```

### Client Output
```
[CLIENT] Connecting to 127.0.0.1:8080...
[CLIENT] Connected to 127.0.0.1:8080

Welcome to TCP Socket Server
Commands: ECHO, TIME, UPLOAD, DOWNLOAD, CLOSE

> ECHO Hello World
Hello World
> TIME
Server time: 2026-03-18 10:30:45
> UPLOAD test.txt
[UPLOAD] Sending: test.txt (1048576 bytes)
Uploading: |██████████████████████████████████████████████████| 100.0% 1.00 MB/1.00 MB
[UPLOAD COMPLETE]
  Total: 1048576 bytes
  Time: 2.34 seconds
  Bitrate: 3584521.37 bps (3499.92 Kbps)
```

## Error Handling

The implementation handles:
- Connection timeouts (30 seconds)
- Network interruptions
- File not found errors
- Incomplete transfers
- Invalid commands

## License

Educational project for TCP socket programming study.
