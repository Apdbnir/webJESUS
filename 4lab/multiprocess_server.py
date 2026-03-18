#!/usr/bin/env python3
"""
Лабораторная работа №4
Многопроцессный сервер с динамическим пулом

Вариант: 1 (TCP, Процессы по запросу)

Архитектура:
- Главный процесс: accept() соединений
- Дочерний процесс: обработка клиента
- Динамический пул: Nmin=2, Nmax=10
- Таймаут для простаивающих процессов

Требования:
- Параллельная обработка клиентов
- Пул процессов с Nmin=2, Nmax=10
- Динамическое масштабирование
- Таймаут для лишних процессов
- Общая статистика
"""

import socket
import sys
import time
import os
import signal
import multiprocessing as mp
from multiprocessing import Process, Value, Array, Lock
from datetime import datetime
from pathlib import Path
import ctypes

# Configuration
HOST = '0.0.0.0'
DEFAULT_PORT = 8080
BUFFER_SIZE = 8192

# Process pool limits
N_MIN = 2  # Minimum idle processes
N_MAX = 10  # Maximum processes
PROCESS_TIMEOUT = 30  # seconds before idle process terminates
CLIENT_TIMEOUT = 300  # seconds client inactivity timeout

# Directories
UPLOADS_DIR = 'uploads'
DOWNLOADS_DIR = 'downloads'


class SharedStats:
    """Shared statistics between processes."""
    
    def __init__(self):
        self.active_clients = Value('i', 0)
        self.total_connections = Value('i', 0)
        self.bytes_transferred = Value('i', 0)
        self.processes_created = Value('i', 0)
        self.lock = Lock()
    
    def increment_clients(self):
        with self.lock:
            self.active_clients.value += 1
            self.total_connections.value += 1
    
    def decrement_clients(self):
        with self.lock:
            self.active_clients.value -= 1
    
    def add_bytes(self, bytes_count):
        with self.lock:
            self.bytes_transferred.value += bytes_count
    
    def increment_processes(self):
        with self.lock:
            self.processes_created.value += 1
    
    def get_stats(self):
        with self.lock:
            return {
                'active_clients': self.active_clients.value,
                'total_connections': self.total_connections.value,
                'bytes_transferred': self.bytes_transferred.value,
                'processes_created': self.processes_created.value
            }


class ClientHandler:
    """Handler for individual client connection."""
    
    def __init__(self, client_sock, addr, stats):
        self.client_sock = client_sock
        self.addr = addr
        self.stats = stats
        self.buffer = b''
        self.state = 'IDLE'
        self.transfer_data = {}
        self.last_activity = time.time()
        
        # Ensure directories exist
        Path(UPLOADS_DIR).mkdir(exist_ok=True)
        Path(DOWNLOADS_DIR).mkdir(exist_ok=True)
    
    def run(self):
        """Handle client connection."""
        print(f"[PROCESS {os.getpid()}] Handling {self.addr[0]}:{self.addr[1]}")
        
        self.stats.increment_clients()
        
        # Send welcome message
        welcome = (
            f"Welcome to Multi-Process TCP Server\n"
            f"Process ID: {os.getpid()}\n"
            f"Active clients: {self.stats.active_clients.value}\n"
            f"Commands: ECHO, TIME, UPLOAD, DOWNLOAD, STATS, CLOSE\n\n> "
        )
        try:
            self.client_sock.send(welcome.encode())
        except:
            self.stats.decrement_clients()
            return
        
        try:
            self._main_loop()
        except KeyboardInterrupt:
            print(f"\n[PROCESS {os.getpid()}] Interrupted")
        except Exception as e:
            print(f"[PROCESS {os.getpid()}] Error: {e}")
        finally:
            self._cleanup()
    
    def _main_loop(self):
        """Main client handling loop."""
        while True:
            try:
                self.client_sock.settimeout(1.0)
                data = self.client_sock.recv(BUFFER_SIZE)
                
                if not data:
                    break
                
                self.last_activity = time.time()
                self.buffer += data
                
                # Process commands
                if self.state == 'IDLE':
                    self._process_commands()
                elif self.state == 'TRANSFERRING':
                    self._process_transfer()
                    
            except socket.timeout:
                # Check for client timeout
                if time.time() - self.last_activity > CLIENT_TIMEOUT:
                    print(f"[TIMEOUT] {self.addr[0]}:{self.addr[1]}")
                    break
                continue
            except (socket.error, OSError) as e:
                print(f"[ERROR] {self.addr[0]}:{self.addr[1]}: {e}")
                break
    
    def _process_commands(self):
        """Process commands from buffer."""
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            line = line.decode('utf-8', errors='replace').strip('\r')
            
            if not line:
                continue
            
            print(f"[CMD] {self.addr[0]}:{self.addr[1]} - {line}")
            
            parts = line.split(' ', 1)
            cmd = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ''
            
            if cmd == 'ECHO':
                self._send(f"{args}\n")
            elif cmd == 'TIME':
                now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                self._send(f"Server time: {now}\n")
            elif cmd == 'STATS':
                self._send_stats()
            elif cmd in ['CLOSE', 'EXIT', 'QUIT']:
                self._send("GOODBYE\n")
                return
            elif cmd == 'UPLOAD':
                self._start_upload(args)
            elif cmd == 'DOWNLOAD':
                self._start_download(args)
            else:
                self._send(f"ERROR: Unknown command '{cmd}'\n")
    
    def _process_transfer(self):
        """Process file transfer data."""
        if not self.transfer_data.get('file'):
            return
        
        data = self.buffer
        self.buffer = b''
        
        self.transfer_data['file'].write(data)
        self.transfer_data['received'] += len(data)
        self.stats.add_bytes(len(data))
        
        if self.transfer_data['received'] >= self.transfer_data['total_size']:
            self._finish_upload()
    
    def _start_upload(self, filename):
        """Start file upload."""
        if not filename:
            self._send("ERROR: No filename provided\n")
            return
        
        self.state = 'TRANSFERRING'
        self.transfer_data = {
            'filename': filename,
            'total_size': 0,  # Will be set when we receive file
            'received': 0,
            'start_time': time.time(),
            'file': None
        }
        
        self._send("READY\n")
        print(f"[UPLOAD] {self.addr[0]}:{self.addr[1]} - {filename}")
    
    def _finish_upload(self):
        """Finish file upload."""
        if self.transfer_data.get('file'):
            self.transfer_data['file'].close()
        
        elapsed = time.time() - self.transfer_data['start_time']
        bitrate = (self.transfer_data['received'] * 8) / elapsed if elapsed > 0 else 0
        
        print(f"[UPLOAD COMPLETE] {self.addr[0]}:{self.addr[1]} - {self.transfer_data['filename']}")
        print(f"  Size: {self.transfer_data['received']} bytes")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        
        self._send(f"OK: Received {self.transfer_data['received']} bytes\n")
        
        self.state = 'IDLE'
        self.transfer_data = {}
    
    def _start_download(self, filename):
        """Start file download."""
        if not filename:
            self._send("ERROR: No filename provided\n")
            return
        
        filepath = Path(UPLOADS_DIR) / filename
        
        if not filepath.exists():
            self._send(f"ERROR: File '{filename}' not found\n")
            return
        
        file_size = filepath.stat().st_size
        
        self._send(f"READY: {file_size}\n")
        print(f"[DOWNLOAD] {self.addr[0]}:{self.addr[1]} - {filename} ({file_size} bytes)")
        
        # Send file
        start_time = time.time()
        bytes_sent = 0
        
        try:
            with open(filepath, 'rb') as f:
                while True:
                    chunk = f.read(BUFFER_SIZE)
                    if not chunk:
                        break
                    self.client_sock.send(chunk)
                    bytes_sent += len(chunk)
                    self.stats.add_bytes(len(chunk))
            
            elapsed = time.time() - start_time
            bitrate = (bytes_sent * 8) / elapsed if elapsed > 0 else 0
            
            print(f"[DOWNLOAD COMPLETE] {self.addr[0]}:{self.addr[1]} - {filename}")
            print(f"  Size: {bytes_sent} bytes")
            print(f"  Time: {elapsed:.2f}s")
            print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
            
        except (socket.error, OSError) as e:
            print(f"[ERROR] Download failed: {e}")
    
    def _send_stats(self):
        """Send server statistics."""
        stats = self.stats.get_stats()
        msg = (
            f"\n=== Server Statistics ===\n"
            f"Active clients: {stats['active_clients']}\n"
            f"Total connections: {stats['total_connections']}\n"
            f"Bytes transferred: {stats['bytes_transferred']}\n"
            f"Processes created: {stats['processes_created']}\n"
            f"Process ID: {os.getpid()}\n"
            f"=========================\n\n"
        )
        self._send(msg)
    
    def _send(self, data):
        """Send data to client."""
        try:
            self.client_sock.send(data.encode() if isinstance(data, str) else data)
        except (socket.error, OSError) as e:
            print(f"[ERROR] Send failed: {e}")
    
    def _cleanup(self):
        """Cleanup resources."""
        print(f"[DISCONNECT] {self.addr[0]}:{self.addr[1]}")
        
        if self.transfer_data.get('file'):
            try:
                self.transfer_data['file'].close()
            except:
                pass
        
        try:
            self.client_sock.close()
        except:
            pass
        
        self.stats.decrement_clients()


def handle_client(client_sock, addr, stats):
    """Target function for child process."""
    handler = ClientHandler(client_sock, addr, stats)
    handler.run()


class MultiProcessServer:
    """Multi-process TCP server with dynamic process pool."""
    
    def __init__(self, host=HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.server_sock = None
        self.stats = SharedStats()
        self.running = False
        self.processes = []
        
        # Ensure directories exist
        Path(UPLOADS_DIR).mkdir(exist_ok=True)
        Path(DOWNLOADS_DIR).mkdir(exist_ok=True)
    
    def start(self):
        """Start the multi-process server."""
        # Create server socket
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(N_MAX * 2)
        self.server_sock.settimeout(1.0)
        
        self.running = True
        
        print(f"[SERVER] Multi-Process TCP Server")
        print(f"[SERVER] Listening on {self.host}:{self.port}")
        print(f"[SERVER] Process pool: Nmin={N_MIN}, Nmax={N_MAX}")
        print(f"[SERVER] Press Ctrl+C to stop\n")
        
        # Start minimum process pool
        for i in range(N_MIN):
            self._spawn_worker()
        
        try:
            self._main_loop()
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down...")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the server and all child processes."""
        self.running = False
        
        # Terminate all child processes
        for proc in self.processes:
            try:
                proc.terminate()
                proc.join(timeout=2)
            except:
                pass
        
        # Close server socket
        if self.server_sock:
            self.server_sock.close()
        
        print("[SERVER] Stopped")
        print(f"[SERVER] Final stats: {self.stats.get_stats()}")
    
    def _main_loop(self):
        """Main server loop."""
        last_cleanup = time.time()
        
        while self.running:
            try:
                # Accept new connection
                client_sock, addr = self.server_sock.accept()
                
                print(f"[CONNECT] {addr[0]}:{addr[1]} (active: {self.stats.active_clients.value})")
                
                # Spawn worker process for client
                self._spawn_worker(client_sock, addr)
                
                # Cleanup old processes
                if time.time() - last_cleanup >= 5:
                    self._cleanup_processes()
                    last_cleanup = time.time()
                
                # Scale down if too many idle processes
                self._scale_down()
                
            except socket.timeout:
                continue
            except (socket.error, OSError) as e:
                if self.running:
                    print(f"[ERROR] Accept failed: {e}")
    
    def _spawn_worker(self, client_sock=None, addr=None):
        """Spawn worker process for client."""
        if len(self.processes) >= N_MAX:
            print("[WARN] Max processes reached")
            return None
        
        if client_sock is None:
            # Pre-spawn idle worker (not used in this implementation)
            return None
        
        # Create process for client
        proc = Process(
            target=handle_client,
            args=(client_sock, addr, self.stats)
        )
        proc.start()
        
        self.processes.append({
            'process': proc,
            'start_time': time.time(),
            'addr': addr
        })
        
        self.stats.increment_processes()
        
        # Close client socket in parent (child has its own copy)
        client_sock.close()
        
        print(f"[SPAWN] Process {proc.pid} (total: {len(self.processes)})")
        
        return proc
    
    def _cleanup_processes(self):
        """Clean up terminated processes."""
        active = []
        
        for item in self.processes:
            proc = item['process']
            
            if not proc.is_alive():
                try:
                    proc.join(timeout=1)
                except:
                    pass
                print(f"[CLEANUP] Process {proc.pid} terminated")
            else:
                active.append(item)
        
        self.processes = active
    
    def _scale_down(self):
        """Scale down process pool when load is low."""
        # Keep only N_MIN processes when idle
        while len(self.processes) > N_MIN and self.stats.active_clients.value == 0:
            # Don't kill active processes
            active_items = [item for item in self.processes if item['process'].is_alive()]
            
            if len(active_items) <= N_MIN:
                break
            
            # Kill oldest idle process
            item = active_items[0]
            try:
                item['process'].terminate()
                item['process'].join(timeout=2)
                print(f"[SCALE DOWN] Terminated process {item['process'].pid}")
            except:
                pass
            
            self.processes.remove(item)


def main():
    """Main entry point."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    
    # Handle signals for clean shutdown
    def signal_handler(sig, frame):
        print("\n[SERVER] Received shutdown signal")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    server = MultiProcessServer(port=port)
    server.start()


if __name__ == '__main__':
    # Required for multiprocessing on Windows
    mp.set_start_method('spawn', force=True)
    main()
