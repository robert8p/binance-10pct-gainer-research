# Quality report — v1.2.0

## Automated validation

- 18 tests passed.
- All application and test Python files compile.
- `render.yaml` parses as valid YAML.
- ZIP integrity is checked after packaging.

## Research-design tests

The test suite verifies that:

- positives and negatives originate from the same quarter-hour grid;
- the entry benchmark is the interval open rather than the future interval low;
- incomplete eight-hour forward windows are excluded;
- negative candidates do not require selective one-minute fetching;
- overlapping positive windows are merged only for data-fetch efficiency;
- chronological split boundaries include an eight-hour embargo;
- subject evidence views exclude the decision interval and all later bars;
- export attempts use unique Supabase Storage prefixes.

## Operational tests

The suite also verifies:

- new `sb_secret_…` keys are sent as both API key and bearer credential;
- legacy service-role JWTs remain compatible;
- files over 6 MB use the direct Supabase Storage hostname and TUS;
- stale nested attempt folders are recursively listed and deleted;
- uploaded object size is verified;
- a TUS 409 without server-side offset progress fails immediately.

## Remaining validation required in production

The first five-symbol proof run must confirm Binance request behaviour, live Supabase writes, full export construction and Render disk stability. No local test can prove third-party service availability or historical data completeness.
