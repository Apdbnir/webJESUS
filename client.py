#!/usr/bin/env python3
"""
TCP Socket Laboratory Work #1
Client implementation for file transfer

Features:
- Interactive command interface
- UPLOAD/DOWNLOAD file transfer
- Resume support for interrupted transfers
- Progress display with speed calculation
- Out-of-band data reception
"""

import socket
import sys
import os
import time
import hashlib
import json
from pathlib import Path

# Configuration
BUFFER_SIZE = 8192
UPLOADS_DIR = 'uploads'
DOWNLOADS_DIR = 'downloads'
CHECKPOINT_DIR = '.checkpoints'


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


def save_checkpoint(filename, offset, total_size, is_upload):
    """Save transfer checkpoint for resume."""
    checkpoint = {
        'filename': filename,
        'offset': offset,
        'total_size': total_size,
        'is_upload': is_upload,
        'timestamp': time.time()
    }
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{Path(filename).name}.json"
    with open(checkpoint_path, 'w') as f:
        json.dump(checkpoint, f)


def load_checkpoint(filename):
    """Load transfer checkpoint for resume."""
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{Path(filename).name}.json"
    if checkpoint_path.exists():
        with open(checkpoint_path, 'r') as f:
            return json.load(f)
    return None


def cleanup_checkpoint(filename):
    """Remove checkpoint after successful transfer."""
    checkpoint_path = Path(CHECKPOINT_DIR) / f"{Path(filename).name}.json"
    if checkpoint_path.exists():
        checkpoint_path.unlink()


def print_progress_bar(current, total, prefix='Progress', length=50):
    """Print animated progress bar."""
    percent = (current / total * 100) if total > 0 else 0
    filled = int(length * current // total) if total > 0 else 0
    bar = '█' * filled + '░' * (length - filled)
    
    # Size formatting
    def format_size(size):
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} TB"
    
    print(f'\r{prefix}: |{bar}| {percent:.1f}% {format_size(current)}/{format_size(total)}', 
          end='', flush=True)
    if current >= total:
        print()  # New line when complete


def upload_file(sock, filename):
    """Upload a file to the server."""
    file_path = Path(filename)
    
    if not file_path.exists():
        print(f"[ERROR] File '{filename}' not found")
        return False
    
    # Check for checkpoint (resume)
    checkpoint = load_checkpoint(filename)
    start_offset = 0
    total_size = file_path.stat().st_size
    
    if checkpoint and checkpoint.get('filename') == str(filename):
        start_offset = checkpoint.get('offset', 0)
        if start_offset >= total_size:
            print("[INFO] File already fully uploaded")
            cleanup_checkpoint(filename)
            return True
        print(f"[RESUME] Uploading from offset {start_offset}")
    
    print(f"[UPLOAD] Sending: {filename} ({total_size} bytes)")
    
    # Send UPLOAD command
    sock.sendall(f"UPLOAD {file_path.name}\r\n".encode('utf-8'))
    
    # Wait for server response
    response = b''
    while b'\n' not in response:
        chunk = sock.recv(1)
        if not chunk:
            print("\n[ERROR] Connection lost")
            return False
        response += chunk
    
    response_text = response.decode('utf-8').strip()
    
    if response_text.startswith('RESUME'):
        start_offset = int(response_text.split()[1])
        print(f"[RESUME] Server has {start_offset} bytes")
    elif response_text != 'READY':
        print(f"[ERROR] Server response: {response_text}")
        return False
    
    # Send file metadata
    meta = f"{total_size} {file_path.name}\r\n".encode('utf-8')
    sock.sendall(meta)
    
    # Send file content
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
                
                sock.sendall(chunk)
                bytes_sent += len(chunk)
                
                # Update progress every second
                current_time = time.time()
                if current_time - last_progress_time >= 0.5:
                    print_progress_bar(bytes_sent, total_size, 'Uploading')
                    last_progress_time = current_time
        
        print_progress_bar(total_size, total_size, 'Uploading')
        
        # Wait for confirmation
        response = b''
        while b'\n' not in response:
            chunk = sock.recv(1)
            if not chunk:
                break
            response += chunk
        
        if b'OK' in response:
            cleanup_checkpoint(filename)
            elapsed = time.time() - start_time
            bitrate = (bytes_sent * 8) / elapsed if elapsed > 0 else 0
            print(f"\n[UPLOAD COMPLETE]")
            print(f"  Total: {bytes_sent} bytes")
            print(f"  Time: {elapsed:.2f} seconds")
            print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
            return True
        else:
            print(f"\n[ERROR] {response.decode('utf-8').strip()}")
            save_checkpoint(filename, bytes_sent, total_size, True)
            return False
            
    except (socket.error, OSError) as e:
        print(f"\n[ERROR] Upload interrupted: {e}")
        save_checkpoint(filename, bytes_sent, total_size, True)
        return False


def download_file(sock, filename):
    """Download a file from the server."""
    # Check for checkpoint (resume)
    checkpoint = load_checkpoint(filename)
    start_offset = 0
    total_size = 0
    
    if checkpoint and checkpoint.get('filename') == filename:
        start_offset = checkpoint.get('offset', 0)
        total_size = checkpoint.get('total_size', 0)
        print(f"[RESUME] Downloading from offset {start_offset}")
    
    print(f"[DOWNLOAD] Requesting: {filename}")
    
    # Send DOWNLOAD command
    sock.sendall(f"DOWNLOAD {filename}\r\n".encode('utf-8'))
    
    # Wait for server response (READY or ERROR)
    response = recv_line(sock)
    
    if response.startswith('ERROR'):
        print(f"[ERROR] {response}")
        return False
    
    if response == 'ALREADY COMPLETE':
        print("[INFO] File already fully downloaded")
        cleanup_checkpoint(filename)
        return True
    
    # Send offset for resume (or 0 for fresh download)
    sock.sendall(f"{start_offset}\r\n".encode('utf-8'))
    
    # Receive file
    file_path = Path(DOWNLOADS_DIR) / filename
    mode = 'ab' if start_offset > 0 else 'wb'
    
    bytes_received = start_offset
    start_time = time.time()
    last_progress_time = start_time
    file_content = b''
    
    try:
        with open(file_path, mode) as f:
            while True:
                chunk = sock.recv(BUFFER_SIZE)
                if not chunk:
                    break
                
                # Check for EOF marker
                if b'EOF' in chunk:
                    # Process data before EOF
                    data = chunk.replace(b'EOF', b'')
                    if data:
                        f.write(data)
                        file_content += data
                        bytes_received += len(data)
                    break
                
                f.write(chunk)
                file_content += chunk
                bytes_received += len(chunk)
                
                # Update progress every second
                current_time = time.time()
                if current_time - last_progress_time >= 0.5:
                    if total_size > 0:
                        print_progress_bar(bytes_received, total_size, 'Downloading')
                    else:
                        print(f'\rDownloading: {bytes_received} bytes', end='', flush=True)
                    last_progress_time = current_time
        
        if total_size > 0:
            print_progress_bar(bytes_received, total_size, 'Downloading')
        else:
            print(f'\nDownloading: {bytes_received} bytes')
        
        elapsed = time.time() - start_time
        bitrate = (bytes_received * 8) / elapsed if elapsed > 0 else 0
        print(f"\n[DOWNLOAD COMPLETE]")
        print(f"  Total: {bytes_received} bytes")
        print(f"  Time: {elapsed:.2f} seconds")
        print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        print(f"  Saved to: {file_path.absolute()}")
        
        cleanup_checkpoint(filename)
        return True
        
    except (socket.error, OSError) as e:
        print(f"\n[ERROR] Download interrupted: {e}")
        if total_size == 0:
            total_size = bytes_received
        save_checkpoint(filename, bytes_received, total_size, False)
        return False


def interactive_mode(sock):
    """Interactive command loop."""
    print("\n=== TCP Socket Client ===")
    print("Commands: ECHO, TIME, UPLOAD, DOWNLOAD, CLOSE")
    print("Type HELP for more info\n")
    
    while True:
        try:
            cmd = input("> ").strip()
            
            if not cmd:
                continue
            
            if cmd.upper() in ['QUIT', 'EXIT', 'CLOSE']:
                sock.sendall(b"CLOSE\r\n")
                response = recv_line(sock)
                print(f"[SERVER] {response}")
                break
            
            if cmd.upper() == 'HELP':
                print("""
Available Commands:
  ECHO <text>     - Echo text from server
  TIME            - Get server time
  UPLOAD <file>   - Upload file to server
  DOWNLOAD <file> - Download file from server
  CLOSE/QUIT/EXIT - Close connection
  HELP            - Show this help
""")
                continue
            
            # Handle file transfer commands
            if cmd.upper().startswith('UPLOAD '):
                filename = cmd[7:].strip()
                upload_file(sock, filename)
                continue
            
            if cmd.upper().startswith('DOWNLOAD '):
                filename = cmd[9:].strip()
                download_file(sock, filename)
                continue
            
            # Send command to server
            sock.sendall(f"{cmd}\r\n".encode('utf-8'))
            
            # Receive and display response
            response = recv_line(sock)
            if response:
                print(response)
                
        except KeyboardInterrupt:
            print("\n[CLIENT] Disconnecting...")
            break
        except (socket.error, OSError) as e:
            print(f"\n[ERROR] Connection error: {e}")
            break


def recv_line(sock):
    """Receive a complete line from server."""
    line = b''
    while True:
        try:
            sock.settimeout(5)
            chunk = sock.recv(1)
            if not chunk:
                break
            line += chunk
            if line.endswith(b'\r\n') or line.endswith(b'\n'):
                break
        except socket.timeout:
            break
    
    # Check for EOF marker in file transfers
    if b'EOF' in line:
        line = line.replace(b'EOF', b'')
    
    return line.decode('utf-8', errors='replace').strip()


def main():
    """Main client entry point."""
    if len(sys.argv) < 2:
        print("Usage: python client.py <server_address> [port]")
        print("Example: python client.py 127.0.0.1 8080")
        sys.exit(1)
    
    server_addr = sys.argv[1]
    server_port = int(sys.argv[2]) if len(sys.argv) > 2 else 8080
    
    ensure_directories()
    
    print(f"[CLIENT] Connecting to {server_addr}:{server_port}...")
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
    
    try:
        sock.connect((server_addr, server_port))
        print(f"[CLIENT] Connected to {server_addr}:{server_port}\n")
        
        # Display welcome message
        welcome = sock.recv(1024)
        print(welcome.decode('utf-8', errors='replace'))
        
        interactive_mode(sock)
        
    except (socket.error, OSError) as e:
        print(f"[ERROR] Connection failed: {e}")
        sys.exit(1)
    finally:
        sock.close()
        print("[CLIENT] Disconnected")


if __name__ == '__main__':
    main()
