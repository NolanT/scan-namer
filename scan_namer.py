#!/usr/bin/env python3
"""
scan-namer - Automatically rename PDFs based on their contents.

Uses OCR to extract text from PDFs, then calls Claude to determine
a descriptive filename with the document date. Processed files are
moved into a 'named' subdirectory; files that can't be processed
are moved into a 'failed' subdirectory.

Usage:
    # Process all PDFs in a folder (one-time)
    python scan_namer.py /path/to/scans

    # Watch folder for new files and rename automatically
    python scan_namer.py /path/to/scans --watch

    # Dry run (show what would be renamed without doing it)
    python scan_namer.py /path/to/scans --dry-run

    # Use a specific model (default: claude-haiku-4-5 for cost efficiency)
    python scan_namer.py /path/to/scans --model claude-sonnet-4-5

Requirements:
    pip install anthropic pytesseract pdf2image Pillow

    System packages (apt/apk):
        tesseract-ocr
        poppler-utils

    Environment variable:
        ANTHROPIC_API_KEY=your-api-key

    Or create a .env file in the same directory as this script:
        ANTHROPIC_API_KEY=your-api-key
"""

import os
import re
import sys
import json
import time
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip install anthropic")
    sys.exit(1)

try:
    import pytesseract
    from pdf2image import convert_from_path
except ImportError:
    print("ERROR: OCR packages not installed. Run: pip install pytesseract pdf2image Pillow")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# OCR settings
OCR_DPI = 200
OCR_MAX_PAGES = 2  # Usually first 1-2 pages have all the info we need
OCR_MAX_CHARS = 3000  # Limit text sent to Claude

# How long to wait after a file appears before processing (seconds)
# Gives the scanner time to finish writing the file
SETTLE_TIME = 10

# Polling interval for watch mode (seconds)
WATCH_INTERVAL = 30

# Subdirectory names for processed files
NAMED_DIR = "named"
FAILED_DIR = "failed"

# Log file name (placed in the scan folder)
LOG_FILENAME = "scan_namer.log"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(folder: Path, verbose: bool = False):
    """Configure logging to both console and file."""
    log_level = logging.DEBUG if verbose else logging.INFO
    log_file = folder / LOG_FILENAME

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # File handler
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    ch.setFormatter(formatter)

    logger = logging.getLogger("scan_namer")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_dotenv(script_dir: Path):
    """Load environment variables from .env file if it exists."""
    env_file = script_dir / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = value


# ---------------------------------------------------------------------------
# OCR extraction
# ---------------------------------------------------------------------------

def extract_text(pdf_path: Path, logger: logging.Logger) -> str:
    """Extract text from a scanned PDF using OCR."""
    try:
        images = convert_from_path(
            str(pdf_path),
            first_page=1,
            last_page=OCR_MAX_PAGES,
            dpi=OCR_DPI,
        )
    except Exception as e:
        logger.error(f"Failed to convert PDF to images: {pdf_path.name} - {e}")
        return ""

    all_text = []
    for i, img in enumerate(images, 1):
        try:
            text = pytesseract.image_to_string(img)
            if text.strip():
                all_text.append(text.strip())
        except Exception as e:
            logger.warning(f"OCR failed on page {i} of {pdf_path.name}: {e}")

    combined = "\n\n".join(all_text)
    return combined[:OCR_MAX_CHARS] if combined else ""


# ---------------------------------------------------------------------------
# Claude API - determine filename
# ---------------------------------------------------------------------------

NAMING_PROMPT = """You are a document filing assistant. Given OCR text extracted from a scanned document, determine the best filename for it.

Rules:
1. Format: "YYYY-MM-DD Description.pdf" (e.g., "2025-03-27 Chase Bank Mortgage Statement.pdf")
2. Use the DATE FROM THE DOCUMENT CONTENTS (invoice date, letter date, statement date, etc.) — NOT today's date
3. If only month/year is available, use "YYYY-MM Description.pdf"
4. If no date can be determined, use just "Description.pdf"
5. The description should be concise but descriptive (company name, document type, key details)
6. Keep filenames under 80 characters total
7. Do NOT include characters that are invalid in filenames: / \\ : * ? " < > |

Respond with ONLY the filename (including .pdf extension), nothing else. No quotes, no explanation."""


def determine_filename(
    ocr_text: str,
    original_name: str,
    client: anthropic.Anthropic,
    model: str,
    logger: logging.Logger,
) -> str | None:
    """Ask Claude to determine the best filename based on OCR text."""
    if not ocr_text or len(ocr_text.strip()) < 20:
        logger.warning(f"Insufficient OCR text for {original_name} ({len(ocr_text)} chars)")
        return None

    try:
        response = client.messages.create(
            model=model,
            max_tokens=150,
            messages=[{
                "role": "user",
                "content": f"Original scan filename: {original_name}\n\nOCR text from document:\n\n{ocr_text}",
            }],
            system=NAMING_PROMPT,
        )
        suggested = response.content[0].text.strip()

        # Basic validation
        if not suggested.lower().endswith(".pdf"):
            suggested += ".pdf"

        # Remove any invalid filename characters
        invalid_chars = r'[\\/:*?"<>|]'
        suggested = re.sub(invalid_chars, "", suggested)

        # Ensure it's not empty after cleanup
        if len(suggested) <= 4:  # just ".pdf"
            return None

        logger.debug(f"Claude suggested: {original_name} -> {suggested}")
        return suggested

    except anthropic.RateLimitError:
        logger.warning("Rate limited by Claude API. Waiting 60 seconds...")
        time.sleep(60)
        return determine_filename(ocr_text, original_name, client, model, logger)

    except Exception as e:
        logger.error(f"Claude API error for {original_name}: {e}")
        return None


# ---------------------------------------------------------------------------
# Rename logic
# ---------------------------------------------------------------------------

def ensure_subdirs(folder: Path, logger: logging.Logger) -> tuple[Path, Path]:
    """Create the named/ and failed/ subdirectories if they don't exist."""
    named_dir = folder / NAMED_DIR
    failed_dir = folder / FAILED_DIR
    named_dir.mkdir(exist_ok=True)
    failed_dir.mkdir(exist_ok=True)
    return named_dir, failed_dir


def move_to_dir(pdf_path: Path, dest_dir: Path, new_name: str, dry_run: bool, logger: logging.Logger) -> bool:
    """Move a file into a subdirectory with a new name, handling conflicts."""
    target = dest_dir / new_name

    # Handle conflicts
    if target.exists():
        stem = Path(new_name).stem
        suffix = Path(new_name).suffix
        counter = 2
        while target.exists():
            new_name = f"{stem} ({counter}){suffix}"
            target = dest_dir / new_name
            counter += 1
        logger.info(f"  Conflict resolved: using {new_name}")

    if dry_run:
        logger.info(f"[DRY RUN] {pdf_path.name} -> {dest_dir.name}/{new_name}")
        return True

    try:
        shutil.move(str(pdf_path), str(target))
        logger.info(f"  Moved: {pdf_path.name} -> {dest_dir.name}/{new_name}")
        return True
    except Exception as e:
        logger.error(f"  Failed to move {pdf_path.name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Process files
# ---------------------------------------------------------------------------

def find_pdf_files(folder: Path) -> list[Path]:
    """Find all PDF files in the root of the folder (not in subdirectories)."""
    return sorted([
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() == ".pdf"
    ])


def process_file(
    pdf_path: Path,
    named_dir: Path,
    failed_dir: Path,
    client: anthropic.Anthropic,
    model: str,
    dry_run: bool,
    logger: logging.Logger,
) -> bool:
    """Process a single PDF: OCR -> Claude -> rename and move to named/, or move to failed/."""
    logger.info(f"Processing: {pdf_path.name} ({pdf_path.stat().st_size / 1024:.0f} KB)")

    # Extract text via OCR
    logger.info(f"  OCR: extracting text from {pdf_path.name}...")
    ocr_text = extract_text(pdf_path, logger)
    if not ocr_text:
        logger.warning(f"  OCR: no text extracted from {pdf_path.name}")
        logger.info(f"  Moving to {FAILED_DIR}/")
        move_to_dir(pdf_path, failed_dir, pdf_path.name, dry_run, logger)
        return False
    logger.info(f"  OCR: extracted {len(ocr_text)} chars from {pdf_path.name}")

    # Ask Claude for a filename
    logger.info(f"  AI: requesting filename suggestion for {pdf_path.name}...")
    new_name = determine_filename(ocr_text, pdf_path.name, client, model, logger)
    if not new_name:
        logger.warning(f"  AI: could not determine name for {pdf_path.name}")
        logger.info(f"  Moving to {FAILED_DIR}/")
        move_to_dir(pdf_path, failed_dir, pdf_path.name, dry_run, logger)
        return False
    logger.info(f"  AI: suggested \"{new_name}\"")

    # Move to named/ with new name
    return move_to_dir(pdf_path, named_dir, new_name, dry_run, logger)


def process_folder(
    folder: Path,
    client: anthropic.Anthropic,
    model: str,
    dry_run: bool,
    logger: logging.Logger,
) -> tuple[int, int]:
    """Process all PDF files in a folder. Returns (success, fail) counts."""
    named_dir, failed_dir = ensure_subdirs(folder, logger)

    files = find_pdf_files(folder)
    if not files:
        logger.info("No PDF files found to process")
        return 0, 0

    logger.info(f"Found {len(files)} PDF(s) to process")

    success = 0
    fail = 0
    for i, f in enumerate(files, 1):
        logger.info(f"[{i}/{len(files)}] {f.name}")
        if process_file(f, named_dir, failed_dir, client, model, dry_run, logger):
            success += 1
        else:
            fail += 1

        # Small delay between API calls to be polite
        if i < len(files):
            time.sleep(1)

    logger.info(f"Done: {success} named, {fail} failed")
    return success, fail


# ---------------------------------------------------------------------------
# Watch mode
# ---------------------------------------------------------------------------

def watch_folder(
    folder: Path,
    client: anthropic.Anthropic,
    model: str,
    dry_run: bool,
    logger: logging.Logger,
):
    """Watch folder for new PDFs and rename them automatically."""
    named_dir, failed_dir = ensure_subdirs(folder, logger)

    logger.info(f"Watching {folder} for new PDFs (Ctrl+C to stop)")
    logger.info(f"Polling every {WATCH_INTERVAL}s, settle time {SETTLE_TIME}s")
    logger.info(f"Named files -> {NAMED_DIR}/, failed files -> {FAILED_DIR}/")

    # Track files we've processed (by original name) to avoid reprocessing
    processed: set[str] = set()

    # Process any existing PDF files first
    existing = find_pdf_files(folder)
    if existing:
        logger.info(f"Found {len(existing)} existing PDF(s) to process")
        for i, f in enumerate(existing, 1):
            logger.info(f"[startup {i}/{len(existing)}] {f.name}")
            processed.add(f.name)
            process_file(f, named_dir, failed_dir, client, model, dry_run, logger)
            if i < len(existing):
                time.sleep(1)
        logger.info("Finished processing existing files. Now watching for new ones...")
    else:
        logger.info("No existing PDF files found. Watching for new ones...")

    try:
        while True:
            time.sleep(WATCH_INTERVAL)

            current_files = find_pdf_files(folder)
            new_files = [
                f for f in current_files
                if f.name not in processed
            ]

            for f in new_files:
                # Check file age - wait for scanner to finish writing
                try:
                    age = time.time() - f.stat().st_mtime
                except FileNotFoundError:
                    continue

                if age < SETTLE_TIME:
                    logger.debug(f"File {f.name} is too new ({age:.0f}s), waiting...")
                    continue

                logger.info(f"New file detected: {f.name}")
                processed.add(f.name)
                process_file(f, named_dir, failed_dir, client, model, dry_run, logger)

    except KeyboardInterrupt:
        logger.info("Watch mode stopped by user")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="scan-namer - Rename PDFs based on their contents using OCR + Claude AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s /path/to/scans                  Process all PDFs in folder
  %(prog)s /path/to/scans --watch           Watch for new files continuously
  %(prog)s /path/to/scans --dry-run         Preview renames without doing them
  %(prog)s /path/to/scans --model claude-sonnet-4-5   Use a different model

Environment:
  ANTHROPIC_API_KEY    Your Anthropic API key (required)
                       Can also be set in a .env file next to this script
        """,
    )
    parser.add_argument("folder", type=Path, help="Folder containing scanned PDFs")
    parser.add_argument("--watch", action="store_true", help="Watch folder for new files")
    parser.add_argument("--dry-run", action="store_true", help="Preview renames without executing")
    parser.add_argument("--model", default="claude-haiku-4-5", help="Claude model to use (default: claude-haiku-4-5)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show debug output")
    parser.add_argument("--dpi", type=int, default=200, help="OCR resolution (default: 200)")
    parser.add_argument("--settle-time", type=int, default=10, help="Seconds to wait for file to finish writing (default: 10)")
    parser.add_argument("--watch-interval", type=int, default=30, help="Seconds between folder checks in watch mode (default: 30)")

    args = parser.parse_args()

    # Apply config overrides
    OCR_DPI = args.dpi
    SETTLE_TIME = args.settle_time
    WATCH_INTERVAL = args.watch_interval

    # Validate folder
    if not args.folder.is_dir():
        print(f"ERROR: {args.folder} is not a directory")
        sys.exit(1)

    # Load .env from script directory
    script_dir = Path(__file__).resolve().parent
    load_dotenv(script_dir)

    # Setup logging
    logger = setup_logging(args.folder, args.verbose)
    logger.info(f"scan-namer started - folder: {args.folder}")
    logger.info(f"Model: {args.model}, DPI: {OCR_DPI}")
    if args.dry_run:
        logger.info("DRY RUN mode - no files will be renamed")

    # Initialize Claude client
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set. Set it as an environment variable or in a .env file.")
        sys.exit(1)

    client = anthropic.Anthropic()

    # Run
    if args.watch:
        watch_folder(args.folder, client, args.model, args.dry_run, logger)
    else:
        success, fail = process_folder(args.folder, client, args.model, args.dry_run, logger)
        sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
