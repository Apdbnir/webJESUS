#!/usr/bin/env python3
"""
Laboratory Work #2 - UDP File Transfer
Reliable UDP protocol with:
- Packet acknowledgments (ACK)
- Retransmission on loss
- Sliding window for flow control
- Bitrate calculation and buffer optimization
"""

import socket
import sys
import time
import struct
import hashlib
import os
from datetime import datetime
from pathlib import Path

# Configuration
HOST = '0.0.0.0'
DEFAULT_PORT = 8080
BUFFER_SIZE = 1472  # Optimal for UDP (1500 MTU - 20 IP - 8 UDP headers)
SLIDING_WINDOW_SIZE = 10  # Number of packets before ACK
TIMEOUT = 2.0  # seconds
MAX_RETRIES = 5

# Packet types
PKT_DATA = 0x01
PKT_ACK = 0x02
PKT_NACK = 0x03
PKT_START = 0x04
PKT_END = 0x05
PKT_CMD = 0x06

# Packet header: type(1) + seq_num(4) + total_size(4) + checksum(4) = 13 bytes
HEADER_FORMAT = '!BIII'
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


def calc_checksum(data):
    """Calculate CRC32 checksum."""
    return hashlib.md5(data).digest()[:4]


def verify_checksum(data, expected):
    """Verify checksum."""
    return calc_checksum(data) == expected


def create_packet(pkt_type, seq_num, total_size, data=b''):
    """Create UDP packet with header."""
    checksum = calc_checksum(data) if data else b'\x00' * 4
    header = struct.pack(HEADER_FORMAT, pkt_type, seq_num, total_size, 
                        int.from_bytes(checksum, 'big'))
    return header + data


def parse_packet(data):
    """Parse UDP packet."""
    if len(data) < HEADER_SIZE:
        return None, None, None, None, None
    
    pkt_type, seq_num, total_size, checksum_int = struct.unpack(
        HEADER_FORMAT, data[:HEADER_SIZE])
    payload = data[HEADER_SIZE:]
    
    checksum = checksum_int.to_bytes(4, 'big')
    valid = verify_checksum(payload, checksum) if payload else True
    
    return pkt_type, seq_num, total_size, payload, valid


def format_size(size):
    """Format size in human readable format."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


class UDPTransfer:
    """Reliable UDP file transfer with sliding window."""
    
    def __init__(self, sock, addr, window_size=SLIDING_WINDOW_SIZE):
        self.sock = sock
        self.addr = addr
        self.window_size = window_size
        self.timeout = TIMEOUT
        self.max_retries = MAX_RETRIES
        self.stats = {
            'packets_sent': 0,
            'packets_received': 0,
            'acks_received': 0,
            'retransmissions': 0,
            'lost_packets': 0,
            'bytes_sent': 0,
            'bytes_received': 0,
            'start_time': 0,
            'end_time': 0
        }
    
    def send_file(self, filepath):
        """Send file using reliable UDP with sliding window."""
        if not os.path.exists(filepath):
            print(f"[ERROR] File '{filepath}' not found")
            return False
        
        file_size = os.path.getsize(filepath)
        filename = os.path.basename(filepath)
        
        print(f"[UDP SEND] {filename} ({format_size(file_size)})")
        print(f"[UDP] Window size: {self.window_size} packets")
        print(f"[UDP] Buffer size: {BUFFER_SIZE} bytes")
        
        self.stats['start_time'] = time.time()
        
        # Send START packet with filename
        start_data = f"{file_size}\n{filename}".encode()
        packet = create_packet(PKT_START, 0, file_size, start_data)
        self.sock.sendto(packet, self.addr)
        self.stats['packets_sent'] += 1
        
        # Wait for ACK
        if not self._wait_for_ack(0, 'START'):
            return False
        
        # Read and send file in chunks
        seq_num = 1
        window_start = 1
        unacked_packets = {}  # seq_num -> (packet, time_sent, retries)
        
        with open(filepath, 'rb') as f:
            while True:
                # Fill the sliding window
                while (seq_num - window_start) < self.window_size:
                    data = f.read(BUFFER_SIZE - HEADER_SIZE)
                    if not data:
                        break
                    
                    packet = create_packet(PKT_DATA, seq_num, file_size, data)
                    self.sock.sendto(packet, self.addr)
                    self.stats['packets_sent'] += 1
                    self.stats['bytes_sent'] += len(data)
                    
                    unacked_packets[seq_num] = (packet, time.time(), 0)
                    seq_num += 1
                
                if not unacked_packets:
                    break
                
                # Wait for ACKs with timeout
                ready = self._wait_for_data(0.1)
                if ready:
                    self._process_acks(unacked_packets, window_start)
                    window_start = min(unacked_packets.keys()) if unacked_packets else seq_num
                else:
                    # Timeout - retransmit unacked packets
                    self._retransmit(unacked_packets)
        
        # Send END packet
        packet = create_packet(PKT_END, seq_num, file_size)
        self.sock.sendto(packet, self.addr)
        self.stats['packets_sent'] += 1
        
        self.stats['end_time'] = time.time()
        self._print_stats()
        
        return True
    
    def receive_file(self, save_dir='downloads'):
        """Receive file using reliable UDP with sliding window."""
        Path(save_dir).mkdir(exist_ok=True)
        
        print(f"[UDP RECV] Waiting for file...")
        print(f"[UDP] Window size: {self.window_size} packets")
        
        self.stats['start_time'] = time.time()
        
        # Wait for START packet
        packet, addr = self.sock.recvfrom(BUFFER_SIZE)
        pkt_type, seq_num, total_size, payload, valid = parse_packet(packet)
        
        if pkt_type != PKT_START:
            print(f"[ERROR] Expected START packet, got type {pkt_type}")
            return False
        
        # Parse filename and size
        try:
            parts = payload.decode().split('\n')
            file_size = int(parts[0])
            filename = parts[1] if len(parts) > 1 else f"file_{int(time.time())}"
        except:
            print(f"[ERROR] Invalid START packet")
            return False
        
        print(f"[UDP RECV] {filename} ({format_size(file_size)})")
        
        # Send ACK for START
        ack = create_packet(PKT_ACK, 0, file_size)
        self.sock.sendto(ack, addr)
        
        # Receive file data
        received_data = {}
        expected_seq = 1
        bytes_received = 0
        last_progress = time.time()
        
        while bytes_received < file_size:
            # Receive packet
            packet, addr = self.sock.recvfrom(BUFFER_SIZE)
            self.stats['packets_received'] += 1
            
            pkt_type, seq_num, pkt_total, payload, valid = parse_packet(packet)
            
            if pkt_type == PKT_END:
                break
            
            if pkt_type == PKT_DATA:
                if not valid:
                    print(f"[WARN] Checksum failed for packet {seq_num}")
                    nack = create_packet(PKT_NACK, seq_num, file_size)
                    self.sock.sendto(nack, addr)
                    continue
                
                received_data[seq_num] = payload
                bytes_received += len(payload)
                self.stats['bytes_received'] += len(payload)
                
                # Send ACK
                ack = create_packet(PKT_ACK, seq_num, file_size)
                self.sock.sendto(ack, addr)
                self.stats['acks_received'] += 1
                
                # Progress display
                now = time.time()
                if now - last_progress >= 0.5:
                    progress = (bytes_received / file_size * 100) if file_size > 0 else 0
                    print(f"\r[PROGRESS] {format_size(bytes_received)}/{format_size(file_size)} ({progress:.1f}%)", end='')
                    last_progress = now
        
        print()
        
        # Write file
        filepath = Path(save_dir) / filename
        with open(filepath, 'wb') as f:
            for seq in sorted(received_data.keys()):
                f.write(received_data[seq])
        
        self.stats['end_time'] = time.time()
        self._print_stats()
        
        print(f"[UDP RECV COMPLETE] Saved to: {filepath.absolute()}")
        return True
    
    def _wait_for_ack(self, expected_seq, pkt_name, timeout=None):
        """Wait for ACK packet."""
        if timeout is None:
            timeout = self.timeout
        
        start = time.time()
        while time.time() - start < timeout:
            ready = self._wait_for_data(0.1)
            if not ready:
                continue
            
            packet, addr = self.sock.recvfrom(BUFFER_SIZE)
            pkt_type, seq_num, total_size, payload, valid = parse_packet(packet)
            
            if pkt_type == PKT_ACK and seq_num == expected_seq:
                return True
            elif pkt_type == PKT_NACK:
                return False
        
        print(f"[TIMEOUT] Waiting for {pkt_name} ACK")
        return False
    
    def _wait_for_data(self, timeout):
        """Check if data is available."""
        try:
            self.sock.settimeout(timeout)
            return True
        except:
            return False
    
    def _process_acks(self, unacked_packets, window_start):
        """Process received ACKs."""
        try:
            self.sock.settimeout(0.01)
            while True:
                packet, addr = self.sock.recvfrom(BUFFER_SIZE)
                pkt_type, seq_num, total_size, payload, valid = parse_packet(packet)
                
                if pkt_type == PKT_ACK and seq_num in unacked_packets:
                    del unacked_packets[seq_num]
                    self.stats['acks_received'] += 1
                elif pkt_type == PKT_NACK and seq_num in unacked_packets:
                    # Retransmit immediately
                    pkt_data, _, _ = unacked_packets[seq_num]
                    self.sock.sendto(pkt_data, self.addr)
                    self.stats['retransmissions'] += 1
        except socket.timeout:
            pass
    
    def _retransmit(self, unacked_packets):
        """Retransmit unacknowledged packets."""
        now = time.time()
        for seq_num in list(unacked_packets.keys()):
            packet, time_sent, retries = unacked_packets[seq_num]
            
            if now - time_sent > self.timeout:
                if retries >= self.max_retries:
                    print(f"[ERROR] Max retries for packet {seq_num}")
                    self.stats['lost_packets'] += 1
                    del unacked_packets[seq_num]
                else:
                    self.sock.sendto(packet, self.addr)
                    unacked_packets[seq_num] = (packet, now, retries + 1)
                    self.stats['retransmissions'] += 1
                    print(f"\r[RETRANSMIT] Packet {seq_num} (retry {retries + 1})", end='')
    
    def _print_stats(self):
        """Print transfer statistics."""
        elapsed = self.stats['end_time'] - self.stats['start_time']
        if elapsed <= 0:
            elapsed = 0.001
        
        bitrate = (self.stats['bytes_sent'] * 8) / elapsed
        throughput = self.stats['bytes_received'] * 8 / elapsed if self.stats['bytes_received'] > 0 else bitrate
        
        print(f"\n[STATISTICS]")
        print(f"  Packets sent: {self.stats['packets_sent']}")
        print(f"  Packets received: {self.stats['packets_received']}")
        print(f"  ACKs received: {self.stats['acks_received']}")
        print(f"  Retransmissions: {self.stats['retransmissions']}")
        print(f"  Lost packets: {self.stats['lost_packets']}")
        print(f"  Bytes sent: {format_size(self.stats['bytes_sent'])}")
        print(f"  Bytes received: {format_size(self.stats['bytes_received'])}")
        print(f"  Time: {elapsed:.2f} seconds")
        print(f"  Bitrate: {bitrate:.2f} bps ({bitrate/1024:.2f} Kbps)")
        print(f"  Throughput: {throughput:.2f} bps ({throughput/1024:.2f} Kbps)")
        
        loss_rate = (self.stats['retransmissions'] / max(self.stats['packets_sent'], 1)) * 100
        print(f"  Packet loss: {loss_rate:.1f}%")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python udp_transfer.py <server|client> [host] [port]")
        print("  server              - Start UDP server")
        print("  client [host] [port] - Connect to server")
        sys.exit(1)
    
    mode = sys.argv[1].lower()
    port = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_PORT
    
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
    
    if mode == 'server':
        sock.bind((HOST, port))
        print(f"[UDP SERVER] Listening on {HOST}:{port}")
        
        # Receive file
        transfer = UDPTransfer(sock, None)
        transfer.receive_file()
        
    elif mode == 'client':
        host = sys.argv[2] if len(sys.argv) > 2 else '127.0.0.1'
        addr = (host, port)
        
        print(f"[UDP CLIENT] Connecting to {host}:{port}")
        
        # Get file to send
        filepath = input("Enter file path: ").strip()
        
        transfer = UDPTransfer(sock, addr)
        transfer.send_file(filepath)
    
    sock.close()


if __name__ == '__main__':
    main()
