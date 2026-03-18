#!/usr/bin/env python3
"""
Laboratory Work #3 - Multiplexed Server
Parallel client handling using select()/poll()
- Single-threaded concurrent server
- Non-blocking I/O with multiplexing
- File transfer without blocking other clients
"""

import socket
import select
import sys
import time
import os
from datetime import datetime
from pathlib import Path

# Configuration
HOST = '0.0.0.0'
DEFAULT_PORT = 8080
BUFFER_SIZE = 4096
MAX_CLIENTS = 100
RESPONSE_TIMEOUT = 0.1  # seconds - must be < ping * 10

# Directories
UPLOADS_DIR = 'uploads'
DOWNLOADS_DIR = 'downloads'


class Client:
    """Represents a connected client."""
    
    def __init__(self, sock, addr):
        self.sock = sock
        self.addr = addr
        self.buffer = b''
        self.state = 'IDLE'  # IDLE, RECEIVING_CMD, TRANSFERRING
        self.transfer_data = {
            'filename': None,
            'total_size': 0,
            'received': 0,
            'sent': 0,
            'start_time': 0,
            'file': None
        }
        self.last_activity = time.time()
    
    def __repr__(self):
        return f"Client({self.addr[0]}:{self.addr[1]})"


class MultiplexedServer:
    """Server with multiplexed I/O using select()."""
    
    def __init__(self, host=HOST, port=DEFAULT_PORT):
        self.host = host
        self.port = port
        self.server_sock = None
        self.clients = {}
        self.running = False
        
        # Ensure directories exist
        Path(UPLOADS_DIR).mkdir(exist_ok=True)
        Path(DOWNLOADS_DIR).mkdir(exist_ok=True)
    
    def start(self):
        """Start the multiplexed server."""
        # Create server socket
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.setblocking(False)
        self.server_sock.bind((self.host, self.port))
        self.server_sock.listen(MAX_CLIENTS)
        
        self.running = True
        print(f"[SERVER] Listening on {self.host}:{self.port}")
        print(f"[SERVER] Max clients: {MAX_CLIENTS}")
        print(f"[SERVER] Response timeout: {RESPONSE_TIMEOUT * 1000}ms")
        print(f"[SERVER] Press Ctrl+C to stop\n")
        
        try:
            self._main_loop()
        except KeyboardInterrupt:
            print("\n[SERVER] Shutting down...")
        finally:
            self.stop()
    
    def stop(self):
        """Stop the server."""
        self.running = False
        
        # Close all client connections
        for client in list(self.clients.values()):
            try:
                client.sock.close()
            except:
                pass
        
        # Close server socket
        if self.server_sock:
            self.server_sock.close()
        
        print("[SERVER] Stopped")
    
    def _main_loop(self):
        """Main event loop with select()."""
        while self.running:
            # Build list of sockets to monitor
            read_sockets = [self.server_sock] + [c.sock for c in self.clients.values()]
            
            # Use select() for multiplexing
            try:
                readable, _, _ = select.select(read_sockets, [], [], RESPONSE_TIMEOUT)
            except select.error:
                continue
            
            # Process readable sockets
            for sock in readable:
                if sock is self.server_sock:
                    # New connection
                    self._accept_connection()
                else:
                    # Client data
                    client = self._get_client_by_sock(sock)
                    if client:
                        self._handle_client(client)
            
            # Cleanup inactive clients
            self._cleanup_inactive_clients()
    
    def _accept_connection(self):
        """Accept new client connection."""
        try:
            client_sock, addr = self.server_sock.accept()
            client_sock.setblocking(False)
            
            client = Client(client_sock, addr)
            self.clients[client_sock] = client
            
            # Send welcome message
            welcome = (
                "Welcome to Multiplexed TCP Server\n"
                f"Connected clients: {len(self.clients)}\n"
                "Commands: ECHO, TIME, UPLOAD, DOWNLOAD, CLOSE\n\n> "
            )
            try:
                client.sock.send(welcome.encode())
            except:
                pass
            
            print(f"[CONNECT] {addr[0]}:{addr[1]} (total: {len(self.clients)})")
            
        except (socket.error, OSError) as e:
            print(f"[ERROR] Accept failed: {e}")
    
    def _get_client_by_sock(self, sock):
        """Get client object by socket."""
        return self.clients.get(sock)
    
    def _handle_client(self, client):
        """Handle client data."""
        try:
            data = client.sock.recv(BUFFER_SIZE)
            
            if not data:
                # Client disconnected
                self._remove_client(client)
                return
            
            client.last_activity = time.time()
            client.buffer += data
            
            # Process based on state
            if client.state == 'IDLE' or client.state == 'RECEIVING_CMD':
                self._process_commands(client)
            elif client.state == 'TRANSFERRING':
                self._process_transfer(client)
                
        except (socket.error, OSError) as e:
            print(f"[ERROR] {client}: {e}")
            self._remove_client(client)
    
    def _process_commands(self, client):
        """Process commands from client buffer."""
        while b'\n' in client.buffer:
            line, client.buffer = client.buffer.split(b'\n', 1)
            line = line.decode('utf-8', errors='replace').strip('\r')
            
            if not line:
                continue
            
            print(f"[CMD] {client} - {line}")
            
            # Parse command
            parts = line.split(' ', 1)
            cmd = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ''
            
            # Execute command (non-blocking)
            if cmd == 'ECHO':
                self._cmd_echo(client, args)
            elif cmd == 'TIME':
                self._cmd_time(client)
            elif cmd == 'CLOSE' or cmd == 'EXIT' or cmd == 'QUIT':
                self._cmd_close(client)
            elif cmd == 'UPLOAD':
                self._cmd_upload_start(client, args)
            elif cmd == 'DOWNLOAD':
                self._cmd_download_start(client, args)
            else:
                self._send(client, f"ERROR: Unknown command '{cmd}'\n")
    
    def _process_transfer(self, client):
        """Process file transfer data."""
        transfer = client.transfer_data
        
        if transfer['file'] is None:
            return
        
        # Write received data
        data = client.buffer
        client.buffer = b''
        
        transfer['file'].write(data)
        transfer['received'] += len(data)
        
        # Check if transfer complete
        if transfer['received'] >= transfer['total_size']:
            self._finish_upload(client)
    
    def _cmd_echo(self, client, args):
        """ECHO command."""
        self._send(client, f"{args}\n")
    
    def _cmd_time(self, client):
        """TIME command."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self._send(client, f"Server time: {now}\n")
    
    def _cmd_close(self, client):
        """CLOSE command."""
        self._send(client, "GOODBYE\n")
        self._remove_client(client)
    
    def _cmd_upload_start(self, client, filename):
        """Start UPLOAD command."""
        if not filename:
            self._send(client, "ERROR: No filename provided\n")
            return
        
        client.state = 'TRANSFERRING'
        client.transfer_data['filename'] = filename
        client.transfer_data['start_time'] = time.time()
        
        self._send(client, "READY\n")
        print(f"[UPLOAD] {client} - {filename}")
    
    def _finish_upload(self, client):
        """Finish file upload."""
        transfer = client.transfer_data
        
        if transfer['file']:
            transfer['file'].close()
        
        elapsed = time.time() - transfer['start_time']
        bitrate = (transfer['received'] * 8) / elapsed if elapsed > 0 else 0
        
        print(f"[UPLOAD COMPLETE] {client} - {transfer['filename']}")
        print(f"  Size: {transfer['received']} bytes")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        
        self._send(client, f"OK: Received {transfer['received']} bytes\n")
        
        # Reset client state
        client.state = 'IDLE'
        client.transfer_data = {
            'filename': None,
            'total_size': 0,
            'received': 0,
            'sent': 0,
            'start_time': 0,
            'file': None
        }
    
    def _cmd_download_start(self, client, filename):
        """Start DOWNLOAD command."""
        if not filename:
            self._send(client, "ERROR: No filename provided\n")
            return
        
        filepath = Path(UPLOADS_DIR) / filename
        
        if not filepath.exists():
            self._send(client, f"ERROR: File '{filename}' not found\n")
            return
        
        file_size = filepath.stat().st_size
        
        client.state = 'TRANSFERRING'
        client.transfer_data['filename'] = filename
        client.transfer_data['total_size'] = file_size
        client.transfer_data['start_time'] = time.time()
        client.transfer_data['file'] = open(filepath, 'rb')
        
        self._send(client, f"READY: {file_size}\n")
        print(f"[DOWNLOAD] {client} - {filename} ({file_size} bytes)")
        
        # Send file data immediately (non-blocking)
        self._send_file_data(client)
    
    def _send_file_data(self, client):
        """Send file data in chunks."""
        transfer = client.transfer_data
        
        if transfer['file'] is None:
            return
        
        try:
            # Send in chunks without blocking
            chunk = transfer['file'].read(BUFFER_SIZE)
            if chunk:
                client.sock.send(chunk)
                transfer['sent'] += len(chunk)
                
                if transfer['sent'] >= transfer['total_size']:
                    self._finish_download(client)
                    
        except (socket.error, OSError) as e:
            # Would block - continue later
            if e.errno in (10035, 11):  # WSAEWOULDBLOCK or EAGAIN
                pass
            else:
                print(f"[ERROR] Download failed: {e}")
                self._finish_download(client, error=True)
    
    def _finish_download(self, client, error=False):
        """Finish file download."""
        transfer = client.transfer_data
        
        if transfer['file']:
            transfer['file'].close()
        
        elapsed = time.time() - transfer['start_time']
        bitrate = (transfer['sent'] * 8) / elapsed if elapsed > 0 else 0
        
        status = "COMPLETE" if not error else "ERROR"
        print(f"[DOWNLOAD {status}] {client} - {transfer['filename']}")
        print(f"  Size: {transfer['sent']} bytes")
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        
        # Reset client state
        client.state = 'IDLE'
        client.transfer_data = {
            'filename': None,
            'total_size': 0,
            'received': 0,
            'sent': 0,
            'start_time': 0,
            'file': None
        }
    
    def _send(self, client, data):
        """Send data to client (non-blocking)."""
        try:
            client.sock.send(data.encode() if isinstance(data, str) else data)
        except (socket.error, OSError) as e:
            if e.errno not in (10035, 11):  # Not WSAEWOULDBLOCK or EAGAIN
                print(f"[ERROR] Send failed to {client}: {e}")
                self._remove_client(client)
    
    def _remove_client(self, client):
        """Remove client from server."""
        print(f"[DISCONNECT] {client}")
        
        if client.sock in self.clients:
            del self.clients[client.sock]
        
        try:
            client.sock.close()
        except:
            pass
        
        # Clean up transfer resources
        if client.transfer_data.get('file'):
            try:
                client.transfer_data['file'].close()
            except:
                pass
    
    def _cleanup_inactive_clients(self):
        """Remove inactive clients."""
        now = time.time()
        timeout = 300  # 5 minutes
        
        inactive = [
            c for c in self.clients.values()
            if now - c.last_activity > timeout and c.state == 'IDLE'
        ]
        
        for client in inactive:
            print(f"[TIMEOUT] {client}")
            self._remove_client(client)


def main():
    """Main entry point."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    
    server = MultiplexedServer(port=port)
    server.start()


if __name__ == '__main__':
    main()
