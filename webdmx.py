import asyncio
import websockets
import json
import time
import dmxseq
import sqlite3
import syslog

config = {
    'http-listen-addr': "0.0.0.0",
    'http-listen-port': 31502,

    'ws-listen-addr': "0.0.0.0",
    'ws-listen-port': 31501,
}

syslog.openlog(ident="webdmx", logoption=syslog.LOG_PID)
syslog.syslog("Initializing webdmx gateway")

class DMXPresets():
    def __init__(self, database="dmx.sqlite3"):
        self.db = sqlite3.connect("dmx.sqlite3")

    def close(self):
        self.db.close()

    def list(self):
        cursor = self.db.cursor()
        cursor.execute('SELECT name, payload FROM presets',)
        presets = []

        for row in cursor.fetchall():
            presets.append({"name": row[0], "value": json.loads(row[1])})

        return presets

    def load(self, name):
        cursor = self.db.cursor()

        cursor.execute('SELECT payload FROM presets WHERE name = ?', (name,))
        data = cursor.fetchone()

        if data == None:
            return {}

        syslog.syslog(f"Loading preset: {name}")

        return json.loads(data[0])

    def save(self, name, payload):
        cursor = self.db.cursor()

        cursor.execute('INSERT INTO presets (name, payload) VALUES (?, ?)', (name, json.dumps(payload)))
        self.db.commit()

        return True

class DMXWebUIServer():
    def __init__(self):
        self.wsclients = set()
        server = ("10.241.0.200", 60877)
        self.dmx = dmxseq.DMXSequencer(server)

    def presets(self):
        return DMXPresets()

    async def wsbroadcast(self, payload):
        if not len(self.wsclients):
            return

        content = json.dumps(payload)

        for client in list(self.wsclients):
            if not client.open:
                continue

            try:
                await client.send(content)

            except Exception as e:
                print(e)

    async def wspayload(self, websocket, payload):
        content = json.dumps(payload)
        await websocket.send(content)

    async def handler(self, websocket, path):
        self.wsclients.add(websocket)

        print("[+] websocket: client connected")

        try:
            state = self.dmx.fetchstate()
            data = {"type": "state", "value": state}
            print(data)

            await self.wspayload(websocket, data)

            while True:
                if not websocket.open:
                    break

                payload = await websocket.recv()

                print("[+] message received")
                data = json.loads(payload)
                print(data)

                if data["type"] == "change":
                    state = data["value"]
                    self.dmx.loads(state)

                if data["type"] == "save":
                    pre = self.presets()
                    prestate = self.dmx.fetchstate()
                    pre.save(data["value"], prestate)
                    pre.close()

                    response = {"type": "save", "value": True}
                    await self.wspayload(websocket, response)

                if data["type"] == "presets":
                    pre = self.presets()
                    prelist = pre.list()
                    pre.close()

                    response = {"type": "presets", "value": prelist}
                    await self.wspayload(websocket, response)

                if data["type"] in ["load", "load-add", "load-sub", "load-replace"]:
                    current = self.dmx.fetchstate()

                    pre = self.presets()
                    loader = pre.load(data["value"])
                    pre.close()

                    if data["type"] == "load-add":
                        for idx, val in enumerate(loader):
                            if val > 0:
                                current[idx] = val

                        loader = current

                    if data["type"] == "load-sub":
                        for idx, val in enumerate(loader):
                            if val > 0:
                                nval = current[idx] - val if current[idx] > val else 0
                                current[idx] = nval

                        loader = current

                    self.dmx.loads(loader)

                    # send frame like it was an update
                    response = {"type": "state", "value": loader}
                    await self.wspayload(websocket, response)

        except websockets.exceptions.ConnectionClosedOK:
            print("[+] websocket: connection closed")

        except websockets.exceptions.ConnectionClosedError:
            print("[+] websocket: connection closed (with error)")

        except ConnectionResetError:
            print("[+] websocket: connection reset")

        finally:
            print("[+] websocket: discarding client")
            self.wsclients.remove(websocket)

    def run(self):
        # standard polling handlers
        loop = asyncio.get_event_loop()
        loop.set_debug(True)

        # handle websocket communication
        websocketd = websockets.serve(self.handler, config['ws-listen-addr'], config['ws-listen-port'])
        asyncio.ensure_future(websocketd, loop=loop)

        print("[+] waiting for clients")
        loop.run_forever()

if __name__ == '__main__':
    webui = DMXWebUIServer()
    webui.run()
