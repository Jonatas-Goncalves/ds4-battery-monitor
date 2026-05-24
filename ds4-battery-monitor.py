#!/usr/bin/python3
import socket
import threading
import time
import os
import shutil
import gi
import cairo
import warnings
import math

gi.require_version("Gtk", "3.0")
gi.require_version('AyatanaAppIndicator3', '0.1')
from gi.repository import (
    Gtk,
    GdkPixbuf,
    GLib,
    Gdk,
    AyatanaAppIndicator3
)

warnings.filterwarnings("ignore", category=DeprecationWarning)
from evdev import InputDevice, list_devices, ecodes, ff

try:
    import evdev
    from evdev import ecodes, InputDevice, ff

    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False
    print(
        "[!] Library 'evdev' not found. Vibration will only be simulated in the terminal.."
    )

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOCAL_ICON_PATH = os.path.join(SCRIPT_DIR, "icons")

if os.path.exists("/.flatpak-info"):
    ICON_PATH = "/app/share/ds4-battery-monitor/icons/"
elif os.path.exists(LOCAL_ICON_PATH):
    ICON_PATH = LOCAL_ICON_PATH + "/"
else:
    ICON_PATH = "/usr/share/ds4-battery-monitor/icons/"

UDP_IP = "127.0.0.1"
UDP_PORT = 54321


def setup_autostart():
    if not os.path.exists("/.flatpak-info"):
        return
    autostart_dir = os.path.expanduser("~/.config/autostart")
    desktop_file = "io.github.jonatas_goncalves.ds4batterymonitor.desktop"
    source = f"/app/share/applications/{desktop_file}"
    dest = os.path.join(autostart_dir, desktop_file)
    try:
        if os.path.exists(source) and not os.path.exists(dest):
            os.makedirs(autostart_dir, exist_ok=True)
            shutil.copy(source, dest)
            print(f"[*] Autostart configured in: {dest}")
    except Exception as e:
        print(f"[X] Error configuring autostart.: {e}")


class MultiBatteryMonitor:
    def __init__(self):
        self.icons = {}
        self.last_updates = {}
        self.running = True
        self.dynamic_map = {}
        self.next_available_id = 1
        self.rumble_devices = {}
        self.rumble_handles = {}

        # Dictionary to track if alerts have already been triggered to avoid infinite vibration
        # Format: { 'control_id': {'25': False, '15': False} }
        self.alert_states = {}

    def find_controller_event(self, mac_fragment):

        for path in list_devices():

            try:
                dev = InputDevice(path)

                print(f"[*] Checking: {path} | uniq={dev.uniq}")

                if (
                    dev.uniq and
                    mac_fragment.replace(":", "").lower()
                    in
                    dev.uniq.replace(":", "").lower()
                    and
                    ecodes.EV_FF in dev.capabilities()
                ):
                    return path

            except:
                pass

        return None

    def trigger_vibration_pattern(self, c_id, rounds, pulses):
        print(
            f"[*] Low battery alert! "
            f"Control {c_id} -> {rounds}x rounds / {pulses}x pulses"
        )

        try:
            event_path = self.rumble_devices.get(c_id)
            if not event_path:
                return
            #
            # rounds = quantas sequências
            # pulses = quantas tremidas por sequência
            #

            for _ in range(rounds):
                for _ in range(pulses):
                    self.send_rumble(c_id, duration_sec=0.07)
                    #
                    # pausa curta entre tremidas
                    #
                    time.sleep(0.05)
                #
                # pausa entre sequências
                #

                time.sleep(0.45)

        except Exception as e:
            print(f"[X] Error in vibration thread: {e}")

    def find_rumble_device(self, mac_address):
        for path in list_devices():
            dev = InputDevice(path)
            if dev.uniq == mac_address and ecodes.EV_FF in dev.capabilities():
                return dev
        return None

    def send_rumble(self, c_id, duration_sec):
        try:
            dev = self.rumble_handles.get(c_id)

            rumble = ff.Rumble(
                strong_magnitude=65535,
                weak_magnitude=65535
            )

            duration_ms = int(duration_sec * 1000)

            effect = ff.Effect(
                ecodes.FF_RUMBLE,
                -1,
                0,
                ff.Trigger(0, 0),
                ff.Replay(duration_ms, 0),
                ff.EffectType(ff_rumble_effect=rumble),
            )

            effect_id = dev.upload_effect(effect)

            # força máxima
            dev.write(ecodes.EV_FF, ecodes.FF_GAIN, 0x7FFF)

            print(f"[*] Playing rumble on controller {c_id}")

            # inicia efeito
            dev.write(ecodes.EV_FF, effect_id, 1)

            # espera terminar
            time.sleep(duration_sec + 0.05)

            # remove efeito
            dev.erase_effect(effect_id)

        except Exception as e:
            print(f"[X] Control failed to vibrate: {e}")

    def check_battery_alerts(self, c_id, percentage):
        if c_id not in self.alert_states:
            self.alert_states[c_id] = {
                "50": False,
                "25": False,
                "15": False
            }
        #
        # Reset alertas se carregar novamente
        #
        if percentage > 50:
            self.alert_states[c_id]["50"] = False
            self.alert_states[c_id]["25"] = False
            self.alert_states[c_id]["15"] = False
        #
        # 50%
        # 3 sequências de 2 tremidas
        #
        if (
            25 < percentage <= 50
            and
            not self.alert_states[c_id]["50"]
        ):
            self.alert_states[c_id]["50"] = True
            threading.Thread(
                target=self.trigger_vibration_pattern,
                args=(c_id, 3, 2),
                daemon=True
            ).start()
        #
        # 25%
        # 3 sequências de 3 tremidas
        #
        if (
            15 < percentage <= 25
            and
            not self.alert_states[c_id]["25"]
        ):
            self.alert_states[c_id]["25"] = True
            threading.Thread(
                target=self.trigger_vibration_pattern,
                args=(c_id, 3, 3),
                daemon=True
            ).start()
        #
        # 15%
        # 4 sequências de 4 tremidas
        #
        if (
            percentage <= 15
            and
            not self.alert_states[c_id]["15"]
        ):
            self.alert_states[c_id]["15"] = True
            threading.Thread(
                target=self.trigger_vibration_pattern,
                args=(c_id, 4, 4),
                daemon=True
            ).start()

    def update_tray_icon(self, c_id, percentage):
        self.check_battery_alerts(c_id, percentage)

        if percentage > 75:
            icon_name = "ds4_100.png"
        elif 50 <= percentage <= 75:
            icon_name = "ds4_50.png"
        elif 16 <= percentage <= 49:
            icon_name = "ds4_25.png"
        else:
            icon_name = "ds4_15.png"

        full_path = os.path.join(ICON_PATH, icon_name)
        GLib.idle_add(self._sync_gtk_icon, c_id, full_path, percentage)

    def _sync_gtk_icon(self, c_id, path, perc):
        try:

            TRAY_SIZE = 32
            RENDER_SIZE = 128

            #
            # Cria indicator apenas 1 vez
            #

            if c_id not in self.icons:

                indicator = AyatanaAppIndicator3.Indicator.new(
                    f"ds4-monitor-{c_id}",
                    "",
                    AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS
                )

                indicator.set_status(
                    AyatanaAppIndicator3.IndicatorStatus.ACTIVE
                )

                menu = Gtk.Menu()

                item_exit = Gtk.MenuItem(label="Exit")
                item_exit.connect("activate", self.stop_all)

                menu.append(item_exit)

                menu.show_all()

                indicator.set_menu(menu)

                self.icons[c_id] = indicator

            #
            # Recupera indicator existente
            #

            indicator = self.icons[c_id]

            #
            # Carrega PNG original
            #

            original = GdkPixbuf.Pixbuf.new_from_file(path)

            CONTROL_SIZE = 152

            pixbuf = original.scale_simple(
                CONTROL_SIZE,
                CONTROL_SIZE,
                GdkPixbuf.InterpType.HYPER
            )

            #
            # Surface Cairo
            #

            surface = cairo.ImageSurface(
                cairo.FORMAT_ARGB32,
                RENDER_SIZE,
                RENDER_SIZE
            )

            ctx = cairo.Context(surface)

            ctx.set_antialias(cairo.ANTIALIAS_BEST)

            #
            # Desenha controle
            #

            CONTROL_OFFSET_Y = -28

            control_x = (RENDER_SIZE - CONTROL_SIZE) // 2
            control_y = CONTROL_OFFSET_Y

            Gdk.cairo_set_source_pixbuf(
                ctx,
                pixbuf,
                control_x,
                control_y
            )

            ctx.paint()

            #
            # Texto P1/P2
            #

            text = f"P{c_id}"

            ctx.select_font_face(
                "Liberation Sans",
                cairo.FONT_SLANT_NORMAL,
                cairo.FONT_WEIGHT_BOLD
            )

            ctx.set_font_size(52)

            extents = ctx.text_extents(text)

            padding_x = 16
            padding_y = 10

            rect_w = extents.width + padding_x * 2
            rect_h = extents.height + padding_y * 2

            rect_x = (RENDER_SIZE - rect_w) / 2
            rect_y = RENDER_SIZE - rect_h

            #
            # Fundo preto translúcido
            #

            radius = 12

            ctx.set_source_rgba(0.05, 0.05, 0.05, 0.95)

            ctx.new_path()

            ctx.arc(rect_x + rect_w - radius, rect_y + radius, radius, -math.pi/2, 0)
            ctx.arc(rect_x + rect_w - radius, rect_y + rect_h - radius, radius, 0, math.pi/2)
            ctx.arc(rect_x + radius, rect_y + rect_h - radius, radius, math.pi/2, math.pi)
            ctx.arc(rect_x + radius, rect_y + radius, radius, math.pi, 3*math.pi/2)

            ctx.close_path()

            ctx.fill()

            #
            # Texto branco
            #

            ctx.set_source_rgb(1, 1, 1)

            text_x = rect_x + (rect_w - extents.width) / 2 - extents.x_bearing
            text_y = rect_y + (rect_h - extents.height) / 2 - extents.y_bearing

            ctx.move_to(text_x, text_y)

            ctx.show_text(text)

            #
            # Exporta PNG temporário
            #

            final_pixbuf = Gdk.pixbuf_get_from_surface(
                surface,
                0,
                0,
                RENDER_SIZE,
                RENDER_SIZE
            )

            scaled = final_pixbuf.scale_simple(
                TRAY_SIZE,
                TRAY_SIZE,
                GdkPixbuf.InterpType.HYPER
            )

            tmp_icon = f"/tmp/ds4_icon_{c_id}_{int(time.time()*1000)}.png"

            scaled.savev(
                tmp_icon,
                "png",
                [],
                []
            )

            #
            # Atualiza AppIndicator
            #

            indicator.set_icon_full(
                tmp_icon,
                f"DS4 Controller {c_id}"
            )

        except Exception as e:
            print(f"[X] Error drawing icon: {e}")

    def on_menu(self, icon, button, activate_time):
        menu = Gtk.Menu()
        item_exit = Gtk.MenuItem(label="Sair")
        item_exit.connect("activate", self.stop_all)
        menu.append(item_exit)
        menu.show_all()
        menu.popup(None, None, None, icon, button, activate_time)

    def stop_all(self, widget=None):
        print("[!] Closing monitor...")
        self.running = False
        Gtk.main_quit()

    def listen_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, UDP_PORT))
        sock.settimeout(1)
        print(f"[*] UDP Listener em {UDP_IP}:{UDP_PORT}")

        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode().strip()
                if ":" in msg:
                    raw_id, c_perc = msg.rsplit(":", 1)
                    c_perc = int(c_perc)
                    clean_raw_id = raw_id.replace(":", "")

                    if clean_raw_id not in self.dynamic_map:
                        if clean_raw_id.isdigit():
                            self.dynamic_map[clean_raw_id] = clean_raw_id
                        else:
                            while (
                                str(self.next_available_id) in self.dynamic_map.values()
                            ):
                                self.next_available_id += 1
                            self.dynamic_map[clean_raw_id] = str(self.next_available_id)

                    c_id = self.dynamic_map[clean_raw_id]
                    if c_id not in self.rumble_devices:

                        event_path = self.find_controller_event(raw_id)

                        if event_path:
                            self.rumble_devices[c_id] = event_path
                            self.rumble_handles[c_id] = InputDevice(event_path)
                    self.last_updates[c_id] = time.time()
                    self.update_tray_icon(c_id, c_perc)
            except socket.timeout:
                self.check_timeouts()
            except Exception as e:
                if self.running:
                    print(f"UDP Error: {e}")

    def check_timeouts(self):
        now = time.time()
        to_remove = [c_id for c_id, last in self.last_updates.items() if now - last > 5]
        for c_id in to_remove:
            print(f"[-] Control {c_id} disconnected (timeout)")
            if c_id in self.icons:
                self.icons[c_id].set_status(
                    AyatanaAppIndicator3.IndicatorStatus.PASSIVE
                )
                del self.icons[c_id]

                if c_id in self.rumble_handles:
                    try:
                        self.rumble_handles[c_id].close()
                    except:
                        pass
                    del self.rumble_handles[c_id]

            # Limpa também o estado de alerta de bateria desse controle
            if c_id in self.alert_states:
                del self.alert_states[c_id]
            if c_id in self.rumble_devices:
                del self.rumble_devices[c_id]

            del self.last_updates[c_id]
            keys_to_del = [k for k, v in self.dynamic_map.items() if v == c_id]
            for k in keys_to_del:
                del self.dynamic_map[k]

    def scan_native_kernel_drivers(self):
        print("[*] Scanning /sys/class/power_supply...")
        internal_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        while self.running:
            try:
                all_supplies = os.listdir("/sys/class/power_supply/")
                controllers = [
                    d for d in all_supplies if "controller" in d and "battery" in d
                ]
                for dev_name in controllers:
                    path = os.path.join("/sys/class/power_supply/", dev_name)
                    try:
                        c_id = (
                            dev_name.split("-")[-1].split("_")[-1][-5:].replace(":", "")
                        )
                        capacity_file = os.path.join(path, "capacity")
                        if os.path.exists(capacity_file):
                            with open(capacity_file, "r") as f:
                                capacity = f.read().strip()
                            msg = f"{c_id}:{capacity}"
                            internal_sock.sendto(msg.encode(), (UDP_IP, UDP_PORT))
                    except:
                        pass
            except:
                pass
            time.sleep(4)


if __name__ == "__main__":
    setup_autostart()
    monitor = MultiBatteryMonitor()

    threading.Thread(target=monitor.scan_native_kernel_drivers, daemon=True).start()
    threading.Thread(target=monitor.listen_udp, daemon=True).start()

    try:
        Gtk.main()
    except KeyboardInterrupt:
        monitor.stop_all()
