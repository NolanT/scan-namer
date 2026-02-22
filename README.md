# scan-namer

Automatically rename scanned PDFs based on their contents using OCR and Claude AI.

Turns files like `20250413_004096.pdf` into `2025-03-27 Cadence Bank Lien Release.pdf`.

## How It Works

1. Watches a folder for PDFs with scan-style names (e.g., `20250413_004096.pdf`)
2. Uses OCR (Tesseract) to extract text from the scanned pages
3. Sends the text to Claude AI to determine a descriptive filename with the document date
4. Renames the file automatically

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
    image: ghcr.io/NolanT/scan-namer:main
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
echo "ANTHROPIC_API_KEY=sk-ant-api03-your-key-here" > .env
```

Get an API key at [console.anthropic.com](https://console.anthropic.com/).

### 4. Start

```bash
docker compose up -d
```

### 5. Check logs

```bash
docker compose logs -f
```

Or check `scan_namer.log` in your scan folder.

## Building Locally

If you prefer to build the image yourself instead of pulling from GHCR:

```bash
git clone https://github.com/NolanT/scan-namer.git
cd scan-namer
docker compose up -d --build
```

Or edit `docker-compose.yml` — comment out `image:` and uncomment `build: .`.

## GitHub Actions (CI/CD)

This repo includes a GitHub Actions workflow (`.github/workflows/build-and-publish.yml`) that automatically builds and publishes the Docker image to GitHub Container Registry.

**What triggers a build:**
- Push to `main` branch
- Creating a version tag (e.g., `v1.0.0`)
- Pull requests (build only, no push)

**Multi-architecture:** Images are built for both `linux/amd64` and `linux/arm64`, so they work on Synology NAS models with Intel or ARM processors.

**To set up:**
1. Push this repo to GitHub
2. Go to Settings > Actions > General and ensure "Read and write permissions" is enabled under "Workflow permissions"
3. Push a commit or tag — the image will be published to `ghcr.io/nolant/scan-namer`
4. The package may initially be private. To make it public: go to your GitHub profile > Packages > scan-namer > Package settings > Change visibility

**Pulling a specific version:**
```bash
# Latest from main branch
docker pull ghcr.io/NolanT/scan-namer:main

# Specific release
docker pull ghcr.io/NolanT/scan-namer:1.0.0

# Specific commit
docker pull ghcr.io/NolanT/scan-namer:sha-abc1234
```

## Usage Modes

### Watch mode (default)

Runs continuously, checking for new scan-named PDFs every 30 seconds:

```bash
docker compose up -d
```

### One-time batch

Process all current scan-named files and exit:

```yaml
# In docker-compose.yml, add:
command: ["/scans"]
```

### Dry run

Preview what would be renamed without actually renaming:

```yaml
command: ["/scans", "--dry-run"]
```

### Custom options

```yaml
command: ["/scans", "--watch", "--settle-time", "15", "--watch-interval", "60", "--model", "claude-sonnet-4-5"]
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--watch` | off | Watch folder continuously for new files |
| `--dry-run` | off | Preview renames without executing |
| `--model` | `claude-haiku-4-5` | Claude model (~$0.001/file with Haiku) |
| `--dpi` | `200` | OCR resolution (higher = more accurate, slower) |
| `--settle-time` | `10` | Seconds to wait for file to finish writing |
| `--watch-interval` | `30` | Seconds between folder checks |
| `--verbose` | off | Show debug output |

## Cost

Using Claude Haiku (default), each file costs roughly $0.001 to process. A batch of 100 files would cost about $0.10.

## File Name Pattern

The script looks for PDFs matching the pattern `XXXXXXXX_XXXX.pdf` (8 digits, underscore, 4+ digits). This matches the default naming from most document scanners. Files that don't match this pattern are left untouched.

## Troubleshooting

**No text extracted**: The PDF might be blank or contain only photos. These are skipped.

**Wrong date picked**: Claude uses the document's content date (invoice date, letter date, etc.), not the scan date. Occasionally OCR errors can cause misreads — check the log for the OCR text.

**Container won't start**: Make sure your `.env` file has a valid `ANTHROPIC_API_KEY` and the volume path in `docker-compose.yml` exists on your Synology.

**GHCR image is private**: After the first build, go to GitHub > your profile > Packages > scan-namer > Package settings > Change visibility to Public.
