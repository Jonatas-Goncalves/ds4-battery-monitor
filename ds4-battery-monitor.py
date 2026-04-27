#!/usr/bin/python3
import socket
import threading
import time
import os
import glob
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw

ICON_PATH = os.path.expanduser("/usr/share/ds4-battery-monitor/icons/")

UDP_IP = "127.0.0.1"
UDP_PORT = 54321

class MultiBatteryMonitor:
    def __init__(self):
        self.icons = {} # { controller_id: IconInstance }
        self.last_updates = {} # { controller_id: timestamp }
        self.running = True

    def create_image(self, percentage, controller_id):
        # Logic of explicit tracks according to your strategy.
        if percentage > 75:
            icon_name = "ds4_100.png"   # Green (Full)
        elif 50 <= percentage <= 75:
            icon_name = "ds4_50.png"    # Yellow (Half)
        elif 11 <= percentage <= 49:
            icon_name = "ds4_25.png"    # Orange (Low)
        else:
            icon_name = "ds4_10.png"    # Red (Critical)

        full_path = os.path.join(ICON_PATH, icon_name)

        try:
            canvas = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            icon_raw = Image.open(full_path).convert('RGBA')
            icon_res = icon_raw.resize((56, 56), Image.Resampling.LANCZOS)
            canvas.paste(icon_res, (4, 0), icon_res)
            d = ImageDraw.Draw(canvas)

            try:
                from PIL import ImageFont
                font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf", 28)
            except:
                font = None

            text = f"P{controller_id}"

            if font:
                bbox = d.textbbox((0, 0), text, font=font)
                text_w = bbox[2] - bbox[0]
                text_h = bbox[3] - bbox[1]

                padding_x = 8
                padding_y = 2
                badge_w = text_w + (2 * padding_x)
                badge_h = text_h + (2 * padding_y)

                badge_x = (64 - badge_w) // 2
                badge_y = 64 - badge_h

                d.rounded_rectangle(
                    [badge_x, badge_y, badge_x + badge_w, badge_y + badge_h],
                    radius=4,
                    fill=(0, 0, 0, 210)
                )

                d.text((32, badge_y + (badge_h // 2)), text, font=font, fill=(255, 255, 255), anchor="mm")

            return canvas
        except Exception as e:
            print(f"[X] Error processing icon: {e}")
            return Image.new('RGBA', (64, 64), (255, 0, 0, 255))

    def listen_udp(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_IP, UDP_PORT))
        sock.settimeout(1)

        # Dynamic mapper: { "mac_or_original_id": "sequential_number" }
        self.dynamic_map = {}
        self.next_available_id = 1

        print(f"[*] Multi-Control Monitor in {UDP_IP}:{UDP_PORT}")

        while self.running:
            try:
                data, addr = sock.recvfrom(1024)
                msg = data.decode().strip()

                # Expect the format "ID:PERCENTAGE"
                if ":" in msg:
                    raw_id, c_perc = msg.rsplit(":", 1)
                    c_perc = int(c_perc)

                    # Limpa o ID (remove ':' do MAC para facilitar)
                    clean_raw_id = raw_id.replace(":", "")

                    # If ds4drv is sending "1", "2", etc., we use it directly.
                    # If it's a new Mac, we assign the next available number.
                    if clean_raw_id not in self.dynamic_map:
                        if clean_raw_id.isdigit():
                            # If it's already a number (ds4drv), reserve it.
                            self.dynamic_map[clean_raw_id] = clean_raw_id
                        else:
                            # If it's a MAC, we give it the next number (1, 2, 3...)
                            # Check if that number is already in use.
                            while str(self.next_available_id) in self.dynamic_map.values():
                                self.next_available_id += 1

                            self.dynamic_map[clean_raw_id] = str(self.next_available_id)

                    c_id = self.dynamic_map[clean_raw_id]

                    self.last_updates[c_id] = time.time()

                    if c_id not in self.icons:
                        self.show_icon(c_id, c_perc)
                    else:
                        self.icons[c_id].icon = self.create_image(c_perc, c_id)
                        self.icons[c_id].title = f"Control {c_id}: {c_perc}%"
            except socket.timeout:
                self.check_timeouts()
            except Exception as e:
                print(f"Error: {e}")

    def show_icon(self, c_id, c_perc):
        # Slight delay to avoid D-Bus conflict in Linux
        time.sleep(0.5)
        print(f"[+] Creating an icon for the Control {c_id}")

        try:
            icon = Icon(
                f"ds4_id_{c_id}", # Unique name for each ID
                icon=self.create_image(c_perc, c_id),
                title=f"DualShock 4 - P{c_id}"
            )
            icon.menu = Menu(MenuItem('Sair', self.stop))
            self.icons[c_id] = icon

            # Running in a separate thread is essential to avoid locking the socket.
            t = threading.Thread(target=icon.run, daemon=True)
            t.start()
        except Exception as e:
            print(f"[X] Error instantiating icon {c_id}: {e}")

    def check_timeouts(self):
        now = time.time()
        # Reduced from 75 to 25 seconds for a faster response.
        to_remove = [c_id for c_id, last in self.last_updates.items() if now - last > 5]
        for c_id in to_remove:
            print(f"[-] Controle {c_id} desconectado (timeout)")
            self.icons[c_id].stop()
            del self.icons[c_id]
            del self.last_updates[c_id]

            # Opcional: Remove from dynamic_map to allow another
            # Control will capture ID "1" if it takes too long to return.
            keys_to_del = [k for k, v in self.dynamic_map.items() if v == c_id]
            for k in keys_to_del:
                del self.dynamic_map[k]

    def stop(self):
        self.running = False
        for icon in self.icons.values():
            icon.stop()

    def scan_native_kernel_drivers(self):
        """Scans the system for native DS4/DualSense controls."""
        print("[*] Starting scan using native kernel drivers...")

        # Local socket for sending data to itself.
        internal_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        while self.running:
            try:
                # List everything in power_supply to work around glob issues with symlinks.
                all_supplies = os.listdir("/sys/class/power_supply/")

                # Filter only what contains 'controller-battery' or 'controller_battery'.
                controllers = [d for d in all_supplies if "controller" in d and "battery" in d]

                for dev_name in controllers:
                    # Build the absolute path.
                    path = os.path.join("/sys/class/power_supply/", dev_name)

                    try:
                        # Extracts the ID (end of MAC address)
                        # Ex: ps-controller-battery-77:14:0c:00:3b:8a -> 3b:8a
                        c_id = dev_name.split("-")[-1].split("_")[-1][-5:].replace(":", "")

                        capacity_file = os.path.join(path, "capacity")

                        # Use os.path.realpath to ensure we are reading the link's destination.
                        if os.path.exists(capacity_file):
                            with open(capacity_file, "r") as f:
                                capacity = f.read().strip()

                            # Sends to the UDP listener.
                            msg = f"{c_id}:{capacity}"
                            internal_sock.sendto(msg.encode(), (UDP_IP, UDP_PORT))

                    except Exception as e:
                        print(f"[X] Erro ao processar {dev_name}: {e}")

            except Exception as e:
                print(f"[X] Erro ao listar power_supply: {e}")

            time.sleep(4)

if __name__ == "__main__":
    monitor = MultiBatteryMonitor()

    # THREAD NOVA: Starts kernel scanning in the background.
    t_kernel = threading.Thread(target=monitor.scan_native_kernel_drivers, daemon=True)
    t_kernel.start()

    try:
        # Starts listening on the UDP socket.
        monitor.listen_udp()
    except KeyboardInterrupt:
        # Press Ctrl+C and end gracefully.
        print("\n[!] Interruption detected. Closing monitor....")
        monitor.stop()
    except Exception as e:
        print(f"\n[X] Unexpected error: {e}")
        monitor.stop()
