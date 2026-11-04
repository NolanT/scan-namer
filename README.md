# scan-namer

Automatically name scanned PDFs from their contents with OCR and Claude, and make them searchable.

Drop a PDF into the watched folder and it gets a searchable text layer, a name like `2025-03-27 Acme Bank Mortgage Statement.pdf`, and is moved into a `named/` subfolder. Files that can't be read or named (blank pages, photos) go to `failed/`.

## How It Works

1. Watches a folder for PDF files
2. Adds an invisible, searchable text layer to every page with [OCRmyPDF](https://ocrmypdf.readthedocs.io/) (Tesseract). Pages look exactly the same; pages that already have text are left alone. Scans become searchable in Synology Universal Search, Spotlight, and any PDF viewer.
3. Sends the first pages' text to Claude, which picks a descriptive filename using the document's own date
4. Optionally suggests which folder of your archive the document belongs in (see [Filing suggestions](#filing-suggestions-optional))
5. Moves the file into `named/`; files that can't be read or named go to `failed/`

Turn off step 2 with `--no-text-layer`. OCR uses one CPU core by default; set `OCR_JOBS=2` to use more.

## Quick Start (Synology NAS)

### 1. Create a project folder on your NAS

```bash
mkdir -p /volume1/docker/scan-namer
cd /volume1/docker/scan-namer
```

### 2. Create `docker-compose.yml`

```yaml
services:
  scan-namer:
    image: ghcr.io/nolant/scan-namer:main
    container_name: scan-namer
    restart: unless-stopped
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    volumes:
      - /volume1/your-scan-folder:/scans
```

Replace `/volume1/your-scan-folder` with your actual scan folder path.

### 3. Create `.env` file

```bash
echo "ANTHROPIC_API_KEY=sk-ant-...your-key-here" > .env
```

Get an API key at [console.anthropic.com](https://console.anthropic.com/).

### 4. Start and check logs

```bash
docker compose up -d
docker compose logs -f
```

Or read `scan_namer.log` in your scan folder (it rotates at 2 MB, keeping 3 old copies).

## Filing suggestions (optional)

If you file scans into your own folder structure later, scan-namer can suggest the destination for each one. Mount your document archive **read-only** and point `FILING_ROOT` at it:

```yaml
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - FILING_ROOT=/archive
      - FILING_EXCLUDE=Private,Old Backups   # optional: folders to leave out
    volumes:
      - /volume1/your-scan-folder:/scans
      - /volume1/your-documents:/archive:ro
```

scan-namer reads the folder names (up to 4 levels deep) at startup and hourly, sends that list to Claude together with each scan's text, and accepts only an answer that is an existing folder. Nothing is moved: each named scan gets a row in `named/_filing_suggestions.csv` (`named_at, file, suggested_folder`) and a line in the log, so filing is a quick copy of the suggested path.

Privacy: with this on, your folder names are sent to the Claude API along with the scan text. They are read from your NAS at runtime and never stored anywhere else.

## Making existing scans searchable (backfill)

Files that were named before the text layer existed are image-only. Add text layers to everything already in `named/`:

```bash
docker exec scan-namer python scan_namer.py /scans --backfill --dry-run   # preview
docker exec scan-namer python scan_namer.py /scans --backfill
```

To do the same for scans you've already filed elsewhere, mount that folder and walk it recursively. `--exclude` skips a folder name anywhere (`Private`) or a relative path (`Work/Old Code`) and is repeatable; `#recycle`, `@eaDir`, `#snapshot` and hidden folders are always skipped. Keep the log outside the archive:

```bash
sudo docker run --rm \
  -v "/volume1/your-documents:/docs" -v /volume1/docker/scan-namer:/logs \
  ghcr.io/nolant/scan-namer:main /docs --backfill --recursive \
  --exclude Private --log-file /logs/backfill.log --dry-run
```

Drop `--dry-run` (and use `-d --name scan-backfill` instead of `--rm` to run it in the background) to do it.

- Files keep their name, look, modified date, owner and NAS permissions (contents are rewritten in place), so **take a snapshot or backup first**.
- It's safe to stop and re-run: files that already have text are skipped.
- Only one backfill can run on a folder at a time; a second one exits with a message.
- Blank pages and photos are reported as "no readable text" and left alone.

## Usage Modes

| Mode | `command:` in docker-compose.yml |
|------|------|
| Watch (default) | *(none)* |
| One-time batch | `["/scans"]` |
| Dry run | `["/scans", "--dry-run"]` |
| Custom | `["/scans", "--watch", "--settle-time", "15", "--watch-interval", "60"]` |

## Options

| Flag / variable | Default | Description |
|------|---------|-------------|
| `--watch` | off | Watch the folder continuously |
| `--dry-run` | off | Preview without changing anything |
| `--model` / `SCAN_NAMER_MODEL` | `claude-haiku-5-5` | Claude model |
| `--filing-root` / `FILING_ROOT` | off | Archive to suggest destination folders from |
| `--filing-exclude` / `FILING_EXCLUDE` | none | Folders to leave out of suggestions |
| `--no-text-layer` | off | Don't add a searchable text layer |
| `OCR_JOBS` | `1` | CPU cores for OCR |
| `--backfill` | off | Add text layers to `named/` (or with `--recursive`, a whole tree), then exit |
| `--exclude` | none | With `--backfill --recursive`: folders to skip |
| `--log-file` | `FOLDER/scan_namer.log` | Where to write the log |
| `--dpi` | `200` | Resolution for the fallback OCR used when no text layer can be added |
| `--settle-time` | `10` | Seconds a file must be unchanged before it's processed |
| `--watch-interval` | `30` | Seconds between folder checks |
| `--verbose` | off | Debug output |

If Anthropic retires the model, the log says "model not found" and new scans wait in the scan folder (nothing goes to `failed/`). Set `SCAN_NAMER_MODEL` to a current model and restart the container; the waiting scans are then named.

## Cost

Naming uses a few thousand tokens per scan, a fraction of a cent with the default Haiku model. With filing suggestions on, the folder list is sent each time but cached, so it adds little.

## Folder Structure

```
/scans/
  named/                       # renamed, searchable files
    2025-03-27 Acme Bank Mortgage Statement.pdf
    2025-01-15 City Water Utility Bill.pdf
    _filing_suggestions.csv    # only with filing suggestions on
  failed/                      # files that couldn't be read or named
    blank_page.pdf
  scan_namer.log
  new_scan.pdf                 # waiting to be processed
```

Only PDFs in the root of the watched folder are processed; `named/` and `failed/` are left alone. A new scan that reuses an old file name (scanner counter reset) is still processed.

## Building Locally

```bash
git clone https://github.com/NolanT/scan-namer.git
cd scan-namer
docker compose up -d --build
```

Or edit `docker-compose.yml`: comment out `image:` and uncomment `build: .`.

## GitHub Actions (CI/CD)

`.github/workflows/build-and-publish.yml` builds and publishes the image to GitHub Container Registry on pushes to `main` and version tags (pull requests build only). Images are built for `linux/amd64` and `linux/arm64`, so they work on Intel and ARM Synology models.

To set up: enable "Read and write permissions" under Settings > Actions > General > Workflow permissions, then push. The package may start private: GitHub profile > Packages > scan-namer > Package settings > Change visibility.

```bash
docker pull ghcr.io/nolant/scan-namer:main          # latest
docker pull ghcr.io/nolant/scan-namer:1.0.0         # a release
docker pull ghcr.io/nolant/scan-namer:sha-abc1234   # a commit
```

## Troubleshooting

**No text extracted**: the PDF is blank or only photos; it's moved to `failed/`.

**Wrong date**: Claude uses the document's own date (invoice, letter, statement), not the scan date. OCR errors occasionally cause misreads; the log shows each decision.

**"model not found"**: the model was retired. Set `SCAN_NAMER_MODEL` to a current Haiku model and restart; scans waiting in the folder are then named.

**"Another scan-namer backfill is already running"**: wait for the other run to finish, or stop it.

**Container won't start**: check that `.env` has a valid `ANTHROPIC_API_KEY` and the volume paths exist on your NAS.
