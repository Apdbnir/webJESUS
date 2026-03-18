#!/usr/bin/env python3
"""
TCP Socket Laboratory Work #1
Server implementation with file transfer support

Features:
- ECHO, TIME, CLOSE commands
- UPLOAD/DOWNLOAD file transfer
- Resume support for interrupted transfers
- SO_KEEPALIVE for connection monitoring
- Out-of-band progress data
- Single-threaded sequential server
"""

import socket
import os
import sys
import time
import hashlib
import json
from datetime import datetime
from pathlib import Path

# Configuration
HOST = '0.0.0.0'
PORT = 8080
BUFFER_SIZE = 8192
KEEPALIVE_TIME = 10  # seconds before sending keepalive
KEEPALIVE_INTERVAL = 5  # seconds between keepalives
KEEPALIVE_COUNT = 3  # number of failed keepalives before disconnect

# Directories for file transfer
UPLOADS_DIR = 'uploads'
DOWNLOADS_DIR = 'downloads'
CHECKPOINT_DIR = '.checkpoints'

# Session storage for resume support
active_sessions = {}


def ensure_directories():
    """Create necessary directories if they don't exist."""
    for dir_path in [UPLOADS_DIR, DOWNLOADS_DIR, CHECKPOINT_DIR]:
        Path(dir_path).mkdir(exist_ok=True)


def get_file_hash(filepath):
    """Calculate MD5 hash of a file."""
    hash_md5 = hashlib.md5()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def save_checkpoint(session_id, filename, offset, total_size, is_upload):
    """Save transfer checkpoint for resume."""
    checkpoint = {
        'session_id': session_id,
        'filename': filename,
        'offset': offset,
        'total_size': total_size,
        'is_upload': is_upload,
        'timestamp': time.time()
    }
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{session_id}.json"
    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint, f)


def load_checkpoint(session_id):
    """Load transfer checkpoint for resume."""
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{session_id}.json"
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def cleanup_checkpoint(session_id):
    """Remove checkpoint after successful transfer."""
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{session_id}.json"
    if checkpoint_path.exists():
        checkpoint_path.unlink()


def set_socket_keepalive(sock):
    """Configure SO_KEEPALIVE for the socket."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    # Platform-specific keepalive settings
    if sys.platform == 'linux':
        # TCP_KEEPIDLE, TCP_KEEPINTVL, TCP_KEEPCNT
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, KEEPALIVE_TIME)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, KEEPALIVE_INTERVAL)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, KEEPALIVE_COUNT)
    elif sys.platform == 'darwin':
        # macOS uses different constants
        try:
            from socket import TCP_KEEPALIVE
            sock.setsockopt(socket.IPPROTO_TCP, TCP_KEEPALIVE, KEEPALIVE_TIME)
        except ImportError:
            pass  # Use default settings
    elif sys.platform == 'win32':
        # Windows uses different API
        try:
            sock.ioctl(socket.SIO_KEEPALIVE_VALS, (
                1,  # Enable keepalive
                KEEPALIVE_TIME * 1000,  # Time in ms
                KEEPALIVE_INTERVAL * 1000  # Interval in ms
            ))
        except (AttributeError, OSError):
            pass  # Use default settings


def send_progress_oob(sock, progress_percent):
    """Send out-of-band progress data."""
    try:
        progress_data = f"{progress_percent:.1f}%".encode('utf-8')
        sock.sendall(progress_data, socket.MSG_OOB)
    except (socket.error, OSError):
        pass  # OOB data is optional


def handle_echo(client_sock, args):
    """ECHO command - returns data back to client."""
    response = args if args else ''
    client_sock.sendall(response.encode('utf-8') + b'\r\n')
    return True


def handle_time(client_sock):
    """TIME command - returns server time."""
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    client_sock.sendall(f"Server time: {current_time}\r\n".encode('utf-8'))
    return True


def handle_close(client_sock):
    """CLOSE command - closes connection."""
    client_sock.sendall(b"GOODBYE\r\n")
    return False


def handle_upload(client_sock, args, client_addr):
    """
    UPLOAD command - receive file from client.
    Format: UPLOAD <filename>
    """
    if not args:
        client_sock.sendall(b"ERROR: No filename provided\r\n")
        return True
    
    filename = args.strip()
    session_id = f"{client_addr[0]}:{client_addr[1]}"
    
    # Check for existing checkpoint (resume)
    checkpoint = load_checkpoint(session_id)
    start_offset = 0
    total_size = 0
    
    if checkpoint and checkpoint.get('filename') == filename and checkpoint.get('is_upload'):
        start_offset = checkpoint.get('offset', 0)
        total_size = checkpoint.get('total_size', 0)
        print(f"[RESUME] Upload {filename} from offset {start_offset}")
        client_sock.sendall(f"RESUME {start_offset}\r\n".encode('utf-8'))
    else:
        # Wait for file size from client
        client_sock.sendall(b"READY\r\n")
        
        # Receive file metadata
        meta_line = b''
        while b'\n' not in meta_line:
            chunk = client_sock.recv(1)
            if not chunk:
                return False
            meta_line += chunk
        
        meta_parts = meta_line.decode('utf-8').strip().split()
        if len(meta_parts) >= 2:
            total_size = int(meta_parts[0])
            filename = meta_parts[1]
        else:
            total_size = int(meta_parts[0])
    
    # Determine file path
    file_path = Path(UPLOADS_DIR) / filename
    
    # Open file in append mode for resume
    mode = 'ab' if start_offset > 0 else 'wb'
    
    print(f"[UPLOAD] Receiving: {filename} ({total_size} bytes)")
    
    bytes_received = start_offset
    start_time = time.time()
    last_progress_time = start_time
    
    try:
        with open(file_path, mode) as f:
            while bytes_received < total_size:
                chunk = client_sock.recv(BUFFER_SIZE)
                if not chunk:
                    # Connection lost - save checkpoint
                    save_checkpoint(session_id, filename, bytes_received, total_size, True)
                    print(f"[INTERRUPTED] At {bytes_received}/{total_size} bytes")
                    return False
                
                f.write(chunk)
                bytes_received += len(chunk)
                
                # Send progress via OOB every second
                current_time = time.time()
                if current_time - last_progress_time >= 1:
                    progress = (bytes_received / total_size) * 100 if total_size > 0 else 0
                    send_progress_oob(client_sock, progress)
                    print(f"[PROGRESS] {bytes_received}/{total_size} bytes ({progress:.1f}%)")
                    last_progress_time = current_time
        
        # Verify transfer
        if bytes_received >= total_size:
            cleanup_checkpoint(session_id)
            elapsed = time.time() - start_time
            bitrate = (bytes_received * 8) / elapsed if elapsed > 0 else 0
            print(f"[UPLOAD COMPLETE] {filename}")
            print(f"  Total: {bytes_received} bytes")
            print(f"  Time: {elapsed:.2f} seconds")
            print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
            client_sock.sendall(b"OK\r\n")
        else:
            save_checkpoint(session_id, filename, bytes_received, total_size, True)
            client_sock.sendall(b"ERROR: Transfer incomplete\r\n")
            return False
            
    except (socket.error, OSError) as e:
        save_checkpoint(session_id, filename, bytes_received, total_size, True)
        print(f"[ERROR] Upload failed: {e}")
        client_sock.sendall(b"ERROR: Connection error\r\n")
        return False
    
    return True


def handle_download(client_sock, args, client_addr):
    """
    DOWNLOAD command - send file to client.
    Format: DOWNLOAD <filename>
    """
    if not args:
        client_sock.sendall(b"ERROR: No filename provided\r\n")
        return True
    
    filename = args.strip()
    session_id = f"{client_addr[0]}:{client_addr[1]}"
    
    # Check for file in uploads directory
    file_path = Path(UPLOADS_DIR) / filename
    
    if not file_path.exists():
        client_sock.sendall(f"ERROR: File '{filename}' not found\r\n".encode('utf-8'))
        return True
    
    total_size = file_path.stat().st_size
    
    # Check for existing checkpoint (resume)
    checkpoint = load_checkpoint(session_id)
    start_offset = 0
    
    if checkpoint and checkpoint.get('filename') == filename and not checkpoint.get('is_upload'):
        start_offset = checkpoint.get('offset', 0)
        if start_offset >= total_size:
            client_sock.sendall(b"ALREADY COMPLETE\r\n")
            cleanup_checkpoint(session_id)
            return True
        print(f"[RESUME] Download {filename} from offset {start_offset}")
    else:
        client_sock.sendall(b"READY\r\n")
        
        # Wait for offset from client
        offset_line = b''
        while b'\n' not in offset_line:
            chunk = client_sock.recv(1)
            if not chunk:
                return False
            offset_line += chunk
        
        try:
            start_offset = int(offset_line.decode('utf-8').strip())
            if start_offset >= total_size:
                cleanup_checkpoint(session_id)
                return True
        except ValueError:
            start_offset = 0
    
    print(f"[DOWNLOAD] Sending: {filename} ({total_size} bytes)")
    
    bytes_sent = start_offset
    start_time = time.time()
    last_progress_time = start_time
    
    try:
        with open(file_path, 'rb') as f:
            f.seek(start_offset)
            
            while bytes_sent < total_size:
                chunk = f.read(BUFFER_SIZE)
                if not chunk:
                    break
                
                client_sock.sendall(chunk)
                bytes_sent += len(chunk)
                
                # Send progress via OOB every second
                current_time = time.time()
                if current_time - last_progress_time >= 1:
                    progress = (bytes_sent / total_size) * 100 if total_size > 0 else 0
                    send_progress_oob(client_sock, progress)
                    print(f"[PROGRESS] {bytes_sent}/{total_size} bytes ({progress:.1f}%)")
                    last_progress_time = current_time
        
        # Send completion marker
        client_sock.sendall(b"EOF\r\n")
        
        if bytes_sent >= total_size:
            cleanup_checkpoint(session_id)
            elapsed = time.time() - start_time
            bitrate = (bytes_sent * 8) / elapsed if elapsed > 0 else 0
            print(f"[DOWNLOAD COMPLETE] {filename}")
            print(f"  Total: {bytes_sent} bytes")
            print(f"  Time: {elapsed:.2f} seconds")
            print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        else:
            save_checkpoint(session_id, filename, bytes_sent, total_size, False)
            
    except (socket.error, OSError) as e:
        save_checkpoint(session_id, filename, bytes_sent, total_size, False)
        print(f"[ERROR] Download failed: {e}")
        return False
    
    return True


def handle_client(client_sock, client_addr):
    """Handle a single client connection."""
    print(f"[CONNECT] {client_addr[0]}:{client_addr[1]}")
    
    set_socket_keepalive(client_sock)
    
    # Send welcome message
    client_sock.sendall(b"Welcome to TCP Socket Server\r\n")
    client_sock.sendall(b"Commands: ECHO, TIME, UPLOAD, DOWNLOAD, CLOSE\r\n\r\n")
    
    connected = True
    buffer = b''
    
    while connected:
        try:
            # Set timeout for connection health check
            client_sock.settimeout(30)
            data = client_sock.recv(BUFFER_SIZE)
            
            if not data:
                print(f"[DISCONNECT] {client_addr[0]}:{client_addr[1]}")
                break
            
            buffer += data
            
            # Process complete lines
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                line = line.decode('utf-8').strip('\r\n')
                
                if not line:
                    continue
                
                print(f"[CMD] {client_addr[0]}:{client_addr[1]} - {line}")
                
                # Parse command
                parts = line.split(' ', 1)
                cmd = parts[0].upper()
                args = parts[1] if len(parts) > 1 else ''
                
                # Execute command
                if cmd == 'ECHO':
                    connected = handle_echo(client_sock, args)
                elif cmd == 'TIME':
                    connected = handle_time(client_sock)
                elif cmd == 'CLOSE' or cmd == 'EXIT' or cmd == 'QUIT':
                    connected = handle_close(client_sock)
                elif cmd == 'UPLOAD':
                    connected = handle_upload(client_sock, args, client_addr)
                elif cmd == 'DOWNLOAD':
                    connected = handle_download(client_sock, args, client_addr)
                else:
                    client_sock.sendall(f"ERROR: Unknown command '{cmd}'\r\n".encode('utf-8'))
                    
        except socket.timeout:
            # Connection health check - send keepalive
            continue
        except (socket.error, OSError) as e:
            print(f"[ERROR] Connection error with {client_addr[0]}:{client_addr[1]}: {e}")
            break
    
    client_sock.close()
    print(f"[CLOSED] {client_addr[0]}:{client_addr[1]}")


def main():
    """Main server entry point."""
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    
    ensure_directories()
    
    # Create server socket
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        server_sock.bind((HOST, port))
        server_sock.listen(5)
        print(f"[SERVER] Listening on {HOST}:{port}")
        print(f"[SERVER] Uploads directory: {Path(UPLOADS_DIR).absolute()}")
        print(f"[SERVER] Downloads directory: {Path(DOWNLOADS_DIR).absolute()}")
        print(f"[SERVER] Press Ctrl+C to stop\n")
        
        while True:
            client_sock, client_addr = server_sock.accept()
            handle_client(client_sock, client_addr)
            
    except KeyboardInterrupt:
        print("\n[SERVER] Shutting down...")
    finally:
        server_sock.close()


if __name__ == '__main__':
    main()
