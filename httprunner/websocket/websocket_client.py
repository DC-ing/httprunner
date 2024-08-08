import json
import time
import socket
import struct
from websocket import WebSocket, ABNF, STATUS_NORMAL, STATUS_ABNORMAL_CLOSED
from websocket._logging import isEnabledForError

from loguru import logger
from httprunner.models import WebsocketMethodEnum


class WebsocketClient(object):
    def __init__(self) -> None:
        self.client = WebSocket()
        self.text = None
        self.binary = None
        self.status_code = None

    def open_connection(self, url, **options):
        self.client.connect(url, **options)
        self.status_code = self.client.status

    def ping(self):
        self.client.ping()

    def write(self, **options):
        opcode = ABNF.OPCODE_TEXT
        if "text" in options.keys() and options["text"] is not None:
            payload = options["text"]
            opcode = ABNF.OPCODE_TEXT
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload)
            elif isinstance(payload, (bytes)):
                payload = str(payload, encoding="utf-8")
            else:
                payload = str(payload)
        elif "binary" in options.keys() and options["binary"] is not None:
            payload = options["binary"]
            opcode = ABNF.OPCODE_BINARY
        else:
            raise ValueError(
                "websocket write method miss text or binary. Please check again..."
            )
        self.client.send(payload, opcode=opcode)

    def read(self):
        resp_read = self.client.recv()
        try:
            resp_read = json.loads(resp_read)
        except json.decoder.JSONDecodeError as jde:
            pass
        return resp_read

    def close_connection(self, close_status=STATUS_NORMAL, timeout=3):
        resp_status = STATUS_ABNORMAL_CLOSED
        if self.client.connected:
            if close_status < 0 or close_status >= ABNF.LENGTH_16:
                raise ValueError("code is invalid range")

            try:
                self.client.connected = False
                self.client.send(
                    struct.pack("!H", close_status) + b"", ABNF.OPCODE_CLOSE
                )
                sock_timeout = self.client.sock.gettimeout()
                self.client.sock.settimeout(timeout)
                start_time = time.time()
                while timeout is None or time.time() - start_time < timeout:
                    try:
                        frame = self.client.recv_frame()
                        if frame.opcode != ABNF.OPCODE_CLOSE:
                            continue
                        if isEnabledForError():
                            recv_status = struct.unpack("!H", frame.data[0:2])[0]
                            if recv_status >= 3000 and recv_status <= 4999:
                                logger.debug("close status: " + repr(recv_status))
                            elif recv_status != STATUS_NORMAL:
                                logger.error("close status: " + repr(recv_status))
                            resp_status = recv_status
                        break
                    except:
                        break
                self.client.sock.settimeout(sock_timeout)
                self.client.sock.shutdown(socket.SHUT_RDWR)
            except:
                pass
            self.client.shutdown()
        # 设置关闭状态
        self.status_code = resp_status
        return resp_status

    def _set_timeout(self, timeout):
        self.client.settimeout(timeout)

    def send_request(self, request_method, url, **options):
        if "timeout" in options.keys() and options["timeout"] is not None:
            self._set_timeout(options["timeout"] / 1000.0)
        if request_method == WebsocketMethodEnum.OPEN:
            self.open_connection(url, **options)
        elif request_method == WebsocketMethodEnum.PING:
            self.ping()
        elif request_method == WebsocketMethodEnum.W:
            self.write(**options)
        elif request_method == WebsocketMethodEnum.R:
            self.text = self.read()
        elif request_method == WebsocketMethodEnum.WR:
            self.write(**options)
            self.text = self.read()
        elif request_method == WebsocketMethodEnum.CLOSE:
            close_status = STATUS_NORMAL
            timeout = 3
            if "timeout" in options.keys() and options["timeout"] is not None:
                timeout = options["timeout"] / 1000.0
            if "close_status" in options.keys() and options["close_status"] is not None:
                close_status = options["close_status"]
            self.close_connection(close_status=close_status, timeout=timeout)
        else:
            raise ValueError(f"unexpected websocket frame type: {request_method}")
        return self

    def get_client(self):
        return self.client

    def __del__(self):
        self.client.close()
