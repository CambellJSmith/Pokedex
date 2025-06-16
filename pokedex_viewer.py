#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys, os, random, json, requests, collections
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PyQt6.QtCore import Qt, QTimer, QRunnable, QThreadPool, pyqtSignal, QObject
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtWidgets import (
    QApplication, QLabel, QWidget, QVBoxLayout,
    QMenu, QMessageBox, QStackedWidget, QScrollArea, QGridLayout
)

# ───────── CONFIG ─────────
BASE_URL   = "https://pokeapi.co/api/v2"
MAX_ID     = 493
SHINY_RATE = 1 / 8192
UK_TZ      = ZoneInfo("Europe/London")
DEX_COLS   = 5

CACHE_DIR  = Path(os.getenv("XDG_CACHE_HOME", Path.home() / ".cache")) / "pokesprites"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE      = CACHE_DIR / "shiny_seen.json"
ENCOUNTER_LOG     = CACHE_DIR / "encounter_data.json"
# ─────────────────────────


def fetch_json(url: str, cache_path: Path):
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    cache_path.write_text(r.text)
    return r.json()


def get_pokemon():
    pid   = random.randint(1, MAX_ID)
    shiny = random.random() < SHINY_RATE

    data    = fetch_json(f"{BASE_URL}/pokemon/{pid}",           CACHE_DIR / f"pokemon_{pid}.json")
    species = fetch_json(f"{BASE_URL}/pokemon-species/{pid}",  CACHE_DIR / f"species_{pid}.json")

    name  = data["name"].capitalize()
    types = "/".join(t["type"]["name"].capitalize() for t in data["types"])
    dex   = f"#{pid:03d}"

    entries = [e["flavor_text"] for e in species["flavor_text_entries"]
               if e["language"]["name"] == "en"]
    flavor  = random.choice(entries).replace("\n", " ").replace("\f", " ").strip() \
              if entries else "(No flavor text found)"

    sprite_url  = data["sprites"]["front_shiny" if shiny else "front_default"]
    sprite_tag  = f"{pid}_{'shiny' if shiny else 'normal'}.png"
    sprite_path = CACHE_DIR / sprite_tag

    if sprite_url and not sprite_path.exists():
        img = requests.get(sprite_url, timeout=10)
        if img.ok:
            sprite_path.write_bytes(img.content)

    return pid, dex, name, types, flavor, str(sprite_path), shiny


# ───────── Persistence helpers ─────────
def load_shiny_history():
    try:
        return json.loads(HISTORY_FILE.read_text())
    except Exception:
        return []


def save_shiny_history(hist):
    try:
        HISTORY_FILE.write_text(json.dumps(hist, indent=2))
    except Exception:
        pass


def load_encounter_data():
    try:
        raw = json.loads(ENCOUNTER_LOG.read_text())
        names = raw.get("names", [])
        ids   = set(raw.get("ids", []))
        name_map = raw.get("names_by_id", {})
        return names, ids, name_map
    except Exception:
        return [], set(), {}


def save_encounter_data(names, ids, name_map):
    try:
        ENCOUNTER_LOG.write_text(json.dumps({
            "names": names,
            "ids": list(ids),
            "names_by_id": name_map
        }, indent=2))
    except Exception:
        pass


# ───────── Thread worker ─────────
class WorkerSignals(QObject):
    result = pyqtSignal(int, str, str, str, str, str, bool)
    error  = pyqtSignal(str)


class PokemonWorker(QRunnable):
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(*get_pokemon())
        except Exception as e:
            self.signals.error.emit(str(e))


# ───────── Utility ─────────
def grey_placeholder(size=48):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.lightGray)
    return pm


# ───────── Main widget ─────────
class PokedexViewer(QWidget):
    def __init__(self):
        super().__init__()

        self.setFixedSize(420, 620)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        font_bold = QFont("Monospace", 11, QFont.Weight.Bold)
        font_norm = QFont("SansSerif", 9)

        # Persistent data
        self.shiny_history            = load_shiny_history()
        (self.encounters,
         self.encounter_ids,
         self.id_to_name)             = load_encounter_data()

        self.last_shiny_time = None

        # ───── Encounter viewer page ─────
        viewer_page = QWidget()
        vlay = QVBoxLayout(viewer_page)
        vlay.setContentsMargins(10, 10, 10, 10)
        vlay.setSpacing(4)

        vlay.addStretch(0)  # spacer

        self.img_lbl   = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.dex_lbl   = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, font=font_bold)
        self.type_lbl  = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, font=font_norm)
        self.shiny_lbl = QLabel("✨ SHINY ✨", alignment=Qt.AlignmentFlag.AlignCenter, font=font_bold)
        self.shiny_lbl.setStyleSheet("color: gold;")

        self.flavor_lbl = QLabel(wordWrap=True, alignment=Qt.AlignmentFlag.AlignCenter, font=font_norm)

        self.since_lbl  = QLabel("", alignment=Qt.AlignmentFlag.AlignCenter, font=font_norm)
        self.stats_lbl  = QLabel("", alignment=Qt.AlignmentFlag.AlignCenter, font=font_norm)

        vlay.addWidget(self.img_lbl)
        vlay.addWidget(self.shiny_lbl)
        vlay.addWidget(self.dex_lbl)
        vlay.addWidget(self.type_lbl)
        vlay.addWidget(self.flavor_lbl)
        vlay.addWidget(self.since_lbl)
        vlay.addWidget(self.stats_lbl)

        # ───── Dex grid page ─────
        dex_page  = QWidget()
        dex_outer = QVBoxLayout(dex_page)
        dex_outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        dex_outer.addWidget(scroll)

        grid_container = QWidget()
        self.dex_grid  = QGridLayout(grid_container)
        self.dex_grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.dex_grid.setSpacing(6)
        self.dex_grid.setContentsMargins(0, 0, 0, 0)

        # Ensure grid width fits inside the window
        cell_w = 60
        cols = DEX_COLS
        spacing = self.dex_grid.spacing()
        grid_container.setFixedWidth(cell_w * cols + spacing * (cols - 1))

        scroll.setWidget(grid_container)

        self.dex_cells = {}  # pid -> (pix_lbl, name_lbl, box_widget)
        self._build_dex_grid(font_norm)

        # ───── Stacked layout ─────
        self.stack = QStackedWidget()
        self.stack.addWidget(viewer_page)  # index 0
        self.stack.addWidget(dex_page)     # index 1

        main = QVBoxLayout(self)
        main.addWidget(self.stack)

        # Timers
        self.card_timer  = QTimer(self, interval=60000)
        self.card_timer.timeout.connect(self.fetch_card)
        self.card_timer.start()

        self.since_timer = QTimer(self, interval=30000)
        self.since_timer.timeout.connect(self.update_shiny_delay)
        self.since_timer.start()

        # Initialise tooltips from saved shiny history
        for entry in self.shiny_history:
            pid = int(entry["dex"][1:])  # strip '#'
            self._append_shiny_tooltip(pid, entry)

        # Kick-off
        self.update_stats()
        self.update_shiny_delay()
        self.fetch_card()

    # ───────── Build Dex Grid ─────────
    def _build_dex_grid(self, font):
        cols = DEX_COLS
        placeholder = grey_placeholder()

        for pid in range(1, MAX_ID + 1):
            r, c = divmod(pid - 1, cols)

            pix_lbl = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
            pix_lbl.setPixmap(placeholder)
            name_lbl = QLabel(
                "***",
                alignment=Qt.AlignmentFlag.AlignCenter,
                font=font,
            )

            box = QWidget()
            v = QVBoxLayout(box)
            v.setSpacing(0)
            v.setContentsMargins(0, 0, 0, 0)
            v.addWidget(pix_lbl)
            v.addWidget(name_lbl)

            box.setFixedSize(60, 72)
            box.setStyleSheet("border: 1px solid lightgray; border-radius: 2px;")

            # Blank tooltip; will fill when shinies logged
            box.setToolTip("No shiny encountered yet.")

            self.dex_grid.addWidget(box, r, c)
            self.dex_cells[pid] = (pix_lbl, name_lbl, box)

            # If already encountered (from saved state) update sprite/name
            if pid in self.encounter_ids:
                spath = CACHE_DIR / f"{pid}_normal.png"
                if spath.exists():
                    pix_lbl.setPixmap(QPixmap(str(spath)).scaled(
                        48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation))
                if pid in self.id_to_name:
                    name_lbl.setText(self.id_to_name[pid])

    # ───────── Fetch & Apply Encounter ─────────
    def fetch_card(self):
        worker = PokemonWorker()
        worker.signals.result.connect(self.apply_card)
        worker.signals.error.connect(self.show_error)
        QThreadPool.globalInstance().start(worker)

    def apply_card(self, pid, dex, name, types, flavor, sprite_path, shiny):
        # Log encounter
        self.encounters.append(name)
        self.encounter_ids.add(pid)
        self.id_to_name[pid] = name
        save_encounter_data(self.encounters, self.encounter_ids, self.id_to_name)

        # Viewer visuals
        pix = QPixmap(sprite_path)
        if not pix.isNull():
            self.img_lbl.setPixmap(pix.scaledToWidth(140, Qt.TransformationMode.SmoothTransformation))
        else:
            self.img_lbl.setText("(no sprite)")

        self.dex_lbl.setText(f"{dex} – {name}")
        self.type_lbl.setText(f"Type: {types}")
        self.flavor_lbl.setText(flavor)

        if shiny:
            self.setWindowTitle("Pokédex Viewer ✨ Shiny!")
            self.dex_lbl.setStyleSheet("color: gold;")
            self.shiny_lbl.show()

            now = datetime.now(UK_TZ)
            entry = {
                "dex":  dex,
                "name": name,
                "time": now.strftime("%I:%M %p").lstrip("0"),
                "date": now.strftime("%d/%m/%Y")
            }
            self.shiny_history.append(entry)
            save_shiny_history(self.shiny_history)
            self.last_shiny_time = now
            self.update_shiny_delay()

            # add to tooltip
            self._append_shiny_tooltip(pid, entry)
        else:
            self.setWindowTitle("Pokédex Viewer")
            self.dex_lbl.setStyleSheet("")
            self.shiny_lbl.hide()

        # Update Dex cell visuals
        self.update_dex_cell(pid, sprite_path, name)
        self.update_stats()

    # ───────── Dex helpers ─────────
    def update_dex_cell(self, pid, sprite_path, name):
        pix_lbl, name_lbl, _ = self.dex_cells[pid]
        pix_lbl.setPixmap(QPixmap(sprite_path).scaled(
            48, 48, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation))
        name_lbl.setText(name)

    def _append_shiny_tooltip(self, pid, entry):
        _, _, box = self.dex_cells.get(pid, (None, None, None))
        if not box:
            return
        existing = box.toolTip().splitlines() if box.toolTip() else []
        if existing and existing[0].startswith("No shiny"):
            existing = []
        new_line = f"{entry['time']} – {entry['date']}"
        existing.append(new_line)
        box.setToolTip("\n".join(existing))

    # ───────── Stats & Timers ─────────
    def update_shiny_delay(self):
        if not self.last_shiny_time:
            self.since_lbl.setText("⏱️ No shiny encountered yet.")
            return
        mins = int((datetime.now(UK_TZ) - self.last_shiny_time).total_seconds() // 60)
        self.since_lbl.setText(
            "✨ Just now!" if mins == 0 else
            "⏱️ 1 minute since last shiny!" if mins == 1 else
            f"⏱️ {mins} minutes since last shiny!"
        )

    def update_stats(self):
        total = len(self.encounters)
        counts = collections.Counter(self.encounters)
        most   = counts.most_common(1)
        shiny_counts = collections.Counter(e["name"] for e in self.shiny_history)
        shiny_top    = shiny_counts.most_common(1)

        parts = [f"🎯 Encounters: {total}"]
        if most and most[0][1] > 1:
            parts.append(f" 🔁 Most encountered: {most[0][0]} ({most[0][1]})")
        if shiny_top:
            parts.append(f" ✨ Top shiny: {shiny_top[0][0]} ({shiny_top[0][1]})")
        self.stats_lbl.setText("  |".join(parts))

    # ───────── Error ─────────
    def show_error(self, msg): self.flavor_lbl.setText(f"Error: {msg}")

    # ───────── Dex Toggle & Context Menu ─────────
    def toggle_dex_view(self):
        self.stack.setCurrentIndex(1 - self.stack.currentIndex())

    def contextMenuEvent(self, e):
        m = QMenu(self)
        act_toggle = m.addAction("Toggle Dex View")
        act_flee   = m.addAction("Flee")
        chosen = m.exec(e.globalPos())
        if chosen == act_toggle:
            self.toggle_dex_view()
        elif chosen == act_flee:
            if QMessageBox.question(
                self, "Confirm Exit",
                "Are you sure you want to flee?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            ) == QMessageBox.StandardButton.Yes:
                self.close()


# ───────── Main ─────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = PokedexViewer()
    viewer.show()
    sys.exit(app.exec())