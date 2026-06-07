"""
youtube_downloader.py – Desktopowa aplikacja GUI do pobierania filmów z YouTube.

Zależności (zainstaluj komendy pip na dole tego pliku lub w README):
    customtkinter, yt-dlp, Pillow, CTkMessagebox

Opcjonalnie (wymagane do konwersji MP3 i scalania strumieni HD):
    FFmpeg – https://ffmpeg.org/download.html  (dodaj do PATH!)

Autor:  wygenerowano z Claude
Python: 3.10+
"""

from __future__ import annotations

# ── Biblioteki standardowe ─────────────────────────────────────────────────
import io
import os
import shutil
import sys
import threading
import urllib.request
from datetime import timedelta
from typing import Optional

# ── Biblioteki zewnętrzne ──────────────────────────────────────────────────
import customtkinter as ctk
from customtkinter import CTkImage
from PIL import Image
from tkinter import filedialog
import yt_dlp

# ── Obsługa CTkMessagebox z awaryjnym fallback ─────────────────────────────
# CTkMessagebox pasuje wizualnie do ciemnego motywu; jeśli nie jest
# zainstalowany, używamy standardowego okna tkinter.
try:
    from CTkMessagebox import CTkMessagebox

    def show_error(parent, title: str, message: str) -> None:
        CTkMessagebox(master=parent, title=title, message=message, icon="cancel")

    def show_info(parent, title: str, message: str) -> None:
        CTkMessagebox(master=parent, title=title, message=message, icon="check")

except ImportError:
    import tkinter.messagebox as _msgbox

    def show_error(parent, title: str, message: str) -> None:  # type: ignore[misc]
        _msgbox.showerror(title, message)

    def show_info(parent, title: str, message: str) -> None:  # type: ignore[misc]
        _msgbox.showinfo(title, message)


# ── Globalne ustawienia wyglądu ────────────────────────────────────────────
ctk.set_appearance_mode("dark")       # Ciemny motyw (alternatywy: "light", "system")
ctk.set_default_color_theme("blue")   # Niebieski kolor akcentów


# ═══════════════════════════════════════════════════════════════════════════
class YouTubeDownloaderApp(ctk.CTk):
    """
    Główna klasa aplikacji YouTube Downloader.

    Dziedziczy po ctk.CTk (co sprawia, że sama pełni rolę okna głównego).
    Metody zorganizowane warstwowo:

      _build_*     → konstruowanie widżetów GUI
      _start_*     → inicjacja operacji, wywoływana przez przyciski
      _*_worker    → logika w tle (w osobnym wątku, bez dostępu do GUI!)
      _on_*        → callbacki zwrotne, zawsze wywoływane w wątku GUI
      _fmt_*       → pomocnicze formatowanie ciągów znaków
    """

    # ── Stałe klasy ───────────────────────────────────────────────────────
    THUMB_W: int = 192            # Szerokość miniatury w pikselach
    THUMB_H: int = 108            # Wysokość miniatury (proporcja 16 : 9)
    FORMAT_VIDEO = "Najwyższa jakość wideo (MP4)"
    FORMAT_AUDIO = "Tylko audio (MP3)"

    # ══════════════════════════════════════════════════════════════════════
    # INICJALIZACJA
    # ══════════════════════════════════════════════════════════════════════

    def __init__(self) -> None:
        super().__init__()

        # ── Konfiguracja okna ─────────────────────────────────────────────
        self.title("YouTube Downloader")
        self.geometry("740x710")
        self.minsize(660, 630)

        # ── Zmienne stanu aplikacji ───────────────────────────────────────

        # Domyślna ścieżka zapisu: folder „Downloads" lub „Pobrane"
        _dl_default = os.path.join(os.path.expanduser("~"), "Downloads")
        self.download_path_var = ctk.StringVar(value=_dl_default)

        # Wybrany format pobierania
        self.format_var = ctk.StringVar(value=self.FORMAT_VIDEO)

        # Flaga blokująca wielokrotne uruchomienie pobierania
        self.is_downloading: bool = False

        # !! WAŻNE: Tkinter usuwa obraz z pamięci (garbage collector), gdy
        # zmienna traci ostatnią referencję. Przechowuj obrazy jako atrybuty!
        self._thumbnail_ref: Optional[CTkImage] = None
        self._placeholder_img: Optional[CTkImage] = None

        # ── Zbuduj interfejs ──────────────────────────────────────────────
        self._build_ui()

    # ══════════════════════════════════════════════════════════════════════
    # SEKCJA I – BUDOWANIE INTERFEJSU UŻYTKOWNIKA
    # ══════════════════════════════════════════════════════════════════════

    def _build_ui(self) -> None:
        """Kompozytor całego układu – układa panele w kolumnie."""
        self.grid_columnconfigure(0, weight=1)
        P = dict(padx=16, pady=8)   # Domyślny padding dla każdego panelu

        self._build_header(row=0)
        self._build_url_panel(row=1, **P)
        self._build_info_panel(row=2, **P)
        self._build_format_panel(row=3, **P)
        self._build_path_panel(row=4, **P)
        self._build_progress_panel(row=5, **P)
        self.grid_rowconfigure(6, weight=1)   # Elastyczny odstęp (push-down)
        self._build_download_button(row=7)

    # ── Nagłówek ──────────────────────────────────────────────────────────

    def _build_header(self, row: int) -> None:
        """Tytuł aplikacji i podtytuł."""
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.grid(row=row, column=0, sticky="ew", pady=(16, 0))

        ctk.CTkLabel(
            frame,
            text="▶  YouTube Downloader",
            font=ctk.CTkFont(size=24, weight="bold"),
            text_color="#FF4444",
        ).pack()

        ctk.CTkLabel(
            frame,
            text="Pobieraj filmy i muzykę szybko i wygodnie",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        ).pack(pady=(2, 12))

    # ── Panel URL ─────────────────────────────────────────────────────────

    def _build_url_panel(self, row: int, **pad) -> None:
        """Pole tekstowe na URL + przycisk 'Analizuj link'."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(0, weight=1)  # Entry rozciąga się

        ctk.CTkLabel(
            frame,
            text="  Link do filmu YouTube:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(10, 2))

        self.url_entry = ctk.CTkEntry(
            frame,
            placeholder_text="https://www.youtube.com/watch?v=...",
            height=40,
            font=ctk.CTkFont(size=13),
        )
        self.url_entry.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(0, 12))

        # Skrót klawiszowy: Enter w polu URL = analizuj
        self.url_entry.bind("<Return>", lambda _: self._start_analyze())

        self.analyze_btn = ctk.CTkButton(
            frame,
            text="🔍 Analizuj",
            width=115,
            height=40,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._start_analyze,
        )
        self.analyze_btn.grid(row=1, column=1, padx=(0, 12), pady=(0, 12))

    # ── Panel informacji o filmie ──────────────────────────────────────────

    def _build_info_panel(self, row: int, **pad) -> None:
        """Miniaturka po lewej, tytuł i długość po prawej."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(1, weight=1)

        # Szary placeholder (pokazywany zanim film zostanie przeanalizowany)
        _placeholder_pil = Image.new("RGB", (self.THUMB_W, self.THUMB_H), (50, 50, 50))
        self._placeholder_img = CTkImage(_placeholder_pil, size=(self.THUMB_W, self.THUMB_H))

        self.thumbnail_label = ctk.CTkLabel(
            frame,
            image=self._placeholder_img,
            text="",
        )
        self.thumbnail_label.grid(
            row=0, column=0, rowspan=4, padx=14, pady=14, sticky="nw"
        )

        # Wiersz 0: etykieta "Tytuł:"
        ctk.CTkLabel(
            frame,
            text="Tytuł:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(16, 0))

        # Wiersz 1: wartość tytułu (z zawijaniem tekstu)
        self.video_title_label = ctk.CTkLabel(
            frame,
            text="—",
            wraplength=420,
            justify="left",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self.video_title_label.grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(2, 8)
        )

        # Wiersz 2: etykieta "Długość:"
        ctk.CTkLabel(
            frame,
            text="Długość:",
            font=ctk.CTkFont(size=12, weight="bold"),
            anchor="w",
        ).grid(row=2, column=1, sticky="w", padx=(0, 12))

        # Wiersz 3: wartość długości
        self.video_duration_label = ctk.CTkLabel(
            frame,
            text="—",
            anchor="w",
            font=ctk.CTkFont(size=13),
        )
        self.video_duration_label.grid(
            row=3, column=1, sticky="w", padx=(0, 12), pady=(2, 16)
        )

    # ── Panel wyboru formatu ───────────────────────────────────────────────

    def _build_format_panel(self, row: int, **pad) -> None:
        """Rozwijane menu wyboru formatu pobierania."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text="  Format pobierania:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=(12, 8), pady=14, sticky="w")

        self.format_menu = ctk.CTkOptionMenu(
            frame,
            variable=self.format_var,
            values=[self.FORMAT_VIDEO, self.FORMAT_AUDIO],
            width=330,
            height=36,
            font=ctk.CTkFont(size=13),
        )
        self.format_menu.grid(row=0, column=1, padx=(0, 12), pady=14, sticky="w")

    # ── Panel ścieżki zapisu ───────────────────────────────────────────────

    def _build_path_panel(self, row: int, **pad) -> None:
        """Pole ze ścieżką + przycisk 'Przeglądaj'."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            frame,
            text="  Folder zapisu:",
            font=ctk.CTkFont(weight="bold"),
        ).grid(row=0, column=0, padx=(12, 8), pady=12, sticky="w")

        ctk.CTkEntry(
            frame,
            textvariable=self.download_path_var,
            height=36,
            font=ctk.CTkFont(size=12),
        ).grid(row=0, column=1, sticky="ew", padx=6, pady=12)

        ctk.CTkButton(
            frame,
            text="📁 Przeglądaj",
            width=125,
            height=36,
            font=ctk.CTkFont(size=12),
            command=self._browse_folder,
        ).grid(row=0, column=2, padx=(0, 12), pady=12)

    # ── Panel postępu ──────────────────────────────────────────────────────

    def _build_progress_panel(self, row: int, **pad) -> None:
        """Pasek postępu i etykieta z procentem / prędkością / ETA."""
        frame = ctk.CTkFrame(self)
        frame.grid(row=row, column=0, sticky="ew", **pad)
        frame.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(frame, height=20, corner_radius=8)
        self.progress_bar.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 6))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            frame,
            text="Gotowy do pobierania",
            font=ctk.CTkFont(size=12),
            text_color="gray",
        )
        self.status_label.grid(row=1, column=0, pady=(0, 12))

    # ── Przycisk POBIERZ ───────────────────────────────────────────────────

    def _build_download_button(self, row: int) -> None:
        """Duży, wyraźny przycisk inicjujący pobieranie."""
        self.download_btn = ctk.CTkButton(
            self,
            text="⬇   POBIERZ",
            height=52,
            font=ctk.CTkFont(size=17, weight="bold"),
            fg_color="#CC0000",
            hover_color="#990000",
            command=self._start_download,
        )
        self.download_btn.grid(row=row, column=0, padx=16, pady=(4, 20), sticky="ew")

    # ══════════════════════════════════════════════════════════════════════
    # SEKCJA II – ANALIZA LINKU
    # ══════════════════════════════════════════════════════════════════════

    def _start_analyze(self) -> None:
        """
        Wywoływana przez przycisk 'Analizuj' lub Enter w polu URL.
        Blokuje przycisk i uruchamia wątek analizujący.
        """
        url = self.url_entry.get().strip()
        if not url:
            show_error(self, "Brak linku", "Wklej link do filmu YouTube w pole powyżej.")
            return

        # Zablokuj przycisk, żeby nie uruchomić kilku analizowań naraz
        self.analyze_btn.configure(state="disabled", text="⏳ Analizuję...")
        self._set_status("Pobieranie informacji o filmie...", "gray")

        # Uruchom w tle (daemon=True → wątek ginie razem z oknem aplikacji)
        thread = threading.Thread(
            target=self._analyze_worker, args=(url,), daemon=True
        )
        thread.start()

    def _analyze_worker(self, url: str) -> None:
        """
        Wątek roboczy: pobiera metadane z YouTube bez ściągania pliku.
        Wyniki przekazuje do wątku GUI metodą self.after(0, callback, ...).

        WAŻNE: Ta metoda działa poza wątkiem GUI – nigdy nie modyfikuj
        tu widżetów bezpośrednio! Używaj wyłącznie self.after().
        """
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,    # Tylko metadane – żaden plik nie jest pobierany
            "noplaylist": True,       # Nie rozwijaj playlist
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            # Wyciągnij interesujące nas dane
            title = info.get("title", "Brak tytułu")
            duration_sec = int(info.get("duration", 0) or 0)
            duration_str = str(timedelta(seconds=duration_sec))
            thumb_url = info.get("thumbnail", "")

            # Pobierz miniaturkę jako obiekt PIL.Image
            thumbnail = self._fetch_thumbnail(thumb_url)

            # Przekaż wyniki do wątku GUI
            self.after(0, self._on_analyze_done, title, duration_str, thumbnail)

        except yt_dlp.utils.DownloadError as exc:
            self.after(0, self._on_analyze_error, str(exc))
        except Exception as exc:
            self.after(0, self._on_analyze_error, f"Nieoczekiwany błąd: {exc}")

    def _fetch_thumbnail(self, url: str) -> Optional[Image.Image]:
        """
        Pobiera miniaturkę z podanego URL-a.
        Zwraca obiekt PIL.Image lub None (przy błędzie / pustym URL).
        """
        if not url:
            return None
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                raw = resp.read()
            img = Image.open(io.BytesIO(raw)).convert("RGB")
            return img
        except Exception:
            return None  # Cicha obsługa błędu – wyświetlimy placeholder

    def _on_analyze_done(
        self,
        title: str,
        duration: str,
        thumbnail: Optional[Image.Image],
    ) -> None:
        """
        Callback wywoływany w wątku GUI po pomyślnej analizie.
        Aktualizuje miniaturkę, tytuł i długość.
        """
        self.video_title_label.configure(text=title)
        self.video_duration_label.configure(text=duration)

        if thumbnail is not None:
            # Tworzymy CTkImage i zapisujemy referencję – bez tego
            # garbage collector usunąłby obraz i wyświetlałby się placeholder!
            ctk_img = CTkImage(thumbnail, size=(self.THUMB_W, self.THUMB_H))
            self._thumbnail_ref = ctk_img
            self.thumbnail_label.configure(image=ctk_img)
        else:
            # Analiza się udała, ale miniatury nie udało się pobrać
            self.thumbnail_label.configure(image=self._placeholder_img)

        self.analyze_btn.configure(state="normal", text="🔍 Analizuj")
        self._set_status("✅ Analiza zakończona. Możesz rozpocząć pobieranie.", "#4CAF50")

    def _on_analyze_error(self, message: str) -> None:
        """Callback wywoływany w wątku GUI przy błędzie analizy."""
        self.analyze_btn.configure(state="normal", text="🔍 Analizuj")
        self._set_status("❌ Nie można przeanalizować linku.", "#F44336")
        show_error(
            self,
            "Błąd analizy",
            "Nie można pobrać informacji o filmie.\n\n"
            "Sprawdź:\n"
            "  • czy link jest poprawny (film publiczny?)\n"
            "  • czy masz połączenie z internetem\n\n"
            f"Szczegóły techniczne:\n{message}",
        )

    # ══════════════════════════════════════════════════════════════════════
    # SEKCJA III – WYBÓR FOLDERU DOCELOWEGO
    # ══════════════════════════════════════════════════════════════════════

    def _browse_folder(self) -> None:
        """
        Otwiera natywne okno dialogowe systemu operacyjnego
        do wyboru folderu docelowego.
        """
        folder = filedialog.askdirectory(
            title="Wybierz folder do zapisania pliku",
            initialdir=self.download_path_var.get(),
        )
        if folder:   # Jeśli użytkownik wybrał folder (nie nacisnął Anuluj)
            self.download_path_var.set(folder)

    # ══════════════════════════════════════════════════════════════════════
    # SEKCJA IV – POBIERANIE PLIKU
    # ══════════════════════════════════════════════════════════════════════

    def _start_download(self) -> None:
        """
        Wywoływana przez przycisk 'POBIERZ'.
        Waliduje dane wejściowe i uruchamia wątek pobierania.
        """
        # Blokada: nie uruchamiaj drugiego pobierania, jeśli trwa pierwsze
        if self.is_downloading:
            show_info(
                self, "W toku", "Pobieranie już trwa. Poczekaj na jego zakończenie."
            )
            return

        url = self.url_entry.get().strip()
        save_path = self.download_path_var.get().strip()

        # Walidacja URL
        if not url:
            show_error(self, "Brak linku", "Wklej link do filmu YouTube.")
            return

        # Walidacja ścieżki
        if not save_path or not os.path.isdir(save_path):
            show_error(
                self,
                "Nieprawidłowa ścieżka",
                f"Podany folder nie istnieje:\n{save_path}\n\n"
                "Wybierz poprawny folder przyciskiem 'Przeglądaj'.",
            )
            return

        # Zablokuj UI na czas pobierania
        self.is_downloading = True
        self.download_btn.configure(state="disabled", text="⏳ Pobieranie...")
        self.analyze_btn.configure(state="disabled")
        self.format_menu.configure(state="disabled")
        self.progress_bar.set(0)
        self._set_status("Przygotowywanie pobierania...", "gray")

        # Uruchom wątek roboczy
        thread = threading.Thread(
            target=self._download_worker,
            args=(url, save_path, self.format_var.get()),
            daemon=True,
        )
        thread.start()

    @staticmethod
    def _find_ffmpeg() -> Optional[str]:
        """
        Wykrywa lokalizację FFmpeg w kolejności priorytetów:
          1. Katalog _MEIPASS (PyInstaller .exe – dołączony FFmpeg)
          2. Katalog obok pliku .py (development – ffmpeg.exe obok skryptu)
          3. Systemowy PATH (FFmpeg zainstalowany globalnie)

        Zwraca:
          str   – ścieżka do katalogu zawierającego ffmpeg.exe
          ""    – FFmpeg dostępny przez PATH (yt-dlp sam go znajdzie)
          None  – FFmpeg całkowicie niedostępny
        """
        # Gdy aplikacja spakowana przez PyInstaller, pliki lądują w _MEIPASS
        if getattr(sys, "frozen", False):
            base = sys._MEIPASS  # type: ignore[attr-defined]
        else:
            base = os.path.dirname(os.path.abspath(__file__))

        # Przypadek 1 i 2: ffmpeg.exe obok pliku aplikacji
        if os.path.isfile(os.path.join(base, "ffmpeg.exe")):
            return base

        # Przypadek 3: FFmpeg dostępny globalnie w PATH
        if shutil.which("ffmpeg"):
            return ""  # Pusty string = yt-dlp znajdzie FFmpeg sam

        return None  # FFmpeg całkowicie niedostępny

    def _build_ydl_opts(self, save_path: str, fmt: str) -> dict:
        """
        Buduje i zwraca słownik opcji dla yt-dlp.
        Automatycznie wykrywa FFmpeg i dobiera odpowiedni format:
          • FFmpeg dostępny    → najlepsza jakość (scalanie strumieni)
          • FFmpeg niedostępny → jeden pre-scalony plik (do ~720p), MP3 niemożliwy

        %(title)s.%(ext)s  →  yt-dlp automatycznie nadaje plikom tytuły filmów.
        progress_hooks     →  lista callbacków wywoływanych co pobrany chunk.
        """
        output_template = os.path.join(save_path, "%(title)s.%(ext)s")
        ffmpeg_dir = self._find_ffmpeg()
        has_ffmpeg = ffmpeg_dir is not None

        common_opts = {
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "progress_hooks": [self._progress_hook],
        }

        # Jeśli FFmpeg znaleziony w konkretnym katalogu – wskaż yt-dlp dokładną ścieżkę
        if ffmpeg_dir:  # niepusty string = znaleziony lokalnie lub w _MEIPASS
            common_opts["ffmpeg_location"] = ffmpeg_dir

        if fmt == self.FORMAT_AUDIO:
            if not has_ffmpeg:
                # Bez FFmpeg konwersja do MP3 jest niemożliwa – rzuć błąd
                raise RuntimeError(
                    "Konwersja do MP3 wymaga FFmpeg, który nie został znaleziony.\n\n"
                    "Rozwiązanie:\n"
                    "  • Pobierz ffmpeg.exe ze strony ffmpeg.org\n"
                    "  • Umieść go w tym samym folderze co aplikacja\n"
                    "  • Lub zainstaluj globalnie: winget install Gyan.FFmpeg"
                )
            # FFmpeg dostępny → konwertuj do MP3 192 kbps
            return {
                **common_opts,
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
        else:
            if has_ffmpeg:
                # FFmpeg dostępny → pobierz osobne strumienie i scal do MP4 (max jakość)
                return {
                    **common_opts,
                    "format": (
                        "bestvideo[ext=mp4]+bestaudio[ext=m4a]"
                        "/bestvideo+bestaudio/best"
                    ),
                    "merge_output_format": "mp4",
                }
            else:
                # Bez FFmpeg → pobierz jeden pre-scalony plik (zazwyczaj 720p)
                self.after(
                    0,
                    self._set_status,
                    "⚠️ FFmpeg niedostępny – pobieranie w jakości do 720p...",
                    "#FFA500",
                )
                return {
                    **common_opts,
                    "format": "best[ext=mp4]/best",
                }

    def _download_worker(self, url: str, save_path: str, fmt: str) -> None:
        """
        Wątek roboczy: wykonuje właściwe pobieranie pliku przez yt-dlp.

        WAŻNE: Nie modyfikuj widżetów GUI bezpośrednio stąd!
        Używaj self.after(0, callback) do komunikacji z wątkiem GUI.
        """
        try:
            # _build_ydl_opts może rzucić RuntimeError (np. brak FFmpeg dla MP3)
            # dlatego musi być wewnątrz bloku try
            opts = self._build_ydl_opts(save_path, fmt)
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            # Sukces – przekaż wynik do GUI
            self.after(0, self._on_download_done)
        except yt_dlp.utils.DownloadError as exc:
            self.after(0, self._on_download_error, str(exc))
        except Exception as exc:
            self.after(0, self._on_download_error, str(exc))

    def _progress_hook(self, d: dict) -> None:
        """
        Hook wywoływany przez yt-dlp przy każdym porcji pobranych danych.
        Działa w wątku pobierania – używa self.after() do aktualizacji GUI!

        Słownik 'd' zawiera (status 'downloading'):
          downloaded_bytes      – już pobrane bajty
          total_bytes           – całkowity rozmiar (lub None)
          total_bytes_estimate  – szacowany całkowity rozmiar (zapas)
          speed                 – prędkość w B/s (lub None)
          eta                   – szacowany czas pozostały w sekundach (lub None)

        Status 'finished' oznacza, że plik jest pobrany, ale FFmpeg może
        jeszcze scalać strumienie (video + audio) lub konwertować format.
        """
        status = d.get("status")

        if status == "downloading":
            downloaded = d.get("downloaded_bytes", 0) or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            speed = d.get("speed") or 0
            eta = d.get("eta") or 0

            # Oblicz procent (jako wartość 0.0 – 1.0 dla CTkProgressBar)
            percent = (downloaded / total) if total > 0 else 0

            status_text = (
                f"Pobieranie: {percent * 100:.1f}%"
                f"  │  Prędkość: {self._fmt_speed(speed)}"
                f"  │  Pozostało: {self._fmt_eta(eta)}"
            )

            # after(0, ...) umieszcza wywołanie w kolejce pętli zdarzeń GUI –
            # jedyna bezpieczna wątkowo metoda aktualizacji widżetów.
            self.after(0, self._update_progress, percent, status_text)

        elif status == "finished":
            # Plik pobrany; FFmpeg może jeszcze przez chwilę pracować
            self.after(
                0,
                self._update_progress,
                1.0,
                "Przetwarzanie pliku (scalanie / konwersja)...",
            )

    def _update_progress(self, value: float, text: str) -> None:
        """
        Aktualizuje pasek postępu i etykietę statusu.
        Musi być wywoływana WYŁĄCZNIE z wątku GUI (przez self.after)!
        """
        self.progress_bar.set(value)
        self.status_label.configure(text=text, text_color="white")

    def _on_download_done(self) -> None:
        """Callback w wątku GUI: pobieranie zakończone pomyślnie."""
        self.is_downloading = False
        self.progress_bar.set(1.0)
        self._set_status("✅ Pobieranie zakończone pomyślnie!", "#4CAF50")

        # Przywróć stan przycisków
        self.download_btn.configure(state="normal", text="⬇   POBIERZ")
        self.analyze_btn.configure(state="normal")
        self.format_menu.configure(state="normal")

        show_info(self, "Gotowe!", "Plik został pomyślnie zapisany w wybranym folderze.")

    def _on_download_error(self, message: str) -> None:
        """Callback w wątku GUI: błąd podczas pobierania."""
        self.is_downloading = False
        self._set_status("❌ Pobieranie nie powiodło się.", "#F44336")

        # Przywróć stan przycisków
        self.download_btn.configure(state="normal", text="⬇   POBIERZ")
        self.analyze_btn.configure(state="normal")
        self.format_menu.configure(state="normal")

        show_error(
            self,
            "Błąd pobierania",
            "Nie udało się pobrać pliku.\n\n"
            "Możliwe przyczyny:\n"
            "  • nieprawidłowy lub prywatny link\n"
            "  • brak połączenia z internetem\n"
            "  • FFmpeg niedostępny (wymagany dla MP3 i scalania HD)\n\n"
            f"Szczegóły techniczne:\n{message}",
        )

    # ══════════════════════════════════════════════════════════════════════
    # SEKCJA V – METODY POMOCNICZE
    # ══════════════════════════════════════════════════════════════════════

    def _set_status(self, text: str, color: str = "gray") -> None:
        """Wygodna metoda aktualizacji etykiety statusu."""
        self.status_label.configure(text=text, text_color=color)

    @staticmethod
    def _fmt_speed(bps: float) -> str:
        """
        Formatuje prędkość pobierania na czytelną postać.
        Przykład: 5242880 B/s  →  '5.00 MB/s'
        """
        if bps <= 0:
            return "—"
        if bps < 1_024:
            return f"{bps:.0f} B/s"
        if bps < 1_024 ** 2:
            return f"{bps / 1_024:.1f} KB/s"
        return f"{bps / 1_024 ** 2:.2f} MB/s"

    @staticmethod
    def _fmt_eta(seconds: float) -> str:
        """
        Formatuje szacowany czas oczekiwania na czytelny format.
        Przykład: 125 sekund  →  '02:05'
        """
        if seconds <= 0:
            return "—"
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"


# ═══════════════════════════════════════════════════════════════════════════
# Punkt wejścia aplikacji
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()
