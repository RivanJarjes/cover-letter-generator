from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Dict, List, Optional, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(Path.home() / '.cover_letter_gen.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from tkfontchooser import families

from . import llm


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / ".cover_letter_state.json"
SETTINGS_PATH = PROJECT_ROOT / ".cover_letter_settings.json"
ENV_PATH = PROJECT_ROOT / ".env"
PDF_FILENAME = os.getenv("COVER_LETTER_OUTPUT", "cover_letter.pdf")
MARGIN = 72  # 1 inch
FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")

# Available models
COVER_LETTER_MODELS = ["gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano"]
FILENAME_MODELS = ["gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano"]
# Models that support temperature and top_p
MODELS_WITH_SAMPLING = {"gpt-5.1"}

DEFAULT_SETTINGS = {
    "cover_letter_model": "gpt-5.1",
    "filename_model": "gpt-5.1",
    "max_tokens": 1200,
    "filename_max_tokens": 60,
    "temperature": 0.3,
    "top_p": 0.95,
    "font_name": "Helvetica",
    "font_size": 12,
    "output_path": str(PROJECT_ROOT),
}

# Regex patterns for detecting linkable content
EMAIL_PATTERN = re.compile(r'[\w.+-]+@[\w.-]+\.\w+')
URL_PATTERN = re.compile(r'\b(?<!@)(?:https?://)?(?:www\.)?[\w.-]+\.(?:com|ca|org)\b(?:/[\w./-]*)?')


class FileUploadApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Cover Letter Generator")
        self.geometry("480x260")
        self.resizable(False, False)

        self._ensure_env_file()

        self.selected_files: Dict[str, Optional[str]] = {"resume": None, "sample": None}
        self.display_vars: Dict[str, tk.StringVar] = {
            slot: tk.StringVar(value="No file selected") for slot in self.selected_files
        }
        self._generation_in_progress = False
        self._font_path: Optional[Path] = None
        self._current_job_description: Optional[str] = None
        self._settings: Dict = DEFAULT_SETTINGS.copy()

        self._load_settings()
        self._apply_llm_settings()
        self._build_menu_bar()
        self._build_widgets()
        self._bind_paste_shortcuts()
        self._load_previous_files()

    def _ensure_env_file(self) -> None:
        if not ENV_PATH.exists():
            ENV_PATH.write_text("OPENAI_API_KEY=\n")

    def _build_menu_bar(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Open Resume", command=lambda: self._select_file("resume"))
        file_menu.add_command(label="Open Sample (Optional)", command=lambda: self._select_file("sample"))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=edit_menu)
        edit_menu.add_command(label="Preferences...", command=self._show_preferences_dialog)

        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about_dialog)

    def _show_about_dialog(self) -> None:
        messagebox.showinfo(
            "About Cover Letter Generator",
            "Cover Letter Generator v1.0\n\n"
            "Generate tailored cover letters using AI.\n\n"
            "1. Upload your resume\n"
            "2. Paste a job description (Cmd+V)\n"
            "3. Your cover letter PDF will be generated automatically",
        )

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            for key in DEFAULT_SETTINGS:
                if key in data:
                    self._settings[key] = data[key]
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse settings JSON: {e}")
        except OSError as e:
            logger.error(f"Failed to read settings file: {e}")

    def _save_settings(self) -> None:
        try:
            SETTINGS_PATH.write_text(json.dumps(self._settings, indent=2))
        except OSError as e:
            logger.error(f"Failed to save settings: {e}")

    def _show_preferences_dialog(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Preferences")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        # Create a frame with padding
        main_frame = tk.Frame(dialog, padx=20, pady=20)
        main_frame.pack(fill=tk.BOTH, expand=True)

        row = 0

        # API Key (write-only)
        tk.Label(main_frame, text="API Key:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        api_key_var = tk.StringVar(value="")
        api_key_entry = tk.Entry(main_frame, textvariable=api_key_var, width=40, show="*")
        api_key_entry.grid(row=row, column=1, sticky="w", pady=5)
        tk.Label(main_frame, text="(leave blank to keep current)", fg="gray").grid(row=row, column=2, sticky="w", padx=5)
        row += 1

        # Cover Letter Model
        tk.Label(main_frame, text="Cover Letter Model:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        cover_model_var = tk.StringVar(value=self._settings["cover_letter_model"])
        cover_model_menu = tk.OptionMenu(main_frame, cover_model_var, *COVER_LETTER_MODELS)
        cover_model_menu.config(width=15)
        cover_model_menu.grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Filename Model
        tk.Label(main_frame, text="Filename Model:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        filename_model_var = tk.StringVar(value=self._settings["filename_model"])
        filename_model_menu = tk.OptionMenu(main_frame, filename_model_var, *FILENAME_MODELS)
        filename_model_menu.config(width=15)
        filename_model_menu.grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Max Tokens
        tk.Label(main_frame, text="Max Tokens:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        max_tokens_var = tk.StringVar(value=str(self._settings["max_tokens"]))
        tk.Entry(main_frame, textvariable=max_tokens_var, width=10).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Filename Max Tokens
        tk.Label(main_frame, text="Filename Max Tokens:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        filename_max_tokens_var = tk.StringVar(value=str(self._settings["filename_max_tokens"]))
        tk.Entry(main_frame, textvariable=filename_max_tokens_var, width=10).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Temperature (only for gpt-5.1)
        tk.Label(main_frame, text="Temperature:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        temperature_var = tk.StringVar(value=str(self._settings["temperature"]))
        temp_entry = tk.Entry(main_frame, textvariable=temperature_var, width=10)
        temp_entry.grid(row=row, column=1, sticky="w", pady=5)
        temp_note = tk.Label(main_frame, text="(gpt-5.1 only)", fg="gray")
        temp_note.grid(row=row, column=2, sticky="w", padx=5)
        row += 1

        # Top P (only for gpt-5.1)
        tk.Label(main_frame, text="Top P:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        top_p_var = tk.StringVar(value=str(self._settings["top_p"]))
        top_p_entry = tk.Entry(main_frame, textvariable=top_p_var, width=10)
        top_p_entry.grid(row=row, column=1, sticky="w", pady=5)
        top_p_note = tk.Label(main_frame, text="(gpt-5.1 only)", fg="gray")
        top_p_note.grid(row=row, column=2, sticky="w", padx=5)
        row += 1

        # Font Selection
        tk.Label(main_frame, text="Font:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        font_var = tk.StringVar(value=self._settings["font_name"])
        available_fonts = sorted(set(name.replace("\\ ", " ") for name in families()))
        font_menu = tk.OptionMenu(main_frame, font_var, *available_fonts)
        font_menu.config(width=20)
        font_menu.grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Font Size
        tk.Label(main_frame, text="Font Size:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        font_size_var = tk.StringVar(value=str(self._settings["font_size"]))
        tk.Entry(main_frame, textvariable=font_size_var, width=10).grid(row=row, column=1, sticky="w", pady=5)
        row += 1

        # Output Path
        tk.Label(main_frame, text="Output Path:", anchor="w").grid(row=row, column=0, sticky="w", pady=5)
        output_path_var = tk.StringVar(value=self._settings["output_path"])
        output_entry = tk.Entry(main_frame, textvariable=output_path_var, width=30)
        output_entry.grid(row=row, column=1, sticky="w", pady=5)
        tk.Button(
            main_frame,
            text="Browse...",
            command=lambda: self._browse_output_path(output_path_var),
        ).grid(row=row, column=2, sticky="w", padx=5)
        row += 1

        # Buttons
        button_frame = tk.Frame(main_frame)
        button_frame.grid(row=row, column=0, columnspan=3, pady=(20, 0))

        def save_preferences() -> None:
            # Validate and save
            try:
                max_tokens = int(max_tokens_var.get())
                filename_max_tokens = int(filename_max_tokens_var.get())
                temperature = float(temperature_var.get())
                top_p = float(top_p_var.get())
                font_size = int(font_size_var.get())
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter valid numeric values.")
                return

            # Update API key in .env if provided
            api_key = api_key_var.get().strip()
            if api_key:
                self._update_api_key(api_key)

            # Update settings
            self._settings["cover_letter_model"] = cover_model_var.get()
            self._settings["filename_model"] = filename_model_var.get()
            self._settings["max_tokens"] = max_tokens
            self._settings["filename_max_tokens"] = filename_max_tokens
            self._settings["temperature"] = temperature
            self._settings["top_p"] = top_p
            self._settings["font_name"] = font_var.get()
            self._settings["font_size"] = font_size
            self._settings["output_path"] = output_path_var.get()

            # Reset font path cache when font changes
            self._font_path = None

            self._save_settings()

            # Update llm module with new settings
            self._apply_llm_settings()

            dialog.destroy()
            logger.info("Settings saved successfully")
            messagebox.showinfo("Preferences", "Settings saved successfully.")

        tk.Button(button_frame, text="Cancel", command=dialog.destroy, width=10).pack(side=tk.LEFT, padx=10)
        tk.Button(button_frame, text="Save", command=save_preferences, width=10).pack(side=tk.LEFT, padx=10)

    def _browse_output_path(self, path_var: tk.StringVar) -> None:
        directory = filedialog.askdirectory(initialdir=path_var.get())
        if directory:
            path_var.set(directory)

    def _update_api_key(self, api_key: str) -> None:
        try:
            env_content = ""
            if ENV_PATH.exists():
                env_content = ENV_PATH.read_text()

            # Check if OPENAI_API_KEY exists in the file
            lines = env_content.splitlines()
            key_found = False
            new_lines = []
            for line in lines:
                if line.startswith("OPENAI_API_KEY="):
                    new_lines.append(f"OPENAI_API_KEY={api_key}")
                    key_found = True
                else:
                    new_lines.append(line)

            if not key_found:
                new_lines.append(f"OPENAI_API_KEY={api_key}")

            ENV_PATH.write_text("\n".join(new_lines))

            # Update environment variable in current process
            os.environ["OPENAI_API_KEY"] = api_key

            # Reset the client so it picks up the new key
            llm._CLIENT = None
            logger.info("API key updated successfully")
        except OSError as e:
            logger.error(f"Failed to update API key: {e}")

    def _apply_llm_settings(self) -> None:
        llm._MODEL_NAME = self._settings["cover_letter_model"]
        llm._FILENAME_MODEL = self._settings["filename_model"]
        llm._MAX_TOKENS = self._settings["max_tokens"]
        llm._FILENAME_MAX_TOKENS = self._settings["filename_max_tokens"]
        llm._TEMPERATURE = self._settings["temperature"]
        llm._TOP_P = self._settings["top_p"]

    def _build_widgets(self) -> None:
        header = tk.Label(self, text="Cover Letter Generator", font=("Helvetica", 16, "bold"))
        header.pack(pady=(20, 10))

        tk.Label(self, text="Upload your resume and optionally a sample cover letter.").pack()

        resume_frame = tk.Frame(self, pady=10)
        resume_frame.pack(fill=tk.X, padx=30)

        tk.Button(resume_frame, text="Upload Resume", command=lambda: self._select_file("resume")).pack(
            side=tk.LEFT
        )
        tk.Label(resume_frame, textvariable=self.display_vars["resume"], anchor="w").pack(
            side=tk.LEFT, padx=10
        )

        sample_frame = tk.Frame(self, pady=5)
        sample_frame.pack(fill=tk.X, padx=30)
        tk.Button(
            sample_frame,
            text="Upload Sample (Optional)",
            command=lambda: self._select_file("sample"),
        ).pack(side=tk.LEFT)
        tk.Label(sample_frame, textvariable=self.display_vars["sample"], anchor="w").pack(
            side=tk.LEFT, padx=10
        )

        self.status_var = tk.StringVar(value="Waiting for resume")
        tk.Label(self, textvariable=self.status_var, fg="gray").pack()

    def _select_file(self, slot: str) -> None:
        title = "Select resume" if slot == "resume" else "Select cover letter sample"
        filepath = filedialog.askopenfilename(title=title)
        if not filepath:
            return

        self.selected_files[slot] = filepath
        self.display_vars[slot].set(filepath)
        if slot == "resume":
            self.status_var.set("Cmd+V the job description")
        else:
            self.status_var.set("Sample uploaded. Resume required before paste.")
        self._persist_state()

    def _bind_paste_shortcuts(self) -> None:
        for sequence in ("<<Paste>>", "<Command-v>", "<Control-v>"):
            self.bind_all(sequence, self._handle_paste_event)

    def _handle_paste_event(self, event: tk.Event) -> str | None:
        if self._generation_in_progress:
            self.status_var.set("Already generating a cover letter...")
            return "break"

        resume_path = self.selected_files["resume"]
        if not resume_path:
            self.status_var.set("Upload your resume before pasting the job description.")
            return "break"

        try:
            job_description = self.clipboard_get()
        except tk.TclError:
            self.status_var.set("Clipboard does not contain text.")
            return "break"

        if not job_description.strip():
            self.status_var.set("Clipboard was empty.")
            return "break"

        logger.debug("--- Pasted Job Description Text ---")
        logger.debug(job_description)
        logger.debug("[DEBUG] --- End Job Description Text ---")

        self._generation_in_progress = True
        self._current_job_description = job_description
        self.status_var.set("Generating cover letter...")
        threading.Thread(
            target=self._generate_cover_letter,
            args=(resume_path, job_description, self.selected_files.get("sample")),
            daemon=True,
        ).start()
        return "break"

    def _generate_cover_letter(
        self,
        resume_path: str,
        job_description: str,
        sample_path: Optional[str] = None,
    ) -> None:
        try:
            cover_letter = llm.generate_cover_letter(resume_path, job_description, sample_path)
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            self.after(0, lambda err=exc: self._on_generation_failed(err))
            return

        self.after(0, lambda: self._on_generation_succeeded(cover_letter))

    def _on_generation_succeeded(self, cover_letter: str) -> None:
        self._generation_in_progress = False
        logger.info("Generated cover letter successfully")
        logger.debug(f"Cover Letter Content:\n{cover_letter}")
        try:
            pdf_path = self._save_cover_letter_pdf(cover_letter)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"PDF generation failed: {exc}", exc_info=True)
            self.status_var.set(f"PDF generation failed: {exc}")
            return

        self.status_var.set(f"Cover letter saved to {pdf_path.name}.")
        logger.info(f"Cover letter PDF saved to {pdf_path}")
        self._show_result_dialog(pdf_path)

    def _on_generation_failed(self, error: Exception) -> None:
        self._generation_in_progress = False
        logger.error(f"Cover letter generation failed: {error}", exc_info=True)
        self.status_var.set(f"Generation failed: {error}")

    def _save_cover_letter_pdf(self, cover_letter: str) -> Path:
        self._register_font()
        filename = self._get_dynamic_filename()
        output_dir = Path(self._settings["output_path"])
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Output directory: {output_dir}")
        pdf_path = output_dir / filename
        font_name = self._settings["font_name"]
        font_size = self._settings["font_size"]
        c = canvas.Canvas(str(pdf_path), pagesize=LETTER)
        max_width = LETTER[0] - (2 * MARGIN)
        current_y = LETTER[1] - MARGIN

        for paragraph in cover_letter.splitlines():
            if not paragraph.strip():
                current_y -= font_size + 2
                if current_y <= MARGIN:
                    c.showPage()
                    current_y = LETTER[1] - MARGIN
                continue

            for line in simpleSplit(paragraph, font_name, font_size, max_width):
                if current_y <= MARGIN:
                    c.showPage()
                    current_y = LETTER[1] - MARGIN
                current_y = self._draw_line_with_links(c, line, MARGIN, current_y, font_name, font_size)

        c.save()
        return pdf_path

    def _find_links_in_text(self, text: str) -> List[Tuple[int, int, str, str]]:
        links = []

        # Track positions that are part of emails (to exclude from URL matching)
        email_positions: set[int] = set()

        # Find emails first
        for match in EMAIL_PATTERN.finditer(text):
            email = match.group()
            links.append((match.start(), match.end(), email, f"mailto:{email}"))
            # Mark all positions in this email as used
            for i in range(match.start(), match.end()):
                email_positions.add(i)

        # Find URLs, but skip any that overlap with emails
        for match in URL_PATTERN.finditer(text):
            # Skip if any part of this match overlaps with an email
            if any(i in email_positions for i in range(match.start(), match.end())):
                continue
            url_text = match.group()
            url = url_text if url_text.startswith(('http://', 'https://')) else f"https://{url_text}"
            links.append((match.start(), match.end(), url_text, url))

        # Sort by position
        links.sort(key=lambda x: x[0])
        return links

    def _draw_line_with_links(
        self, c: canvas.Canvas, line: str, x: float, y: float,
        font_name: str, font_size: int
    ) -> float:
        links = self._find_links_in_text(line)

        if not links:
            # No links, just draw the text normally
            c.setFont(font_name, font_size)
            c.drawString(x, y, line)
            return y - (font_size + 2)

        # Draw text segments with links
        current_x = x
        last_end = 0

        for start, end, display_text, url in links:
            # Draw text before the link
            if start > last_end:
                before_text = line[last_end:start]
                c.setFont(font_name, font_size)
                c.drawString(current_x, y, before_text)
                current_x += pdfmetrics.stringWidth(before_text, font_name, font_size)

            # Draw the link (blue and underlined)
            c.setFillColorRGB(0, 0, 0.8)  # Blue color
            c.setFont(font_name, font_size)
            link_width = pdfmetrics.stringWidth(display_text, font_name, font_size)
            c.drawString(current_x, y, display_text)

            # Add the clickable link annotation
            c.linkURL(url, (current_x, y - 2, current_x + link_width, y + font_size), relative=0)

            # Draw underline
            c.setStrokeColorRGB(0, 0, 0.8)
            c.line(current_x, y - 1, current_x + link_width, y - 1)

            current_x += link_width
            c.setFillColorRGB(0, 0, 0)  # Reset to black
            c.setStrokeColorRGB(0, 0, 0)
            last_end = end

        # Draw remaining text after last link
        if last_end < len(line):
            remaining_text = line[last_end:]
            c.setFont(font_name, font_size)
            c.drawString(current_x, y, remaining_text)

        return y - (font_size + 2)

    def _register_font(self) -> None:
        try:
            font_name = self._settings["font_name"]
            if font_name in pdfmetrics.getRegisteredFontNames():
                return
            if self._font_path is None:
                self._font_path = self._resolve_font_path()
            pdfmetrics.registerFont(TTFont(font_name, str(self._font_path)))
            logger.info(f"Font '{font_name}' registered successfully")
        except Exception as e:
            logger.error(f"Failed to register font: {e}", exc_info=True)
            raise

    def _resolve_font_path(self) -> Path:
        font_name = self._settings["font_name"]
        available_fonts = {name.replace("\\ ", " ") for name in families()}
        if font_name not in available_fonts:
            error_msg = f"System font '{font_name}' not found. Ensure it is installed or choose another font."
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)

        font_path = self._find_system_font_file(font_name)
        if not font_path:
            error_msg = f"Unable to locate the '{font_name}' font file on this system."
            logger.error(error_msg)
            raise FileNotFoundError(error_msg)
        return font_path

    def _find_system_font_file(self, family: str) -> Optional[Path]:
        file_stems = [
            family,
            family.replace(" ", ""),
            family.replace(" ", "-"),
            f"{family} Regular",
            f"{family}-Regular",
        ]

        if sys.platform.startswith("darwin"):
            for stem in file_stems:
                for ext in FONT_EXTENSIONS:
                    found = self._mdfind_font(f"{stem}{ext}")
                    if found:
                        return found

        for directory in self._font_search_dirs():
            for stem in file_stems:
                for ext in FONT_EXTENSIONS:
                    candidate = directory / f"{stem}{ext}"
                    if candidate.exists():
                        return candidate
        return None

    def _mdfind_font(self, filename: str) -> Optional[Path]:
        if not sys.platform.startswith("darwin"):
            return None
        query = f'kMDItemKind == "Font" && kMDItemDisplayName == "{filename}"'
        try:
            result = subprocess.run(
                ["mdfind", query], capture_output=True, text=True, check=False
            )
        except FileNotFoundError as e:
            logger.warning(f"mdfind command not found: {e}")
            return None
        for line in result.stdout.splitlines():
            path = Path(line.strip())
            if path.exists():
                return path
        return None

    def _font_search_dirs(self) -> list[Path]:
        dirs = [
            PROJECT_ROOT / "fonts",
            Path.home() / "Library/Fonts",
            Path("/Library/Fonts"),
            Path("/System/Library/Fonts"),
            Path("/System/Library/Fonts/Supplemental"),
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
        ]
        return [d for d in dirs if d.exists()]

    def _load_previous_files(self) -> None:
        if not STATE_PATH.exists():
            return
        try:
            data = json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse state JSON: {e}")
            return
        except OSError as e:
            logger.error(f"Failed to read state file: {e}")
            return

        resume_loaded = False
        state_dirty = False

        for slot in self.selected_files:
            path = data.get(slot)
            if path:
                if Path(path).exists():
                    self.selected_files[slot] = path
                    self.display_vars[slot].set(path)
                    if slot == "resume":
                        resume_loaded = True
                else:
                    # File in config missing on disk; mark state as dirty to clear it
                    logger.warning(f"Previously loaded {slot} file not found: {path}")
                    state_dirty = True

        if state_dirty:
            self._persist_state()

        if resume_loaded:
            self.status_var.set("Cmd+V the job description (using previous resume)")

    def _persist_state(self) -> None:
        try:
            STATE_PATH.write_text(json.dumps(self.selected_files))
        except OSError as e:
            logger.error(f"Failed to persist state: {e}")

    def _get_dynamic_filename(self) -> str:
        if not self._current_job_description:
            logger.info("No job description stored, using default filename")
            return PDF_FILENAME
        try:
            logger.info("Generating dynamic filename...")
            base = llm.generate_filename(self._current_job_description)
            logger.info(f"Generated filename base: {base}")
            return f"{base}.pdf"
        except Exception as e:  # noqa: BLE001
            logger.error(f"Filename generation failed: {e}", exc_info=True)
            return PDF_FILENAME

    def _show_result_dialog(self, pdf_path: Path) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Cover Letter Ready")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="Resume outputted", font=("Helvetica", 14, "bold")).pack(
            padx=20, pady=(20, 10)
        )
        tk.Label(dialog, text=str(pdf_path)).pack(padx=20)

        button_frame = tk.Frame(dialog)
        button_frame.pack(pady=(10, 20))

        tk.Button(button_frame, text="Close", command=dialog.destroy, width=12).pack(side=tk.LEFT, padx=10)
        tk.Button(
            button_frame,
            text="View",
            width=12,
            command=lambda: self._view_pdf(pdf_path, dialog),
        ).pack(side=tk.LEFT, padx=10)

    def _view_pdf(self, pdf_path: Path, dialog: tk.Toplevel) -> None:
        try:
            self._open_file(pdf_path)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Unable to open file {pdf_path}: {exc}", exc_info=True)
            messagebox.showerror("Unable to open file", str(exc))
            return

        dialog.destroy()

    def _open_file(self, pdf_path: Path) -> None:
        try:
            if sys.platform.startswith("darwin"):
                subprocess.run(["open", str(pdf_path)], check=False)
            elif os.name == "nt":
                os.startfile(str(pdf_path))  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(pdf_path)], check=False)
            logger.info(f"Opened file: {pdf_path}")
        except Exception as e:
            logger.error(f"Failed to open file {pdf_path}: {e}", exc_info=True)
            raise


def main() -> None:
    app = FileUploadApp()
    app.mainloop()


if __name__ == "__main__":
    main()
