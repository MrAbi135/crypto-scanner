# Golden Datasets (placeholder — format fixed by Sprint S3)

Curated, hand-labeled SLS datasets and their expected detector outputs. Golden
datasets are the detector-development workflow (Constitution §32.3–§32.4): a
detector without a golden dataset may not ship, and passing datasets grow
monotonically — they are never deleted or weakened to make a release.

## Rules (provisional; S3 ratifies the format)
- **Curated input + expected output, versioned together.** Each case pins the
  `algo_version` / `param_set_version` it was labeled against.
- **Labeling provenance.** Every case records who labeled it and against which
  SLS section(s), so a reviewer can trace `expected` back to doctrine.
- **Raw captures stay local.** `golden/**/raw/` is gitignored; only curated,
  trimmed, labeled datasets are committed.
- **Determinism.** Replaying a dataset must produce byte-identical output
  (Constitution §32.5); the golden harness (S3) hash-compares runs.

The dataset format specification and the first curated dataset (BTC
swing/structure cases) land in Sprint S3.
