#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import random
import string
import time

import aiofiles
import dotenv

import logging
import sys

from gmqtt import Client as MQTTClient


class MessageQue:
    def __init__(self, name: str):
        self.messages = []
        self.name = name

    def get(self) -> (tuple, None):
        if len(self.messages):
            return self.messages.pop()

    def add(self, topic: str, message: str):
        self.messages.append((topic, message))


class CustomLogger:
    def __init__(self):
        self.logger = logging.getLogger()
        self.logger.setLevel(logging.INFO)
        self.formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

    def setup_stdout_handler(self):
        stdout_handler = logging.StreamHandler(sys.stdout)
        stdout_handler.setLevel(logging.DEBUG)
        stdout_handler.setFormatter(self.formatter)
        self.logger.addHandler(stdout_handler)

    def setup_file_handler(self, log_name):
        logfile = 'logs/%s.log' % log_name
        file_handler = logging.FileHandler(logfile)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(self.formatter)
        self.logger.addHandler(file_handler)


class Status:
    AVAILABLE = 'AVAILABLE'
    BUSY = 'BUSY'


class MqttClient:
    def __init__(self, host: str, port: int, subscriptions: list, client_id: str, verbosity: int,
                 logger: logging.Logger = None):
        self.host = host
        self.port = port
        self.logger = logger
        self.cid = client_id
        self.subscriptions = subscriptions
        self.client = None
        self.STOP = asyncio.Event()
        self.verbosity = verbosity
        self.outgoing_queue = MessageQue('outgoing')
        self.incoming_queue = MessageQue('incoming')
        self.running = True

    def _print(self, text, _type='ok'):
        if self.logger is None:
            if _type == 'good':
                print('[+] %s' % text)
            elif _type == 'bad':
                print('[-] %s' % text)
            elif _type == 'ok':
                print('[*] %s' % text)
            else:
                print('[!] %s' % text)
        else:
            if _type in ['ok', 'good']:
                self.logger.info(text)
            elif _type in 'bad':
                self.logger.error(text)

    def on_connect(self, client, flags, rc, properties):
        self._print('Connected')

    def on_message(self, client, topic, payload, qos, properties):
        if self.verbosity > 0:
            self._print(f'RECV MSG: {topic} {payload}')
        self.incoming_queue.add(topic, payload)

    def on_subscribe(self, client, mid, qos, properties):
        self._print('SUBSCRIBED')

    def on_disconnect(self, client, packet, exc=None):
        self._print('Disconnected')

    def ask_exit(self, *args):
        self.STOP.set()

    def subscribe(self, topics: list):
        for topic in topics:
            self._print(f'Subscribing to {topic}')
            self.client.subscribe(topic)

    def publish(self, topic, message, qos=1):
        self._print(f'Publishing ... {message} to {topic}')
        self.client.publish(topic, message, qos=qos)

    async def publish_from_que(self):
        self._print('Start pub from que ..')
        while self.running:
            msg = self.outgoing_queue.get()
            if msg:
                self.publish(topic=msg[0], message=msg[1])
            await asyncio.sleep(1)

    def shutdown(self):
        self.running = False

    async def start_loop(self):
        cid = self.cid + '_' + ''.join(random.sample(string.hexdigits, 6))
        self.client = MQTTClient(client_id=cid)

        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.on_disconnect = self.on_disconnect

        # Connect to mqtt proxy
        await self.client.connect(host=self.host, port=self.port)
        self.subscribe(self.subscriptions)
        await self.publish_from_que()
        await self.STOP.wait()
        await self.client.disconnect()


class Scanner:
    def __init__(self, mq_broker_address: str, loop: asyncio.AbstractEventLoop):
        host, port = self.parse_address_port(mq_broker_address)
        self.loop = loop
        self.client_id = self.generate_slug()
        self.subscriptions = [f'/client/{self.client_id}/+']
        self.mq = MqttClient(host, port, self.subscriptions, self.client_id, 0)
        self.outgoing_queue = self.mq.outgoing_queue
        self.incoming_queue = self.mq.incoming_queue
        self.status_messages = Status
        self.system_status = self.status_messages.AVAILABLE
        logger = CustomLogger()
        logger.setup_file_handler('coordinator')
        self.tasks = set()
        self.logger = logger.logger
        self.running = True

    def generate_slug(self, length: int = 6):
        return ''.join(random.sample(string.hexdigits, length))

    def quit(self):
        print('[+] Received QUIT instruction, shutting down.')
        self.running = False
        self.mq.running = False
        exit(0)

    def parse_address_port(self, address_str):
        host = address_str.split(':')[0]
        port = int(address_str.split(':')[1])
        return host, port

    def _parse_ports(self, port_seq: str):
        """Yield an iterator with integers extracted from a string
        consisting of mixed port numbers and/or ranged intervals.
        Ex: From '20-25,53,80,111' to (20,21,22,23,24,25,53,80,111)
        """
        if not port_seq:
            return '0-65365'
        for port in port_seq.split(','):
            try:
                port = int(port)
                if not 0 < port < 65536:
                    raise SystemExit(f'Error: Invalid port number {port}.')
                yield port
            except ValueError:
                start, end = (int(port) for port in port.split('-'))
                yield from range(start, end + 1)

    def parse_ports(self, port_seq: str):
        ports = []
        port_gen = self._parse_ports(port_seq)
        for x in port_gen:
            ports.append(x)
        return ports

    def status_toggle(self, state: str):
        if hasattr(self.status_messages, state):
            setattr(self, 'system_status', getattr(self.status_messages, state))

    async def execute_cmd(self, cmd: str):
        self.status_toggle(self.status_messages.BUSY)
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE
        )

        stdout, stderr = await proc.communicate()

        print(f'[{cmd!r} exited with {proc.returncode}]')
        if stdout:
            print(f'[stdout]\n{stdout.decode()}')
        if stderr:
            print(f'[stderr]\n{stderr.decode()}')
        self.status_toggle(self.status_messages.AVAILABLE)

    async def broadcast(self, topic: str, message: dict):
        self.outgoing_queue.add(topic, json.dumps(message))

    async def start_status_loop(self):
        while self.running:
            message = {'type': 'ping', 'timestamp': time.time(), 'cid': self.client_id, 'status': self.system_status}
            await self.broadcast(f'/client/{self.client_id}/status', message)
            await asyncio.sleep(5)

    async def write_targets(self, targets: list, ):
        data = '\n'.join(targets)
        async with aiofiles.open('../targets/targets.txt', 'w') as f:
            await f.write(data)

    async def process_targets(self, targets: list) -> tuple[list, str]:
        parsed_targets = []
        parsed_ports = []
        parsed_port_str = ' -p0'

        def parse_target(_target):
            split_target = _target.split(':')
            parsed_targets.append(split_target[0])
            parsed_ports.append(split_target[1])

        targets = sorted(set(targets))
        for _target in targets:
            parse_target(_target)
        parsed_targets = sorted(set(parsed_targets))
        for port in sorted(set(parsed_ports)):
            parsed_port_str += ',%s' % port
        return parsed_targets, parsed_port_str

    async def process_message(self, subtopic, message):
        if subtopic == 'execute':
            print('[+] Received new `execute` instruction')
            command = message.get('command_line')
            targets = message.get('targets')
            if len(targets):
                targets, ports = await self.process_targets(targets)
                command = command + ports
                print(f'[+] Executing: \n{command}')
                await self.write_targets(targets)

            if self.system_status == self.status_messages.AVAILABLE:

                print('[+] Executing command from coordinator ... ')
                await self.execute_cmd(command)
            else:
                print('[!] System is busy, rejecting command!')

        elif subtopic == 'ping':
            print('[+] Ping, pong')
            message = {'type': 'pong', 'cid': self.client_id, 'timestamp': time.time()}
            await self.broadcast(f'/client/{self.client_id}/pong', message)
        elif subtopic == 'quit':
            self.quit()
        else:
            pass
            # ignore

    async def parse_incoming_message(self, message: tuple):
        topic = message[0].split('/')
        message = json.loads(message[1])
        print('[+] Processing message ... ')
        _topic = topic[1]  # client,coordinator,status
        recipient = topic[2]  # client_id, global
        subtopic = topic[3]  # execute, quit, ping
        if _topic == 'client':
            if recipient == self.client_id or recipient == 'global':
                self.loop.create_task(self.process_message(subtopic, message))
        else:
            print(f'[?] Received message on unknown topic: {topic}')

    async def start(self):
        while self.running:
            msg = self.incoming_queue.get()
            if msg:
                await self.parse_incoming_message(msg)
            await asyncio.sleep(1)
        self.logger.info('Shutting down at %s' % time.time())

    async def start_loop(self):
        self.tasks.add(self.loop.create_task(self.mq.start_loop()))
        self.tasks.add(self.loop.create_task(self.start_status_loop()))
        self.tasks.add(self.loop.create_task(self.start()))
        await asyncio.gather(*self.tasks)


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('-v', '--verbosity', action='count', default=0,
                      help='The verbosity of standard output.')
    args.add_argument('-o', '--output', dest='output', help='Ignored. For axiom compatability')
    args.add_argument('-b', '--broker', dest='mqtt_broker', type=str, default=None)
    args = args.parse_args()
    dotenv.load_dotenv()

    if args.mqtt_broker:
        mq_broker = args.mqtt_broker
    else:
        if os.path.exists('.env'):
            mq_broker = os.environ.get('mqtt_broker')
        else:
            mq_broker = '127.0.0.1:1883'

    print(f'[+] Broker: {mq_broker}')
    loop = asyncio.new_event_loop()
    scanner = Scanner(mq_broker, loop)

    try:
        loop.run_until_complete(scanner.start_loop())
    except KeyboardInterrupt:
        print('[+] Caught Signal, exit with grace ...')
        loop.close()
