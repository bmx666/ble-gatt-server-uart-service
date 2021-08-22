#!/usr/bin/python3

# based on script from article "Creating BLE GATT Server (UART Service) on Raspberry Pi"
# https://scribles.net/creating-ble-gatt-server-uart-service-on-raspberry-pi/

import argparse
import sys
import dbus, dbus.mainloop.glib
from gi.repository import GLib
from example_advertisement import Advertisement
from example_advertisement import register_ad_cb, register_ad_error_cb
from example_gatt_server import Service, Characteristic
from example_gatt_server import register_app_cb, register_app_error_cb

BLUEZ_SERVICE_NAME =           'org.bluez'
DBUS_OM_IFACE =                'org.freedesktop.DBus.ObjectManager'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_MANAGER_IFACE =           'org.bluez.GattManager1'
GATT_CHRC_IFACE =              'org.bluez.GattCharacteristic1'

DEFAULT_HCI_IFACE =  'hci0'
DEFAULT_LOCAL_NAME = 'uart-gatt-server'

NRF_UART_SERVICE_UUID =                 '6e400001-b5a3-f393-e0a9-e50e24dcca9e'
NRF_UART_RX_CHARACTERISTIC_UUID =       '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
NRF_UART_TX_CHARACTERISTIC_UUID =       '6e400003-b5a3-f393-e0a9-e50e24dcca9e'

CC254X_UART_SERVICE_UUID =              '0000ffe0-0000-1000-8000-00805f9b34fb'
CC254X_UART_RX_TX_CHARACTERISTIC_UUID = '0000ffe1-0000-1000-8000-00805f9b34fb'

mainloop = None

HCI_IFACE = None
LOCAL_NAME = None
UART_SERVICE_UUID = None

class TxCharacteristic(Characteristic):
    def __init__(self, bus, uart_tx_characteristic_uuid, index, service):
        Characteristic.__init__(self, bus, index, uart_tx_characteristic_uuid,
                                ['notify'], service)
        self.notifying = False
        GLib.io_add_watch(sys.stdin, GLib.IO_IN, self.on_console_input)

    def on_console_input(self, fd, condition):
        s = fd.readline()
        if s.isspace():
            pass
        else:
            self.send_tx(s)
        return True

    def send_tx(self, s):
        if not self.notifying:
            return
        value = []
        for c in s:
            value.append(dbus.Byte(c.encode()))
        self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': value}, [])

    def StartNotify(self):
        if self.notifying:
            return
        self.notifying = True

    def StopNotify(self):
        if not self.notifying:
            return
        self.notifying = False

class RxCharacteristic(Characteristic):
    def __init__(self, bus, uart_rx_characteristic_uuid, index, service):
        Characteristic.__init__(self, bus, index, uart_rx_characteristic_uuid,
                                ['write'], service)

    def WriteValue(self, value, options):
        print('remote: {}'.format(bytearray(value).decode()))

class RxTxCharacteristic(RxCharacteristic, TxCharacteristic):
    def __init__(self, bus, uart_rx_tx_characteristic_uuid, index, service):
        Characteristic.__init__(self, bus, index, uart_rx_tx_characteristic_uuid,
                                ['notify','write'], service)
        self.notifying = False
        GLib.io_add_watch(sys.stdin, GLib.IO_IN, self.on_console_input)

class UartService(Service):
    def __init__(self, bus, index):

        Service.__init__(self, bus, index, UART_SERVICE_UUID, True)

        if UART_SERVICE_UUID == CC254X_UART_SERVICE_UUID:
            self.add_characteristic(RxTxCharacteristic(bus, CC254X_UART_RX_TX_CHARACTERISTIC_UUID, 0, self))

        elif UART_SERVICE_UUID == NRF_UART_SERVICE_UUID:
            self.add_characteristic(TxCharacteristic(bus, NRF_UART_TX_CHARACTERISTIC_UUID, 0, self))
            self.add_characteristic(RxCharacteristic(bus, NRF_UART_RX_CHARACTERISTIC_UUID, 1, self))

        else:
            raise('invalid UART_SERVICE_UUID')

class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            chrcs = service.get_characteristics()
            for chrc in chrcs:
                response[chrc.get_path()] = chrc.get_properties()
        return response

class UartApplication(Application):
    def __init__(self, bus):
        Application.__init__(self, bus)
        self.add_service(UartService(bus, 0))

class UartAdvertisement(Advertisement):
    def __init__(self, bus, index):
        Advertisement.__init__(self, bus, index, 'peripheral')
        self.add_service_uuid(UART_SERVICE_UUID)
        self.add_local_name(LOCAL_NAME)
        self.include_tx_power = True

def find_adapter(bus):
    remote_om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, '/'),
                               DBUS_OM_IFACE)
    objects = remote_om.GetManagedObjects()
    for o, props in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in props and GATT_MANAGER_IFACE in props:
            if str(o).endswith('/' + HCI_IFACE):
                return o
        print('Skip adapter:', o)
    return None

def main():
    global mainloop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter = find_adapter(bus)
    if not adapter:
        print('BLE adapter not found')
        return

    service_manager = dbus.Interface(
                                bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                GATT_MANAGER_IFACE)
    ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                LE_ADVERTISING_MANAGER_IFACE)

    app = UartApplication(bus)
    adv = UartAdvertisement(bus, 0)

    mainloop = GLib.MainLoop()

    service_manager.RegisterApplication(app.get_path(), {},
                                        reply_handler=register_app_cb,
                                        error_handler=register_app_error_cb)
    ad_manager.RegisterAdvertisement(adv.get_path(), {},
                                     reply_handler=register_ad_cb,
                                     error_handler=register_ad_error_cb)
    try:
        mainloop.run()
    except KeyboardInterrupt:
        adv.Release()

if __name__ == '__main__':

    parser = argparse.ArgumentParser()
    parser.add_argument('-u', '--uart_service_uuid', choices=['nrf', 'cc254x'], help="uart service")
    parser.add_argument('-i', '--interface', help=f"hci interface, by default '{DEFAULT_HCI_IFACE}'", default=DEFAULT_HCI_IFACE)
    parser.add_argument('-n', '--local-name', help=f"local name, by default '{DEFAULT_LOCAL_NAME}'", default=DEFAULT_LOCAL_NAME)
    args = parser.parse_args()

    if args.uart_service_uuid == 'cc254x':
        UART_SERVICE_UUID = CC254X_UART_SERVICE_UUID
    elif args.uart_service_uuid == 'nrf':
        UART_SERVICE_UUID = NRF_UART_SERVICE_UUID

    LOCAL_NAME = str(args.local_name)
    HCI_IFACE = str(args.interface)

    main()
