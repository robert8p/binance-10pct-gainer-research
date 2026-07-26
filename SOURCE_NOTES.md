# Source notes

Implementation decisions follow the official service documentation current at package construction:

- Binance Spot public market-data REST API and public archive conventions.
- Supabase backend secret keys for trusted server-side access.
- Supabase Storage standard uploads for small files and TUS resumable uploads for files above 6 MB, using 6 MB chunks and the direct storage hostname.
- Supabase Storage list/remove operations for job-prefix cleanup and object-info retrieval for persisted-size verification.
- Supabase documents `409 Conflict` for concurrent or duplicate resumable uploads; v1.1.2 resumes only where the server offset proves progress and otherwise fails immediately.
- Render Blueprint service, environment-variable and persistent-disk configuration.
- ChatGPT file uploads have a 512 MB hard limit per file, so raw evidence is normalised and sharded.

The application remains read-only and requires no Binance account credentials.
