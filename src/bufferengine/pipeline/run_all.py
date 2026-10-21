"""One entry point that reproduces data-v1.0 from the public BTS files.

    python -m bufferengine.pipeline.run_all

Steps: download (existing valid files are skipped), base build, reference (airport time zones), rotations,
clean (+ dictionary and sample), D2 evidence, feature log, checksums and manifest.
Options: --skip-download, --skip-evidence, --only STEP[,STEP...].
"""

from __future__ import annotations

import argparse
import time

STEPS = ["download", "base", "reference", "rotations", "clean", "evidence", "features", "checksums"]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-download", action="store_true", help="do not call BTS (raw files must already exist)")
    ap.add_argument("--skip-evidence", action="store_true", help="skip the D2 tables and figures")
    ap.add_argument("--only", help="comma-separated subset of: " + ", ".join(STEPS))
    a = ap.parse_args(argv)
    chosen = [s for s in STEPS if (not a.only or s in a.only.split(","))]
    if a.skip_download and "download" in chosen:
        chosen.remove("download")
    if a.skip_evidence and "evidence" in chosen:
        chosen.remove("evidence")

    # Imports are local so that `--help` is instant and Databricks can import single steps.
    from bufferengine.pipeline import (build_base, build_rotations, clean, d2_evidence, download_bts, feature_log, manifest, reference)

    t0 = time.time()
    for step in chosen:
        t = time.time()
        print(f"\n=== {step} ===")
        if step == "download":
            download_bts.run()
        elif step == "base":
            build_base.run()
        elif step == "reference":
            tz, issues = reference.build_from_base()
            print(f"airports_tz.csv: {len(tz)} airports, {tz['timezone'].isna().sum()} without a zone")
        elif step == "rotations":
            build_rotations.run()
        elif step == "clean":
            clean.run()
            clean.write_dictionary()
            clean.write_sample()
        elif step == "evidence":
            d2_evidence.run()
        elif step == "features":
            feature_log.write()
        elif step == "checksums":
            manifest.write_checksums_and_manifest()
        print(f"--- {step} done in {time.time() - t:.0f}s")
    print(f"\nall steps done in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
