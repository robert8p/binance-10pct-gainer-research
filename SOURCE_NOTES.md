# Source notes

Implementation decisions follow the official service documentation current at package construction:

- Binance Spot public market-data REST API and public archive conventions.
- Supabase backend secret keys for trusted server-side access.
- Supabase Storage standard uploads for small files and TUS resumable uploads for files above 6 MB, using 6 MB chunks and the direct storage hostname.
- Render Blueprint service, environment-variable and persistent-disk configuration.
- ChatGPT file uploads have a 512 MB hard limit per file, so raw evidence is normalised and sharded.

The application remains read-only and requires no Binance account credentials.
