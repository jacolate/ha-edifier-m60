import asyncio, sys
from bleak import BleakScanner, BleakClient

SEARCH_UUID = "0000f600-0000-1000-8000-00805f9b34fb"
EDIFIER_ID  = 0x07E0
WRITE  = "48090002-1a48-11e9-ab14-d663bd873d93"
NOTIFY = "48090001-1a48-11e9-ab14-d663bd873d93"

def packet(cmd, payload=b""):
    d = bytes([0xAA, 0xEC, cmd, len(payload) >> 8, len(payload) & 255]) + bytes(payload)
    return d + bytes([sum(d) & 255])

def on_notify(_, data):
    print("Response:", data.hex(" ").upper())

async def main():
    print("Scanning ...")
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    target = None
    for dev, adv in found.values():
        if SEARCH_UUID in [u.lower() for u in adv.service_uuids] or EDIFIER_ID in adv.manufacturer_data:
            target = dev
            print("Found:", adv.local_name or dev.name, dev.address)
            print("  Manufacturer:", {hex(k): v.hex(" ") for k, v in adv.manufacturer_data.items()})
    if not target:
        print("Nothing found – is the Mac connected for audio and the M60 in Bluetooth mode?")
        return

    async with BleakClient(target) as c:
        await c.start_notify(NOTIFY, on_notify)
        print("Querying input ...")
        await c.write_gatt_char(WRITE, packet(0x61), response=True)
        await asyncio.sleep(2)
        if len(sys.argv) == 3:
            typ, val = int(sys.argv[1], 16), int(sys.argv[2], 16)
            print(f"Setting type {typ:02X} -> input {val:02X} ...")
            await c.write_gatt_char(WRITE, packet(0x62, [typ, val]), response=True)
            await asyncio.sleep(2)

asyncio.run(main())
