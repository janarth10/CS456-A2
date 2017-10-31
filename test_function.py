#!/usr/bin/python
import socket
from struct import *
import time
import sys
import signal
import select
import threading

DATA_PACKET_TYPE = 0
ACK_PACKET_TYPE = 1
EOT_PACKET_TYPE = 2

DUMMY_IP = "0.0.0.0"
DUMMY_PORT = 500

CHANNEL_INFO_FILE = "channelInfo"
MAX_PAYLOAD = 5
WINDOW_SIZE = 10


def log(packet_header, was_sent):
    if was_sent:
        sent_or_recv = 'SEND'
    else:
        sent_or_recv = 'RECV'

    if packet_header[0] == DATA_PACKET_TYPE:
        pkt_type = 'DAT'
    elif packet_header[0] == ACK_PACKET_TYPE:
        pkt_type = 'ACK'
    else:
        pkt_type = 'EOT'

    print 'PKT {0} {1} {2} {3}'.format(sent_or_recv, pkt_type, packet_header[1], packet_header[2])


def read_channel_info():
    for i in range(6):  # try opening channelInfo for 1 minute
        try:
            with open(CHANNEL_INFO_FILE, 'r') as f:
                temp = f.readline().split(' ')
                channel_info = (temp[0], int(temp[1]))
                break
        except IOError as e:
            time.sleep(10)  # wait for user to run channel script

    if 'channel_info' not in locals():
        sys.exit("Error: Could not retrieve channelInfo")

    return channel_info

# timeout in milliseconds
def go_back_n(filename, utimeout):
    base = next_seq_num = 1
    sent_EOT = False
    timeout = utimeout/1000.0
    window = []

    sender_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    channel_info = read_channel_info()

    def timeout_handler(signum, frame):
        signal.setitimer(signal.ITIMER_REAL, timeout)
        for i in range(base, next_seq_num):
            sender_socket.sendto(window[i - 1], (channel_info[0], channel_info[1]))

    signal.signal(signal.SIGALRM, timeout_handler)
    file_to_send = open(filename, 'rb')
    while True:
        try:
            print 'blocking temporarily to check for acks'
            readers, _, _ = select.select([sender_socket], [], [], timeout/1000.0)
            if len(readers) == 1:
                data, addr = readers[0].recvfrom(12)  # since sender only recieves ack and eots
                header = unpack('>III', data[:12])
                log(header, False)
                if header[0] == ACK_PACKET_TYPE and header[2] == base:  # ignore dup acks
                    base = header[2] + 1
                    signal.setitimer(signal.ITIMER_REAL, timeout)
                elif header[0] == EOT_PACKET_TYPE:
                    sys.exit()
        except select.error:
            pass

        if (next_seq_num < base + WINDOW_SIZE) and not file_to_send.closed:
            payload = file_to_send.read(MAX_PAYLOAD)

            if payload == "":
                file_to_send.close()
            else:
                fmt = '>III{0}s'.format(len(payload))

                packet = pack(fmt, DATA_PACKET_TYPE, calcsize(fmt), next_seq_num, payload)
                window.append(packet)
                sender_socket.sendto(packet, (channel_info[0], channel_info[1]))
                log((DATA_PACKET_TYPE, calcsize(fmt), next_seq_num), True)
                if base == next_seq_num:
                    signal.setitimer(signal.ITIMER_REAL, timeout)
                next_seq_num += 1
        elif base == next_seq_num and not sent_EOT:
            signal.setitimer(signal.ITIMER_REAL, 0)
            eot_packet = pack('>III', EOT_PACKET_TYPE, 12, 0)
            sender_socket.sendto(eot_packet, (channel_info[0], channel_info[1]))
            log((EOT_PACKET_TYPE, 12, 0), True)
            sent_EOT = True


def selective_repeat(filename, utimeout):
    base = next_seq_num = 1
    timeout = utimeout / 1000.0
    pkts_sent = {}
    pending_acks = []
    acks_recvd = []

    channel_info = read_channel_info()
    send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send_socket.settimeout(timeout)
    file_to_send = open(filename, 'rb')
    threads = []

    def send_packet(pkt, size, seq):
        send_socket.sendto(pkt, channel_info)
        log((DATA_PACKET_TYPE, size, seq), True)
        pkts_sent[seq] = pkt
        pending_acks.append(seq)
        while True:
            try:
                print 'subthread waiting on ack'
                data, addr = send_socket.recvfrom(12)
                header = unpack('>III', data[:12])
                log(header, False)
                pending_acks.remove(header[2])
                acks_recvd.append(seq)
                break
            except:
                send_socket.sendto(pkts_sent[pending_acks[0]], channel_info)
                pass

    while not file_to_send.closed or base != next_seq_num:
        if len(acks_recvd) > 0:
            for _ in range(len(acks_recvd)):
                if base in acks_recvd:
                    acks_recvd.remove(base)
                    base += 1

        if (next_seq_num < base + WINDOW_SIZE) and not file_to_send.closed:
            payload = file_to_send.read(MAX_PAYLOAD)

            if payload == "":
                file_to_send.close()
            else:
                fmt = '>III{0}s'.format(len(payload))
                packet = pack(fmt, DATA_PACKET_TYPE, calcsize(fmt), next_seq_num, payload)
                t = threading.Thread(target=send_packet, args=(packet, calcsize(fmt), next_seq_num))
                threads.append(t)
                t.start()
                next_seq_num += 1

    print "waiting for subthreads to complete"  # if base == next_seq_num this should already be true, but w.e
    for t in threads:
        t.join()

    # print "sending EOT"
    eot_packet = pack('>III', EOT_PACKET_TYPE, 12, 0)
    send_socket.sendto(eot_packet, channel_info)
    log((EOT_PACKET_TYPE, 12, 0), True)
    send_socket.setblocking(True)
    while True:  # there might be duplicate acks
        print "blocking waiting for EOT"
        data, _ = send_socket.recvfrom(12)
        header = unpack('>III', data[:12])
        log(header, False)
        if header[0] == EOT_PACKET_TYPE:
            sys.exit()

