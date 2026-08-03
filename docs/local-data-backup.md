# Local personal-data backup with rclone

The repository includes a small local-only adapter that copies a personal-data directory to an
[rclone](https://rclone.org/) remote. Its initial use case is backing up data produced by the
[local Garmin Connect downloader](garmin-sidecar.md), but profiles can point at any local directory
and an rclone destination whose remote name contains no whitespace and does not begin with `-`.

The adapter deliberately uses `rclone copy`, not `rclone sync`: files absent locally are **not**
deleted from the backup. It invokes the installed rclone binary and does not handle Google
credentials itself.

## Set up Google Drive

Install rclone (for example, `brew install rclone`) and create a remote:

```bash
rclone config
rclone lsd gdrive:
```

`gdrive` is only an example remote name. Authentication and tokens stay in rclone's local config;
do not commit that config to this repository.

Copy and edit the example backup configuration:

```bash
mkdir -p ~/.config/fitness-coach-agent
cp tools/self-data-backup/config.example.toml ~/.config/fitness-coach-agent/backups.toml
```

The checked-in example expects Garmin downloads in `downloads/garmin-fit` and stores the contents
under `gdrive:fitness-coach-agent/garmin-fit`. Relative source paths are resolved from the directory
where the command runs. `~` is supported in source, rclone config, and CLI override paths.

## Run a backup

Preview the copy first:

```bash
uv run --package fitness-coach-self-data-backup self-data-backup backup \
  --profile garmin-gdrive \
  --dry-run --verbose
```

Then run it without `--dry-run`:

```bash
uv run --package fitness-coach-self-data-backup self-data-backup backup \
  --profile garmin-gdrive
```

To back up a Garmin downloader output folder other than the configured default:

```bash
uv run --package fitness-coach-self-data-backup self-data-backup backup \
  --profile garmin-gdrive \
  --source /Volumes/private-activities/garmin
```

Use `--config /path/to/backups.toml` to select another configuration. A failed rclone operation
returns rclone's non-zero exit status, making the command suitable for launchd, cron, or a local
scheduler. The tool is not intended for Vercel or GitHub Actions, and it never uploads Garmin or
rclone authentication files unless a profile's source directory explicitly contains them.

## Configuration reference

```toml
version = 1

[rclone]
binary = "rclone" # may also be an absolute executable path
config = "~/.config/rclone/rclone.conf" # optional

[profiles.garmin-gdrive]
source = "downloads/garmin-fit"
destination = "gdrive:fitness-coach-agent/garmin-fit"
checksum = true
```

Each profile requires a local `source` directory and a remote `destination` in `remote:path` form.
`checksum` defaults to `true`; set it to `false` for a remote that does not support hashes well.
Running a profile again is incremental according to rclone's normal `copy` behavior.
