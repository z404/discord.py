"""
The MIT License (MIT)

Copyright (c) 2015-present Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import sys
import socket
import pickle
import select
import time
import struct
import json
import traceback

from .opus import DecodeManager
from .sink import RawData


class AudioProcessor:
    def __init__(self):
        self.address = sys.argv[0]
        self.local_address = sys.argv[1]
        self.ssrc_map = json.loads(" ".join(sys.argv[2:]))

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        while True:
            ready, _, err = select.select([self.socket], [],
                                          [self.socket], 0.01)
            if not ready:
                if err:
                    print(f"Socket error: {err}")
                continue

            data, address = self.socket.recvfrom(4096)
            if address == self.local_address:
                self.sink = pickle.loads(data)
                break

        self.decoder = DecodeManager(self)
        self.paused = False
        self.recording = True
        self.starting_time = None
        self.user_timestamps = {}

        self.actions = {
            "STOP": self.stop_recording,
            "PAUSE": self.toggle_pause,
            "SSRC": self.add_ssrc,
        }

    def stop_recording(self):
        self.recording = False
        self.paused = False
        self.decoder.stop()

    def toggle_pause(self, value):
        self.paused = json.loads(value)

    def add_ssrc(self, ssrc):
        self.ssrc_map.update(json.loads(ssrc))

    @staticmethod
    def format_args(kwargs):
        return {arg.split()[0]: " ".split(arg.split()[1:]) for arg in kwargs}

    def run(self):
        self.decoder.start()
        self.starting_time = time.perf_counter()
        while self.recording:
            ready, _, err = select.select([self.socket], [],
                                          [self.socket], 0.01)
            if not ready:
                if err:
                    print(f"Socket error: {err}")
                continue
            try:
                data, address = self.socket.recvfrom(4096)
            except OSError:
                self.stop_recording()
                continue


            if address == self.local_address:
                action = data.decode('utf-8').split('--')
                kwargs = self.format_args(action[1:])
                action = action[0]
                if action in self.actions:
                    self.actions[action](**kwargs)
                continue
            elif address != self.address:
                continue

            self.unpack_audio(data)
        self.sink.cleanup()
        print(pickle.dumps(self.sink))

    def unpack_audio(self, data):
        """Takes an audio packet received from Discord and decodes it into pcm audio data.
        If there are no users talking in the channel, `None` will be returned.

        You must be connected to receive audio.

        Parameters
        ---------
        data: :class:`bytes`
            Bytes received by Discord via the UDP connection used for sending and receiving voice data.
        """
        if 200 <= data[1] <= 204:
            # RTCP received.
            # RTCP provides information about the connection
            # as opposed to actual audio data, so it's not
            # important at the moment.
            return
        if self.paused:
            return

        data = RawData(data, self)

        if data.decrypted_data == b'\xf8\xff\xfe':  # Frame of silence
            return

        self.decoder.decode(data)

    def recv_decoded_audio(self, data):
        if data.ssrc not in self.user_timestamps:
            self.user_timestamps.update({data.ssrc: data.timestamp})
            # Add silence of when they were not being recorded.
            data.decoded_data = struct.pack('<h', 0) * round(
                self.decoder.CHANNELS * self.decoder.SAMPLING_RATE * (time.perf_counter() - self.starting_time)
            ) + data.decoded_data
        else:
            self.user_timestamps[data.ssrc] = data.timestamp

        silence = data.timestamp - self.user_timestamps[data.ssrc] - 960
        data.decoded_data = struct.pack('<h', 0) * silence + data.decoded_data
        while data.ssrc not in self.ssrc_map:
            time.sleep(0.05)

        self.sink.write(data.decoded_data, self.ssrc_map[data.ssrc]['user_id'])


audio_proc = AudioProcessor()
audio_proc.run()
sys.exit()
