# Releasing data-v1.0

`aa_clean_v1.0.parquet` (68.4 MB) is larger than the 50 MB repository limit, so it is **not committed**. It is attached to a GitHub Release
under the tag `data-v1.0`. The repository holds everything needed to verify it: `data/CHECKSUMS.sha256`, `data/data_manifest.json`,
the dictionary, the cleaning script and a 5,000-row sample (`data/sample/aa_clean_sample.csv`).

## Steps

1. Push the commit and the tag (the tag is created locally by the D2 commit step):

   ```bash
   git push origin vaibhav_new_build
   git push origin data-v1.0
   ```

2. On GitHub open the repository, then **Releases**, then **Draft a new release**.
3. **Choose a tag:** select the existing tag `data-v1.0`.
4. **Release title:** `data-v1.0`.
5. **Attach binaries:** drag in `data/processed/aa_clean_v1.0.parquet` from your computer (a release asset can be up to 2 GB). Optionally also attach `data/CHECKSUMS.sha256`.
6. **Release notes** (paste):

   ```
   Cleaned dataset v1.0: American Airlines mainline flights from the BTS Reporting Carrier On-Time Performance files,
   January 2025 to August 2026. 1,641,071 rows (no rows deleted), 59 columns.
   Verify: SHA-256 of aa_clean_v1.0.parquet is in data/CHECKSUMS.sha256 (processed/aa_clean_v1.0.parquet).
   Data dictionary: docs/data_dictionary_clean.md. Licence wording and caveats: DATA_NOTICE.md.
   For academic use. Not connected to airline systems.
   ```

7. Leave "Set as the latest release" ticked and press **Publish release**. While the repository is private, the asset is visible only to people with access.

## Using the asset

```bash
# download aa_clean_v1.0.parquet from the release into data/processed/, then:
python -m bufferengine.pipeline.manifest --verify
```

`--verify` reports a problem for any file in `CHECKSUMS.sha256` that is missing (for example the other parquet tables, which you only have after running
`python -m bufferengine.pipeline.run_all`) or different. To check only the downloaded file on Windows PowerShell:

```powershell
Get-FileHash data\processed\aa_clean_v1.0.parquet -Algorithm SHA256
```

and compare the hash with the line for `processed/aa_clean_v1.0.parquet` in `data/CHECKSUMS.sha256`.

## Authoritative checksum

The file SHA-256 is authoritative: the clean step writes identical bytes on repeated runs (checked twice, and again in a full rebuild from the raw files).
For the other parquet tables the manifest also stores `content_sha256`, a hash of the logical content; if a file hash ever differs after a rebuild with a
different library version, an equal `content_sha256` means the data are identical.
